"""autodev-watch のステージのリスト。今のフローの段を 1 行ずつ並べ、それぞれに属する実行を持たせる。

**`flow_version` が `flow.version` と違う実行は、今のフローの段に重ねない。** その `step` は書き直す
前のフローの段の添字で、今の段とは別物である（ADDENDUM §12）。そうした実行は、段の後ろに
「前の版のフロー」として 1 つずつ並べる。
"""

from __future__ import annotations

from dataclasses import dataclass

from hud.core.pipeline import Mark, full_name, step_of
from hud.core.runs import executions, flow_of

#: 実行の `status` から印へ。表に無いもの（completed・reported）は済んだもの。中断・捨てた・
#: やり直したものは失敗ではないので、飛ばした段と同じ印にする
EXECUTION_MARK = {
    "running": Mark.CURRENT,
    "requested": Mark.NEXT,
    "deferred": Mark.NEXT,
    "failed": Mark.FAILED,
    "refused": Mark.FAILED,
    "interrupted": Mark.SKIPPED,
    "abandoned": Mark.SKIPPED,
    "restarted": Mark.SKIPPED,
}


@dataclass(frozen=True)
class StageItem:
    #: リストの中で項目を見分ける値。読み直してもカーソルを同じ項目に保つのに使う
    key: str
    #: 画面に出す名前（`実装`・`ジャッジ r2`）
    label: str
    mark: Mark
    #: この段の実行（始めた順）。前の版の実行の項目なら、その実行 1 つ
    runs: tuple[dict, ...]
    #: 書き直す前のフローの実行
    older: bool = False


def for_task(task: dict) -> list[StageItem]:
    flow = flow_of(task)
    version = flow.get("version") if flow else None
    mine = executions(task)
    items: list[StageItem] = []
    for index, entry in enumerate((flow or {}).get("steps") or []):
        if not isinstance(entry, dict):
            continue
        step = step_of(entry)
        runs = tuple(e for e in mine if e.get("flow_version") == version and e.get("step") == index)
        items.append(StageItem(f"step:{index}", step.label, step.mark, runs))
    for e in mine:
        if e.get("flow_version") == version:
            continue
        label = f"{full_name(str(e.get('stage') or '?'))} r{e.get('round')}"
        mark = EXECUTION_MARK.get(str(e.get("status")), Mark.DONE)
        items.append(StageItem(f"exec:{e.get('id')}", label, mark, (e,), older=True))
    return items
