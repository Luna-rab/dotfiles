"""autodev-watch の右ペイン。ラン・タスクの詳細と、ステージの指示と出力。"""

from __future__ import annotations

from typing import Any

from rich.console import Group
from rich.text import Text

from hud.core.activity import Activity, Kind
from hud.core.headline import Headline, outcome
from hud.core.pipeline import Step, full_name
from hud.core.review import Review
from hud.core.runs import Stage, short, tasks
from hud.render.parts import bar
from hud.render.tasklist import pipeline
from hud.render.theme import (
    ACCENT,
    BLUE,
    BOLD,
    DIM,
    FINDING_LABEL,
    GREEN,
    RATING_STYLE,
    RED,
    STATUS_LABEL,
    status_mark,
)

#: 詳細ペインの末尾に出す、計画ステージが決めたタスクの項目
PLAN_FIELDS = (("acceptance", "受入条件"), ("dod", "DoD"), ("scope", "範囲"))
PROGRESS_BAR_WIDTH = 20


def task_detail(task: dict, steps: list[Step], stages: list[Stage], review: Review | None) -> Group:
    """選んだタスクの詳細。`stages` はこのタスクで走っているステージだけを渡す。"""
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
        Text("ステージの履歴", style=BOLD),
        stage_history(task, stages),
        Text("レビュー", style=BOLD),
        review_text(review),
    ]
    for key, label in PLAN_FIELDS:
        if task.get(key):
            parts += [Text(label, style=BOLD), Text(f"{task[key]}\n")]
    return Group(*parts)


def stage_history(task: dict, stages: list[Stage]) -> Text:
    """済んだステージを 1 行ずつ。走っているステージはターン数と直前のツールを添えて最後に置く。"""
    out = Text()
    for entry in task.get("stages") or []:
        ok = bool(entry.get("ok", True))
        out.append("✔ " if ok else "✘ ", style=GREEN if ok else RED)
        out.append(f"{full_name(str(entry.get('name')))} r{entry.get('round')}\n")
    for stage in stages:
        out.append(f"◼ {full_name(stage.name)} r{stage.round}", style=ACCENT)
        out.append(f"  {short(stage.seconds)}", style=DIM)
        if stage.turns:
            out.append(f" {stage.turns}ターン {stage.tool}".rstrip(), style=DIM)
        out.append("\n")
    return out if out.plain else Text("（まだ走っていない）\n", style=DIM)


def review_text(review: Review | None) -> Text:
    """指摘の件数と、未解決の指摘の中身。"""
    if review is None:
        return Text("（指摘なし）\n", style=DIM)
    counts = " · ".join(f"{FINDING_LABEL.get(k, k)} {v}" for k, v in review.counts.items())
    out = Text(counts + "\n", style=DIM)
    for finding in review.open:
        out.append(f"{finding.key} ", style=DIM)
        out.append(f"{finding.rating:<10}", style=RATING_STYLE.get(finding.rating, DIM))
        out.append(f" {finding.location}\n")
        if finding.summary:
            out.append(f"    {finding.summary[:200]}\n", style=DIM)
    return out


def activity_line(activity: Activity) -> Text:
    """ステージの出力の 1 件。発言は全文を、2 行目以降も字下げしてそろえる。"""
    if activity.kind is Kind.TOOL:
        return Text("▸ ", style=BLUE).append(activity.text)
    return Text("\n".join(f"  {line}" for line in activity.text.splitlines()))


def run_detail(head: Headline, st: dict, overview: str | None) -> Group:
    """ランを選んだときの詳細。ゴール・進み具合・判断ログとスコープ外。"""
    title = Text(head.run_name, style=BOLD).append(f" · {outcome(head)}", style=DIM)
    if head.overview_pr:
        title.append(f" · 概要 PR #{head.overview_pr}", style=DIM)
    parts: list[Any] = [title, Text()]

    parts += [Text("ゴール", style=BOLD), Text("起動時の指示", style=DIM)]
    parts.append(Text(f"{str(st.get('instruction') or '（なし）').strip()}\n"))
    if overview and overview.strip():
        parts += [Text("まとめステージの説明", style=DIM), Text(f"{overview.strip()}\n")]

    pct = head.stacked / head.total * 100 if head.total else 0.0
    progress = Text("進み具合  ", style=BOLD).append_text(bar(pct, PROGRESS_BAR_WIDTH))
    progress.append(f" {head.stacked}/{head.total} スタック済み", style=DIM)
    if head.held:
        progress.append(f" · 要対応 {head.held}", style=RED)
    parts.append(progress)
    for task in tasks(st):
        parts.append(task_line(task))
    parts.append(Text())

    for key, label in (("decisions", "判断ログ"), ("deferrals", "スコープ外")):
        entries = [e for e in st.get(key) or [] if isinstance(e, dict)]
        if entries:
            parts.append(Text(label, style=BOLD))
            parts += [Text(f"・{e.get('body', '')}") for e in entries]
            parts.append(Text())
    return Group(*parts)


def task_line(task: dict) -> Text:
    """タスクの一覧の 1 行（記号・id・件名・PR）。"""
    status = str(task.get("status"))
    mark, mark_style, body_style = status_mark(status)
    line = Text("  ").append(mark, style=mark_style).append(f" {task.get('id', '?')} ", style=DIM)
    line.append(str(task.get("subject") or ""), style=body_style)
    label = STATUS_LABEL.get(status, status)
    line.append(f"  {label}" + (f" · #{task['pr']}" if task.get("pr") else ""), style=DIM)
    return line


def stage_prompt(text: str | None) -> Text:
    """ステージに渡した指示。driver が書き残し始める前のランには無い。"""
    heading = Text("渡した指示\n", style=BOLD)
    if text is None:
        return heading.append("（指示の記録が無い。この機能より前に走ったステージ）", style=DIM)
    return heading.append(text.rstrip())


def stage_output_heading() -> Text:
    return Text("\nClaude Code の出力", style=BOLD)
