"""git 管理タスクの決定的なステージのうち、ブランチを切って積むものの中身。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ....domain.services.union import UnionChecker
from ....domain.stages.kinds import has_own_commits
from ....domain.value_objects.artifact_ref import ArtifactRef
from ....domain.value_objects.branch_name import BranchName
from ....domain.value_objects.cut_point import CutPoint
from ....domain.value_objects.pr_number import PrNumber
from ....domain.value_objects.union_file_verdict import UnionFileVerdict
from ....domain.value_objects.union_verdict import UnionVerdict
from .. import markers
from ..stage_context import StageContext
from .common import (
    ProgramOutcome,
    Tools,
    _branch,
    _job,
    _overview_pr,
    _start_of,
    _verify,
    own_commits,
)


def cut_point(tools: Tools, point: CutPoint) -> str:
    """切る元の名前。ランの base は origin にあればそちらを使う（手元の base は古いことがある）。"""
    return _start_of(tools, str(point.start)) if point.run_base else str(point.start)


def cut_branch(ctx: StageContext, tools: Tools) -> ProgramOutcome:
    """仕事が決めた切る元（`GitJob.cut_point`）から、ブランチと worktree を切る。"""
    job = _job(ctx)
    point = job.cut_point
    if point is None:
        raise RuntimeError(f"仕事 {job.id}（{job.kind.value}）に切る元が無い")
    git, paths = tools.git, tools.setting.paths
    git.prune_worktrees()
    start = cut_point(tools, point)
    branch: BranchName | None
    if point.detached:
        top = git.rev_parse(git.repo, start)
        if top is None:
            raise RuntimeError(f"切る元の {start} が無い")
        tree = paths.stack_top_tree
        git.add_detached_worktree(tree, str(top), run_dir=paths.root)
        branch = None
        base = top
    else:
        branch = _branch(job)
        tree = ctx.tree
        found = git.rev_parse(git.repo, start)
        if found is None:
            raise RuntimeError(f"切る元の {start} が無い")
        base = found
        git.create_branch(branch, start)
        git.switch_worktree(tree, branch)
    task = job.task
    if task is None:
        raise RuntimeError(f"仕事 {job.id} に worktree を使うタスクが無い")
    result: dict[str, Any] = {
        "task": task.value,
        "tree": paths.relative(tree),
        "branch": str(branch) if branch is not None else None,
        "base": str(base),
    }
    return ProgramOutcome(result=result)


def rebase(ctx: StageContext, tools: Tools) -> ProgramOutcome:
    """タスクのブランチの根元から上を、取り出したときのスタックの一番上へ載せ直す。衝突したら止めたまま返す。

    載せ直すのは、根元（`Task.base_commit`）から HEAD までのコミットだけ（`git rebase --onto <一番上>
    <根元>`）。積み直すブランチは前に積んだブランチから切るので、破棄した下のタスクのコミットも含むが、
    それは根元より下にあるので載せない。載せ直すものがあるかはドメインに聞き
    （`has_own_commits`）、無ければブランチを一番上へ動かさずに数だけ返す（落とすのは Task）。流す前に
    実行器が、途中の rebase を取りやめ、始めた時点へ戻している（`StageSpec.abandons_rebase`・
    `restores_start`）。
    """
    job = _job(ctx)
    git, tree = tools.git, ctx.tree
    if job.base is None:
        raise RuntimeError("積む仕事に一番上が無い")
    onto = git.rev_parse(git.repo, str(job.base))
    if onto is None:
        raise RuntimeError(f"一番上の {job.base} が無い")
    # 載せ直した後の HEAD は古い根元から辿れないので、載せ直す前に数える
    commits = len(own_commits(ctx, tools, tree))
    if not has_own_commits(commits):
        return ProgramOutcome(commits=commits)
    # 載せ直した先は、タスクのブランチの新しい根元になる（Task.base_commit）
    result = {"onto": str(onto)}
    outcome = git.rebase(tree, str(onto), str(ctx.base_commit))
    return ProgramOutcome(result=result, conflicts=outcome.conflicts, commits=commits)


def check_union(ctx: StageContext, tools: Tools) -> ProgramOutcome:
    """衝突したファイルで両側の変更を残したかを UnionChecker（ドメイン）に照らし、残していれば rebase を続ける。

    続けた先のコミットでまた衝突したら、そのファイルは解いていないので、残していないものとして返す。
    rebase は止めたまま残す（取りやめるのは IntegrationFailed を受けた側）。
    """
    git, tree = tools.git, ctx.tree
    verdicts: list[UnionFileVerdict] = []
    for path in ctx.conflicts:
        sides = git.conflict_sides(tree, path)
        resolved = git.read_file(tree, path)
        verdicts.append(UnionChecker.check(path, sides.base, sides.ours, sides.theirs, resolved))
    checked = UnionVerdict(tuple(verdicts))
    if not checked.passed or not git.rebase_in_progress(tree):
        return ProgramOutcome(union=checked)
    # 続けるのは、このステージの中身。両側を残したかの答えは UnionVerdict が出す
    outcome = git.rebase_continue(tree)
    verdicts += [UnionChecker.still_conflicted(p) for p in outcome.conflicts]
    return ProgramOutcome(union=UnionVerdict(tuple(verdicts)))


def verify(ctx: StageContext, tools: Tools) -> ProgramOutcome:
    """積む直前の回帰を、ラン共通の verify で確かめる。"""
    return ProgramOutcome(verify=_verify(ctx, tools, ctx.tree))


def push(ctx: StageContext, tools: Tools) -> ProgramOutcome:
    tools.git.push(ctx.tree, _branch(_job(ctx)))
    return ProgramOutcome()


def _read(tools: Tools, ref: ArtifactRef | None, what: str) -> str:
    if ref is None:
        raise RuntimeError(f"{what} が無い")
    return (tools.setting.paths.root / ref.at).read_text(encoding="utf-8")


def create_pr(ctx: StageContext, tools: Tools) -> ProgramOutcome:
    """タスク PR を、実装タスクが書いた本文で作る。同じブランチの PR があればそれを使う。"""
    job = _job(ctx)
    branch = _branch(job)
    found = tools.forge.find_pr(ctx.tree, branch)
    if found is not None:
        return ProgramOutcome(result={"pr": int(found.number.value)})
    if job.base is None or ctx.target.title is None:
        raise RuntimeError("積む仕事に一番上か、タスクの件名が無い")
    body = markers.fill(
        markers.template("task-pr-body"),
        {
            "overview-pr": f"概要 PR: #{_overview_pr(ctx)}",
            "body": _read(tools, ctx.target.pr_body, "タスク PR の本文（pr-body）"),
        },
    )
    number = tools.forge.create_pr(
        ctx.tree, base=job.base, head=branch, title=ctx.target.title, body=body
    )
    return ProgramOutcome(result={"pr": int(number.value)})


def _link(ctx: StageContext, tools: Tools, prs: Sequence[PrNumber]) -> None:
    """概要 PR から下から順に渡して、つないだ後に概要 PR の base がランの base のままか確かめる。"""
    paths, base = tools.setting.paths, tools.setting.base
    tools.forge.stack_link(paths.overview_tree, base, prs)
    seen = tools.forge.view_pr(paths.overview_tree, prs[0])
    if seen.base != str(base):
        raise RuntimeError(f"つないだ後の概要 PR の base が {seen.base}（ランの base は {base}）")


def stack_link(ctx: StageContext, tools: Tools) -> ProgramOutcome:
    """概要 PR から一番上（このタスクの PR）まで、全部を下から順につなぐ（link は足すだけ）。"""
    branch = _branch(_job(ctx))
    found = tools.forge.find_pr(ctx.tree, branch)
    if found is None:
        raise RuntimeError(f"{branch} の PR が無い")
    prs = [_overview_pr(ctx), *(entry.pr for entry in ctx.stack.entries), found.number]
    _link(ctx, tools, prs)
    return ProgramOutcome(result={"pr": int(found.number.value)})
