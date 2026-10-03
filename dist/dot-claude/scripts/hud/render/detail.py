"""autodev-watch の右ペイン。ラン・タスク・段の詳細。"""

from __future__ import annotations

import datetime as dt
from typing import Any

from rich.console import Group
from rich.text import Text

from hud.core.headline import Headline
from hud.core.pipeline import Mark, Step, full_name
from hud.core.runs import (
    age,
    dicts,
    escalations,
    executions,
    flow_of,
    items,
    questions,
    run_of,
    short,
    task_label,
    tasks,
)
from hud.core.stagelist import OLDER, OUTSIDE, StageItem
from hud.render.parts import bar
from hud.render.tasklist import driver_notes, pipeline
from hud.render.theme import (
    ACCENT,
    BOLD,
    DIM,
    EXECUTION_LABEL,
    RED,
    STATUS_LABEL,
    YELLOW,
    status_mark,
)

PROGRESS_BAR_WIDTH = 20
#: ランの詳細に出す、拒んだコマンドの件数（新しいものから）
REJECTIONS_SHOWN = 3
#: git 管理タスクの仕事（`flow.job.kind`）の表示名
JOB_LABEL = {
    "cut-overview": "概要ブランチを切る",
    "cut-task": "タスクのブランチを切る",
    "cut-stack-top": "スタックの一番上を固定する",
    "open-overview": "概要 PR を開く",
    "rewrite-overview": "概要 PR を書き直す",
    "stack": "積む",
    "discard": "破棄する",
    "finish": "仕上げ",
}
#: 段に入らない実行（`StageItem.note`）の説明
NOTE_LABEL = {
    OLDER: "書き直す前のフローの実行",
    OUTSIDE: "今のフローの実行だが、step がどの段にも当たらない",
}
#: 設計の提案の状態（`plan.design.proposal.state`）の表示名
PROPOSAL_LABEL = {"judging": "ジャッジ中", "revising": "書き直し中", "awaiting": "回答待ち"}


def run_detail(head: Headline, st: dict, now: dt.datetime) -> Group:
    """ランを選んだときの詳細。進み具合・回答待ちの質問・エスカレーション・スタック・計画。"""
    title = Text(head.run_name, style=BOLD).append(f" · {head.phase}", style=DIM)
    if head.waiting:
        title.append(" · 回答待ち", style=YELLOW)
    if head.overview_pr:
        title.append(f" · 概要 PR #{head.overview_pr}", style=DIM)
    parts: list[Any] = [title, run_meta(st, now)]
    notes = driver_notes(head)
    if head.panic_cause:
        notes.append(f" · パニックの原因: {head.panic_cause}", style=RED)
    if notes.plain:
        # 頭の「 · 」は見出しに続けるときの区切りなので、行の頭では落とす
        parts.append(notes[3:])
    parts.append(Text())

    pct = head.stacked / head.total * 100 if head.total else 0.0
    progress = Text("進み具合  ", style=BOLD).append_text(bar(pct, PROGRESS_BAR_WIDTH))
    progress.append(f" {head.stacked}/{head.total} スタック済み", style=DIM)
    if head.escalated:
        progress.append(f" · エスカレーション中 {head.escalated}", style=YELLOW)
    parts.append(progress)
    parts += [task_line(task) for task in tasks(st)]
    parts.append(Text())

    asked = questions(st)
    if asked:
        parts.append(Text("回答を待っている質問", style=BOLD))
        for q in asked:
            parts.append(Text(f"{q.get('id')}", style=YELLOW).append(f"  {q.get('body') or ''}"))
        parts.append(Text())
    raised = escalations(st)
    if raised:
        parts += [Text("エスカレーション", style=BOLD), *map(run_escalation, raised), Text()]
    parts += [Text("スタック", style=BOLD), stack_text(st.get("stack")), Text()]
    parts += [Text("計画", style=BOLD), plan_text(st.get("plan")), Text()]
    rejected = dicts(st.get("rejections"))
    if rejected:
        parts.append(Text(f"拒んだコマンド {len(rejected)} 件", style=BOLD))
        for r in rejected[-REJECTIONS_SHOWN:]:
            parts.append(Text(f"{r.get('type')} ", style=DIM).append(str(r.get("reason") or "")))
    return Group(*parts)


