"""autodev のタスクリスト。Claude Code のタスクリストのように、現在地・済んだもの・これからを出す。

autodev add-cache ▸ task2 ジャッジ r2 · 4m12s 7ターン Read · 概要 PR #4
  ✔ task1 パーサの土台を作る      #5
  ◼ task2 範囲指定を足す          テスト作成 ✔ › 実装 ✔ › ジャッジ r2 ◼ › 完了チェック › PR 本文
  ◻ task3 CLI に出す
"""

from __future__ import annotations

from rich.text import Text

from hud.core.headline import Headline, State
from hud.core.pipeline import Mark, Step
from hud.core.runs import short, task_label
from hud.core.tasklist import Summary
from hud.render.parts import clip
from hud.render.theme import ACCENT, ARROW, DIM, GREEN, RED, YELLOW, status_mark

SUBJECT_WIDTH = 22
ESCALATION_WIDTH = 24
#: 段の並びを出すタスクの状態。積む順番を待つ間も、フローの最後の段まで見せる
PIPELINE_STATUSES = ("running", "gated", "stacking")


def headline(head: Headline) -> Text:
    line = Text("autodev ", style=DIM)
    line.append(head.run_name, style=ACCENT)
    line.append(" ▸ ", style=DIM)
    if head.state is State.PANICKED:
        line.append(f"{head.doing} · 呼び直すまで進まない", style=RED)
    elif head.state is State.RUNNING:
        line.append(head.doing)
        if head.elapsed is not None:
            line.append(f" · {short(head.elapsed)}", style=DIM)
        if head.turns:
            line.append(f" {head.turns}ターン {head.tool}".rstrip(), style=DIM)
    else:
        line.append(f"{head.doing} · スタック済み {head.stacked}/{head.total}", style=DIM)
    if head.waiting:
        line.append(f" · 回答待ち {' '.join(head.waiting)}", style=YELLOW)
    if head.escalated:
        line.append(f" · エスカレーション {head.escalated}", style=YELLOW)
    if head.overview_pr:
        line.append(f" · 概要 PR #{head.overview_pr}", style=DIM)
    return line


def pipeline(steps: list[Step]) -> Text:
    """段の並びを 1 行にする（`テスト作成 ✔ › 実装 ◼ › レビュー › 完了チェック › PR 本文`）。"""
    out = Text()
    for i, step in enumerate(steps):
        if i:
            out.append_text(ARROW)
        if step.mark is Mark.ELIDED:
            out.append("…", style=DIM)
        elif step.mark is Mark.DONE:
            out.append(f"{step.label} ", style=DIM).append("✔", style=GREEN)
        elif step.mark is Mark.SKIPPED:
            out.append(f"{step.label} –", style=DIM)
        elif step.mark is Mark.FAILED:
            out.append(f"{step.label} ", style=DIM).append("✘", style=RED)
        elif step.mark is Mark.CURRENT:
            out.append(f"{step.label} ◼", style=ACCENT)
        else:
            out.append(step.label, style=DIM)
    return out


def task_row(task: dict, steps: list[Step]) -> Text:
    """タスク 1 本の行。`steps` は段の並びを出す状態のときだけ使う。"""
    status = str(task.get("status"))
    mark, mark_style, body_style = status_mark(status)
    row = Text("  ")
    row.append(mark, style=mark_style)
    row.append(f" {task.get('id', '?')} ", style=DIM)
    detail = Text()
    if status == "stacked" and task.get("pr"):
        detail = Text(f"#{task['pr']}", style=DIM)
    elif status == "escalated":
        kinds = " ".join(str(e.get("kind")) for e in task.get("escalations") or [])
        detail = clip(Text(kinds or "エスカレーション中", style=YELLOW), ESCALATION_WIDTH)
    elif status in PIPELINE_STATUSES:
        detail = pipeline(steps)
    subject = Text(task_label(task), style=body_style)
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


def broken_row(count: int) -> Text:
    """status が読めなかったランの数。statusline では 1 行にまとめ、中身は autodev-watch で見せる。"""
    return Text(f"autodev 読めないラン {count} 本 · autodev-watch で見る", style=YELLOW)


def failure_row(message: str) -> Text:
    """`status --json` そのものが返らなかった。黙ると「ランが無い」と見分けが付かない。"""
    return Text("autodev status を読めない · ", style=RED).append(message, style=DIM)
