"""statusline に出すタスクの行。多いときは、今のタスクの前後だけに窓を切る。

完了は 1 行にまとめ、未着手は次の `PENDING_ROWS` 本だけ出す。実行中と保留は必ず出す。
高さを一定に保つためで、全部を見るのは autodev-watch の役目である。
"""

from __future__ import annotations

from dataclasses import dataclass

#: タスクがこれより多いと、今のタスクの前後だけに窓を切る
MAX_TASK_ROWS = 5
#: 窓を切ったときに出す未着手の本数
PENDING_ROWS = 2


@dataclass(frozen=True)
class Summary:
    """まとめた行。`status` が stacked なら「N 件完了」、pending なら「他 N 件」。"""

    status: str
    count: int


def visible(tasks: list[dict]) -> list[dict | Summary]:
    if len(tasks) <= MAX_TASK_ROWS:
        return list(tasks)
    rows: list[dict | Summary] = []
    stacked = sum(1 for t in tasks if t.get("status") == "stacked")
    if stacked:
        rows.append(Summary("stacked", stacked))
    shown = hidden = 0
    for task in tasks:
        status = task.get("status")
        if status == "stacked":
            continue
        if status == "pending":
            if shown >= PENDING_ROWS:
                hidden += 1
                continue
            shown += 1
        rows.append(task)
    if hidden:
        rows.append(Summary("pending", hidden))
    return rows