def run_meta(st: dict, now: dt.datetime) -> Text:
    run = run_of(st)
    out = Text(style=DIM)
    if run.get("repository"):
        out.append(f"{run['repository']} · base {run.get('base')}")
    started = age(run.get("started_at"), now)
    if started is not None:
        out.append(f" · 始めてから {short(started)}")
    if run.get("resumes"):
        out.append(f" · 呼び直し {run['resumes']} 回")
    updated = age(st.get("updated_at"), now)
    if updated is not None:
        # driver が生きているかの目安ではない。イベントを最後に確定してからの時間でしかない
        out.append(f" · 最後のイベントから {short(updated)}")
    return out


def run_escalation(e: dict) -> Text:
    line = Text(f"{e.get('id')} ", style=DIM).append(str(e.get("kind")), style=YELLOW)
    if e.get("task"):
        line.append(f"  {e['task']}", style=DIM)
    if e.get("for_user"):
        line.append("  /autodev の回答を待つ", style=YELLOW)
    if e.get("failures"):
        line.append(f"  統括が応じなかった {e['failures']} 回", style=RED)
    return line


def job_text(job: Any) -> str:
    if not isinstance(job, dict):
        return ""
    kind = str(job.get("kind"))
    target = job.get("task") or job.get("branch") or ""
    return f"{JOB_LABEL.get(kind, kind)} {target}".rstrip()


def stack_text(stack: Any) -> Text:
    if not isinstance(stack, dict):
        return Text("（無い）\n", style=DIM)
    out = Text()
    overview = stack.get("overview")
    if isinstance(overview, dict):
        out.append(f"概要 PR #{overview.get('pr')} {overview.get('branch')}\n")
    for entry in dicts(stack.get("entries")):
        out.append(f"  #{entry.get('pr')} {entry.get('task')} ", style="")
        out.append(f"{entry.get('branch')}\n", style=DIM)
    if stack.get("current"):
        out.append(f"処理中: {job_text(stack['current'])}\n", style=ACCENT)
    queue = [job_text(j) for j in dicts(stack.get("queue"))]
    if queue:
        out.append(f"順番待ち: {' / '.join(queue)}\n", style=DIM)
    if stack.get("parked"):
        out.append(f"戻す回数の上限で止めた: {job_text(stack['parked'])}\n", style=RED)
    if stack.get("paused"):
        out.append("再計画の間は積まない\n", style=YELLOW)
    if stack.get("cuts_pending"):
        out.append(f"破棄して閉じ終えていない {stack['cuts_pending']} 本\n", style=YELLOW)
    return out if out.plain else Text("（まだ積んでいない）\n", style=DIM)


def plan_text(plan: Any) -> Text:
    if not isinstance(plan, dict):
        return Text("（無い）\n", style=DIM)
    out = Text()
    if plan.get("planning"):
        out.append("計画が進んでいる\n", style=ACCENT)
    elif not plan.get("planned"):
        out.append("まだ計画を反映していない\n", style=DIM)
    design = plan.get("design") if isinstance(plan.get("design"), dict) else {}
    versions = items(design.get("versions"))
    if versions:
        out.append(
            f"設計の版 {', '.join(map(str, versions))} · 確定 {design.get('settled')}"
            f" · 反映 {plan.get('applied_design')}\n",
            style=DIM,
        )
    proposal = design.get("proposal")
    if isinstance(proposal, dict):
        state = str(proposal.get("state"))
        out.append(f"提案 v{proposal.get('version')} {PROPOSAL_LABEL.get(state, state)}")
        out.append(f" r{proposal.get('round')}", style=DIM)
        if proposal.get("awaiting"):
            out.append(f" · {proposal['awaiting']}", style=YELLOW)
        out.append("\n")
    if plan.get("replans_without_stack"):
        out.append(f"1 本も積まずに続けた再計画 {plan['replans_without_stack']} 回\n", style=DIM)
    return out if out.plain else Text("（計画を反映した）\n", style=DIM)


def task_line(task: dict) -> Text:
    """タスクの一覧の 1 行（記号・id・件名・状態・PR）。"""
    status = str(task.get("status"))
    mark, mark_style, body_style = status_mark(status)
    line = Text("  ").append(mark, style=mark_style).append(f" {task.get('id', '?')} ", style=DIM)
    line.append(task_label(task), style=body_style)
    label = STATUS_LABEL.get(status, status)
    line.append(f"  {label}" + (f" · #{task['pr']}" if task.get("pr") else ""), style=DIM)
    return line


