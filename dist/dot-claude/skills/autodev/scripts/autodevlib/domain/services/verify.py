"""VerifySelector: 流す検証コマンドを、流す時点で選ぶ。

- 実装タスクの ConfirmRed と Gate は、そのタスクの `verify` だけを流す。タスクが確かめるのは、自分が
  手を付けた範囲である
- git 管理タスクの Verify（積む直前、rebase の後）は、ラン共通の `verify` を流す。タスクが手を
  付けていない場所を壊した（回帰）なら、ここで止まる
- 他のタスクが足したコマンドは積み上げない。並列・差し込み・積み直しで「前のタスク」の意味が
  スタックの順とずれるため

「流す時点で」なので、呼ぶ側は、そのときの TaskSpec（ScopeChanged の後ならその範囲）と、そのときの
ラン共通の verify（最後の TasksPlanned）を渡す。
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum

from ..value_objects.stage_kind import StageKind
from ..value_objects.verify_command import VerifyCommand


class VerifyScope(Enum):
    #: そのタスクの verify
    TASK = "task"
    #: ラン共通の verify
    RUN = "run"


#: 検証コマンドを流すステージと、流す範囲
VERIFY_SCOPES: Mapping[StageKind, VerifyScope] = {
    StageKind.CONFIRM_RED: VerifyScope.TASK,
    StageKind.GATE: VerifyScope.TASK,
    StageKind.VERIFY: VerifyScope.RUN,
}


class VerifySelector:
    @staticmethod
    def scope_of(stage: StageKind) -> VerifyScope | None:
        """そのステージが流す範囲。検証コマンドを流さないステージは None。"""
        return VERIFY_SCOPES.get(stage)

    @staticmethod
    def select(
        stage: StageKind,
        task_verify: tuple[VerifyCommand, ...],
        run_verify: tuple[VerifyCommand, ...],
    ) -> tuple[VerifyCommand, ...]:
        """流すコマンド。空なら流すものが無い（その検証は通る）。"""
        scope = VerifySelector.scope_of(stage)
        if scope is None:
            raise ValueError(f"{stage.value} は検証コマンドを流さない")
        return task_verify if scope is VerifyScope.TASK else run_verify
