"""ドメインイベント（DOMAIN_MODEL §8.1・§8.3・ADDENDUM）。

イベントは起きたことで、過去形で名付ける。各イベントは形の版（`VERSION`）を持つ。形を変えたら
`VERSION` を上げ、古い版を新しい版に読み替えるアップキャスタを `UPCASTERS` に足す。古いランの
`events.db` も再生できるようにするためである。

**作り直しを最初に出す（配る）までは、版を上げない。** それまでに作られた `events.db` は検査の中の
ものだけで、読み替える古いランが無い。形を変えるときは、欄を足して既定値を持たせるか、そのまま
直す。今は読み替えるものが無い。

イベントストアへの保存は `to_record`、読み出しは `from_record` を通す。名前 → クラスの表は
`EVENT_TYPES`、どの集約がどのイベントを出すか（apply に書くイベント）は `EVENTS_BY_AGGREGATE`。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, ClassVar

from . import codec
from .flow import Cursor, Flow, FlowStep
from .stages import Handoff
from .values import (
    ArtifactKind,
    ArtifactRef,
    BranchName,
    CommitSha,
    Decision,
    DesignJudgement,
    DesignVersion,
    EscalationKind,
    EventId,
    ExecutionId,
    FindingId,
    FindingOrigin,
    FindingSummary,
    FindingTransfer,
    GateItemResult,
    GitJob,
    GitJobOutcome,
    Hint,
    Instruction,
    InterruptCause,
    Location,
    ParallelLimit,
    PlannedTask,
    Pointers,
    PrNumber,
    Proposal,
    QuestionId,
    Rating,
    Repository,
    RunName,
    SessionId,
    StackEntry,
    StageResult,
    StallCause,
    TaskId,
    TaskKind,
    TaskSpec,
    TaskStatus,
    VerifyCommand,
)


@dataclass(frozen=True)
class Event:
    """ドメインイベントの土台。"""

    #: 形の版。形を変えたら上げて、アップキャスタを足す
    VERSION: ClassVar[int] = 1


# --- Run（`run`） ---


@dataclass(frozen=True)
class RunStarted(Event):
    name: RunName
    instruction: Instruction
    repository: Repository
    base: BranchName
    limit: ParallelLimit


@dataclass(frozen=True)
class TaskStarted(Event):
    """タスクを始めた。そのときのラン共通の成果物（brief・codemap・design の版）を載せる（ADDENDUM §7）。"""

    task: TaskId
    kind: TaskKind
    #: 実装タスクだけが持つ
    spec: TaskSpec | None = None
    blocked_by: frozenset[TaskId] = frozenset()
    artifacts: tuple[ArtifactRef, ...] = ()
    #: 実装タスクのブランチ（`stack/<ラン名>--task-<番号>`）。名前の規約は Run が持つ
    branch: BranchName | None = None
    #: 一度始めて、積む列から外したタスクをもう一度始めた（範囲が変わった。TasksPlanned の
    #: withdraw）。ポリシーは OpenTask ではなく ChangeScope を出す
    reopened: bool = False
    #: 統合に失敗したタスクを引き継いだなら、解き直す衝突したファイル（ResolveConflict が書いてよい）
    conflicts: tuple[str, ...] = ()


@dataclass(frozen=True)
class TasksPlanned(Event):
    """確定した提案を反映した。"""

    design: DesignVersion
    #: 反映した後の、終端でない実装タスク（提案に書かれていないタスクは今の中身のまま）と、
    #: 提案に書かれた積み済みのタスク
    tasks: tuple[PlannedTask, ...]
    verify: tuple[VerifyCommand, ...]
    artifacts: tuple[ArtifactRef, ...]
    #: 再計画の反映か（初回なら概要 PR を作り、再計画なら本文を差し替える）
    replan: bool
    #: 再計画のきっかけになったエスカレーション（確定した提案を退けて頼み直したなら、前のきっかけも）。
    #: 一緒に出る EscalationClosed で閉じる
    triggers: tuple[EventId, ...] = ()
    #: 走っている・止まっているタスクのうち、範囲が変わったものと、きっかけのエスカレーションを
    #: 上げたタスク。ポリシーが ChangeScope を送る（ScopeChanged が待っているエスカレーションを閉じる）
    scope_changed: frozenset[TaskId] = frozenset()
    #: 積む列で待っていて範囲が変わったタスク。Run はまだ始めていない状態に戻し、TaskScheduler が
    #: 上限と依存を見て始め直す（TaskStarted の reopened）。ポリシーは積む頼みを外す（WithdrawRequest）
    withdraw: frozenset[TaskId] = frozenset()
    carry: tuple[FindingTransfer, ...] = ()
    #: 反映した後もまだ開いている Run のエスカレーション。ラン統括を起こし直す（ADDENDUM §3）
    still_open: tuple[EventId, ...] = ()


@dataclass(frozen=True)
class ReplanRequested(Event):
    reason: str
    trigger: EventId | None = None
    answer: QuestionId | None = None
    #: 設計が一度でも確定していたか。まだなら Replan ではなく Prepare → Plan からやり直す（ADDENDUM §3）
    settled_before: bool = True
    #: 反映したときに閉じる Run のエスカレーション。進んでいた再計画（確定した提案を退けた）の
    #: きっかけに、今度のきっかけを足したもの。計画タスクのエスカレーション（一緒に閉じる）は足さない
    closes_on_apply: tuple[EventId, ...] = ()


@dataclass(frozen=True)
class TaskInserted(Event):
    """ラン統括がタスクを差し込んだ。id は Run が使っていない番号から決める。"""

    task: TaskId
    spec: TaskSpec
    blocked_by: frozenset[TaskId] = frozenset()
    takes_over: TaskId | None = None
    #: 引き継ぎ元の統合で衝突したファイル（IntegrationFailureRecorded）。引き継ぎ先が解き直す
    conflicts: tuple[str, ...] = ()


@dataclass(frozen=True)
class TaskSuperseded(Event):
    task: TaskId
    by: TaskId


@dataclass(frozen=True)
class TasksStopped(Event):
    tasks: frozenset[TaskId]


@dataclass(frozen=True)
class TasksDiscarded(Event):
    tasks: frozenset[TaskId]
    #: 破棄した所より上に積んであって、破棄しないタスク（下から）。積む列に戻す（ReturnToQueue）
    requeue: tuple[TaskId, ...] = ()


@dataclass(frozen=True)
class TasksReturnedToQueue(Event):
    #: 積み直す順（下から）
    tasks: tuple[TaskId, ...]
    #: 積み直すときに切り直すブランチ（tasks と同じ順。ADDENDUM §10 の `-r<積み直した回数>`）
    branches: tuple[BranchName, ...] = ()


@dataclass(frozen=True)
class TaskMarkedStacked(Event):
    task: TaskId
    pr: PrNumber
    #: 破棄した所を閉じ終える前に積み終えたので、閉じる中に入り、閉じた後に積み直す
    requeue: bool = False


@dataclass(frozen=True)
class EscalationRaised(Event):
    """ループで解けない問題が起きた。Task と Run の両方が出す。

    このイベントの id（EventId）が、エスカレーションの id になる。
    """

    kind: EscalationKind
    pointers: Pointers
    hint: Hint = field(default_factory=Hint)
    #: 上げてきたタスク（Task が出すなら自分、Run が出すなら EscalateToRun を出した統括のタスク）
    task: TaskId | None = None
    #: どの実行で起きたか
    origin: ExecutionId | None = None
    #: Run が出すとき、上げる元になったタスクの側のエスカレーション（回答をそこへ渡す）
    source: EventId | None = None
    #: 上げる理由。Run が出すときは統括が書いた理由、Task が出すときは報告の中身（reportReason）
    #: や受け取る側が受けなかった理由。本文や出力そのものは載せない
    reason: str = ""
    #: ユーザーに聞くとよいこと（DesignJudge が書いた問い）。ラン統括が質問を出すときに使う
    question: str | None = None
    #: supervisor-failed なら、統括が応じられなかった知らせ（統括を起こしたイベント）。答えたら、
    #: その統括を新しいセッションで、この知らせで起こし直す
    failed_notice: EventId | None = None
    #: supervisor-failed なら、同じ知らせから起こし直しても続けて応じなかった数（最初の上げで 1）。
    #: 上限を超えたら、ラン統括はユーザーの回答を添えて答える（EscalationRouter.requires_user_answer）
    failures: int = 0
    #: 答え以外では閉じられない。ラン統括の答え以外の判断（insert-task・stop-tasks・apply-plan の
    #: respondsTo、replan の trigger）で閉じようとしたら Run が拒む。やめるとランが終わらない止めた
    #: git の仕事（`GitJobKind.can_drop` が偽）の上げで立てる
    answer_only: bool = False


@dataclass(frozen=True)
class TaskStatusChanged(Event):
    """Run が持つタスクの状態が変わった（ADDENDUM §2）。`cause` はきっかけのイベントの名前。"""

    task: TaskId
    from_status: TaskStatus
    to_status: TaskStatus
    cause: str


@dataclass(frozen=True)
class AllTasksSettled(Event):
    """最後の実装タスクが終端になった（積んだ・止めた・引き継がれた・破棄した）。ラン統括を仕上げに起こす。"""


@dataclass(frozen=True)
class SettledPlanRecorded(Event):
    """確定した提案を Run が受け取った。DiscardTasks の検査と ApplyPlan に使う（ADDENDUM §3）。"""

    proposal: Proposal
    #: ラン共通の成果物（brief・codemap・design）
    artifacts: tuple[ArtifactRef, ...] = ()
    #: 計画をすでに 1 回反映しているか。反映していなければポリシーが ApplyPlan を出し、
    #: していればラン統括を起こして apply-plan（ApplyReplan）を返させる
    replan: bool = False


@dataclass(frozen=True)
class AnswerRecorded(Event):
    """ユーザーの回答を Run が受け取った。回答を渡す・再計画の上限を外すのに、1 回だけ使える。"""

    question: QuestionId
    answer: str
    #: その質問を出したエスカレーション（Run の側）。回答はこのエスカレーションにだけ使える
    escalation: EventId | None = None
    #: ラン統括が応じられなかったので聞いた質問なら、その知らせ。ラン統括を新しいセッションで、
    #: この知らせで起こし直す（回答はそのエスカレーションを閉じる）
    failed_notice: EventId | None = None
    #: 届いたとき、質問を出したエスカレーションはもう回答以外で閉じていた（回答と stop-tasks・replan
    #: が前後した）。答える先が無いので、ラン統括を起こさない。起こすと answer を拒まれ続け、
    #: supervisor-failed から同じ知らせで起こし直す輪になる
    escalation_closed: bool = False


@dataclass(frozen=True)
class EscalationAnswered(Event):
    """ラン統括が Run のエスカレーションに答えた。回答は、上げてきたタスクへ下ろす（ポリシーが ResolveEscalation）。

    ユーザーの回答を渡したなら、本文は AnswerRecorded から写したもので、`question` を持つ。
    """

    escalation: EventId
    task: TaskId | None
    #: 回答を渡す先（タスクの側のエスカレーション）。無ければ、ポリシーは回答を AddNote で残す
    source: EventId | None
    answer: str
    question: QuestionId | None = None
    #: supervisor-failed への回答なら、タスク統括が応じられなかった知らせ。そのタスク統括を新しい
    #: セッションで、この知らせで起こし直す
    failed_notice: EventId | None = None


@dataclass(frozen=True)
class RunFinished(Event):
    ready_overview: bool


@dataclass(frozen=True)
class RunPanicked(Event):
    cause: str


@dataclass(frozen=True)
class RunResumed(Event):
    """既にあるラン名で呼び直された（パニックの後も、driver が落ちた後も。ADDENDUM §9）。

    ポリシーは `tasks` の各タスクに ResumeInterrupted を送り、どの実行を続きから再開するかは
    Task が止めた理由（InterruptCause）で決める。
    """

    #: 終端でないタスク（計画タスク・git 管理タスクを含む）
    tasks: tuple[TaskId, ...] = ()
    #: パニックの後か
    after_panic: bool = False


@dataclass(frozen=True)
class IntegrationFailureRecorded(Event):
    """積んでいる途中のタスクの統合が失敗した（Stack の IntegrationFailed を受けて）。

    衝突したファイルを覚え、そのタスクを引き継ぐタスクを差し込んだら（InsertTask の takes_over）、
    引き継ぎ先に渡す（TaskInserted.conflicts）。
    """

    task: TaskId
    reason: str
    files: tuple[str, ...] = ()


@dataclass(frozen=True)
class IntegrationFailureCleared(Event):
    """統合の失敗に回答が届き、同じ仕事で統合をやり直す。もう引き継げない（ClearIntegrationFailure）。"""

    task: TaskId


# --- Task（`task/<TaskId>`） ---


@dataclass(frozen=True)
class FlowAccepted(Event):
    flow: Flow
    #: 上がってきたエスカレーションへの応答なら、そのエスカレーション（閉じる）
    closes: EventId | None = None
    #: 最初の位置（合成ステージで始まるなら、1 ラウンド目の最初の中のステージ）。Task が決める
    cursor: Cursor = field(default_factory=Cursor)


@dataclass(frozen=True)
class FlowRejected(Event):
    steps: tuple[FlowStep, ...]
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class TaskOpened(Event):
    """Task のストリームの最初のイベント。種類・中身・そのときのラン共通の成果物を持つ。"""

    kind: TaskKind
    spec: TaskSpec | None = None
    artifacts: tuple[ArtifactRef, ...] = ()
    blocked_by: frozenset[TaskId] = frozenset()
    #: 実装タスクのブランチ。フローを終えたら、この名前で積む列に入る（TaskGated）
    branch: BranchName | None = None
    #: 統合に失敗したタスクを引き継いだなら、解き直す衝突したファイル。ResolveConflict が書いてよい
    #: ファイル（WriteScope.LISTED）で、成果物 conflicts の中身
    conflicts: tuple[str, ...] = ()


@dataclass(frozen=True)
class StageRequested(Event):
    """ステージを走らせると決めた。実行器がこれを受けて HEAD とセッション id を集め、BeginStage を出す。

    試行の回数は Task が決め、`execution` に載せる（実行器はこの id で BeginStage を出す）。
    """

    execution: ExecutionId
    #: Flow.steps の添字
    step: int


@dataclass(frozen=True)
class StageStarted(Event):
    execution: ExecutionId
    #: 始めた時点のコミット。再開に失敗したら、ここに戻して新しい実行を作る
    start_commit: CommitSha
    #: LLM のステージのセッション。決定的なステージには無い
    session: SessionId | None = None


@dataclass(frozen=True)
class ExecutionRestarted(Event):
    """再開に失敗した実行を捨てた（restarted。§9.2）。続きは新しい実行で、始めた時点のコミットから。"""

    execution: ExecutionId
    reason: str
    #: 戻す先（反応が worktree をここへ戻してから、新しい実行を始める）
    start_commit: CommitSha | None = None


@dataclass(frozen=True)
class StageCompleted(Event):
    """ステージが完了した。ポリシーは、ここに載った欄だけで、結果を受け取る側へのコマンドを組む。"""

    execution: ExecutionId
    produced: tuple[ArtifactRef, ...] = ()
    #: Rebase が衝突したファイル。空なら、続く「衝突したときだけ」のステージを飛ばす
    conflicts: tuple[str, ...] = ()
    #: 完了して外した成果物（StageSpec の consumes）
    removed: tuple[ArtifactKind, ...] = ()
    #: 進んだ先。None なら cursor は動かない（書き直す前のフローの実行・並列の見る役の途中・判定・
    #: 結果を渡した先が受けるのを待っている）。飛ばす段を飛ばした後の位置で、Task が決める
    cursor: Cursor | None = None
    #: 結果の中身（StageSpec.result の欄を読み替えたもの）。書き直す前のフローの実行には無い
    result: StageResult | None = None
    #: 結果をどこへどう渡すか（StageSpec.hands_to）。在れば、ConfirmHandoff（判定は Conclude*Round）が
    #: 届くまで cursor を進めない。ポリシーは、これで受け取る側へのコマンドを決める
    handoff: Handoff | None = None
    #: 設計の段で、このステージが見た提案の版（DesignReview・DesignJudge。RecordFindings・
    #: RecordJudgement の design）
    reviewed: DesignVersion | None = None
    #: 提案を Design へ渡すとき、ラン共通の成果物（brief・codemap）の在りか（ProposeDesign の artifacts）
    shared: tuple[ArtifactRef, ...] = ()
    #: git 管理タスクのフローなら、処理している仕事（相手のタスク・ブランチ・base・閉じる所）
    job: GitJob | None = None
    #: 結果を渡すとき、調べる先（受けられなかったら、上げるエスカレーションに添える）
    pointers: Pointers | None = None


@dataclass(frozen=True)
class HandoffConfirmed(Event):
    """結果を渡した先が受けた。cursor を進める（行き先は Task が決める）。"""

    execution: ExecutionId
    cursor: Cursor | None = None


@dataclass(frozen=True)
class HandoffFailed(Event):
    """結果を渡した先が受けなかった。実行は refused になり、一緒に出る EscalationRaised
    （result-refused）が解けたら、同じステージをもう一度走らせる。"""

    execution: ExecutionId
    reason: str


@dataclass(frozen=True)
class StageFailed(Event):
    execution: ExecutionId
    reason: str


@dataclass(frozen=True)
class StageInterrupted(Event):
    execution: ExecutionId
    #: 止めた理由。呼び直したときに再開するかは、これで決まる（InterruptCause.resumes_on_restart）
    cause: InterruptCause


@dataclass(frozen=True)
class StageCancelled(Event):
    """走らせると決めてまだ始めていない実行を、フローを捨てたのでやめた。以後 BeginStage を受けない。"""

    execution: ExecutionId
    reason: str


@dataclass(frozen=True)
class StageDeferred(Event):
    """計画ステージの ask が defer で止まった。"""

    execution: ExecutionId
    tool_use_id: str


@dataclass(frozen=True)
class StageReported(Event):
    """報告（designGap など）を返して終わった。cursor は進めない。"""

    execution: ExecutionId
    kind: EscalationKind
    #: 報告の中身（結果の reportReason・期待した証拠と食い違った所）。RejectRequest の理由になる
    reason: str = ""
    #: git 管理タスクのフローなら、処理している仕事（RejectRequest の相手のタスク）
    job: GitJob | None = None


@dataclass(frozen=True)
class GateFailed(Event):
    """Gate が不合格だった（ステージの失敗ではない）。cursor は直前の ReviewLoop の直す役（Fix）を指す。

    Fix はすぐには起動しない。G- の指摘を判定した台帳の結果（FindingsEvaluated）を受けた
    ポリシーの ConcludeGateRound で起動する（ADDENDUM §6）。
    """

    execution: ExecutionId
    failed: tuple[GateItemResult, ...]
    #: 戻った先（直前の ReviewLoop の、最後のラウンドの直す役）
    cursor: Cursor
    #: Gate の結果に添えた調べる先。G- の指摘が停滞したときの上げに載せる
    pointers: Pointers | None = None


@dataclass(frozen=True)
class WorktreeReady(Event):
    """git 管理タスクの CutBranch が worktree を切った。"""

    #: その worktree を使うタスク
    task: TaskId
    #: ランディレクトリからのパス（`trees/overview`・`trees/stack-top`・`trees/<TaskId>`）
    tree: str
    branch: BranchName | None = None
    #: 切った仕事。計画タスクの統括は、仕事の種類（概要ブランチ・stack-top）で組む並びを決める
    job: GitJob | None = None
    #: 切った元のコミット。ポリシーが、その worktree を使うタスクに RecordBase で渡す
    base: CommitSha | None = None


@dataclass(frozen=True)
class BranchRebased(Event):
    """git 管理タスクの Rebase が、タスクのブランチを載せ直した（積み直しで根元が動いた）。"""

    #: 載せ直したブランチのタスク
    task: TaskId
    #: 載せ直した先のコミット。ポリシーが、そのタスクに RecordBase で渡す
    onto: CommitSha
    job: GitJob | None = None


@dataclass(frozen=True)
class BaseRecorded(Event):
    """タスクのブランチの根元（差分の起点）と、そのタスクのために切った worktree を覚えた。"""

    base: CommitSha
    #: 切った worktree（ランディレクトリからのパス）。Rebase で根元だけが動いたなら None
    tree: str | None = None


@dataclass(frozen=True)
class RoundConcluded(Event):
    """合成ステージの 1 ラウンドの判定を受けた（ADDENDUM §4）。Gate の不合格の後の判定も含む。

    `finished` なら合成ステージを抜けて `produced` を作り、次の段へ進む。そうでなければ、
    同じラウンドの直す役（ReviewLoop は Fix、DesignLoop は Revise）へ進む。行き先は `cursor`。
    """

    step: int
    round: int
    finished: bool
    cursor: Cursor
    produced: tuple[ArtifactRef, ...] = ()
    #: 判定の実行（Judge・DesignJudge・Gate）。判定の結果を渡した先を待っていたなら、待ちを解く
    judge: ExecutionId | None = None


@dataclass(frozen=True)
class FlowFinished(Event):
    """フローの最後の段を終えた。種類を問わず出る（実装タスクは TaskGated も出す）。"""

    version: int
    #: git 管理タスクのフローなら、終えた仕事（FinishGitJob。仕上げなら git 管理タスクを終える）
    job: GitJob | None = None


@dataclass(frozen=True)
class FlowAbandoned(Event):
    """フローを続けずに捨てた（回答以外でエスカレーションを閉じた・AbandonFlow）。置き換えを待つ。

    git 管理タスクなら、処理していた仕事を終える（FinishGitJob）。
    """

    version: int
    reason: str
    job: GitJob | None = None


@dataclass(frozen=True)
class TaskGated(Event):
    """フローを終えた（gated と pr-body が揃った）。実装タスクだけが出す。"""

    branch: BranchName | None = None


@dataclass(frozen=True)
class TaskStopped(Event):
    """Run がタスクを止めた（TasksStopped）。以後、このタスクはステージを始めない。"""

    reason: str


@dataclass(frozen=True)
class EscalationResolved(Event):
    """回答が届いた。ポリシーは種類で続きを決める（design-* なら ResumeDesign、ask なら反応が回答の
    ファイルを書いてから ResumeStage）。"""

    escalation: EventId
    answer: str
    #: 答えたエスカレーションの種類
    kind: EscalationKind
    #: そのエスカレーションが起きた実行
    origin: ExecutionId | None = None
    #: ユーザーの回答を渡したなら、その質問
    question: QuestionId | None = None
    #: 回答を受けて続きから再開する実行（defer で止まった計画ステージの ask）。Task が決める
    resume: ExecutionId | None = None
    #: 再開する実行の止めた呼び出し。反応は `answers/<tool_use_id>.json` に回答を書いてから再開する
    tool_use_id: str | None = None


@dataclass(frozen=True)
class EscalationClosed(Event):
    """エスカレーションを閉じた。Task と Run の両方が出す。

    Task では回答以外で片付いたときで、defer で止まっていた実行は abandoned になり、そのフローは
    置き換えを待つ。Run では、ラン統括の判断（answer・insert-task・stop-tasks・apply-plan）や
    再計画を処理したとき（ADDENDUM §8）。
    """

    escalation: EventId
    reason: str
    #: Run が出すとき、上げてきたタスクと、そのタスクの側のエスカレーション。`source` があれば
    #: ポリシーはタスクの側も CloseEscalation で閉じる。答えたとき（EscalationAnswered）は回答を
    #: 下ろすので載せない
    task: TaskId | None = None
    source: EventId | None = None
    #: Task が出すとき、閉じたエスカレーションの種類と、起きた実行（EscalationRaised のもの）。
    #: ポリシーが、どの上げを答え以外で閉じたかを見分ける（止めた git の仕事をやめる など）
    kind: EscalationKind | None = None
    origin: ExecutionId | None = None


@dataclass(frozen=True)
class ScopeChanged(Event):
    spec: TaskSpec
    #: そのときのラン共通の成果物（design の版など）
    artifacts: tuple[ArtifactRef, ...]
    pointers: Pointers
    #: 積む列から外して始め直したタスクの、新しいブランチ（使ったブランチの名前は使い回さない）
    branch: BranchName | None = None


@dataclass(frozen=True)
class NoteAdded(Event):
    decision: Decision


# --- ReviewLedger（`review/<TaskId>`・`review/design`） ---


@dataclass(frozen=True)
class FindingRaised(Event):
    finding: FindingId
    rating: Rating
    body: str
    location: Location | None = None
    carried_from: FindingOrigin | None = None
    #: 立てたステージの実行
    source: ExecutionId | None = None
    #: 設計の台帳の指摘だけが持つ。立てたときにレビューした設計の版
    design: DesignVersion | None = None


@dataclass(frozen=True)
class FindingCommented(Event):
    finding: FindingId
    body: str
    author: ExecutionId | None = None


@dataclass(frozen=True)
class FindingClosed(Event):
    finding: FindingId
    comment: str
    execution: ExecutionId


@dataclass(frozen=True)
class FindingRejected(Event):
    finding: FindingId
    comment: str
    execution: ExecutionId


@dataclass(frozen=True)
class FindingReopened(Event):
    finding: FindingId
    comment: str
    #: 開き直した判定の実行（レビューの指摘は Judge・DesignJudge、Gate の項目の指摘は Gate）
    execution: ExecutionId | None = None


@dataclass(frozen=True)
class FixCounted(Event):
    """修正を 1 回受けた。そのとき open だった指摘の、修正を受けた回数が 1 増える。"""

    execution: ExecutionId
    findings: tuple[FindingId, ...]


@dataclass(frozen=True)
class FindingStalled(Event):
    """判定の後も、修正を STALL_AFTER_FIXES 回以上受けたまま open に残った（ADDENDUM §5）。

    判定を締めたとき（EvaluateStall・RecordGateResult）に、条件を満たす指摘ごとに 1 つ出る。
    **ただの記録で、これを受けてエスカレーションを出さない。** 停滞のエスカレーションは、同じ
    コマンドの最後に出る FindingsEvaluated の `stalled` を受けて 1 回だけ上げる（ADDENDUM §4）。
    台帳は `fixes` を覚え、そこから STALL_AFTER_FIXES 回の修正を受けるまで、同じ指摘を停滞に
    しない（回答の後にすぐまた上げない）。
    """

    finding: FindingId
    fixes: int


@dataclass(frozen=True)
class FindingsEvaluated(Event):
    """判定を締めた。その判定する者（Judge・DesignJudge・Gate）が判定する指摘のうち、open のものと、
    停滞したものを持つ。

    ポリシーは台帳を読めないので、ReviewLoop の次の手（抜ける・Fix・stall で上げる）、DesignLoop の
    次の手（確定する・Revise・前の版に戻った・曖昧）、Gate が落ちた後の手（Fix・stall で上げる）を、
    このイベントだけで決められるようにする。停滞が無くても必ず出る。設計の台帳では、今の提案に付いた
    指摘だけを数える。ジャッジの分類（停滞の原因・設計の分類）は、判定と一緒に届いたものを写す。
    """

    execution: ExecutionId
    open_findings: tuple[FindingSummary, ...]
    stalled: tuple[FindingId, ...] = ()
    #: Judge が停滞に付けた分類（ConcludeReviewRound の cause）
    stall_cause: StallCause | None = None
    #: 設計の台帳: DesignJudge が判定した提案の版（SettleDesign の design）
    design: DesignVersion | None = None
    #: 設計の台帳: DesignJudge の設計の分類（MarkReverted・MarkAmbiguous）
    design_cause: DesignJudgement | None = None


@dataclass(frozen=True)
class CommentRefused(Event):
    """ステージの結果のコメントが、台帳に無い指摘を指していた。状態は変えない（記録だけ）。

    コメントは受け渡しを待たないので（結果を待つ Task がいない）、拒否にせず、受けなかったことを残す。
    """

    finding: FindingId
    reason: str
    author: ExecutionId | None = None


@dataclass(frozen=True)
class CarryRefused(Event):
    """再計画の提案が移すとした指摘を、移せなかった（無い・open でない・もう移した・移す先が違う）。

    提案を反映した後に分かる中身の問題なので、拒否にせず、移さなかったことを残す。状態は変えない。
    """

    finding: FindingId
    to_task: TaskId
    reason: str


@dataclass(frozen=True)
class ResultReceived(Event):
    """ステージの結果を受けた（Design・ReviewLedger・Stack が出す）。ポリシーが ConfirmHandoff を出す。"""

    #: 結果を返したステージの実行
    source: ExecutionId


@dataclass(frozen=True)
class ResultRefused(Event):
    """ステージの結果を受けられなかった（Design・ReviewLedger・Stack が出す）。中身の問題（使った版・
    確定していない提案がある・無い指摘を判定した など）は、拒否ではなくこれで出す。ポリシーが
    ConfirmHandoff（refused）を出し、Task が result-refused で上げる。状態は変えない。"""

    source: ExecutionId
    reason: str


@dataclass(frozen=True)
class ProposalTracked(Event):
    """設計の台帳が、数える指摘を `design` の版から後に絞った（新しい提案が入った）。"""

    design: DesignVersion


@dataclass(frozen=True)
class FindingCarried(Event):
    """移す元を carried にした。移した先に立てるポリシーが要るので、指摘の中身を載せる。"""

    finding: FindingId
    to_task: TaskId
    rating: Rating
    body: str
    location: Location | None = None


# --- Design（`design`） ---


@dataclass(frozen=True)
class DesignProposed(Event):
    proposal: Proposal
    #: 提案を出した計画タスクが持っていた、ラン共通の成果物（brief・codemap）の在りか
    artifacts: tuple[ArtifactRef, ...] = ()


@dataclass(frozen=True)
class DesignRevised(Event):
    """直した提案の新しい版が入った（ADDENDUM §11: Revise の結果は ProposeDesign で入れる）。"""

    proposal: Proposal
    artifacts: tuple[ArtifactRef, ...] = ()


@dataclass(frozen=True)
class DesignRevisionStarted(Event):
    """Revise を起動する前に、ラウンドを 1 つ使った（ADDENDUM §11）。"""

    round: int
    #: 回答を待っていた所から続けるなら、その回答。Revise の入力に足す
    answer: str | None = None
    #: 直すと決めた判定（DesignJudge の実行）。Task が今のラウンドの判定かを確かめる
    execution: ExecutionId | None = None


@dataclass(frozen=True)
class DesignSettled(Event):
    """提案を確定した。

    `appendix` は確定した時点で open だった must-fix 以外の指摘で、反応が設計ファイル
    （`proposal.design` の版）の末尾に書き足し、ポリシーが `execution` の判定として rejected にする
    （DOMAIN_MODEL §6.4・LEDGER CT-17）。
    """

    proposal: Proposal
    #: 確定を決めた判定（DesignJudge の実行）
    execution: ExecutionId
    appendix: tuple[FindingSummary, ...] = ()
    #: ラン共通の成果物（brief・codemap と、確定した design の版）。RecordSettledPlan の artifacts
    artifacts: tuple[ArtifactRef, ...] = ()


@dataclass(frozen=True)
class DesignReverted(Event):
    """DesignJudge が、今の提案は前の版の形に戻ったと判定した。回答が来るまで直さない。"""

    to_version: DesignVersion
    #: そう判定した DesignJudge の実行
    execution: ExecutionId | None = None


@dataclass(frozen=True)
class DesignAmbiguous(Event):
    """DesignJudge が、設計の受入条件が曖昧だと判定した。回答が来るまで直さない。"""

    execution: ExecutionId


@dataclass(frozen=True)
class DesignRoundsExhausted(Event):
    rounds: int
    #: must-fix を残した判定（DesignJudge の実行）
    execution: ExecutionId | None = None


@dataclass(frozen=True)
class DesignRoundsReset(Event):
    answer: str


@dataclass(frozen=True)
class DesignProposalAbandoned(Event):
    """確定していない提案を捨てた（再計画を頼まれた。ADDENDUM §3）。"""

    design: DesignVersion | None
    reason: str


# --- Stack（`stack`） ---


@dataclass(frozen=True)
class GitJobQueued(Event):
    """git 管理タスクの仕事を列に入れた。ポリシーが TakeNextGitJob を出す。"""

    job: GitJob


@dataclass(frozen=True)
class GitJobTaken(Event):
    """列から仕事を取り出した（base と閉じる所は、取り出したときに埋めてある）。

    git 管理タスクの統括（プログラム）が、この仕事の決まった並び（`flow.git_job_flow`）を組む。
    積む仕事なら、ポリシーが相手のタスクを stacking にする。
    """

    job: GitJob


@dataclass(frozen=True)
class GitJobFinished(Event):
    """処理していた仕事を終えた（git 管理タスクのフローが終わった・捨てられた）。次を取り出せる。

    `outcome.returns_to_queue` なら、仕事を列の先頭へ戻した（同じ番号のまま、次に取り出す）。
    """

    job: GitJob
    outcome: GitJobOutcome = GitJobOutcome.DONE


@dataclass(frozen=True)
class GitJobWithdrawn(Event):
    """タスクを止めたので、そのタスクを相手にする仕事を列から外した（ADDENDUM §2）。

    `in_progress` なら処理中の仕事で、ポリシーが git 管理タスクのフローを捨てさせる（AbandonFlow）。
    その後に届く、この仕事への AppendEntry などは受けない（ResultRefused）。
    """

    job: GitJob
    in_progress: bool


@dataclass(frozen=True)
class RebaseConflicted(Event):
    task: TaskId
    files: tuple[str, ...]


@dataclass(frozen=True)
class TaskStacked(Event):
    entry: StackEntry


@dataclass(frozen=True)
class IntegrationFailed(Event):
    task: TaskId
    reason: str
    files: tuple[str, ...] = ()


@dataclass(frozen=True)
class StackCutBack(Event):
    """破棄したタスクから上を閉じた。`removed` は閉じた中に入っていたタスク（下から）。

    積む列に戻すタスクは Run が決めて持っている（TasksDiscarded.requeue）。ポリシーは ReturnToQueue を
    出すだけで、一覧を写さない。閉じるものが無かった（前の破棄で閉じてあった）なら `from_entry` は None。
    """

    from_entry: StackEntry | None
    removed: tuple[TaskId, ...] = ()


@dataclass(frozen=True)
class OverviewRecorded(Event):
    """概要 PR を作った。スタックの一番下で、その base はランの base。"""

    entry: StackEntry


@dataclass(frozen=True)
class GitJobRetried(Event):
    """止めた仕事を、列の先頭へ戻した（ラン統括が続けると答えた）。戻した回数は数え直す。"""

    job: GitJob


@dataclass(frozen=True)
class GitJobDropped(Event):
    """止めた仕事をやめた（ラン統括が答え以外の判断で閉じた）。後ろの仕事を取り出せる。"""

    job: GitJob


@dataclass(frozen=True)
class IntegrationRetried(Event):
    """統合の失敗に回答が届き、同じ仕事で統合をやり直す。失敗の印を下ろした。"""

    task: TaskId


@dataclass(frozen=True)
class StackingPaused(Event):
    """再計画が始まったので、積む仕事（積む・積み直す）を取り出すのを止めた。"""


@dataclass(frozen=True)
class StackingResumed(Event):
    """再計画を反映したので、積む仕事をまた取り出す。ポリシーが TakeNextGitJob を出す。

    取り出しを TasksPlanned ではなくこのイベントで頼むのは、範囲が変わった積む仕事を列から外す
    （TasksPlanned の withdraw → WithdrawRequest）のが、取り出すより先になるようにするためである。
    メインループは seq の順に配るので、TasksPlanned を受けた受け手がみな処理を終えてから、この
    イベントが配られる。
    """


# --- Questions（`questions`） ---


@dataclass(frozen=True)
class QuestionPosted(Event):
    question: QuestionId
    body: str
    #: 経路。この質問を出すきっかけになった Run の側のエスカレーション
    escalation: EventId | None = None


@dataclass(frozen=True)
class QuestionAnswered(Event):
    question: QuestionId
    answer: str
    escalation: EventId | None = None


@dataclass(frozen=True)
class QuestionWithdrawn(Event):
    """回答を待っていた質問を、経路のエスカレーションが回答以外で閉じたので取り下げた。"""

    question: QuestionId
    escalation: EventId
    #: エスカレーションを閉じた理由
    reason: str


# --- 表 ---

#: 集約ごとに、apply に書くイベント（DOMAIN_MODEL §8.3 に ADDENDUM の追加を足したもの）
EVENTS_BY_AGGREGATE: Mapping[str, tuple[type[Event], ...]] = {
    "Run": (
        RunStarted,
        TaskStarted,
        TasksPlanned,
        ReplanRequested,
        TaskInserted,
        TaskSuperseded,
        TasksStopped,
        TasksDiscarded,
        TasksReturnedToQueue,
        TaskMarkedStacked,
        EscalationRaised,
        EscalationClosed,
        EscalationAnswered,
        AnswerRecorded,
        TaskStatusChanged,
        AllTasksSettled,
        SettledPlanRecorded,
        IntegrationFailureRecorded,
        IntegrationFailureCleared,
        RunFinished,
        RunPanicked,
        RunResumed,
    ),
    "Task": (
        TaskOpened,
        FlowAccepted,
        FlowRejected,
        StageRequested,
        StageStarted,
        ExecutionRestarted,
        StageCompleted,
        HandoffConfirmed,
        HandoffFailed,
        StageFailed,
        StageInterrupted,
        StageCancelled,
        StageDeferred,
        StageReported,
        GateFailed,
        RoundConcluded,
        WorktreeReady,
        BranchRebased,
        BaseRecorded,
        FlowFinished,
        FlowAbandoned,
        TaskGated,
        TaskStopped,
        EscalationRaised,
        EscalationResolved,
        EscalationClosed,
        ScopeChanged,
        NoteAdded,
    ),
    "ReviewLedger": (
        FindingRaised,
        FindingCommented,
        FindingClosed,
        FindingRejected,
        FindingReopened,
        FixCounted,
        FindingStalled,
        FindingsEvaluated,
        FindingCarried,
        ProposalTracked,
        CommentRefused,
        CarryRefused,
        ResultReceived,
        ResultRefused,
    ),
    "Design": (
        DesignProposed,
        DesignRevised,
        DesignRevisionStarted,
        DesignSettled,
        DesignReverted,
        DesignAmbiguous,
        DesignRoundsExhausted,
        DesignRoundsReset,
        DesignProposalAbandoned,
        ResultReceived,
        ResultRefused,
    ),
    "Stack": (
        GitJobQueued,
        GitJobTaken,
        GitJobFinished,
        GitJobWithdrawn,
        RebaseConflicted,
        TaskStacked,
        IntegrationFailed,
        StackCutBack,
        OverviewRecorded,
        StackingPaused,
        StackingResumed,
        GitJobRetried,
        GitJobDropped,
        IntegrationRetried,
        ResultReceived,
        ResultRefused,
    ),
    "Questions": (QuestionPosted, QuestionAnswered, QuestionWithdrawn),
}

#: 名前 → クラス。名前はクラス名で、イベントストアの `type` の列に入る
EVENT_TYPES: Mapping[str, type[Event]] = {
    cls.__name__: cls for classes in EVENTS_BY_AGGREGATE.values() for cls in classes
}


# --- 保存と読み出し ---


@dataclass(frozen=True)
class EventRecord:
    """イベントストアの 1 行のうち、イベントの中身にあたる列（`type`・`v`・`data`）。"""

    type: str
    v: int
    data: dict[str, Any]


#: 古い版の中身を、新しい形に読み替える処理。名前を変えることもあるので、(名前, 版, 中身) を返す
Upcaster = Callable[[dict[str, Any]], tuple[str, int, dict[str, Any]]]

#: (イベントの名前, 版) → 読み替える処理。今は読み替えるものが無い
UPCASTERS: Mapping[tuple[str, int], Upcaster] = {}


class UnknownEventType(ValueError):
    """イベントストアに、この driver が知らないイベントがある。"""


def to_record(event: Event) -> EventRecord:
    name = type(event).__name__
    if EVENT_TYPES.get(name) is not type(event):
        raise UnknownEventType(f"表に無いイベント: {name}")
    return EventRecord(type=name, v=type(event).VERSION, data=codec.to_json(event))


def from_record(
    record: EventRecord, upcasters: Mapping[tuple[str, int], Upcaster] = UPCASTERS
) -> Event:
    """古い版はアップキャスタで今の版に読み替えてから読む。今の版より新しい版は読めない。"""
    name, v, data = record.type, record.v, record.data
    seen: set[tuple[str, int]] = set()
    while True:
        cls = EVENT_TYPES.get(name)
        if cls is not None and v == cls.VERSION:
            return codec.from_json(cls, data)
        if cls is not None and v > cls.VERSION:
            raise UnknownEventType(
                f"{name} の版 {v} はこの driver より新しい（{cls.VERSION} まで）"
            )
        upcaster = upcasters.get((name, v))
        if upcaster is None:
            raise UnknownEventType(f"{name} の版 {v} を読み替える処理が無い")
        # 読み替えが輪になっていたら、いつまでも終わらない
        seen.add((name, v))
        name, v, data = upcaster(data)
        if (name, v) in seen:
            raise UnknownEventType(f"{name} の版 {v} への読み替えが輪になっている")