def task_detail(task: dict, steps: list[Step], now: dt.datetime) -> Group:
    """選んだタスクの詳細。段の並び・走っている実行・エスカレーション。"""
    status = str(task.get("status"))
    title = Text(f"{task.get('id')} ", style=DIM).append(task_label(task), style=BOLD)
    meta = Text(f"{STATUS_LABEL.get(status, status)} · {task.get('kind')}", style=DIM)
    if task.get("pr"):
        meta.append(f" · PR #{task['pr']}", style=DIM)
    if task.get("branch"):
        meta.append(f" · {task['branch']}", style=DIM)
    parts: list[Any] = [title, meta, *relations(task), Text()]

    flow = flow_of(task)
    parts.append(Text("フロー", style=BOLD))
    if flow is None:
        parts.append(Text("（無い。始めていないか、組み直しを待っている）", style=DIM))
    else:
        parts.append(pipeline(steps))
        if flow.get("halted"):
            parts.append(Text("このフローは捨てた（置き換えを待つ）", style=YELLOW))
        if flow.get("job"):
            parts.append(Text(f"仕事: {job_text(flow['job'])}", style=ACCENT))
    parts.append(Text())

    parts += [Text("実行", style=BOLD), executions_text(executions(task), now)]
    raised = escalations(task)
    if raised:
        parts.append(Text("エスカレーション", style=BOLD))
        for e in raised:
            line = Text(f"{e.get('id')} ", style=DIM).append(str(e.get("kind")), style=YELLOW)
            if e.get("origin"):
                line.append(f"  {e['origin']}", style=DIM)
            parts.append(line)
    return Group(*parts)


def relations(task: dict) -> list[Text]:
    """ほかのタスクとの関係と、知らないと読み違える印。"""
    out = []
    blocked = items(task.get("blocked_by"))
    if blocked:
        out.append(Text(f"待っているタスク: {' '.join(map(str, blocked))}", style=DIM))
    if task.get("takes_over"):
        out.append(Text(f"{task['takes_over']} を引き継いだ", style=DIM))
    if task.get("superseded_by"):
        out.append(Text(f"{task['superseded_by']} に引き継がれた", style=DIM))
    if task.get("integration_failed"):
        out.append(Text("統合に失敗した", style=RED))
    if task.get("awaiting_requeue"):
        out.append(Text("下の PR を閉じ終えたら積み直す", style=YELLOW))
    return out


def executions_text(runs: list[dict] | tuple[dict, ...], now: dt.datetime) -> Text:
    out = Text()
    for e in runs:
        out.append_text(execution_line(e, now)).append("\n")
    return out if out.plain else Text("（走っている実行は無い）\n", style=DIM)


def execution_line(e: dict, now: dt.datetime) -> Text:
    """実行 1 つ。走っていれば経過時間と進み具合を添える。"""
    status = str(e.get("status"))
    running = status == "running"
    line = Text("◼ " if running else "  ", style=ACCENT)
    line.append(f"{full_name(str(e.get('stage')))} r{e.get('round')} a{e.get('attempt')}")
    line.append(f"  {EXECUTION_LABEL.get(status, status)}", style=ACCENT if running else DIM)
    seconds = age(e.get("started_at"), now)
    if running and seconds is not None:
        line.append(f" · {short(seconds)}", style=DIM)
    progress = e.get("progress")
    if not isinstance(progress, dict):
        progress = {}
    if progress.get("turns"):
        line.append(
            f" · {progress['turns']}ターン {progress.get('lastTool') or ''}".rstrip(), style=DIM
        )
    if progress.get("hookDenials"):
        line.append(f" · フックが止めた {progress['hookDenials']} 回", style=YELLOW)
    return line


def stage_detail(item: StageItem, now: dt.datetime) -> Group:
    title = Text(item.label, style=BOLD)
    if item.note:
        title.append(f" · {NOTE_LABEL.get(item.note, item.note)}", style=DIM)
    if not item.runs:
        empty = "飛ばした" if item.mark is Mark.SKIPPED else "まだ走っていない"
        return Group(title, Text(), Text(empty, style=DIM))
    return Group(title, Text(), executions_text(item.runs, now))


def broken_detail(name: str, error: str) -> Group:
    """status が読めなかったラン。読めない理由だけがある。"""
    return Group(Text(name, style=BOLD).append(" · 読めない", style=RED), Text(), Text(error))


def failure_detail(message: str) -> Text:
    return Text("autodev status を読めない\n\n", style=RED).append(message, style=DIM)
