"""VerifySelector: 流す検証コマンドを、流す時点で選ぶ。

- ConfirmRed は、そのタスクのテストだけを流す。落ちることを確かめる段なので、lint の落ちを
  合格と取り違えない
- Gate は、そのタスクのテストと軽い検査を流す。積む直前に落ちて引き継ぎのタスクが増えるのを防ぐ
- git 管理タスクの統合検査（積む直前、rebase の後）は、軽い検査と回帰テストを流す。タスクが手を
  付けていない場所を壊した（回帰）なら、ここで止まる
- 他のタスクが足したコマンドは積み上げない。並列・差し込み・積み直しで「前のタスク」の意味が
  スタックの順とずれるため
- 同じコマンドが重なったら、初めに出た 1 回だけ流す

「流す時点で」なので、呼ぶ側は、そのときの TaskSpec（ScopeChanged の後ならその範囲）と、そのときの
ラン共通の軽い検査・回帰テスト（最後の TasksPlanned）を渡す。
"""

from __future__ import annotations

from collections.abc import Mapping

from ..value_objects.stage_kind import StageKind
from ..value_objects.verify_command import VerifyCommand
from ..value_objects.verify_kind import VerifyKind

#: 検証コマンドを流すステージと、流す種類（並びの順に流す）
VERIFY_KINDS: Mapping[StageKind, tuple[VerifyKind, ...]] = {
    StageKind.CONFIRM_RED: (VerifyKind.TASK_TESTS,),
    StageKind.GATE: (VerifyKind.TASK_TESTS, VerifyKind.QUICK_CHECKS),
    StageKind.INTEGRATION_CHECK: (VerifyKind.QUICK_CHECKS, VerifyKind.REGRESSION_TESTS),
}


class VerifySelector:
    @staticmethod
    def kinds_of(stage: StageKind) -> tuple[VerifyKind, ...] | None:
        """そのステージが流す種類。検証コマンドを流さないステージは None。"""
        return VERIFY_KINDS.get(stage)

    @staticmethod
    def select(
        stage: StageKind,
        *,
        task_tests: tuple[VerifyCommand, ...] = (),
        quick_checks: tuple[VerifyCommand, ...] = (),
        regression_tests: tuple[VerifyCommand, ...] = (),
    ) -> tuple[VerifyCommand, ...]:
        """流すコマンド。空なら流すものが無い（その検証は通る）。"""
        kinds = VerifySelector.kinds_of(stage)
        if kinds is None:
            raise ValueError(f"{stage.value} は検証コマンドを流さない")
        given = {
            VerifyKind.TASK_TESTS: task_tests,
            VerifyKind.QUICK_CHECKS: quick_checks,
            VerifyKind.REGRESSION_TESTS: regression_tests,
        }
        return tuple(dict.fromkeys(command for kind in kinds for command in given[kind]))
