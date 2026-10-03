"""値オブジェクト（DOMAIN_MODEL §4・ADDENDUM）。

どれも不変で、等しさは値で決まる。作るときに形を検査し、不正な値はその場で `InvalidValue` にする。
集約の状態を見ないと決められない検査（「使ったことのある番号と重ねない」など）は、ここではなく
集約の `handle` で行う。

文字列 1 つ・整数 1 つを包む値は `Text`・`Number` を土台にする。イベントストアの JSON では、
包みを外した素の値になる（`codec.py`）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from .guard import AskVerdict, Operation, Refusal, WriteTarget


class InvalidValue(ValueError):
    """値オブジェクトの形が不正。"""


# --- 土台 ---


@dataclass(frozen=True)
class Text:
    """文字列 1 つを包む値の土台。`PATTERN` があれば、全体がそれに合うことを求める。"""

    value: str
    PATTERN: ClassVar[re.Pattern[str] | None] = None

    def __post_init__(self) -> None:
        if not isinstance(self.value, str):
            raise InvalidValue(f"{type(self).__name__} は文字列: {self.value!r}")
        if self.PATTERN is not None and not self.PATTERN.fullmatch(self.value):
            raise InvalidValue(f"{type(self).__name__} の形が違う: {self.value!r}")
        self._check()

    def _check(self) -> None:
        """`PATTERN` で書けない検査を足すときに上書きする。"""

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class Number:
    """整数 1 つを包む値の土台。`MINIMUM` 以上を求める。"""

    value: int
    MINIMUM: ClassVar[int] = 1

    def __post_init__(self) -> None:
        # bool は int の部分型なので、True を 1 として通さない
        if isinstance(self.value, bool) or not isinstance(self.value, int):
            raise InvalidValue(f"{type(self).__name__} は整数: {self.value!r}")
        if self.value < self.MINIMUM:
            raise InvalidValue(f"{type(self).__name__} は {self.MINIMUM} 以上: {self.value}")

    def __str__(self) -> str:
        return str(self.value)


def _non_blank(owner: str, value: str) -> None:
    if not value.strip():
        raise InvalidValue(f"{owner} が空")


# --- 識別子 ---


class RunName(Text):
    """ラン名。ブランチ名と置き場のパスに入るので、英小文字・数字・`-` の 1〜49 字で、先頭は `-` でない（LEDGER FP-12）。

    `--` と末尾の `-` も拒む。ブランチ名の `stack/<ラン名>--task-<番号>` で、`--` がラン名と
    タスクの区切りになっているため。
    """

    # `-` は英小文字か数字が続くときだけ置ける
    PATTERN = re.compile(r"[a-z0-9](?:[a-z0-9]|-(?=[a-z0-9])){0,48}")


#: TaskId の形。StreamId の形もこれから組む
_TASK_ID = r"task[1-9][0-9]*|planning|git"


class TaskId(Text):
    """`task<番号>`。計画タスクと git 管理タスクは、ランに 1 つずつなので固定の名前を持つ。

    番号を使い回さないこと（捨てたタスクのブランチが残る。LEDGER GH-12）は、使った番号を知っている
    Run が確かめる。
    """

    PATTERN = re.compile(_TASK_ID)
    PLANNING: ClassVar[str] = "planning"
    GIT: ClassVar[str] = "git"

    @classmethod
    def numbered(cls, number: int) -> TaskId:
        return cls(f"task{number}")

    @classmethod
    def planning(cls) -> TaskId:
        return cls(cls.PLANNING)

    @classmethod
    def git(cls) -> TaskId:
        return cls(cls.GIT)

    @property
    def number(self) -> int | None:
        """実装タスクの番号。計画タスクと git 管理タスクは None。"""
        if self.value.startswith("task"):
            return int(self.value[len("task") :])
        return None

    @property
    def kind(self) -> TaskKind:
        """id から決まるタスクの種類。計画タスクと git 管理タスクは固定の名前を持つ。"""
        if self.value == self.PLANNING:
            return TaskKind.PLANNING
        if self.value == self.GIT:
            return TaskKind.GIT
        return TaskKind.IMPLEMENTATION


class QuestionId(Text):
    PATTERN = re.compile(r"[a-z0-9][a-z0-9-]*")


class SessionId(Text):
    """claude のセッション id。driver が `--session-id` で決める UUID。"""

    PATTERN = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


class CommitSha(Text):
    PATTERN = re.compile(r"[0-9a-f]{40}")


class FindingId(Text):
    """`R<番号>`（タスクの台帳）・`D<番号>`（設計の台帳）・`G-<項目>`（Gate の項目ごとの指摘。ADDENDUM §6）。

    台帳の中で一意かは、ReviewLedger が確かめる。
    """

    PATTERN = re.compile(r"[RD][1-9][0-9]*|G-[a-z][a-z-]*")

    def _check(self) -> None:
        if self.value.startswith("G-") and self.value[len("G-") :] not in _GATE_ITEM_VALUES:
            raise InvalidValue(f"Gate の項目に無い: {self.value!r}")

    @classmethod
    def review(cls, number: int) -> FindingId:
        return cls(f"R{number}")

    @classmethod
    def design(cls, number: int) -> FindingId:
        return cls(f"D{number}")

    @classmethod
    def gate(cls, item: GateItem) -> FindingId:
        return cls(f"G-{item.value}")

    @property
    def gate_item(self) -> GateItem | None:
        """Gate の項目の指摘なら、その項目。レビューの指摘（R・D）は None。"""
        if self.value.startswith("G-"):
            return GateItem(self.value[len("G-") :])
        return None


class BranchName(Text):
    """git のブランチ名。ランの base（`main` など）もこれで持つ。

    autodev が切るブランチの規約（LEDGER N-93 を ADDENDUM §11 で採用・ADDENDUM §10）は
    `overview` と `for_task` で作る。使ったことのある名前と重ねないことは、集約が確かめる。
    """

    # 使える文字を絞ってあるので、git check-ref-format が拒む空白・制御文字・`~^:?*[\`・`@{` は入らない
    PATTERN = re.compile(r"[A-Za-z0-9._/-]+")

    def _check(self) -> None:
        """git check-ref-format（と `git branch`）が拒む形を拒む。"""
        name = self.value
        if name == "HEAD" or name.startswith("-") or name.endswith(".") or ".." in name:
            raise InvalidValue(f"git が受けないブランチ名: {name!r}")
        for part in name.split("/"):
            # 先頭と末尾の `/`・`//` は空の成分になる
            if not part or part.startswith(".") or part.endswith(".lock"):
                raise InvalidValue(f"git が受けないブランチ名: {name!r}")

    @classmethod
    def overview(cls, run: RunName) -> BranchName:
        return cls(f"stack/{run}--task-0")

    @classmethod
    def for_task(cls, run: RunName, number: int, branch_round: int = 0) -> BranchName:
        """タスクのブランチ。`branch_round` は切り直した回数（Run の TaskEntry.branch_round）で、
        破棄の後に積み直す・積む列から外して始め直すたびに、新しい名前で切り直す。"""
        if number < 1 or branch_round < 0:
            raise InvalidValue(
                f"タスクの番号は 1 以上・切り直した回数は 0 以上: {number}, {branch_round}"
            )
        suffix = f"-r{branch_round}" if branch_round else ""
        return cls(f"stack/{run}--task-{number}{suffix}")


class PrNumber(Number):
    pass


class Seq(Number):
    """ラン全体のイベントの通し番号。追記の順に 1 ずつ増える。"""


class StreamId(Text):
    """集約 1 つぶんのイベントの列。"""

    PATTERN = re.compile(
        rf"run|design|stack|questions|review/design|(?:task|review)/(?:{_TASK_ID})"
    )

    @classmethod
    def run(cls) -> StreamId:
        return cls("run")

    @classmethod
    def task(cls, task: TaskId) -> StreamId:
        return cls(f"task/{task}")

    @classmethod
    def review(cls, task: TaskId) -> StreamId:
        return cls(f"review/{task}")

    @classmethod
    def design_review(cls) -> StreamId:
        return cls("review/design")

    @classmethod
    def design(cls) -> StreamId:
        return cls("design")

    @classmethod
    def stack(cls) -> StreamId:
        return cls("stack")

    @classmethod
    def questions(cls) -> StreamId:
        return cls("questions")

    @property
    def is_review(self) -> bool:
        return self.value.startswith("review/")

    @property
    def is_task(self) -> bool:
        """タスクのストリーム（`task/<TaskId>`）か。"""
        return self.value.startswith("task/")


class EventId(Text):
    """イベントの id。`<StreamId>#<ストリームの中の版>`。

    ストリームと版の組は一意（`events` の UNIQUE）なので、乱数を使わずに決まる。集約は `handle` の中で、
    これから出すイベントの id を知ることができる（エスカレーションの id にする）。
    """

    PATTERN = re.compile(r"[a-z]+(?:/[a-z0-9]+)?#[1-9][0-9]*")

    def _check(self) -> None:
        StreamId(self.value.split("#", 1)[0])

    @classmethod
    def of(cls, stream: StreamId, version: int) -> EventId:
        return cls(f"{stream}#{version}")

    @property
    def stream(self) -> StreamId:
        return StreamId(self.value.split("#", 1)[0])

    @property
    def version(self) -> int:
        return int(self.value.split("#", 1)[1])


class CommandId(Text):
    """コマンドの id。

    ポリシーと反応が出すものは `derived` で、受けたイベントの id と自分の名前から決める。受け直しで
    同じコマンドがもう一度出ても同じ id になり、2 回目を弾ける。
    """

    PATTERN = re.compile(r"\S+")

    @classmethod
    def derived(cls, event: EventId, source: str, index: int = 0) -> CommandId:
        """1 つのイベントから同じ受け手が複数のコマンドを出すときは、`index` で分ける。"""
        return cls(f"{event}/{source}/{index}")


class Instruction(Text):
    """`/autodev` に渡された指示の本文。"""

    def _check(self) -> None:
        _non_blank("指示", self.value)


class Repository(Text):
    """対象リポジトリの絶対パス。"""

    PATTERN = re.compile(r"/.*")


class VerifyCommand(Text):
    """検証コマンド 1 本。`bash -lc` で流すので、パイプやリダイレクトを含んでよい（LEDGER FP-01）。"""

    def _check(self) -> None:
        _non_blank("検証コマンド", self.value)


class GlobPattern(Text):
    """テストのパス・変更禁止パス・テストが要らないパス。照合の規則（LEDGER HK-15）はアダプタとフックが持つ。"""

    def _check(self) -> None:
        _non_blank("glob", self.value)


#: テストのパスの既定（LEDGER N-99 を ADDENDUM §11 で採用）
DEFAULT_TEST_GLOBS: tuple[GlobPattern, ...] = tuple(
    GlobPattern(g)
    for g in (
        "**/test_*.py",
        "**/*_test.py",
        "**/tests/**",
        "**/*_test.go",
        "**/*.test.ts",
        "**/*.test.tsx",
        "**/*.spec.ts",
        "**/*.spec.tsx",
        "**/*Test.java",
        "**/*_spec.rb",
        "spec/**",
    )
)


class Location(Text):
    """指摘の位置。`path`・`path:12`・`path:12-15` のどれか。"""

    # path は短い方から伸ばす。長い方からだと `a.py:3` の `:3` まで path に取られる
    PATTERN = re.compile(
        r"(?P<path>\S(?:.*?\S)?)(?::(?P<start>[1-9][0-9]*)(?:-(?P<end>[1-9][0-9]*))?)?"
    )

    def _check(self) -> None:
        match = self._match()
        if match["end"] is not None and int(match["end"]) < int(match["start"]):
            raise InvalidValue(f"範囲の終わりが始まりより前: {self.value!r}")

    def _match(self) -> re.Match[str]:
        match = Location.PATTERN.fullmatch(self.value)
        assert match is not None  # __post_init__ で確かめてある
        return match

    @property
    def path(self) -> str:
        return self._match()["path"]

    @property
    def lines(self) -> tuple[int, int] | None:
        """行の範囲（1 行なら始まりと終わりが同じ）。行番号の無い位置は None。"""
        match = self._match()
        if match["start"] is None:
            return None
        start = int(match["start"])
        return start, int(match["end"] or start)


class DesignVersion(Number):
    """設計ファイルの版。本文は `design/v<版>.md` にあり、イベントは版の番号だけを持つ。"""

    @property
    def path(self) -> str:
        return f"design/v{self.value}.md"


class ParallelLimit(Number):
    """同時に走る実装タスクの上限。"""

    DEFAULT: ClassVar[int] = 3


# --- 歯止めの値（DOMAIN_MODEL §4）。進め方の形は縛らず、回り続けるループを止めて上へ上げる ---

#: タスクを 1 本も積まないまま続けた再計画の数の上限
MAX_REPLANS_WITHOUT_STACK = 2
#: 同じ指摘が修正をこの回数受けても `open` なら停滞
STALL_AFTER_FIXES = 2
#: 1 つの提案で回す設計のラウンドの上限
MAX_DESIGN_ROUNDS = 5
#: フローを捨てた git の仕事を、列の先頭へ戻す回数の上限。超えたら戻さずに止め、ラン統括に
#: 続けるかやめるかを聞く（何度やっても落ちる仕事で回り続けない）
MAX_JOB_RETURNS = 2
#: タスク統括が同じ知らせに（ラン統括の答えで起こし直しても）続けて応じなかった数のうち、ラン統括が
#: 受ける数。超えたら `/autodev` に聞く（ラン統括が毎回「続けて」と答えると、輪が止まらない）
MAX_SUPERVISOR_FAILURES = 2


# --- 列挙 ---


class StageKind(Enum):
    """ステージの種類（DOMAIN_MODEL §11）。値はフローの JSON に書く名前。"""

    # 計画タスク
    PREPARE = "Prepare"
    PLAN = "Plan"
    REPLAN = "Replan"
    DESIGN_LOOP = "DesignLoop"
    DESIGN_REVIEW = "DesignReview"
    DESIGN_JUDGE = "DesignJudge"
    REVISE = "Revise"
    # 実装タスク
    TEST_GEN = "TestGen"
    CONFIRM_RED = "ConfirmRed"
    IMPL = "Impl"
    REVIEW_LOOP = "ReviewLoop"
    EXPECT = "Expect"
    REVIEW = "Review"
    ADVERSARIAL_REVIEW = "AdversarialReview"
    JUDGE = "Judge"
    FIX = "Fix"
    GATE = "Gate"
    WRITE_PR_BODY = "WritePrBody"
    # 実装タスク（統合をやり直す差し込んだタスク）と git 管理タスク
    RESOLVE_CONFLICT = "ResolveConflict"
    # git 管理タスク
    CUT_BRANCH = "CutBranch"
    REBASE = "Rebase"
    CHECK_UNION = "CheckUnion"
    VERIFY = "Verify"
    PUSH = "Push"
    CREATE_PR = "CreatePR"
    STACK_LINK = "StackLink"
    REFRESH_OVERVIEW = "RefreshOverview"
    WRITE_OVERVIEW = "WriteOverview"
    CREATE_OVERVIEW_PR = "CreateOverviewPR"
    READY_OVERVIEW = "ReadyOverview"
    CLOSE_PRS = "ClosePRs"
    UNSTACK = "Unstack"
    RELINK = "Relink"


class ArtifactKind(Enum):
    """ステージが作り、後のステージが要るもの。

    `proposal`（確定前の設計の提案）は DOMAIN_MODEL §4 の列挙に無いが、§11.1 の Plan・Replan の
    produces と DesignLoop の needs に「提案」として現れるので足した。FlowValidator が計画タスクの
    フローも同じ規則で照合できるようにするためである。
    """

    BRIEF = "brief"
    CODEMAP = "codemap"
    PROPOSAL = "proposal"
    DESIGN = "design"
    TESTS = "tests"
    #: 期待値が空のテストがある（TestGen が報告した）。Expect が期待値を書いたら外れる
    AWAITING_EXPECTATIONS = "awaiting-expectations"
    RED_TESTS = "red-tests"
    IMPL = "impl"
    REVIEWED = "reviewed"
    GATED = "gated"
    PR_BODY = "pr-body"
    #: 引き継いだタスクが解き直す衝突（統合に失敗したタスクを差し込みで引き継いだ）。在りかは
    #: 引き継ぎ元のタスクの id で、ファイルの一覧は TaskOpened.conflicts にある。差し込んだタスクの
    #: ResolveConflict が書いてよいファイル（WriteScope.LISTED）になる
    CONFLICTS = "conflicts"

    @property
    def committed(self) -> bool:
        """作ったコミットが実物になる成果物か。始めた時点から HEAD が進んだことで作ったと確かめ、
        在りかは HEAD にする。"""
        return self in (ArtifactKind.TESTS, ArtifactKind.IMPL)


#: ラン共通の成果物。計画タスクが作り、TaskStarted・ScopeChanged で実装タスクへ渡る（ADDENDUM §7）
RUN_SHARED_ARTIFACTS: frozenset[ArtifactKind] = frozenset(
    {ArtifactKind.BRIEF, ArtifactKind.CODEMAP, ArtifactKind.DESIGN}
)


class WriteScope(Enum):
    """worktree の中で書いてよい範囲。"""

    NONE = "none"
    NON_TESTS = "non-tests"
    TESTS_AND_STUBS = "tests-and-stubs"
    TESTS_ONLY = "tests-only"
    #: 実行のときに渡すパスの一覧だけ（ResolveConflict の衝突したファイル）
    LISTED = "listed"


class Rating(Enum):
    MUST_FIX = "must-fix"
    SHOULD_FIX = "should-fix"
    NIT = "nit"


class FindingStatus(Enum):
    OPEN = "open"
    CLOSED = "closed"
    REJECTED = "rejected"
    #: 再計画で別のタスクへ移した元。未解決に数えない。終端
    CARRIED = "carried"


class StallCause(Enum):
    """ジャッジが停滞に付ける分類。"""

    TESTS = "tests"
    APPROACH = "approach"
    SCOPE = "scope"
    AMBIGUOUS = "ambiguous"


class DesignCause(Enum):
    """設計のジャッジの分類。"""

    REVERTED = "reverted"
    AMBIGUOUS = "ambiguous"


class TaskKind(Enum):
    PLANNING = "planning"
    IMPLEMENTATION = "implementation"
    GIT = "git"


class TaskStatus(Enum):
    """Run が持つ、各タスクの状態（DOMAIN_MODEL §9.1・ADDENDUM §2）。"""

    PENDING = "pending"
    RUNNING = "running"
    ESCALATED = "escalated"
    GATED = "gated"
    STACKING = "stacking"
    STACKED = "stacked"
    DROPPED = "dropped"
    SUPERSEDED = "superseded"
    DISCARDED = "discarded"
    #: 計画タスクと git 管理タスクの終端。RunFinished の後に移る
    FINISHED = "finished"

    @property
    def is_terminal(self) -> bool:
        """終端の状態か（ADDENDUM §11）。`stacked` は破棄されると動くが、終端に数える。"""
        return self in _TERMINAL_STATUSES


_TERMINAL_STATUSES = frozenset(
    {
        TaskStatus.STACKED,
        TaskStatus.DROPPED,
        TaskStatus.SUPERSEDED,
        TaskStatus.DISCARDED,
        TaskStatus.FINISHED,
    }
)


class DecisionOrigin(Enum):
    USER = "user"
    RUN_SUPERVISOR = "run-supervisor"


class EscalationKind(Enum):
    """DOMAIN_MODEL §8.2 の一覧に、ADDENDUM §6 の `gate-unfixable` を足したもの。"""

    STALL = "stall"
    DESIGN_GAP = "design-gap"
    TEST_CONFLICT = "test-conflict"
    ASK = "ask"
    RED_CHECK_FAILED = "red-check-failed"
    UNTESTED_CHANGE = "untested-change"
    GATE_UNFIXABLE = "gate-unfixable"
    STAGE_ERRORS = "stage-errors"
    DESIGN_AMBIGUOUS = "design-ambiguous"
    DESIGN_REVERTED = "design-reverted"
    DESIGN_ROUNDS_EXHAUSTED = "design-rounds-exhausted"
    INTEGRATION_FAILED = "integration-failed"
    NEEDS_REPLAN = "needs-replan"
    NEEDS_HUMAN = "needs-human"
    QUESTION = "question"
    #: ステージの結果を受け取る側（Design・指摘の台帳・Stack）が受けなかった（使った版が古い・
    #: 確定していない提案がある・無い指摘を判定した、など）。cursor は進めない
    RESULT_REFUSED = "result-refused"
    #: LLM の統括が、差し戻しと呼び直しを使い切っても知らせに応じなかった（driver が上げる）。
    #: タスク統括ならラン統括が、ラン統括ならユーザーが受ける（EscalationRouter）
    SUPERVISOR_FAILED = "supervisor-failed"


class IssuerKind(Enum):
    """コマンドを出した者の種類。driver が記録するので、ステージや統括は名乗れない。"""

    #: ラン統括（LLM）
    RUN_SUPERVISOR = "run-supervisor"
    #: タスクの統括。実装タスクは LLM、計画タスクと git 管理タスクはプログラム
    TASK_SUPERVISOR = "task-supervisor"
    POLICY = "policy"
    #: 反応（副作用を終えた後の続き）
    REACTION = "reaction"
    #: 実行器（ステージの結果と証拠を集めた変換層）
    EXECUTOR = "executor"
    #: `/autodev` の CLI（`requests` から届いた要求）
    CLI = "cli"
    #: アプリケーション層そのもの（パニック・起動時の後始末）
    DRIVER = "driver"


class GateItem(Enum):
    """完了チェックの項目（ADDENDUM §6）。"""

    #: そのタスクのコミットが親ブランチから 1 件以上ある
    COMMITS = "commits"
    #: 指摘の台帳に `open` の指摘が無い
    NO_OPEN_FINDINGS = "no-open-findings"
    #: フローの最後の ReviewLoop で、その回の reviewers がすべて走り終えている
    REVIEWERS_RAN = "reviewers-ran"
    #: TestGen があれば、そのコミットの後でテストのファイルが変わっていない
    TESTS_UNCHANGED = "tests-unchanged"
    #: TestGen が無ければ、変わったファイルがすべてテストが要らないパスに収まっている
    UNTESTED_PATHS = "untested-paths"
    #: そのタスクの verify がすべて通る
    VERIFY = "verify"

    @property
    def escalation(self) -> EscalationKind | None:
        """この項目が落ちたときに上げるエスカレーション。None ならコードを直せば解けるので、
        上げずに G- の指摘を開く（ADDENDUM §6）。"""
        return _GATE_ESCALATIONS[self]


_GATE_ITEM_VALUES = frozenset(item.value for item in GateItem)

_GATE_ESCALATIONS: dict[GateItem, EscalationKind | None] = {
    GateItem.COMMITS: EscalationKind.GATE_UNFIXABLE,
    GateItem.NO_OPEN_FINDINGS: None,
    GateItem.REVIEWERS_RAN: EscalationKind.GATE_UNFIXABLE,
    GateItem.TESTS_UNCHANGED: EscalationKind.GATE_UNFIXABLE,
    GateItem.UNTESTED_PATHS: EscalationKind.UNTESTED_CHANGE,
    GateItem.VERIFY: None,
}


class StageExit(Enum):
    """ステージのプロセスの終わり方。"""

    OK = "ok"
    ERROR = "error"


class InterruptCause(Enum):
    """走っていた実行を止めた理由（StageInterrupted）。止めた実行を再開するかは、これで決まる。"""

    #: driver の起動時の後始末。前の driver が走らせていて、誰も見ていない（MarkInterrupted）
    STARTUP = "startup"
    #: パニックで止めた（MarkInterrupted・InterruptStage）
    PANIC = "panic"
    #: タスクを止めた（StopTask）。タスクはもうステージを始めない
    STOPPED = "stopped"
    #: フローを捨てた（AbandonFlow）・ポリシーが止めた（InterruptStage）。そのフローは続けない
    REQUESTED = "requested"

    @property
    def resumes_on_restart(self) -> bool:
        """呼び直したとき（RunResumed）に、同じ実行を続きから再開するか。

        止めたのが driver の都合（落ちた・パニック）なら再開する。タスクやフローを止めると決めて
        止めたなら再開しない。
        """
        return self in (InterruptCause.STARTUP, InterruptCause.PANIC)


# --- 組の値 ---


@dataclass(frozen=True)
class ExecutionId:
    """ステージの実行 1 回の id。ラウンドは ReviewLoop・DesignLoop の中で 1 から、ほかは 0。"""

    task: TaskId
    stage: StageKind
    round: int
    attempt: int

    def __post_init__(self) -> None:
        if self.round < 0 or self.attempt < 1:
            raise InvalidValue(f"ラウンドは 0 以上・試行は 1 以上: {self.round}, {self.attempt}")

    def __str__(self) -> str:
        return f"{self.task}-{self.stage.value}-r{self.round}-a{self.attempt}"


@dataclass(frozen=True)
class ArtifactRef:
    """成果物と、実物の在りか。

    `at` の中身は種類で決まる。`tests` は TestGen（Expect が期待値を書いたらそのコミット）の
    CommitSha、`design` と `proposal` は版の番号（`proposal` は、実行器が提案の本文を書き出した
    `design/v<版>.md` の版）、`conflicts` は引き継ぎ元のタスクの id、ほかはランディレクトリからの
    パスかコミット。
    """

    kind: ArtifactKind
    at: str

    def __post_init__(self) -> None:
        _non_blank("成果物の在りか", self.at)


@dataclass(frozen=True)
class Guard:
    """ステージの種類ごとの書き込みの範囲と権限。

    worktree の外の扱いは、どの LLM のステージでも同じなので欄にしない: ランディレクトリ（自分の
    worktree を除く）・ホームディレクトリ・対象リポジトリの手元の checkout・場所の分からないものへの
    書き込みは止め、OS の一時ディレクトリ（`/tmp`・`$TMPDIR`）は許す。`gh` と `git push` も止める
    （DOMAIN_MODEL §4・ARCHITECTURE §10）。規則は `domain/guard.py` にあり、下のメソッドが呼ぶ。
    決定的なステージは claude を起動しないので、Guard を持たない。
    """

    #: worktree の中で書いてよい範囲
    writes: WriteScope
    #: 指摘の状態を動かせるか（JudgeCapability。Judge・DesignJudge）
    judge: bool = False
    #: 設計ファイルを渡すか。AdversarialReview には渡さない（LEDGER CT-11・CT-15）
    reads_design: bool = True
    #: ask で聞けるか（計画ステージ。PreToolUse のフックの defer で止める）
    can_ask: bool = False

    # 規則の置き場の domain/guard.py が Guard を import するので、循環を避けて呼ぶときに import する

    def judge_write(self, target: WriteTarget) -> Refusal | None:
        """書き込みの宛先 1 つを止めるか。止めるなら理由。"""
        from .guard import judge_write  # noqa: PLC0415

        return judge_write(self, target)

    def judge_operation(self, operation: Operation) -> Refusal | None:
        """`gh`・`git push`・名前の決まらないコマンドを止めるか。"""
        from .guard import judge_operation  # noqa: PLC0415

        return judge_operation(self, operation)

    def judge_ask(self, answered: bool) -> AskVerdict:
        from .guard import judge_ask  # noqa: PLC0415

        return judge_ask(self, answered)

    @property
    def withholds_github(self) -> bool:
        """このステージに GitHub の権限を渡さないか。起動する側は、gh の認証と git の資格情報を外す。"""
        from .guard import WITHHOLD_GITHUB_FROM_LLM  # noqa: PLC0415

        return WITHHOLD_GITHUB_FROM_LLM


@dataclass(frozen=True)
class TaskSpec:
    """タスクの中身。計画ステージが返し、再計画で書き換わる。"""

    title: str
    dod: str = ""
    acceptance: tuple[str, ...] = ()
    scope: tuple[str, ...] = ()
    #: 入口（次のステージが読み始める場所）
    entry_points: tuple[str, ...] = ()
    #: 境界の形（公開する型・関数の形）
    boundary: str = ""
    #: このタスクで足す検証コマンド。実装タスクの ConfirmRed と Gate で流す
    verify: tuple[VerifyCommand, ...] = ()

    def __post_init__(self) -> None:
        _non_blank("タスクの件名", self.title)


@dataclass(frozen=True)
class Decision:
    """回答で決めたこと。出どころを持つ（DOMAIN_MODEL §8.2・ADDENDUM §8）。

    ユーザーの回答は、その質問の QuestionId を持つ。ラン統括が自分で答えたものは持たない。
    """

    text: str
    origin: DecisionOrigin
    question: QuestionId | None = None

    def __post_init__(self) -> None:
        _non_blank("回答", self.text)
        if (self.origin is DecisionOrigin.USER) != (self.question is not None):
            raise InvalidValue("ユーザーの回答だけが QuestionId を持つ")

    @property
    def is_human(self) -> bool:
        """DOMAIN_MODEL の `HumanDecision`。ラン統括が答えるときの根拠にしてよいのはこれだけ。"""
        return self.origin is DecisionOrigin.USER


@dataclass(frozen=True)
class Pointers:
    """調べる先。パスはランディレクトリから。"""

    task_dir: str | None = None
    result: str | None = None
    log: str | None = None
    tree: str | None = None
    session: SessionId | None = None


@dataclass(frozen=True)
class Hint:
    """どこから読めばよいかの手がかり。本文は載せない。"""

    finding_ids: tuple[FindingId, ...] = ()
    stall_cause: StallCause | None = None
    design_cause: DesignCause | None = None
    gate_items: tuple[GateItem, ...] = ()


@dataclass(frozen=True)
class Issuer:
    """コマンドを出した者。`kind` ごとに要る欄が決まっている。

    タスクの統括は、実装タスクなら LLM（セッションを持つ）、計画タスクと git 管理タスクなら
    プログラム（セッションを持たない）で、タスクの id の種類で見分ける。名乗りが宛先と合うかは、
    集約の土台が確かめる（`aggregate.py` の「名乗りは宛先と合う」）。
    """

    kind: IssuerKind
    #: TASK_SUPERVISOR: どのタスクの統括か
    task: TaskId | None = None
    #: LLM の統括（ラン統括と実装タスクの統括）のセッション
    session: SessionId | None = None
    #: POLICY・REACTION: 受け手の名前
    name: str | None = None
    #: POLICY・REACTION: 受けたイベント
    event: EventId | None = None
    #: EXECUTOR: どの実行の結果か
    execution: ExecutionId | None = None

    def __post_init__(self) -> None:
        needs = {
            IssuerKind.RUN_SUPERVISOR: ("session",),
            IssuerKind.TASK_SUPERVISOR: ("task",),
            IssuerKind.POLICY: ("name", "event"),
            IssuerKind.REACTION: ("name", "event"),
            IssuerKind.EXECUTOR: ("execution",),
        }.get(self.kind, ())
        missing = [field for field in needs if getattr(self, field) is None]
        if missing:
            raise InvalidValue(f"{self.kind.value} の Issuer に {', '.join(missing)} が無い")
        if self.kind is IssuerKind.TASK_SUPERVISOR and self.task is not None:
            llm = self.task.kind is TaskKind.IMPLEMENTATION
            if llm != (self.session is not None):
                raise InvalidValue(
                    "セッションを持つのは実装タスクの統括（LLM）だけ。"
                    f"計画タスクと git 管理タスクの統括はプログラム: {self.task}"
                )

    @classmethod
    def run_supervisor(cls, session: SessionId) -> Issuer:
        return cls(IssuerKind.RUN_SUPERVISOR, session=session)

    @classmethod
    def task_supervisor(cls, task: TaskId, session: SessionId | None = None) -> Issuer:
        return cls(IssuerKind.TASK_SUPERVISOR, task=task, session=session)

    @classmethod
    def policy(cls, name: str, event: EventId) -> Issuer:
        return cls(IssuerKind.POLICY, name=name, event=event)

    @classmethod
    def reaction(cls, name: str, event: EventId) -> Issuer:
        return cls(IssuerKind.REACTION, name=name, event=event)

    @classmethod
    def executor(cls, execution: ExecutionId) -> Issuer:
        return cls(IssuerKind.EXECUTOR, execution=execution)

    @classmethod
    def cli(cls) -> Issuer:
        return cls(IssuerKind.CLI)

    @classmethod
    def driver(cls) -> Issuer:
        return cls(IssuerKind.DRIVER)


# --- 検証とソース管理 ---


@dataclass(frozen=True)
class GateItemResult:
    item: GateItem
    passed: bool
    reason: str = ""


@dataclass(frozen=True)
class GateReport:
    """完了チェックの項目ごとの合否。"""

    items: tuple[GateItemResult, ...]

    @property
    def passed(self) -> bool:
        return all(result.passed for result in self.items)

    @property
    def failed(self) -> tuple[GateItemResult, ...]:
        return tuple(result for result in self.items if not result.passed)


@dataclass(frozen=True)
class VerifyResult:
    command: VerifyCommand
    exit_code: int
    #: 出力の末尾
    tail: str = ""

    @property
    def passed(self) -> bool:
        return self.exit_code == 0


@dataclass(frozen=True)
class UnionFileVerdict:
    """衝突したファイル 1 つで、両側が足した行を残し、どちらも消していない行を消していないか。

    片方だけが消した行が戻っていても、衝突の印（`<<<<<<<` など）が残っていても、`kept_both` は
    偽にする。
    """

    path: str
    kept_both: bool
    #: 残っていなければならないのに、解いた結果に無い行
    missing: tuple[str, ...] = ()
    #: 片方が消したのに（もう片方は足し直していない）、解いた結果に戻った行
    revived: tuple[str, ...] = ()
    #: 解いた結果に増えた衝突の印の行（元のファイルにあった数を超えた分）
    markers: tuple[str, ...] = ()
    #: どれかの本文が UTF-8 として読めないかバイナリで、行で比べられなかった（意味が変わる統合とする）
    unreadable: bool = False


@dataclass(frozen=True)
class UnionVerdict:
    files: tuple[UnionFileVerdict, ...]

    @property
    def passed(self) -> bool:
        return all(file.kept_both for file in self.files)


@dataclass(frozen=True)
class StackEntry:
    """スタックに積んだ 1 本。一番下は概要ブランチで、その base はランの base。"""

    task: TaskId
    branch: BranchName
    pr: PrNumber
    base: BranchName


class GitJobKind(Enum):
    """git 管理タスクの仕事の種類（DOMAIN_MODEL §11.4）。種類ごとの並びは `stages.GIT_JOB_STAGES`。"""

    #: ランの開始: 概要ブランチと trees/overview を切る
    CUT_OVERVIEW = "cut-overview"
    #: 実装タスクを始めた: スタックの一番上から、タスクのブランチと trees/<TaskId> を切る
    CUT_TASK = "cut-task"
    #: 再計画を頼まれた: スタックの一番上で HEAD を固定した trees/stack-top を切り直す
    CUT_STACK_TOP = "cut-stack-top"
    #: 計画を初めて反映した: 概要 PR のまとめを書き、draft で作る
    OPEN_OVERVIEW = "open-overview"
    #: 再計画を反映した: 概要 PR のまとめを書き直す
    REWRITE_OVERVIEW = "rewrite-overview"
    #: フローを終えたタスクを積む（破棄の後の積み直しも）
    STACK = "stack"
    #: 積んだタスクを破棄した: 破棄した所から上を閉じ、スタックを作り直す
    DISCARD = "discard"
    #: ランが終わった: 仕上げのまとめを書き、概要 PR を draft から外す（外すと決めたときだけ）
    FINISH = "finish"

    @property
    def needs_overview(self) -> bool:
        """概要 PR を作った後でないと取り出さないか。スタックの一番上・概要 PR を相手にする仕事。"""
        return self not in (GitJobKind.CUT_OVERVIEW, GitJobKind.OPEN_OVERVIEW)

    @property
    def builds_on_top(self) -> bool:
        """取り出したときのスタックの一番上を base にするか。"""
        return self in (GitJobKind.CUT_TASK, GitJobKind.CUT_STACK_TOP, GitJobKind.STACK)

    @property
    def must_finish(self) -> bool:
        """済ませないとランが終わらない仕事か。フローを捨てても消さずに、列の先頭へ戻す。

        概要 PR の書き直しだけは、次の書き直しか仕上げで本文が作り直されるので、捨てたら消してよい。
        ほかは、切らないとタスクも再計画も始まらず、積まない・閉じないと Run が終端にならない。
        """
        return self is not GitJobKind.REWRITE_OVERVIEW

    @property
    def can_drop(self) -> bool:
        """止めた仕事を、ラン統括の判断でやめてよいか。

        タスクを切る・積む・stack-top を切り直す仕事は、やめても相手のタスクを止めるか再計画を
        やり直せば、ランの終わりを待たせるものが残らない。概要ブランチ・概要 PR を作る仕事は、やめると
        概要 PR を待つ仕事が二度と取り出されない。破棄はやめると閉じ終えるのを待つ数が減らず、仕上げは
        やめると git 管理タスクが終わらない。どちらもランが終わらなくなる。
        """
        return self in (
            GitJobKind.CUT_TASK,
            GitJobKind.CUT_STACK_TOP,
            GitJobKind.REWRITE_OVERVIEW,
            GitJobKind.STACK,
        )


@dataclass(frozen=True)
class GitJob:
    """git 管理タスクの仕事 1 つと、その相手。Stack の列に並び、1 つずつ取り出される。

    git 管理タスクの統括（プログラム）は、取り出した仕事から決まった並びを組むだけで、相手（タスク・
    ブランチ・base・閉じる所）は仕事が持つ。`base` と `cut_from` は、取り出したときに Stack が埋める
    （そのときのスタックの一番上・閉じる中で一番下）。
    """

    #: Stack が列に入れた順に振る番号（1 から）
    id: int
    kind: GitJobKind
    #: 相手のタスク。切る・積むタスク、worktree を使うタスク（概要・stack-top は計画タスク）
    task: TaskId | None = None
    #: 切る・積むブランチ。概要 PR の仕事は概要ブランチ
    branch: BranchName | None = None
    #: 載せる先。切る・積む仕事はスタックの一番上、概要ブランチを切る仕事はランの base
    base: BranchName | None = None
    #: 積み直すとき、前に積んだブランチ。CutBranch がそこから新しい名前で切り直す
    previous: BranchName | None = None
    #: 破棄する仕事: 破棄したタスク
    discarded: frozenset[TaskId] = frozenset()
    #: 破棄する仕事: 閉じる中で一番下。閉じるものが無ければ None
    cut_from: StackEntry | None = None
    #: 仕上げ: 概要 PR を draft から外すか
    ready_overview: bool = False

    def __post_init__(self) -> None:
        if self.id < 1:
            raise InvalidValue(f"仕事の番号は 1 以上: {self.id}")

    @property
    def cut_point(self) -> CutPoint | None:
        """CutBranch が切る元（ADDENDUM §10 の 4 つの場合）。切る元を持たない仕事は None。

        stack-top は一番上のコミットに HEAD を固定する。概要ブランチはランの base から切る。積み直す
        タスクは前に積んだブランチから、ほかはスタックの一番上から、新しい名前で切る。
        """
        if self.kind is GitJobKind.CUT_STACK_TOP:
            return CutPoint(self.base, detached=True) if self.base is not None else None
        if self.kind is GitJobKind.CUT_OVERVIEW:
            return CutPoint(self.base, run_base=True) if self.base is not None else None
        if self.previous is not None:
            return CutPoint(self.previous, roots_branch=False)
        return CutPoint(self.base) if self.base is not None else None


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


class FlowEnding(Enum):
    """git 管理タスクのフローの終わり方（FinishGitJob）。ポリシーが FlowFinished・FlowAbandoned から写す。"""

    FINISHED = "finished"
    ABANDONED = "abandoned"


class GitJobOutcome(Enum):
    """処理していた仕事の終わり方（GitJobFinished）。Stack が、フローの終わり方と仕事の記録から決める。"""

    #: フローを最後まで終えた
    DONE = "done"
    #: 相手のタスクを止めたので、列から外した（GitJobWithdrawn）
    WITHDRAWN = "withdrawn"
    #: 統合に失敗した（IntegrationFailed）。引き継ぐタスクが新しい仕事として積み直す
    INTEGRATION_FAILED = "integration-failed"
    #: 上のどれでもなくフローを捨てた（ステージが落ち続けて上げたエスカレーションを閉じた など）。
    #: 済ませないとランが終わらない仕事なので、列の先頭へ戻した
    ABANDONED = "abandoned"
    #: 捨てたが、済ませなくてもランが終わる仕事（`GitJobKind.must_finish` が偽）なので消した
    DROPPED = "dropped"
    #: 戻した回数が上限（MAX_JOB_RETURNS）に達した。戻さずに止め、ラン統括が続けるかやめるかを決める
    #: まで、後ろの仕事も取り出さない
    STUCK = "stuck"

    @property
    def returns_to_queue(self) -> bool:
        """仕事を列の先頭へ戻すか（名前の付いた規則）。終えてもいず、やめる理由も無い仕事は消さない。"""
        return self is GitJobOutcome.ABANDONED

    @property
    def keeps_job(self) -> bool:
        """仕事がまだ残っているか（列に戻した・止めてラン統括を待つ）。積む仕事なら相手は積む列で待つ。"""
        return self in (GitJobOutcome.ABANDONED, GitJobOutcome.STUCK)


# --- ステージの証拠 ---


@dataclass(frozen=True)
class DeferredCall:
    """計画ステージの ask が PreToolUse のフックの defer で止まった呼び出し。"""

    tool_use_id: str
    question: str

    def __post_init__(self) -> None:
        _non_blank("tool_use_id", self.tool_use_id)


@dataclass(frozen=True)
class Evidence:
    """実行器が外から集めた証拠。判断は入れない（完了・失敗・エスカレーションを決めるのは Task）。"""

    exit: StageExit
    #: 結果の JSON がスキーマの形をしているか（空なら False。LEDGER AR-16）
    result_valid: bool
    #: `produces` の実物を確かめられた成果物
    products: tuple[ArtifactRef, ...] = ()
    #: タスクのブランチの根元（`Task.base_commit`）から HEAD までのコミットの数。根元が無く数えられ
    #: なければ None（0 件と取り違えない）
    commits: int | None = None
    verify: tuple[VerifyResult, ...] = ()
    deferred: DeferredCall | None = None
    #: Gate のときだけ在る
    gate: GateReport | None = None
    #: CheckUnion のときだけ在る
    union: UnionVerdict | None = None
    #: Rebase が衝突したファイル
    conflicts: tuple[str, ...] = ()
    #: エラーで終わったときの理由（result が無ければ標準エラー。LEDGER AR-13）
    error: str | None = None
    #: ガードのフックに拒まれた呼び出しの数（LEDGER AR-26・AR-27）。LLM のステージだけが持つ
    hook_denials: int = 0
    #: claude を `--resume` で起こした（LLM のステージだけ）
    resumed: bool = False
    #: claude が `system/init` を出した（セッションを開いてターンを始めた）
    initialized: bool = False
    #: claude が result を返さずに自分で終わった（こちらが kill したのは含めない）
    ended_without_result: bool = False


# --- 計画 ---


@dataclass(frozen=True)
class PlannedTask:
    """提案の中のタスク 1 つ。残すタスクは今の id、新しいタスクはまだ使っていない番号の id で書く。"""

    id: TaskId
    spec: TaskSpec
    blocked_by: frozenset[TaskId] = frozenset()


@dataclass(frozen=True)
class FindingTransfer:
    """再計画で、未解決の指摘を別のタスクへ移す。"""

    finding: FindingId
    from_task: TaskId
    to_task: TaskId


@dataclass(frozen=True)
class FindingSummary:
    """指摘 1 件の中身。台帳の外（ポリシー・Design）が、台帳を読まずに判断するのに使う。"""

    finding: FindingId
    rating: Rating
    body: str
    location: Location | None = None


@dataclass(frozen=True)
class FindingOrigin:
    """移した指摘の、移す元。"""

    ledger: StreamId
    finding: FindingId


@dataclass(frozen=True)
class Proposal:
    """計画ステージ（Plan・Replan・Revise）が返した、確定前の提案。

    設計の本文は `design/v<版>.md` にあり、ここは版の番号だけを持つ（イベントが本文で膨らまない）。
    タスクの一覧はポリシーが ApplyPlan に変えるときに要るので、ここに持つ。
    """

    design: DesignVersion
    tasks: tuple[PlannedTask, ...]
    #: ラン共通の検証コマンド。git 管理タスクの Verify で流す
    verify: tuple[VerifyCommand, ...] = ()
    #: 止める候補（走っている実装タスク）
    stop: frozenset[TaskId] = frozenset()
    #: 破棄する候補（積んだタスク）。再利用できない理由は設計の本文に書く
    discard: frozenset[TaskId] = frozenset()
    carry: tuple[FindingTransfer, ...] = ()
    #: 計画ステージが自分の判断で決めたこと。概要 PR の判断ログに載せる（LEDGER N-81）
    decisions: tuple[str, ...] = ()
    #: 計画ステージがスコープの外にしたもの。概要 PR の判断ログに載せる（LEDGER N-81）
    deferrals: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        ids = [task.id for task in self.tasks]
        if len(ids) != len(set(ids)):
            raise InvalidValue("提案の中でタスクの id が重なっている")


# --- ステージの結果の中身（StageCompleted.result） ---


@dataclass(frozen=True)
class ReportedFinding:
    """レビューが挙げた指摘 1 件（Expect の defects も、ここに読み替える）。id は台帳が振る。"""

    rating: Rating
    body: str
    location: Location | None = None


@dataclass(frozen=True)
class FindingComment:
    """指摘への直し方・判断のコメント（CommentFinding）。"""

    finding: FindingId
    body: str


@dataclass(frozen=True)
class FindingVerdict:
    """ジャッジの判定 1 件（JudgeFinding）。"""

    finding: FindingId
    to: FindingStatus
    comment: str


@dataclass(frozen=True)
class DesignJudgement:
    """DesignJudge の設計の分類（MarkReverted・MarkAmbiguous）。"""

    cause: DesignCause
    #: 前の版に戻ったなら、戻った先の版
    reverted_to: DesignVersion | None = None
    #: そう判定した理由（回答を待つエスカレーションの理由になる）
    reason: str = ""
    #: 受入条件が曖昧なら、ユーザーに聞くこと
    question: str | None = None


@dataclass(frozen=True)
class WorktreeCut:
    """CutBranch が切った worktree（WorktreeReady）。"""

    #: その worktree を使うタスク
    task: TaskId
    #: ランディレクトリからのパス
    tree: str
    branch: BranchName | None = None
    #: 切った元のコミット。タスクのブランチなら、そのタスクの差分の起点になる（Task.base_commit）
    base: CommitSha | None = None

    def __post_init__(self) -> None:
        _non_blank("worktree の在りか", self.tree)


@dataclass(frozen=True)
class StageResult:
    """ステージの結果の JSON を、ドメインの値に読み替えたもの（`domain/results.py` が組む）。

    読むのは StageSpec.result が宣言した欄だけで、宣言していない欄は空のまま残る。ポリシーは、
    これと StageCompleted のほかの欄だけで、受け取る側へのコマンド（RecordFindings・
    RecordJudgement・ProposeDesign・AppendEntry など）を組む。
    """

    report: EscalationKind | None = None
    report_reason: str | None = None
    findings: tuple[ReportedFinding, ...] = ()
    comments: tuple[FindingComment, ...] = ()
    verdicts: tuple[FindingVerdict, ...] = ()
    stall_cause: StallCause | None = None
    #: 停滞の分類（stall_cause）を付けた理由。停滞のエスカレーションの理由になる
    stall_reason: str | None = None
    design_cause: DesignJudgement | None = None
    proposal: Proposal | None = None
    #: 確かめた結果「変えない」と決めて、何も作らずに終えた（StageSpec.can_keep のステージだけ）
    unchanged: bool = False
    pr: PrNumber | None = None
    worktree: WorktreeCut | None = None
    #: Rebase がタスクのブランチを載せ直した先のコミット（BranchRebased）
    onto: CommitSha | None = None
