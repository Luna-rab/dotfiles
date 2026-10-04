"""autodev-watch のステージのリスト。今のフローの段を 1 行ずつ並べ、それぞれに属する実行を持たせる。

**`flow_version` が `flow.version` と違う実行は、今のフローの段に重ねない。** その `step` は書き直す
前のフローの段の添字で、今の段とは別物である。そうした実行と、`step` が今の段の
どれにも当たらない実行は、段の後ろに 1 つずつ並べる。
"""

from __future__ import annotations

from dataclasses import dataclass

from hud.core.pipeline import Mark, full_name, step_of
from hud.core.runs import executions, flow_of, items

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
    #: この段の実行（始めた順）。段に入らない実行の項目なら、その実行 1 つ
    runs: tuple[dict, ...]
    #: 段に入らない実行の項目なら、その訳（`OLDER`・`OUTSIDE`）。段の項目は空
    note: str = ""


#: 書き直す前のフローの実行
OLDER = "前の版"
#: 今のフローの実行なのに、`step` が段の範囲の外か null。捨てると、走っている実行が画面から消える
OUTSIDE = "段の外"


def for_task(task: dict) -> list[StageItem]:
    flow = flow_of(task)
    version = flow.get("version") if flow else None
    mine = executions(task)
    # 添字は flow.steps の元の位置で数える。崩れた要素を詰めると、`step` と食い違う
    entries = items((flow or {}).get("steps"))
    out: list[StageItem] = []
    placed: set[int] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        step = step_of(entry)
        runs = tuple(e for e in mine if e.get("flow_version") == version and e.get("step") == index)
        placed.update(id(e) for e in runs)
        out.append(StageItem(f"step:{index}", step.label, step.mark, runs))
    loose = [e for e in mine if id(e) not in placed]
    # 前の版の実行を先に、どの段にも入らない実行を最後に並べる
    loose.sort(key=lambda e: e.get("flow_version") == version)
    for e in loose:
        note = OUTSIDE if e.get("flow_version") == version else OLDER
        label = f"{full_name(str(e.get('stage') or '?'))} r{e.get('round')}"
        mark = EXECUTION_MARK.get(str(e.get("status")), Mark.DONE)
        out.append(StageItem(f"exec:{e.get('id')}", label, mark, (e,), note))
    return out
