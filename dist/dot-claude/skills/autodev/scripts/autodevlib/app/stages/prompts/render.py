"""ステージと統括に渡すプロンプトの組み立て。

- **指示書の本文はプロンプトに入れない。** 指示書のパスと、指示書の `<名前>`（プレースホルダ）が
  このランで何を指すかの表だけを渡し、指示書はモデルが自分で読む
- **何を渡すかは指示書の「入力」の表が決める。** ここは指示書が挙げたプレースホルダを、下の
  `SOURCES` の出どころから埋めるだけで、どれを渡すかを自分で選ばない。指示書に足したプレースホルダが
  `SOURCES` に無ければ、組み立ての時点で落ちる（検査でも確かめる）
- 埋める値は、イベントストア（再生した集約と、確定したイベントの列）と成果物の在りか
  （`ArtifactRef`）から読む。実行のときにしか分からない値（ステージの cwd）は、呼ぶ側が渡す
- 1 行で書ける値は表に、複数行の値（タスクの中身・指摘の台帳など）は表の後ろの節に置く
- 破ると取り返しがつかない決まりだけを `--append-system-prompt` に置く。指示書の要約は
  置かない（出どころが 2 つになる）
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from typing import Any

from ....domain import codec
from ....domain.aggregates.base import Aggregate
from ....domain.supervision import Notice, Supervisor, Wake, wake_for
from ....domain.value_objects.event_id import EventId
from ....domain.value_objects.stream_id import StreamId
from ....domain.value_objects.task_id import TaskId
from ....infra.paths import RunPaths
from ...driving.mainloop import Delivery
from ..stage_context import StageContext, StagePrompt
from .assets import asset_name, contract_inputs, contract_path
from .sources import SOURCES, Block, Sources, _camel_keys, _json

_INLINE_UNSAFE = re.compile(r"[\n|`]")


def render(name: str, sources: Sources, role: str) -> str:
    """指示書 `name` の「入力」の表のプレースホルダを全部埋めた、利用者のメッセージ。"""
    rows: list[str] = []
    sections: list[str] = []
    for placeholder in contract_inputs(name):
        source = SOURCES.get(placeholder)
        if source is None:
            raise KeyError(f"指示書 {name} の <{placeholder}> を埋める出どころが無い（SOURCES）")
        value = source(sources)
        if isinstance(value, str) and not _INLINE_UNSAFE.search(value):
            rows.append(f"| `<{placeholder}>` | {f'`{value}`' if value else '（空）'} |")
            continue
        text = value.text if isinstance(value, Block) else value
        rows.append(f"| `<{placeholder}>` | 下の節「`<{placeholder}>`」 |")
        sections.append(f"## `<{placeholder}>`\n\n{text.strip() or '（空）'}")
    head = [
        f"あなたは autodev の {role} である。",
        "",
        f"指示書 `{contract_path(name)}` を最初に読み、そのとおりに務める。"
        "指示書の中の `<名前>` は、下の表の値を指す。",
        "",
        "| 表記 | 値 |",
        "| --- | --- |",
        *rows,
    ]
    return "\n".join(head) + ("\n\n" + "\n\n".join(sections) if sections else "") + "\n"


def system_rules(role: str) -> str:
    """`--append-system-prompt` に置く、破ると取り返しがつかない決まり。"""
    return "\n".join(
        [
            f"あなたは autodev の {role} である。進め方を決めるのは driver で、あなたは自分の役だけを"
            "務める。次のステージを自分で呼ばない。",
            "",
            "## 何があっても守ること",
            "",
            "- `gh` を呼ばない。`git push` をしない（GitHub を触るのは driver だけ）。",
            "- 指示書が書いてよいとした所の外へ書かない（フックが止める）。",
            "- 終わりに結果を StructuredOutput ツールで 1 つだけ返す。形はそのツールの入力スキーマにある。",
        ]
    )


def _notice_body(
    notice: Notice | None,
    replay: EventId | None,
    delivery: Delivery,
    find: Callable[[EventId], Delivery | None],
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "notice": notice.value if notice is not None else None,
        "id": delivery.event_id.value,
        "event": type(delivery.event).__name__,
        **_camel_keys(codec.to_json(delivery.event)),
    }
    if replay is not None and (original := find(replay)) is not None:
        # 起こし直す元の知らせを、そのときと同じ形で入れ子にする。元も起こし直しなら、最初の知らせ
        # までたどって 1 段だけ載せる（起こし直すたびに入れ子が深くならない。途中の回答は載せない）
        inner = wake_for(original.event, original.event_id)
        seen = {original.event_id}
        while (
            inner is not None
            and inner.replay is not None
            and inner.replay not in seen
            and (earlier := find(inner.replay)) is not None
        ):
            original, seen = earlier, seen | {earlier.event_id}
            inner = wake_for(original.event, original.event_id)
        body["retry"] = _notice_body(
            inner.notice if inner is not None else None, None, original, find
        )
    return body


def render_notice(
    wake: Wake,
    delivery: Delivery,
    find: Callable[[EventId], Delivery | None] = lambda _: None,
) -> str:
    """統括を起こしたイベントを、知らせの JSON にする。イベントの欄をそのまま載せる。

    起こし直す（`Wake.replay`）なら、元の知らせを `find` で確定したイベントの列から引いて `retry` に載せる。
    """
    return _json(_notice_body(wake.notice, wake.replay, delivery, find)).text


def render_rejection(reason: str) -> str:
    """返した判断が拒まれたときの知らせ（同じセッションに差し戻す）。"""
    body = {"notice": Notice.DECISION_REJECTED.value, "reason": reason}
    return (
        "## `<通知>`\n\n"
        + _json(body).text
        + "\n\n返した判断は実行していない。理由を読んで、直した判断を 1 つ返す。\n"
    )


class Prompts:
    """ステージ（実行器が呼ぶ `StagePrompts`）と統括のプロンプト。メインループのスレッドで呼ぶ。"""

    def __init__(
        self,
        paths: RunPaths,
        history: Callable[[], Sequence[Delivery]],
        status_command: str = "",
    ) -> None:
        self._paths = paths
        self._history = history
        self._status = status_command

    def _sources(self, aggregates: Mapping[StreamId, Aggregate], task: TaskId) -> Sources:
        return Sources(self._paths, aggregates, self._history(), task, status_command=self._status)

    def prompt(
        self, context: StageContext, aggregates: Mapping[StreamId, Aggregate]
    ) -> StagePrompt:
        execution = context.execution
        role = f"{execution.stage.value} のステージ"
        sources = replace(
            self._sources(aggregates, execution.task),
            stage=execution.stage,
            step=context.step,
            tree=context.tree,
        )
        text = render(asset_name(execution.stage), sources, role)
        return StagePrompt(text, system_rules(role))

    def continuation(self, context: StageContext) -> str:
        """interrupt で止めた実行を `--resume` で続けるときの、短い続きの指示（実測が無い）。"""
        name = asset_name(context.execution.stage)
        return (
            "driver の都合で、このステージを途中で止めた。止まったところから、指示書 "
            f"`{contract_path(name)}` のとおりに続け、終わりに結果を StructuredOutput ツールで返す。"
        )

    def wake(
        self, wake: Wake, delivery: Delivery, aggregates: Mapping[StreamId, Aggregate]
    ) -> StagePrompt:
        """`wake` で統括を起こすプロンプト（知らせは `delivery`）。"""
        history = self._history()

        def find(event: EventId) -> Delivery | None:
            return next((d for d in history if d.event_id == event), None)

        return self.supervisor(wake.supervisor, render_notice(wake, delivery, find), aggregates)

    def supervisor(
        self, supervisor: Supervisor, notice: str, aggregates: Mapping[StreamId, Aggregate]
    ) -> StagePrompt:
        if supervisor.task is None:
            name, role, task = "supervisor-run", "ラン統括", TaskId.planning()
        else:
            name, role, task = (
                "supervisor-task",
                f"タスク統括（{supervisor.task}）",
                supervisor.task,
            )
        sources = replace(self._sources(aggregates, task), notice=notice)
        return StagePrompt(render(name, sources, role), system_rules(role))
