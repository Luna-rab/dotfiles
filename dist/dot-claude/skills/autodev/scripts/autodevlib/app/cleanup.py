"""`autodev clean`・`purge` の中身: ランの手元の跡を外す・消す。

**GitHub の PR とリモートのブランチには触らない。** 公開したものは消すと戻せない。消す前に確かめる
ことは `purge_problems` が並べ、1 つでもあれば何も消さない（呼んだ側が `--force` で越えてよい）。
worktree を外すと、無視されたファイルも一緒に消える（LEDGER GH-04）。
"""

from __future__ import annotations

import shutil
from pathlib import Path

from ..adapters.git import Git
from ..domain.values import BranchName, Repository
from ..infra.paths import RunPaths
from .assembly import running_executions


def trees(paths: RunPaths) -> list[Path]:
    """ランの worktree（`trees/` の下のディレクトリ）。"""
    if not paths.trees.is_dir():
        return []
    return sorted(path for path in paths.trees.iterdir() if path.is_dir())


def remove_worktrees(paths: RunPaths, repository: Repository | None) -> list[Path]:
    """worktree を外し、外したものを返す。ブランチと記録は残る。"""
    removed = trees(paths)
    if repository is None:
        # 始める前に止まったラン。git に登録した worktree は無い
        return removed
    repo = Git(repository.value)
    for tree in removed:
        repo.remove_worktree(tree)
    repo.prune_worktrees()
    return removed


def run_branches(paths: RunPaths, repository: Repository | None) -> list[BranchName]:
    """そのランが切った、手元のブランチ。"""
    if repository is None:
        return []
    return Git(repository.value).branches(BranchName.run_prefix(paths.name) + "*")


def purge_problems(paths: RunPaths, repository: Repository | None) -> list[str]:
    """消すと失うもの。記録の上で走っている実行と、origin のどこにも無いコミットを持つブランチ。"""
    problems = [
        f"記録の上で走っている実行がある: {execution}" for execution in running_executions(paths)
    ]
    if repository is not None:
        repo = Git(repository.value)
        for branch in run_branches(paths, repository):
            if lost := repo.unpushed_count(branch):
                problems.append(f"{branch} に origin に無いコミットがある（{lost} 件）")
    return problems


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
