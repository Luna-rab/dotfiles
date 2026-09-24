"""autodev-watch の左ペイン。ラン・タスク・ステージのリストの行と、今の位置。"""

from __future__ import annotations

from rich.text import Text

from hud.core.headline import Headline, State, outcome
from hud.core.pipeline import Mark
from hud.core.stagelist import StageItem
from hud.render.theme import ACCENT, DIM, GREEN, RED, STATUS_LABEL, YELLOW, status_mark

#: ステージの印。statusline の段の並びと同じ記号にする
STAGE_MARK = {
    Mark.DONE: ("✔", GREEN),
    Mark.FAILED: ("✘", RED),
    Mark.CURRENT: ("◼", ACCENT),
    Mark.NEXT: ("◻", DIM),
}
#: タスクのリストの先頭に置く、どのタスクにも属さないステージ（計画・まとめ）の行の件名
RUN_TASK_LABEL = "準備と仕上げ（計画・まとめ）"


def breadcrumb(run_name: str | None, task_id: str | None) -> Text:
    """今の位置。ランのリストに居るときは「ラン」だけ。"""
    out = Text("ラン", style=DIM)
    if run_name:
        out.append(" › ", style=DIM).append(run_name, style=ACCENT)
    if task_id:
        out.append(" › ", style=DIM).append(task_id, style=ACCENT)
    return out


def run_row(head: Headline) -> Text:
    label = outcome(head)
    style = {"回答待ち": YELLOW, "要対応": RED, "完了": GREEN}.get(label, DIM)
    mark = "◼" if head.state in (State.RUNNING, State.BETWEEN) else " "
    row = Text(f"{mark} ", style=ACCENT).append(head.run_name)
    row.append(f"  {label}", style=style)
    row.append(f"  {head.stacked}/{head.total}", style=DIM)
    return row


def run_task_row(active: bool) -> Text:
    return Text("◼ " if active else "  ", style=ACCENT).append(RUN_TASK_LABEL, style=DIM)


def task_row(task: dict) -> Text:
    status = str(task.get("status"))
    mark, mark_style, body_style = status_mark(status)
    row = Text(f"{mark} ", style=mark_style).append(f"{task.get('id', '?')} ", style=DIM)
    row.append(str(task.get("subject") or ""), style=body_style)
    row.append(f"  {STATUS_LABEL.get(status, status)}", style=DIM)
    return row


def stage_row(item: StageItem) -> Text:
    mark, style = STAGE_MARK[item.mark]
    row = Text(f"{mark} ", style=style)
    row.append(item.label, style=DIM if item.mark is Mark.NEXT else "")
    return row
