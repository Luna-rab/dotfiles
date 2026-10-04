from __future__ import annotations

from dataclasses import dataclass

from .branch_name import BranchName


@dataclass(frozen=True)
class CutPoint:
    """CutBranch が切る元。"""

    start: BranchName
    #: 切る元がランの base か。ランの base は対象リポジトリの外で進むので、手元より origin の方が
    #: 新しいことがある。autodev が切ったブランチは手元が正しい
    run_base: bool = False
    #: ブランチを作らず、HEAD を切る元のコミットに固定した worktree にする（読むだけの stack-top）
    detached: bool = False
    #: 切った元のコミットが、相手のタスクのブランチの根元（`Task.base_commit`）になるか。積み直す
    #: タスクは前に積んだブランチの先端から切るので、そこは根元ではない。根元を上書きすると、Rebase
    #: が載せ直すコミットが 0 件になる（根元は前に覚えたまま、Rebase がそこから上を載せ直す）
    roots_branch: bool = True
