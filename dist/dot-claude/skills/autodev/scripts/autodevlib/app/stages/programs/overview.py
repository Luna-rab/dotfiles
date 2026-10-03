"""git 管理タスクの決定的なステージのうち、概要 PR を作り・更新し・レビュー待ちにするものの中身。"""

from __future__ import annotations

from collections.abc import Mapping

from ....domain.value_objects.decision_origin import DecisionOrigin
from ....domain.value_objects.overview_pr_title import overview_pr_title
from ....domain.value_objects.task_status import TaskStatus
from .. import markers
from ..stage_context import StageContext
from .common import (
    ProgramOutcome,
    Tools,
    bullets,
    job_branch,
    job_of,
    overview_pr_of,
    start_commit_of,
)

_STATUS_LABELS: Mapping[TaskStatus, str] = {
    TaskStatus.PENDING: "未着手",
    TaskStatus.RUNNING: "作業中",
    TaskStatus.ESCALATED: "回答待ち",
    TaskStatus.GATED: "積む順番待ち",
    TaskStatus.STACKING: "積んでいる",
    TaskStatus.STACKED: "積んだ",
    TaskStatus.DROPPED: "止めた",
    TaskStatus.SUPERSEDED: "引き継がれた",
    TaskStatus.DISCARDED: "破棄した",
}
_ORIGIN_LABELS: Mapping[DecisionOrigin, str] = {
    DecisionOrigin.USER: "ユーザーの回答",
    DecisionOrigin.RUN_SUPERVISOR: "ラン統括の判断",
}


def overview_values(ctx: StageContext, tools: Tools) -> dict[str, str]:
    """概要 PR のマーカーの中身。表のラベルに無い状態は、隠さずに値のまま出す。"""
    facts = ctx.overview
    rows = [
        f"{row.task} {row.title} — {_STATUS_LABELS.get(row.status, row.status.value)}"
        + (f"（#{row.pr}）" if row.pr is not None else "")
        for row in facts.tasks
    ]
    waiting = [f"{task or 'ラン'}: {kind}" for task, kind in facts.waiting]
    decisions = [*(f"{d}（計画）" for d in facts.decisions)]
    decisions += [f"{note.text}（{_ORIGIN_LABELS[note.origin]}）" for note in facts.notes]
    return {
        "tasks": bullets(rows),
        "waiting": bullets(waiting),
        "decisions": bullets(decisions),
        "deferrals": bullets(list(facts.deferrals)),
        "instruction": tools.setting.instruction,
        "signature": f"autodev のラン `{tools.setting.paths.name}` が {tools.clock()} に更新した",
    }


def _overview_body(ctx: StageContext, tools: Tools) -> str:
    """保存したマーカー入りの本文から、毎回埋め直す。"""
    path = tools.setting.paths.overview_body
    if not path.is_file():
        raise RuntimeError("概要 PR の本文（WriteOverview の結果）が無い")
    return markers.fill(path.read_text(encoding="utf-8"), overview_values(ctx, tools))


def _overview_title(tools: Tools) -> str:
    path = tools.setting.paths.overview_title
    if not path.is_file():
        raise RuntimeError("概要 PR のタイトル（WriteOverview の結果）が無い")
    return overview_pr_title(path.read_text(encoding="utf-8"))


def create_overview_pr(ctx: StageContext, tools: Tools) -> ProgramOutcome:
    """概要ブランチを push し、概要 PR を draft で作る。base との差分が 0 だと作れないので、空のコミットを
    1 つ載せる。同じブランチの PR があればそれを使う。"""
    job = job_of(ctx)
    branch = job_branch(job)
    git, tree = tools.git, tools.setting.paths.overview_tree
    base = job.base or tools.setting.base
    if git.commit_count(tree, start_commit_of(tools, str(base))) == 0:
        git.commit_empty(tree, f"autodev: ラン {tools.setting.paths.name} の概要")
    git.push(tree, branch)
    found = tools.forge.find_pr(tree, branch)
    if found is not None:
        return ProgramOutcome(result={"pr": int(found.number.value)})
    number = tools.forge.create_pr(
        tree,
        base=base,
        head=branch,
        title=_overview_title(tools),
        body=_overview_body(ctx, tools),
        draft=True,
    )
    return ProgramOutcome(result={"pr": int(number.value)})


def refresh_overview(ctx: StageContext, tools: Tools) -> ProgramOutcome:
    tools.forge.edit_pr(
        tools.setting.paths.overview_tree,
        overview_pr_of(ctx),
        title=_overview_title(tools),
        body=_overview_body(ctx, tools),
    )
    return ProgramOutcome()


def ready_overview(ctx: StageContext, tools: Tools) -> ProgramOutcome:
    tools.forge.ready_pr(tools.setting.paths.overview_tree, overview_pr_of(ctx))
    return ProgramOutcome()
