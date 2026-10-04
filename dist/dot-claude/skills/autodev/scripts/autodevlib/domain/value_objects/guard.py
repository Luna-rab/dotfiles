from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .write_scope import WriteScope

if TYPE_CHECKING:
    from ..guard import AskVerdict, Operation, Refusal, WriteTarget


@dataclass(frozen=True)
class Guard:
    """ステージの種類ごとの書き込みの範囲と権限。

    worktree の外の扱いは、どの LLM のステージでも同じなので欄にしない: ランディレクトリ（自分の
    worktree を除く）・ホームディレクトリ・対象リポジトリの手元の checkout・場所の分からないものへの
    書き込みは止め、OS の一時ディレクトリ（`/tmp`・`$TMPDIR`）は許す。`gh` と `git push` も止める。
    規則は `domain/guard.py` にあり、下のメソッドが呼ぶ。
    決定的なステージは claude を起動しないので、Guard を持たない。
    """

    #: worktree の中で書いてよい範囲
    writes: WriteScope
    #: 指摘の状態を動かせるか（JudgeCapability。Judge・DesignJudge）
    judge: bool = False
    #: 設計ファイルを渡すか。AdversarialReview には渡さない
    reads_design: bool = True
    #: ask で聞けるか（計画ステージ。PreToolUse のフックの defer で止める）
    can_ask: bool = False

    # 規則の置き場の domain/guard.py が Guard を import するので、循環を避けて呼ぶときに import する

    def judge_write(self, target: WriteTarget) -> Refusal | None:
        """書き込みの宛先 1 つを止めるか。止めるなら理由。"""
        from ..guard import judge_write  # noqa: PLC0415

        return judge_write(self, target)

    def judge_operation(self, operation: Operation) -> Refusal | None:
        """`gh`・`git push`・名前の決まらないコマンドを止めるか。"""
        from ..guard import judge_operation  # noqa: PLC0415

        return judge_operation(self, operation)

    def judge_ask(self, answered: bool) -> AskVerdict:
        from ..guard import judge_ask  # noqa: PLC0415

        return judge_ask(self, answered)

    @property
    def withholds_github(self) -> bool:
        """このステージに GitHub の権限を渡さないか。起動する側は、gh の認証と git の資格情報を外す。"""
        from ..guard import WITHHOLD_GITHUB_FROM_LLM  # noqa: PLC0415

        return WITHHOLD_GITHUB_FROM_LLM
