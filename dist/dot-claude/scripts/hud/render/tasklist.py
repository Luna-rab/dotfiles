"""autodev のタスクリスト。Claude Code のタスクリストのように、現在地・済んだもの・これからを出す。

autodev range-field ▸ task2 impl r0 · 4m12s 26往復 Edit · 土台 PR #4
  ✔ task1 パーサの土台を作る      #5
  ◼ task2 範囲指定を足す          testgen ✔ › impl ◼ › review › judge › PR
  ◻ task3 CLI に出す
"""

from __future__ import annotations

from rich.text import Text

from hud.core.headline import Headline, State
from hud.core.pipeline import Mark, Step
from hud.core.runs import short
from hud.core.tasklist import Summary
from hud.render.parts import clip
from hud.render.theme import ACCENT, ARROW, DIM, GREEN, RED, YELLOW, status_mark

SUBJECT_WIDTH = 22
REASON_WIDTH = 24


def headline(head: Headline) -> Text:
    line = Text("autodev ", style=DIM)
    line.append(head.work, style=ACCENT)
    line.append(" ▸ ", style=DIM)
    if head.state is State.WAITING:
        line.append(head.doing, style=YELLOW)
    elif head.state is State.RUNNING:
        line.append(head.doing)
        line.append(f" · {short(head.elapsed or 0)}", style=DIM)
        if head.overdue:
            line.append("!", style=RED)
        if head.turns:
            line.append(f" {head.turns}往復 {head.tool}".rstrip(), style=DIM)
    elif head.state is State.STOPPED:
        line.append(f"{head.doing} · 積んだ {head.stacked}/{head.total}", style=DIM)
        if head.held:
            line.append(f" · 保留 {head.held}", style=YELLOW)
    else:
        line.append(head.doing, style=DIM)
    if head.stack_pr and head.state is not State.STOPPED:
        line.append(f" · 土台 PR #{head.stack_pr}", style=DIM)
    return line


def pipeline(steps: list[Step]) -> Text:
    """段の並びを 1 行にする（`testgen ✔ › impl ◼ › review › judge › PR`）。"""
    out = Text()
    for i, step in enumerate(steps):
        if i:
            out.append_text(ARROW)
        if step.mark is Mark.ELIDED:
            out.append("…", style=DIM)
        elif step.mark in (Mark.DONE, Mark.FAILED):
            ok = step.mark is Mark.DONE
            out.append(f"{step.label} ", style=DIM)
            out.append("✔" if ok else "✘", style=GREEN if ok else RED)
        elif step.mark is Mark.CURRENT:
            out.append(f"{step.label} ◼", style=ACCENT)
        else:
            out.append(step.label, style=DIM)
    return out


def task_row(task: dict, steps: list[Step]) -> Text:
    """タスク 1 本の行。`steps` は実行中のタスクのときだけ使う。"""
    status = str(task.get("status"))
    mark, mark_style, body_style = status_mark(status)
    row = Text("  ")
    row.append(mark, style=mark_style)
    row.append(f" {task.get('id', '?')} ", style=DIM)
    detail = Text()
    if status == "stacked" and task.get("pr"):
        detail = Text(f"#{task['pr']}", style=DIM)
    elif status == "running":
        detail = pipeline(steps)
    elif status in ("blocked", "failed") and task.get("reason"):
        detail = clip(Text(str(task["reason"]), style=RED), REASON_WIDTH)
    subject = Text(str(task.get("subject") or ""), style=body_style)
    # 右に何か続くときだけ件名の幅をそろえる（続かない行の末尾に空白を残さない）
    row.append_text(clip(subject, SUBJECT_WIDTH, pad=bool(detail.plain)))
    if detail.plain:
        row.append("  ")
        row.append_text(detail)
    return row


def summary_row(summary: Summary) -> Text:
    if summary.status == "stacked":
        return Text("  ✔ ", style=GREEN).append(f"{summary.count} 件完了", style=DIM)
    return Text("  ◻ ", style=DIM).append(f"他 {summary.count} 件", style=DIM)
