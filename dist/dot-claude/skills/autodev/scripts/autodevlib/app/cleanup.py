"""`autodev clean`・`purge` の中身: 外す・消す前の証拠を集め、ランの手元の跡を外す・消す。

**GitHub の PR とリモートのブランチには触らない。** 公開したものは消すと戻せない。消してよいかは
ここで決めず、集めた証拠（`Leftovers`）を `domain/services/housekeeping.py` に渡して聞く。
worktree を外すと、無視されたファイルも一緒に消える（LEDGER GH-04）。
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from ..adapters import children
from ..adapters import git as git_adapter
from ..adapters.git import Git
from ..domain.services.housekeeping import Leftovers, LiveProcess, WorktreeState
from ..domain.values import BranchName, Repository
from ..infra.paths import RunPaths
from .assembly import running_executions


def trees(paths: RunPaths) -> list[Path]:
    """ランの worktree の置き場（`trees/` の下のディレクトリ）。"""
    if not paths.trees.is_dir():
        return []
    return sorted(path for path in paths.trees.iterdir() if path.is_dir())


def live_children(paths: RunPaths) -> tuple[LiveProcess, ...]:
    """前の driver が起こして、まだ生きている子プロセス。"""
    return tuple(LiveProcess(c.pid, c.command) for c in children.survivors(paths.children))


def _worktree(paths: RunPaths, repo: Git, tree: Path) -> WorktreeState | None:
    """git の worktree でないディレクトリ（作りかけで止まった など）は、失う変更も無いので数えない。"""
    top = git_adapter.toplevel(tree)
    if top is None or os.path.realpath(top) != os.path.realpath(tree):
        return None
    return WorktreeState(
        paths.relative(tree),
        dirty=tuple(repo.dirty_files(tree)),
        detached_commits=repo.commits_off_refs(tree),
    )


def leftovers(paths: RunPaths, repository: Repository | None, *, count_unpushed: bool) -> Leftovers:
    """外す・消す前の証拠。`count_unpushed` なら、origin を fetch してから push していないコミットを
    数える（fetch しないと、ほかの口から push 済みのコミットも push していないと数える）。"""
    live = live_children(paths)
    running = tuple(running_executions(paths))
    if repository is None:
        return Leftovers(running=running, live=live)
    repo = Git(repository.value)
    unpushed: tuple[tuple[BranchName, int], ...] = ()
    if count_unpushed:
        repo.fetch()
        unpushed = tuple(
            (branch, repo.unpushed_count(branch)) for branch in run_branches(paths, repository)
        )
    states = (_worktree(paths, repo, tree) for tree in trees(paths))
    return Leftovers(
        running=running,
        unpushed=unpushed,
        worktrees=tuple(state for state in states if state is not None),
        live=live,
    )


def remove_worktrees(paths: RunPaths, repository: Repository | None) -> list[Path]:
    """worktree を外し、実際に外したものを返す。ブランチと記録は残る。"""
    if repository is None:
        # 始める前に止まったラン。git に登録した worktree は無い
        return []
    repo = Git(repository.value)
    removed: list[Path] = []
    for tree in trees(paths):
        repo.remove_worktree(tree)
        if not tree.exists():
            removed.append(tree)
    repo.prune_worktrees()
    return removed


def run_branches(paths: RunPaths, repository: Repository | None) -> list[BranchName]:
    """そのランが切った、手元のブランチ。"""
    if repository is None:
        return []
    return Git(repository.value).branches(BranchName.run_prefix(paths.name) + "*")


def purge(paths: RunPaths, repository: Repository | None) -> list[BranchName]:
    """worktree・手元のブランチ・ランディレクトリを消し、消したブランチを返す。"""
    remove_worktrees(paths, repository)
    branches = run_branches(paths, repository)
    if repository is not None:
        repo = Git(repository.value)
        for branch in branches:
            repo.delete_branch(branch)
    shutil.rmtree(paths.root)
    return branches
