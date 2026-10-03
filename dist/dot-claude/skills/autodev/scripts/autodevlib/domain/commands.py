"""コマンド（DOMAIN_MODEL §7.2・ADDENDUM）。

コマンドは「〜せよ」という命令で、宛先の集約は 1 つ（`target`）。どれも `command_id` と、出した者
（`issuer`）を持つ。出してよい者は `ISSUERS` に書き、外れた者のコマンドは集約の土台が拒む
（`aggregate.py`）。出す者は driver が記録するので、ステージや統括は名乗りで偽れない。

計画タスクと git 管理タスクの統括（プログラム）は、`TASK_SUPERVISOR` として出す。タスクの統括が
名乗ってよいタスクは `supervised_task`、実行器が名乗ってよい実行は `reported_execution` で、
集約の土台がどちらも名乗りと照らし合わせる。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, ClassVar

from .flow import FlowStep
from .values import (
    ArtifactRef,
    BranchName,
    CommandId,
    CommitSha,
    Decision,
    DesignJudgement,
    DesignVersion,
    EscalationKind,
    EventId,
    Evidence,
    ExecutionId,
    FindingId,
    FindingOrigin,
    FindingStatus,
    FindingSummary,
    FindingVerdict,
    FlowEnding,
    GateItemResult,
    GitJob,
    GitJobKind,
    Hint,
    Instruction,
    InterruptCause,
    InvalidValue,
    Issuer,
    IssuerKind,
    Location,
    ParallelLimit,
    Pointers,
    PrNumber,
    Proposal,
    QuestionId,
    Rating,
    ReportedFinding,
    Repository,
    RunName,
    SessionId,
    StackEntry,
    StallCause,
    StreamId,
    TaskId,
    TaskKind,
    TaskSpec,
    TaskStatus,
)

_K = IssuerKind


@dataclass(frozen=True, kw_only=True)
class Command:
    command_id: CommandId
    issuer: Issuer

    #: 出してよい者
    ISSUERS: ClassVar[frozenset[IssuerKind]] = frozenset()
    #: 宛先の集約の名前（`events.EVENTS_BY_AGGREGATE` のキーと同じ）
    AGGREGATE: ClassVar[str] = ""

    @property
    def target(self) -> StreamId:
        raise NotImplementedError

    @property
    def supervised_task(self) -> TaskId | None:
        """タスクの統括が出すとき、名乗ってよいタスク。None なら、タスクの統括は出せない。"""
        return None

    @property
    def reported_execution(self) -> ExecutionId | None:
        """実行器が出すとき、名乗ってよい実行。None なら、実行器は出せない。"""
        execution = getattr(self, "execution", None)
        return execution if isinstance(execution, ExecutionId) else None


# --- Run ---


@dataclass(frozen=True, kw_only=True)
class RunCommand(Command):
    AGGREGATE: ClassVar[str] = "Run"

    @property
    def target(self) -> StreamId:
        return StreamId.run()


@dataclass(frozen=True, kw_only=True)
class StartRun(RunCommand):
    ISSUERS = frozenset({_K.CLI})
    name: RunName
    instruction: Instruction
    repository: Repository
    base: BranchName
    limit: ParallelLimit = field(default_factory=lambda: ParallelLimit(ParallelLimit.DEFAULT))


@dataclass(frozen=True, kw_only=True)
class StartTask(RunCommand):
    ISSUERS = frozenset({_K.POLICY})
    task: TaskId


@dataclass(frozen=True, kw_only=True)
class ApplyPlan(RunCommand):
    """記録してある確定した提案（`RecordSettledPlan`）のうち、`design` の版のものを、初めて反映する。

    `SettledPlanRecorded.replan` が偽のときにポリシーが出す。再計画の反映は ApplyReplan。
    """

    ISSUERS = frozenset({_K.POLICY})
    design: DesignVersion


@dataclass(frozen=True, kw_only=True)
class ApplyReplan(RunCommand):
    """ラン統括の `apply-plan`。確定した再計画を、止める・破棄する・反映するまで 1 回で行う。

    ADDENDUM §3 はアプリケーション層が StopTasks → DiscardTasks → ApplyPlan の 3 つに置き換えると
    するが、それだと「前が拒まれたら後ろを出さない」という並びの知識がドメインの外に残り、前半だけが
    確定することもある。そこで 1 つのコマンドにし、Run がまとめて確かめて TasksStopped・
    TasksDiscarded・TasksPlanned を出す（空の一覧のイベントは出さない）。初回の反映は ApplyPlan。
    """

    ISSUERS = frozenset({_K.RUN_SUPERVISOR})
    design: DesignVersion
    #: 止める実装タスク。確定した提案の止める候補から選ぶ
    stop: frozenset[TaskId] = frozenset()
    #: 破棄する積んだタスク。確定した提案の破棄する候補から選ぶ
    discard: frozenset[TaskId] = frozenset()
    #: 応える Run のエスカレーション（再計画のきっかけ以外に、処理したら閉じるもの）
    responds_to: EventId | None = None


@dataclass(frozen=True, kw_only=True)
class RequestReplan(RunCommand):
    ISSUERS = frozenset({_K.RUN_SUPERVISOR})
    reason: str
    #: きっかけのエスカレーション（needs-replan や計画タスクの ask に応じるとき）
    trigger: EventId | None = None
    #: 再計画の上限に達した後だけ要る、ユーザーの回答
    answer: QuestionId | None = None


@dataclass(frozen=True, kw_only=True)
class StartReadyTasks(RunCommand):
    """始められる実装タスクを、TaskScheduler の選んだだけ始める。

    ポリシーは Run の状態を持たないので、どれを始めるかは Run が TaskScheduler に聞いて決める。
    ポリシーは、始められるタスクが増えうるイベント（TasksPlanned・TaskMarkedStacked など）を
    受けてこれを出すだけでよい。
    """

    ISSUERS = frozenset({_K.POLICY, _K.DRIVER})


@dataclass(frozen=True, kw_only=True)
class InsertTask(RunCommand):
    ISSUERS = frozenset({_K.RUN_SUPERVISOR})
    spec: TaskSpec
    blocked_by: frozenset[TaskId] = frozenset()
    takes_over: TaskId | None = None
    #: 応える Run のエスカレーション。処理したら閉じる（ADDENDUM §8）
    responds_to: EventId | None = None


@dataclass(frozen=True, kw_only=True)
class StopTasks(RunCommand):
    ISSUERS = frozenset({_K.RUN_SUPERVISOR})
    tasks: frozenset[TaskId]
    #: 応える Run のエスカレーション。処理したら閉じる（ADDENDUM §8）
    responds_to: EventId | None = None


@dataclass(frozen=True, kw_only=True)
class ReturnToQueue(RunCommand):
    """閉じ終えた（StackCutBack）。積む列に戻すタスクは Run が持っている一覧（TasksDiscarded.requeue）
    で、ポリシーは写さない。一覧の出どころを 1 つにするためである。"""

    ISSUERS = frozenset({_K.POLICY})


@dataclass(frozen=True, kw_only=True)
class RecordIntegrationFailure(RunCommand):
    """積んでいる途中のタスクの統合が失敗した（Stack の IntegrationFailed を受けて）。"""

    ISSUERS = frozenset({_K.POLICY})
    task: TaskId
    reason: str
    files: tuple[str, ...] = ()


@dataclass(frozen=True, kw_only=True)
class ClearIntegrationFailure(RunCommand):
    """統合をやり直すので、統合の失敗の印を下ろす（Stack の IntegrationRetried を受けて）。"""

    ISSUERS = frozenset({_K.POLICY})
    task: TaskId


@dataclass(frozen=True, kw_only=True)
class MarkStacked(RunCommand):
    ISSUERS = frozenset({_K.POLICY})
    task: TaskId
    pr: PrNumber


@dataclass(frozen=True, kw_only=True)
class EscalateToRun(RunCommand):
    ISSUERS = frozenset({_K.TASK_SUPERVISOR})
    #: 上げてきたタスク
    task: TaskId
    kind: EscalationKind
    #: 上げる理由（統括が書く。プログラムの統括は、タスクの側のエスカレーションの理由を写す）
    reason: str
    pointers: Pointers
    hint: Hint = field(default_factory=Hint)
    #: 上げる元になった、タスクの側のエスカレーション。ラン統括の回答はそこへ下りる
    source: EventId | None = None
    #: ユーザーに聞くとよいこと（DesignJudge が書いた問いなど。タスクの側のものを写す）
    question: str | None = None
    #: 答え以外では閉じられない（タスクの側のものを写す。EscalationRaised.answer_only）
    answer_only: bool = False

    @property
    def supervised_task(self) -> TaskId | None:
        return self.task


@dataclass(frozen=True, kw_only=True)
class CloseRelayedEscalation(RunCommand):
    """タスクの側で閉じたエスカレーション（`source`）を中継していた、Run の側のエスカレーションを閉じる。

    タスクの側の EscalationClosed を受けたポリシーが、中継があるかを知らずに出す。無ければ何もしない。
    """

    ISSUERS = frozenset({_K.POLICY})
    source: EventId
    reason: str


@dataclass(frozen=True, kw_only=True)
class AnswerEscalation(RunCommand):
    """ラン統括の `answer`。Run のエスカレーションに答え、上げてきたタスクへ回答を下ろす。

    自分で答えるなら `answer`、ユーザーの回答を渡すなら `question` のどちらか一方を書く。ユーザーの
    回答の本文は、ラン統括の判断からではなく、Run が受け取った回答（AnswerRecorded）から写す
    （ADDENDUM §8）。
    """

    ISSUERS = frozenset({_K.RUN_SUPERVISOR})
    escalation: EventId
    answer: str | None = None
    question: QuestionId | None = None


@dataclass(frozen=True, kw_only=True)
class RecordAnswer(RunCommand):
    """ユーザーの回答を Run に渡す。QuestionAnswered を受けたポリシーが出す。"""

    ISSUERS = frozenset({_K.POLICY})
    question: QuestionId
    answer: str
    escalation: EventId | None = None


@dataclass(frozen=True, kw_only=True)
class Panic(RunCommand):
    ISSUERS = frozenset({_K.DRIVER})
    cause: str


@dataclass(frozen=True, kw_only=True)
class ReportSupervisorFailure(RunCommand):
    """LLM の統括が、差し戻しと呼び直しを使い切っても知らせに応じなかった（driver が出す）。

    どこへ上げるかは Run が EscalationRouter に聞いて決める（段を飛ばさない）。
    """

    ISSUERS = frozenset({_K.DRIVER})
    #: 応じなかったタスク統括のタスク。None ならラン統括
    supervisor: TaskId | None
    #: 応じられなかった知らせ（統括を起こしたイベント）
    notice: EventId
    reason: str


@dataclass(frozen=True, kw_only=True)
class FinishRun(RunCommand):
    ISSUERS = frozenset({_K.RUN_SUPERVISOR})
    ready_overview: bool


@dataclass(frozen=True, kw_only=True)
class UpdateTaskStatus(RunCommand):
    """Task・Stack のイベントを受けたポリシーが、Run の持つタスクの状態を動かす（ADDENDUM §2）。"""

    ISSUERS = frozenset({_K.POLICY})
    task: TaskId
    to_status: TaskStatus
    #: きっかけのイベントの名前
    cause: str


@dataclass(frozen=True, kw_only=True)
class RecordSettledPlan(RunCommand):
    ISSUERS = frozenset({_K.POLICY})
    proposal: Proposal
    #: ラン共通の成果物（brief・codemap・design）
    artifacts: tuple[ArtifactRef, ...] = ()


@dataclass(frozen=True, kw_only=True)
class ResumeRun(RunCommand):
    """既にあるラン名で呼び直された（パニックの後も、driver が落ちた後も。ADDENDUM §9）。"""

    ISSUERS = frozenset({_K.CLI, _K.DRIVER})


# --- Task ---


@dataclass(frozen=True, kw_only=True)
class TaskCommand(Command):
    AGGREGATE: ClassVar[str] = "Task"
    task: TaskId

    @property
    def target(self) -> StreamId:
        return StreamId.task(self.task)

    @property
    def supervised_task(self) -> TaskId | None:
        return self.task


@dataclass(frozen=True, kw_only=True)
class AcceptFlow(TaskCommand):
    """統括が返したフロー。版の番号は、受け入れるときに Task が付ける。"""

    ISSUERS = frozenset({_K.TASK_SUPERVISOR})
    steps: tuple[FlowStep, ...]
    #: 上がってきたエスカレーションへの応答なら、そのエスカレーション
    responds_to: EventId | None = None
    #: git 管理タスクの統括（プログラム）が、取り出した仕事（GitJobTaken.job）を写す。ほかの統括は書かない
    job: GitJob | None = None


@dataclass(frozen=True, kw_only=True)
class OpenTask(TaskCommand):
    """Task のストリームの最初のコマンド。TaskStarted（Run）を受けたポリシーが出す。"""

    ISSUERS = frozenset({_K.POLICY})
    kind: TaskKind
    #: 実装タスクだけが持つ
    spec: TaskSpec | None = None
    #: そのときのラン共通の成果物（brief・codemap・design の版。ADDENDUM §7）
    artifacts: tuple[ArtifactRef, ...] = ()
    blocked_by: frozenset[TaskId] = frozenset()
    #: 実装タスクのブランチ（TaskStarted から写す）
    branch: BranchName | None = None
    #: 引き継いで解き直す衝突したファイル（TaskStarted から写す）
    conflicts: tuple[str, ...] = ()


@dataclass(frozen=True, kw_only=True)
class BeginStage(TaskCommand):
    """走らせると決めたステージを始める（2 段目）。StageRequested を受けた実行器が出す。

    HEAD とセッション id はドメインの外の値なので、実行器が集めて載せる（DOMAIN_MODEL §6.7）。
    まだ始まっていない StageRequested の実行と合わなければ、Task が拒む。
    """

    ISSUERS = frozenset({_K.EXECUTOR})
    execution: ExecutionId
    head: CommitSha
    #: LLM のステージのセッション。決定的なステージには無いので None を明示して渡す
    session: SessionId | None


@dataclass(frozen=True, kw_only=True)
class ReportBeginFailure(TaskCommand):
    """走らせると決めたステージを、実行器が始められなかった（worktree を戻せない・HEAD が取れない・
    止めた実行が同じ worktree で終わらない）。BeginStage の代わりに出す。

    やり直すか上げるかは、走らせて落ちたときと同じ規則で Task が決める。出さないと、実行は requested
    のまま誰も進めない。
    """

    ISSUERS = frozenset({_K.EXECUTOR})
    execution: ExecutionId
    error: str


@dataclass(frozen=True, kw_only=True)
class ReportStageResult(TaskCommand):
    """実行器が集めた証拠。完了・失敗・エスカレーションのどれにするかは Task が決める。"""

    ISSUERS = frozenset({_K.EXECUTOR})
    execution: ExecutionId
    evidence: Evidence
    pointers: Pointers
    #: ステージが返した結果の JSON（形を確かめられなかったら None）
    result: dict[str, Any] | None = None


@dataclass(frozen=True, kw_only=True)
class InterruptStage(TaskCommand):
    """ポリシーが走っている実行を止める。理由はパニックか、ポリシーの判断（requested）。"""

    ISSUERS = frozenset({_K.POLICY})
    execution: ExecutionId
    cause: InterruptCause = InterruptCause.REQUESTED


@dataclass(frozen=True, kw_only=True)
class MarkInterrupted(TaskCommand):
    """driver が、running のまま残っていた実行を interrupted にする（ADDENDUM §9）。

    起動時の後始末なら startup、パニックで止めるときなら panic。どれが running かは Task に聞く
    （`Task.running_executions`）。
    """

    ISSUERS = frozenset({_K.DRIVER})
    execution: ExecutionId
    cause: InterruptCause = InterruptCause.STARTUP


@dataclass(frozen=True, kw_only=True)
class ResumeInterrupted(TaskCommand):
    """呼び直された（RunResumed）。止めた理由が再開を求める実行を、続きから再開する。

    どれを再開するかは Task が決める（InterruptCause.resumes_on_restart・書き直す前のフローの実行は
    再開しない）。再開するものが無ければ何もしない。
    """

    ISSUERS = frozenset({_K.POLICY})


@dataclass(frozen=True, kw_only=True)
class ConfirmHandoff(TaskCommand):
    """結果を渡した先の答え（ResultReceived・ResultRefused を受けて）。

    受けたなら cursor を進め、受けなかったなら `refused` に理由を書き、Task が result-refused で上げる。
    判定の役（Judge・DesignJudge）の結果を受けたことは Conclude*Round が知らせるので、受けなかった
    ときだけ届く。
    """

    ISSUERS = frozenset({_K.POLICY})
    #: 結果を返したステージの実行（ResultReceived・ResultRefused の source）
    execution: ExecutionId
    refused: str | None = None


@dataclass(frozen=True, kw_only=True)
class AbandonFlow(TaskCommand):
    """今のフローを捨てる（処理中の git 管理タスクの仕事を外した GitJobWithdrawn を受けて）。

    走っている実行を止め、統括の次のフローを待つ。
    """

    ISSUERS = frozenset({_K.POLICY})
    reason: str


@dataclass(frozen=True, kw_only=True)
class ResumeStage(TaskCommand):
    ISSUERS = frozenset({_K.CLI, _K.REACTION, _K.DRIVER})
    execution: ExecutionId


@dataclass(frozen=True, kw_only=True)
class Escalate(TaskCommand):
    ISSUERS = frozenset({_K.POLICY})
    kind: EscalationKind
    pointers: Pointers
    hint: Hint = field(default_factory=Hint)
    origin: ExecutionId | None = None
    #: 上げる理由。書かなければ、Task が判定した実行（origin）の結果から読む
    reason: str = ""
    #: 答え以外では閉じられない（EscalationRaised.answer_only）
    answer_only: bool = False


@dataclass(frozen=True, kw_only=True)
class ResolveEscalation(TaskCommand):
    """回答をタスクへ下ろす。Run の EscalationAnswered を受けたポリシーが出す。

    ラン統括の `answer` は Run が受け（AnswerEscalation）、経路と QuestionId を確かめてから下ろす。
    """

    ISSUERS = frozenset({_K.POLICY})
    escalation: EventId
    answer: str
    #: ユーザーの回答を渡すなら、その質問。無ければラン統括の回答
    question: QuestionId | None = None


@dataclass(frozen=True, kw_only=True)
class CloseEscalation(TaskCommand):
    """回答以外で閉じる。もう閉じていれば何もしない（Run の EscalationClosed を受けたポリシーは、
    タスクの側でもう閉じたかを知らずに出す）。"""

    ISSUERS = frozenset({_K.POLICY})
    escalation: EventId
    reason: str


@dataclass(frozen=True, kw_only=True)
class ChangeScope(TaskCommand):
    """実装タスクにだけ送る（ADDENDUM §1）。"""

    ISSUERS = frozenset({_K.POLICY})
    spec: TaskSpec
    #: そのときのラン共通の成果物（design の版など）
    artifacts: tuple[ArtifactRef, ...]
    pointers: Pointers
    #: 積む列から外して始め直すなら、新しいブランチ（TaskStarted の reopened から写す）
    branch: BranchName | None = None


@dataclass(frozen=True, kw_only=True)
class AddNote(TaskCommand):
    ISSUERS = frozenset({_K.POLICY})
    decision: Decision


@dataclass(frozen=True, kw_only=True)
class RecordBase(TaskCommand):
    """タスクのブランチの根元を覚えさせる（WorktreeReady・BranchRebased を受けたポリシーが出す）。"""

    ISSUERS = frozenset({_K.POLICY})
    base: CommitSha
    #: そのタスクのために切った worktree。根元だけが動いた（Rebase）なら None
    tree: str | None = None


@dataclass(frozen=True, kw_only=True)
class ConcludeReviewRound(TaskCommand):
    """ReviewLoop の Judge の判定を台帳に当て終えた。台帳の結果を Task に渡す（ADDENDUM §4・§5）。

    指摘の台帳の FindingsEvaluated（RecordJudgement が出す）から組む。次の一手（Fix・抜ける・停滞で
    上げる）は Task が決める。Judge の結果を台帳が受けたことの知らせも兼ねる。
    """

    ISSUERS = frozenset({_K.POLICY})
    #: 判定を返した Judge の実行（FindingsEvaluated.execution）
    judge: ExecutionId
    #: 判定の後も open の指摘（FindingsEvaluated.open_findings）
    unresolved: tuple[FindingId, ...]
    #: そのうち停滞したもの（FindingsEvaluated.stalled）
    stalled: tuple[FindingId, ...] = ()
    #: ジャッジが停滞に付けた分類（FindingsEvaluated.stall_cause）
    cause: StallCause | None = None
    pointers: Pointers = field(default_factory=Pointers)


@dataclass(frozen=True, kw_only=True)
class ConcludeGateRound(TaskCommand):
    """Gate の不合格の後、G- の指摘を判定した台帳の結果を Task に渡す（ADDENDUM §6）。

    指摘の台帳の FindingsEvaluated（RecordGateResult が出す）から組む。Fix へ戻るか、停滞で上げるかは
    Task が決める。Gate が通った後の結果なら、何もしない。
    """

    ISSUERS = frozenset({_K.POLICY})
    #: G- の指摘を判定した Gate の実行（FindingsEvaluated.execution）
    gate: ExecutionId
    unresolved: tuple[FindingId, ...]
    stalled: tuple[FindingId, ...] = ()
    pointers: Pointers = field(default_factory=Pointers)


@dataclass(frozen=True, kw_only=True)
class ConcludeDesignRound(TaskCommand):
    """DesignLoop の DesignJudge の後、Design が確定したか直すかを Task に渡す（ADDENDUM §4・§11）。

    `settled` があれば設計が確定した（DesignSettled）ので DesignLoop を抜ける。無ければ Revise の
    ラウンドを使った（DesignRevisionStarted）ので Revise へ進む。
    """

    ISSUERS = frozenset({_K.POLICY})
    #: そう決めた DesignJudge の実行（DesignSettled.execution・DesignRevisionStarted.execution）
    judge: ExecutionId
    settled: DesignVersion | None = None


@dataclass(frozen=True, kw_only=True)
class StopTask(TaskCommand):
    """Run が止めたタスク（TasksStopped）を、Task の側でも止める。ポリシーが出す。

    走っている実行を中断し、開いているエスカレーションを閉じ、以後ステージを始めない。
    """

    ISSUERS = frozenset({_K.POLICY})
    reason: str


# --- ReviewLedger ---


@dataclass(frozen=True, kw_only=True)
class ReviewCommand(Command):
    AGGREGATE: ClassVar[str] = "ReviewLedger"
    #: `review/<TaskId>` か `review/design`
    ledger: StreamId

    def __post_init__(self) -> None:
        if not self.ledger.is_review:
            raise InvalidValue(f"指摘の台帳のストリームではない: {self.ledger}")

    @property
    def target(self) -> StreamId:
        return self.ledger


@dataclass(frozen=True, kw_only=True)
class RecordFindings(ReviewCommand):
    """見る役（Review・AdversarialReview・DesignReview）と Expect が挙げた指摘を、まとめて立てる。

    受けたら ResultReceived、中身の問題（本文が空・見た版が今の提案より古い）なら ResultRefused を出す。
    1 件ずつの RaiseFinding にしないのは、受けた／受けられないをステージの結果 1 つに 1 回で返すため。
    """

    ISSUERS = frozenset({_K.POLICY})
    #: 指摘を挙げたステージの実行
    source: ExecutionId
    findings: tuple[ReportedFinding, ...]
    #: 設計の台帳に立てるときだけ要る。DesignReview が見た提案の版（StageCompleted.reviewed）
    design: DesignVersion | None = None


@dataclass(frozen=True, kw_only=True)
class RaiseFinding(ReviewCommand):
    """指摘を 1 件立てる（移した指摘を、移した先に立てる。FindingCarried を受けて）。

    ステージの結果の指摘は RecordFindings、Gate の項目の指摘（G-）は RecordGateResult が立てる。
    """

    ISSUERS = frozenset({_K.POLICY})
    rating: Rating
    body: str
    location: Location | None = None
    carried_from: FindingOrigin | None = None
    source: ExecutionId | None = None
    #: 設計の台帳に立てるときだけ要る。レビューした設計の版
    design: DesignVersion | None = None


@dataclass(frozen=True, kw_only=True)
class CommentFinding(ReviewCommand):
    ISSUERS = frozenset({_K.POLICY})
    finding: FindingId
    body: str
    author: ExecutionId | None = None


@dataclass(frozen=True, kw_only=True)
class JudgeFinding(ReviewCommand):
    """JudgeCapability: `execution` がその指摘を判定する者の実行であるものだけを、台帳が受ける。"""

    ISSUERS = frozenset({_K.POLICY})
    finding: FindingId
    to: FindingStatus
    comment: str
    execution: ExecutionId


@dataclass(frozen=True, kw_only=True)
class CountFix(ReviewCommand):
    ISSUERS = frozenset({_K.POLICY})
    #: 完了した Fix の実行
    execution: ExecutionId


@dataclass(frozen=True, kw_only=True)
class RecordJudgement(ReviewCommand):
    """Judge・DesignJudge の判定を当て、判定を締める（ADDENDUM §5）。

    判定をすべて当ててから、修正を STALL_AFTER_FIXES 回以上受けたまま open に残った指摘ごとに
    FindingStalled を出し、最後に FindingsEvaluated（ジャッジの分類を写す）を出す。中身の問題（無い
    指摘・許されない遷移・コメントが空・見た版が古い）があれば、どれも当てずに ResultRefused を出す。
    """

    ISSUERS = frozenset({_K.POLICY})
    #: 判定を返した Judge・DesignJudge の実行
    execution: ExecutionId
    verdicts: tuple[FindingVerdict, ...] = ()
    #: Judge が停滞に付けた分類（タスクの台帳だけ）
    stall_cause: StallCause | None = None
    #: 設計の台帳だけ: DesignJudge が見た提案の版（StageCompleted.reviewed）と、設計の分類
    design: DesignVersion | None = None
    design_cause: DesignJudgement | None = None


@dataclass(frozen=True, kw_only=True)
class RecordGateResult(ReviewCommand):
    """Gate の結果で、Gate の項目の指摘（G-）を判定する（Gate の StageCompleted・GateFailed を受けて）。

    落ちた項目の指摘は開き（閉じていれば開き直し）、通った項目の open の指摘は閉じる。判定を
    締めるので、RecordJudgement と同じく FindingStalled と FindingsEvaluated も出す。
    """

    ISSUERS = frozenset({_K.POLICY})
    #: Gate の実行
    execution: ExecutionId
    #: 落ちた項目（GateFailed.failed）。Gate が通ったなら空
    failed: tuple[GateItemResult, ...] = ()


@dataclass(frozen=True, kw_only=True)
class TrackProposal(ReviewCommand):
    """設計の台帳が数える指摘を、新しい提案の版から後に絞る（DesignProposed を受けて）。"""

    ISSUERS = frozenset({_K.POLICY})
    design: DesignVersion


@dataclass(frozen=True, kw_only=True)
class CarryFinding(ReviewCommand):
    """宛先は移す元の台帳。"""

    ISSUERS = frozenset({_K.POLICY})
    finding: FindingId
    to_task: TaskId


# --- Design ---


@dataclass(frozen=True, kw_only=True)
class DesignCommand(Command):
    AGGREGATE: ClassVar[str] = "Design"

    @property
    def target(self) -> StreamId:
        return StreamId.design()


@dataclass(frozen=True, kw_only=True)
class ProposeDesign(DesignCommand):
    """Plan・Replan・Revise の結果の提案を入れる。

    新しい提案（Plan・Replan）か、今の提案の新しい版（Revise）かは、`execution` のステージで決まる。
    受けたら ResultReceived、中身の問題（確定していない提案がすでにある・使った版の番号など）なら
    ResultRefused を出す。
    """

    ISSUERS = frozenset({_K.POLICY})
    proposal: Proposal
    #: 提案を返したステージの実行
    execution: ExecutionId
    #: ラン共通の成果物（brief・codemap）の在りか（StageCompleted.shared）。確定したら Run へ渡る
    artifacts: tuple[ArtifactRef, ...] = ()


@dataclass(frozen=True, kw_only=True)
class ReviseDesign(DesignCommand):
    """Revise を起動する前に出す（ADDENDUM §11）。"""

    ISSUERS = frozenset({_K.POLICY})
    #: must-fix を残した判定（設計の台帳の FindingsEvaluated の DesignJudge の実行）
    execution: ExecutionId


@dataclass(frozen=True, kw_only=True)
class ResumeDesign(DesignCommand):
    """回答を待っていた設計を、回答を持って Revise から続ける（ResetDesignRounds を一般化したもの）。

    `kind` は答えたエスカレーションの種類（design-rounds-exhausted・design-reverted・
    design-ambiguous）で、Design が待っている原因と合わなければ拒む。
    """

    ISSUERS = frozenset({_K.POLICY})
    kind: EscalationKind
    answer: str


@dataclass(frozen=True, kw_only=True)
class SettleDesign(DesignCommand):
    """設計の台帳の FindingsEvaluated を受けて出す。Design は台帳を読めないので、open の指摘を載せる。"""

    ISSUERS = frozenset({_K.POLICY})
    #: 判定を締めた DesignJudge の実行
    execution: ExecutionId
    #: 判定した設計の版
    design: DesignVersion
    #: そのときの設計の台帳の open の指摘
    open_findings: tuple[FindingSummary, ...] = ()


@dataclass(frozen=True, kw_only=True)
class MarkReverted(DesignCommand):
    ISSUERS = frozenset({_K.POLICY})
    to_version: DesignVersion
    #: そう判定した DesignJudge の実行
    execution: ExecutionId


@dataclass(frozen=True, kw_only=True)
class MarkAmbiguous(DesignCommand):
    """DesignJudge が設計の受入条件を曖昧と判定した（DesignCause の ambiguous）。"""

    ISSUERS = frozenset({_K.POLICY})
    execution: ExecutionId


@dataclass(frozen=True, kw_only=True)
class DiscardProposal(DesignCommand):
    """確定していない提案を捨てる（ReplanRequested を受けて。ADDENDUM §3）。"""

    ISSUERS = frozenset({_K.POLICY})
    reason: str


# --- Stack ---


@dataclass(frozen=True, kw_only=True)
class StackCommand(Command):
    AGGREGATE: ClassVar[str] = "Stack"

    @property
    def target(self) -> StreamId:
        return StreamId.stack()

    @property
    def supervised_task(self) -> TaskId | None:
        """スタックを変えるのは git 管理タスクだけ（DOMAIN_MODEL §6.5）。"""
        return TaskId.git()


#: Stack のコマンドを出せる者。git 管理タスクのステージの結果は、ポリシーが StageCompleted から組む
_STACK_ISSUERS = frozenset({_K.POLICY, _K.TASK_SUPERVISOR})


@dataclass(frozen=True, kw_only=True)
class EnqueueStack(StackCommand):
    """フローを終えたタスク（TaskGated）・積む列に戻すタスク（TasksReturnedToQueue）を積む仕事を入れる。"""

    ISSUERS = frozenset({_K.POLICY})
    task: TaskId
    branch: BranchName


@dataclass(frozen=True, kw_only=True)
class EnqueueGitJob(StackCommand):
    """積む以外の git 管理タスクの仕事を列に入れる。相手は、きっかけのイベントから写す。

    概要ブランチを切る仕事（RunStarted）は、概要ブランチとランの base を持つ。Stack はそれを覚え、
    概要 PR を相手にする仕事に埋める。
    """

    ISSUERS = frozenset({_K.POLICY})
    kind: GitJobKind
    task: TaskId | None = None
    branch: BranchName | None = None
    base: BranchName | None = None
    discarded: frozenset[TaskId] = frozenset()
    ready_overview: bool = False


@dataclass(frozen=True, kw_only=True)
class TakeNextGitJob(StackCommand):
    """次の仕事を取り出す。処理中の仕事がある・取り出せる仕事が無いなら、何もしない。"""

    ISSUERS = _STACK_ISSUERS


@dataclass(frozen=True, kw_only=True)
class FinishGitJob(StackCommand):
    """処理していた仕事を終える（git 管理タスクの FlowFinished・FlowAbandoned を受けて）。

    取り下げ・統合の失敗で捨てたのか、それ以外で捨てたのか（列の先頭へ戻す）は、Stack が自分の記録と
    合わせて決める。ポリシーはフローの終わり方しか知らない。
    """

    ISSUERS = frozenset({_K.POLICY})
    #: 終えた仕事の番号（FlowFinished.job.id）。処理中の仕事でなければ何もしない
    job: int
    ending: FlowEnding = FlowEnding.FINISHED


@dataclass(frozen=True, kw_only=True)
class RecordConflict(StackCommand):
    ISSUERS = _STACK_ISSUERS
    task: TaskId
    files: tuple[str, ...]


@dataclass(frozen=True, kw_only=True)
class AppendEntry(StackCommand):
    """積んだ 1 本（StackLink の StageCompleted の job と result.pr から組む）。"""

    ISSUERS = _STACK_ISSUERS
    entry: StackEntry
    #: 結果を返したステージ（StackLink）の実行。受けたかを ResultReceived・ResultRefused で返す
    source: ExecutionId | None = None


@dataclass(frozen=True, kw_only=True)
class RejectRequest(StackCommand):
    """統合に失敗した（git 管理タスクの StageReported の integration-failed から組む）。衝突した
    ファイルを書かなければ、処理中の仕事に記録した衝突（RecordConflict）を使う。"""

    ISSUERS = _STACK_ISSUERS
    task: TaskId
    reason: str
    files: tuple[str, ...] = ()


@dataclass(frozen=True, kw_only=True)
class UnstackFrom(StackCommand):
    """破棄したタスクから上を閉じた（Relink の StageCompleted の job から組む）。"""

    ISSUERS = _STACK_ISSUERS
    #: 閉じた中で一番下（GitJob.cut_from）。閉じるものが無ければ None
    entry: StackEntry | None
    #: 破棄したタスク（GitJob.discarded）
    discarded: frozenset[TaskId]
    source: ExecutionId | None = None


@dataclass(frozen=True, kw_only=True)
class RecordOverview(StackCommand):
    """概要 PR を作った（CreateOverviewPR の StageCompleted の job と result.pr から組む）。
    `entry.task` は git 管理タスク。"""

    ISSUERS = _STACK_ISSUERS
    entry: StackEntry
    source: ExecutionId | None = None


@dataclass(frozen=True, kw_only=True)
class WithdrawRequest(StackCommand):
    """止めたタスクを相手にする仕事を、列から外す（TasksStopped を受けて。ADDENDUM §2）。"""

    ISSUERS = frozenset({_K.POLICY})
    task: TaskId


@dataclass(frozen=True, kw_only=True)
class RetryGitJob(StackCommand):
    """止めた仕事（戻した回数が上限に達した）を、ラン統括が続けると答えた。列の先頭へ戻し、数え直す。

    git 管理タスクの、上げ元の実行が無い stage-errors への回答（EscalationResolved）を受けたポリシーが、
    止めた仕事があるかを知らずに出す。無ければ何もしない。
    """

    ISSUERS = frozenset({_K.POLICY})


@dataclass(frozen=True, kw_only=True)
class DropGitJob(StackCommand):
    """止めた仕事を、ラン統括が答え以外の判断（差し込む・止める など）で閉じた。仕事をやめる。

    git 管理タスクの側の EscalationClosed を受けたポリシーが、止めた仕事があるかを知らずに出す。
    """

    ISSUERS = frozenset({_K.POLICY})


@dataclass(frozen=True, kw_only=True)
class RetryIntegration(StackCommand):
    """統合の失敗のエスカレーションに回答が届き、同じ仕事で統合をやり直す（失敗の印を下ろす）。

    git 管理タスクの integration-failed への回答を受けたポリシーが出す。印が無ければ何もしない。
    """

    ISSUERS = frozenset({_K.POLICY})


@dataclass(frozen=True, kw_only=True)
class PauseStacking(StackCommand):
    """再計画が始まった（ReplanRequested）。積む仕事を取り出すのを止める。もう止めていれば何もしない。"""

    ISSUERS = frozenset({_K.POLICY})


@dataclass(frozen=True, kw_only=True)
class ResumeStacking(StackCommand):
    """計画を反映した（TasksPlanned）。積む仕事をまた取り出す。止めていなければ何もしない。"""

    ISSUERS = frozenset({_K.POLICY})


# --- Questions ---


@dataclass(frozen=True, kw_only=True)
class QuestionsCommand(Command):
    AGGREGATE: ClassVar[str] = "Questions"

    @property
    def target(self) -> StreamId:
        return StreamId.questions()


@dataclass(frozen=True, kw_only=True)
class PostQuestion(QuestionsCommand):
    #: ポリシーが出すのは、ラン統括が応じなかったとき（ラン統括の 1 段上は /autodev）
    ISSUERS = frozenset({_K.RUN_SUPERVISOR, _K.POLICY})
    question: QuestionId
    body: str
    #: 経路。この質問を出すきっかけになった Run の側のエスカレーション
    escalation: EventId | None = None


@dataclass(frozen=True, kw_only=True)
class AnswerQuestion(QuestionsCommand):
    ISSUERS = frozenset({_K.CLI})
    question: QuestionId
    answer: str


@dataclass(frozen=True, kw_only=True)
class WithdrawQuestions(QuestionsCommand):
    """Run の側のエスカレーションが閉じたので、それを経路に持つ回答待ちの質問を取り下げる。"""

    ISSUERS = frozenset({_K.POLICY})
    escalation: EventId
    #: エスカレーションを閉じた理由（EscalationClosed.reason）
    reason: str


# --- 表 ---

#: 集約ごとに、handle に書くコマンド
COMMANDS_BY_AGGREGATE: Mapping[str, tuple[type[Command], ...]] = {
    "Run": (
        StartRun,
        StartTask,
        StartReadyTasks,
        ApplyPlan,
        ApplyReplan,
        RequestReplan,
        InsertTask,
        StopTasks,
        ReturnToQueue,
        RecordIntegrationFailure,
        ClearIntegrationFailure,
        MarkStacked,
        EscalateToRun,
        CloseRelayedEscalation,
        AnswerEscalation,
        RecordAnswer,
        Panic,
        ReportSupervisorFailure,
        FinishRun,
        UpdateTaskStatus,
        RecordSettledPlan,
        ResumeRun,
    ),
    "Task": (
        OpenTask,
        AcceptFlow,
        BeginStage,
        ReportBeginFailure,
        ReportStageResult,
        ConfirmHandoff,
        InterruptStage,
        MarkInterrupted,
        ResumeStage,
        ResumeInterrupted,
        AbandonFlow,
        Escalate,
        ResolveEscalation,
        CloseEscalation,
        ChangeScope,
        AddNote,
        RecordBase,
        ConcludeReviewRound,
        ConcludeGateRound,
        ConcludeDesignRound,
        StopTask,
    ),
    "ReviewLedger": (
        RecordFindings,
        RaiseFinding,
        CommentFinding,
        JudgeFinding,
        CountFix,
        RecordJudgement,
        RecordGateResult,
        TrackProposal,
        CarryFinding,
    ),
    "Design": (
        ProposeDesign,
        ReviseDesign,
        ResumeDesign,
        SettleDesign,
        MarkReverted,
        MarkAmbiguous,
        DiscardProposal,
    ),
    "Stack": (
        EnqueueStack,
        EnqueueGitJob,
        TakeNextGitJob,
        FinishGitJob,
        RecordConflict,
        AppendEntry,
        RejectRequest,
        UnstackFrom,
        RecordOverview,
        WithdrawRequest,
        PauseStacking,
        ResumeStacking,
        RetryGitJob,
        DropGitJob,
        RetryIntegration,
    ),
    "Questions": (PostQuestion, AnswerQuestion, WithdrawQuestions),
}

#: 名前 → クラス。`requests` の `type` の列に入る
COMMAND_TYPES: Mapping[str, type[Command]] = {
    cls.__name__: cls for classes in COMMANDS_BY_AGGREGATE.values() for cls in classes
}
