"""autodev-watch の詳細ペインとログのペイン。"""

from __future__ import annotations

from typing import Any

from rich.console import Group
from rich.text import Text

from hud.core.activity import Activity, Kind
from hud.core.pipeline import Step
from hud.core.review import Review
from hud.core.runs import Stage, short
from hud.render.tasklist import pipeline
from hud.render.theme import ACCENT, BLUE, BOLD, DIM, GREEN, RATING_STYLE, RED, STATUS_LABEL

#: 詳細ペインの末尾に出す、計画段が決めたタスクの項目
PLAN_FIELDS = (("acceptance", "受入条件"), ("dod", "DoD"), ("scope", "範囲"))


def task_detail(task: dict, steps: list[Step], stages: list[Stage], review: Review | None) -> Group:
    """選んだタスクの詳細。`stages` はこのタスクで走っている段だけを渡す。"""
    status = str(task.get("status"))
    title = Text(f"{task.get('id')} ", style=DIM).append(str(task.get("subject") or ""), style=BOLD)
    meta = Text(f"{STATUS_LABEL.get(status, status)} · {task.get('tier', '?')}", style=DIM)
    if task.get("pr"):
        meta.append(f" · PR #{task['pr']}", style=DIM)
    if task.get("branch"):
        meta.append(f" · {task['branch']}", style=DIM)
    parts: list[Any] = [title, meta, Text()]
    if task.get("reason"):
        parts += [Text(str(task["reason"]), style=RED), Text()]
    if status == "running":
        parts += [pipeline(steps), Text()]
    parts += [
        Text("段の履歴", style=BOLD),
        stage_history(task, stages),
        Text("レビュー", style=BOLD),
        review_text(review),
    ]
    for key, label in PLAN_FIELDS:
        if task.get(key):
            parts += [Text(label, style=BOLD), Text(f"{task[key]}\n")]
    return Group(*parts)


def stage_history(task: dict, stages: list[Stage]) -> Text:
    """済んだ段を 1 行ずつ。走っている段は往復数と直前のツールを添えて最後に置く。"""
    out = Text()
    for entry in task.get("stages") or []:
        ok = bool(entry.get("ok", True))
        out.append("✔ " if ok else "✘ ", style=GREEN if ok else RED)
        out.append(f"{entry.get('name')} r{entry.get('round')}\n")
    for stage in stages:
        out.append(f"◼ {stage.name} r{stage.round}", style=ACCENT)
        out.append(f"  {short(stage.seconds)}", style=DIM)
        if stage.turns:
            out.append(f" {stage.turns}往復 {stage.tool}".rstrip(), style=DIM)
        out.append("\n")
    return out if out.plain else Text("（まだ走っていない）\n", style=DIM)


def review_text(review: Review | None) -> Text:
    """指摘の件数と、未解決の指摘の中身。"""
    if review is None:
        return Text("（指摘なし）\n", style=DIM)
    out = Text(" · ".join(f"{k} {v}" for k, v in review.counts.items()) + "\n", style=DIM)
    for finding in review.open:
        out.append(f"{finding.key} ", style=DIM)
        out.append(f"{finding.rating:<10}", style=RATING_STYLE.get(finding.rating, DIM))
        out.append(f" {finding.location}\n")
        if finding.summary:
            out.append(f"    {finding.summary[:200]}\n", style=DIM)
    return out


def activity_line(activity: Activity) -> Text:
    if activity.kind is Kind.TOOL:
        return Text("▸ ", style=BLUE).append(activity.text)
    return Text(f"  {activity.text}", style=DIM)
