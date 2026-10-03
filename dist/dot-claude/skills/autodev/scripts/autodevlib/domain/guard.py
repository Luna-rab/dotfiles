"""ガードの規則。LLM のステージに何を書かせ、何をさせないか。

ここに書くのは「してよいか・いけないか」だけである。実パスをどの場所（`WriteZone`）に振り分けるか、
テストのパスかどうか、コマンド行から宛先と操作を取り出すことは、アダプタ（`adapters/guard.py`）が
翻訳して `WriteTarget`・`Operation` にして渡す。止めた理由（`Refusal`）を文面にするのもアダプタである。

`values.Guard` の `judge_write`・`judge_operation`・`judge_ask` が、ここの関数を呼ぶ。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .value_objects.guard import Guard
from .value_objects.write_scope import WriteScope


class WriteZone(Enum):
    """書き込みの宛先がある場所。入れ子の場所は、一番深いものに振り分ける。"""

    #: ステージの worktree。WriteScope が掛かる
    TREE = "tree"
    #: ランディレクトリ（`events.db` がある所。自分の worktree を除く）
    RUN_DIR = "run-dir"
    #: ホームディレクトリ（設定を含む）
    HOME = "home"
    #: 対象リポジトリの手元の checkout。手元のブランチと作業中のファイルに触らない
    TARGET_REPO = "target-repo"
    #: OS の一時ディレクトリ（`/tmp`・`$TMPDIR`）。テストやツールが一時ファイルを置く
    TEMP = "temp"
    #: どれにも入らない場所（OS の権限が守る）
    ELSEWHERE = "elsewhere"
    #: 場所が決まらない（展開できない変数や置換で始まるパス・分からない cwd からの相対パス）
    UNKNOWN = "unknown"


#: ステージ 1 回でフックに拒まれてよい数。これを超えたら打ち切る。何度も止められるステージは指示書を
#: 読み違えていて、ターンの上限まで使っても直らない
HOOK_DENIALS_BEFORE_CUTOFF = 10


def cut_off_by_denials(denials: int) -> bool:
    """フックに拒まれた数から、走っているステージを打ち切るか。"""
    return denials > HOOK_DENIALS_BEFORE_CUTOFF


#: worktree の外で止める場所
_DENIED_ZONES = frozenset({WriteZone.RUN_DIR, WriteZone.HOME, WriteZone.TARGET_REPO})


@dataclass(frozen=True)
class WriteTarget:
    """書き込みの宛先 1 つ。`is_test`・`listed` は TREE のときだけ意味を持つ。"""

    zone: WriteZone
    #: 文面に出すパス（TREE なら worktree の根から）
    path: str
    #: テストのパスに当たるか（ディレクトリなら、中にテストのパスが入りうるか）
    is_test: bool = False
    #: 実行のときに渡された「書いてよいファイル」に入っているか（WriteScope.LISTED）
    listed: bool = False


class Operation(Enum):
    """書き込み以外で、止めるかを決める操作。"""

    #: `gh` を呼ぶ
    GITHUB_CLI = "github-cli"
    #: `git push`（別名を含む）
    GIT_PUSH = "git-push"
    #: コマンドの名前が決まらない（`$GH` のような、展開できない変数がコマンドの位置にある）
    UNKNOWN_COMMAND = "unknown-command"


class RefusalReason(Enum):
    READ_ONLY_TREE = "read-only-tree"
    TESTS_PROTECTED = "tests-protected"
    NON_TESTS_PROTECTED = "non-tests-protected"
    NOT_LISTED = "not-listed"
    OUTSIDE_TREE = "outside-tree"
    UNKNOWN_PLACE = "unknown-place"
    GITHUB = "github"
    PUSH = "push"
    UNKNOWN_COMMAND = "unknown-command"
    ASK_NOT_ALLOWED = "ask-not-allowed"


@dataclass(frozen=True)
class Refusal:
    reason: RefusalReason
    #: 何を止めたか（パス・操作）
    subject: str = ""
    #: 止めた場所（worktree の外で止めたとき）
    zone: WriteZone | None = None


class AskVerdict(Enum):
    """ask の呼び出しへの判断。"""

    #: 回答が無いので、PreToolUse の defer で止めて待つ
    DEFER = "defer"
    #: 回答があるので通す
    PASS = "pass"
    #: このステージは ask で聞けない
    REFUSE = "refuse"


#: LLM のステージにも統括にも、GitHub の権限を渡さない。GitHub を触るのは、
#: git 管理タスクの決定的なステージだけである
WITHHOLD_GITHUB_FROM_LLM = True

_SCOPE_REFUSAL = {
    WriteScope.NONE: RefusalReason.READ_ONLY_TREE,
    WriteScope.NON_TESTS: RefusalReason.TESTS_PROTECTED,
    WriteScope.TESTS_ONLY: RefusalReason.NON_TESTS_PROTECTED,
    WriteScope.LISTED: RefusalReason.NOT_LISTED,
}


def _scope_allows(scope: WriteScope, target: WriteTarget) -> bool:
    if scope is WriteScope.NONE:
        return False
    if scope is WriteScope.NON_TESTS:
        return not target.is_test
    if scope is WriteScope.TESTS_AND_STUBS:
        # テストとスタブを書く。スタブか実装かはフックで見分けられない
        return True
    if scope is WriteScope.TESTS_ONLY:
        return target.is_test
    return target.listed


def judge_write(guard: Guard, target: WriteTarget) -> Refusal | None:
    zone = target.zone
    if zone is WriteZone.TREE:
        if _scope_allows(guard.writes, target):
            return None
        return Refusal(_SCOPE_REFUSAL[guard.writes], target.path, zone)
    if zone is WriteZone.UNKNOWN:
        # 場所が決まらないものは、書いてよい範囲にかかわらず止める
        return Refusal(RefusalReason.UNKNOWN_PLACE, target.path, zone)
    if zone in _DENIED_ZONES:
        return Refusal(RefusalReason.OUTSIDE_TREE, target.path, zone)
    return None


def judge_operation(guard: Guard, operation: Operation) -> Refusal | None:
    del guard  # どの LLM のステージでも同じ
    if operation is Operation.UNKNOWN_COMMAND:
        # 名前が決まらないコマンドは、gh や git push かもしれない
        return Refusal(RefusalReason.UNKNOWN_COMMAND)
    if not WITHHOLD_GITHUB_FROM_LLM:
        return None
    if operation is Operation.GITHUB_CLI:
        return Refusal(RefusalReason.GITHUB)
    return Refusal(RefusalReason.PUSH)


def judge_ask(guard: Guard, answered: bool) -> AskVerdict:
    """回答のファイルの実在だけで決め、中身を解釈しない。何度再開しても同じ判断になる。"""
    if not guard.can_ask:
        return AskVerdict.REFUSE
    return AskVerdict.PASS if answered else AskVerdict.DEFER
