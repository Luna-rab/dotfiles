"""ポリシー: 集約どうしのつなぎ目（イベント → 次のコマンドの一覧）。

ポリシーは、受けたイベントとその id（どのストリームの何番目か）だけからコマンドを返す純粋な関数で、
集約の状態を読まない。状態で決まることは、受けた集約が `handle` で決める（取り出せる仕事が無ければ
何もしない、など）。正本は `test/autodev/test_seams.py` の `SEAMS` で、ここはその表を行ごとに書き
写したもの。行の名前（`Policy.name`）は表の名前と同じで、受け手の名前・チェックポイントの鍵・出す
コマンドの id（`CommandId.derived`）に入る。名前を変えると、変える前のイベントに反応しなくなる。

出す者（`By`）は 3 つある:

- `POLICY`: ポリシー
- `SUPERVISOR`: 計画タスクと git 管理タスクの統括（プログラム）。決まった並びを組むだけなので、
  ポリシーと同じ形で書く。出すコマンド（`AcceptFlow`・`EscalateToRun`）はタスクの統括にしか出せない
- `REACTION`: 反応の続き。反応が副作用を済ませてから、ここで組んだコマンドを返す（`FOLLOW_UPS`）

アプリケーション層は `RECEIVERS`（名前 → 受ける関数）を、この順に受け手として登録するだけである。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any

from .commands.base import Command
from .commands.design import (
    DiscardProposal,
    MarkAmbiguous,
    MarkReverted,
    ProposeDesign,
    ResumeDesign,
    ReviseDesign,
    SettleDesign,
)
from .commands.questions import PostQuestion, WithdrawQuestions
from .commands.review_ledger import (
    CarryFinding,
    CommentFinding,
    CountFix,
    JudgeFinding,
    RaiseFinding,
    RecordFindings,
    RecordGateResult,
    RecordJudgement,
    TrackProposal,
)
from .commands.run import (
    ApplyPlan,
    ClearIntegrationFailure,
    CloseRelayedEscalation,
    EscalateToRun,
    MarkStacked,
    RecordAnswer,
    RecordIntegrationFailure,
    RecordSettledPlan,
    ReturnToQueue,
    StartReadyTasks,
    StartTask,
    UpdateTaskStatus,
)
from .commands.stack import (
    AppendEntry,
    DropGitJob,
    EnqueueGitJob,
    EnqueueStack,
    FinishGitJob,
    PauseStacking,
    RecordConflict,
    RecordOverview,
    RejectRequest,
    ResumeStacking,
    RetryGitJob,
    RetryIntegration,
    TakeNextGitJob,
    UnstackFrom,
    WithdrawRequest,
)
from .commands.task import (
    AbandonFlow,
    AcceptFlow,
    AddNote,
    ChangeScope,
    CloseEscalation,
    ConcludeDesignRound,
    ConcludeGateRound,
    ConcludeReviewRound,
    ConfirmHandoff,
    Escalate,
    OpenTask,
    RecordBase,
    ResolveEscalation,
    ResumeInterrupted,
    ResumeStage,
    StopTask,
)
from .design import DESIGN_WAITS
from .events.base import Event
from .events.design import (
    DesignAmbiguous,
    DesignProposed,
    DesignReverted,
    DesignRevisionStarted,
    DesignRoundsExhausted,
    DesignSettled,
)
from .events.questions import QuestionAnswered
from .events.review_ledger import FindingCarried, FindingsEvaluated, ResultReceived, ResultRefused
from .events.run import (
    EscalationAnswered,
    EscalationRaised,
    ReplanRequested,
    RunFinished,
    RunResumed,
    RunStarted,
    SettledPlanRecorded,
    TaskInserted,
    TaskMarkedStacked,
    TasksDiscarded,
    TasksPlanned,
    TasksReturnedToQueue,
    TasksStopped,
    TaskStarted,
    TaskStatusChanged,
)
from .events.stack import (
    GitJobDropped,
    GitJobFinished,
    GitJobQueued,
    GitJobRetried,
    GitJobTaken,
    GitJobWithdrawn,
    IntegrationFailed,
    IntegrationRetried,
    StackCutBack,
    StackingResumed,
    TaskStacked,
)
from .events.task import (
    BranchRebased,
    EscalationClosed,
    EscalationResolved,
    FlowAbandoned,
    FlowAccepted,
    FlowFinished,
    GateFailed,
    ScopeChanged,
    StageCompleted,
    StageReported,
    TaskGated,
    WorktreeReady,
)
from .flow import git_job_flow, planning_flow
from .review import JudgeCapability
from .services.escalation_router import EscalationRouter, SupervisorLevel, task_of_stream
from .services.task_scheduler import TaskScheduler
from .stages import Handoff
from .value_objects.branch_name import BranchName
from .value_objects.command_id import CommandId
from .value_objects.decision import Decision
from .value_objects.decision_origin import DecisionOrigin
from .value_objects.design_cause import DesignCause
from .value_objects.escalation_kind import EscalationKind
from .value_objects.event_id import EventId
from .value_objects.execution_id import ExecutionId
from .value_objects.finding_origin import FindingOrigin
from .value_objects.finding_status import FindingStatus
from .value_objects.flow_ending import FlowEnding
from .value_objects.git_job_kind import GitJobKind
from .value_objects.git_job_outcome import GitJobOutcome
from .value_objects.hint import Hint
from .value_objects.issuer import Issuer
from .value_objects.limits import MAX_JOB_RETURNS
from .value_objects.pointers import Pointers
from .value_objects.question_id import QuestionId
from .value_objects.rating import Rating
from .value_objects.stack_entry import StackEntry
from .value_objects.stage_kind import StageKind
from .value_objects.stream_id import StreamId
from .value_objects.task_id import TaskId
from .value_objects.task_kind import TaskKind
from .value_objects.task_status import TaskStatus

_S = StageKind
_J = GitJobKind
_E = EscalationKind
_PLANNING = TaskId.planning()
_GIT = TaskId.git()


class By(Enum):
    """コマンドを出す者。"""

    POLICY = "policy"
    #: 計画タスクと git 管理タスクの統括（プログラム）
    SUPERVISOR = "supervisor"
    #: 反応が副作用を済ませた後の続き
    REACTION = "reaction"


@dataclass
class Stamp:
    """1 つのイベントから 1 つの行が出すコマンドに、出す順に id と出した者を振る。

    id は受けたイベントの id と行の名前と順番で決まるので、配り直しで同じコマンドがもう一度出ても
    同じ id になり、2 回目は何もしない（メインループと集約の土台が弾く）::

        return [StartTask(**stamp(), task=planning), StartTask(**stamp(), task=git)]
    """

    name: str
    source: EventId
    by: By
    _count: int = field(default=0, init=False)

    def __call__(self, supervisor: TaskId | None = None) -> dict[str, Any]:
        """次のコマンドの `command_id` と `issuer`。統括（プログラム）は名乗るタスクを渡す。"""
        command_id = CommandId.derived(self.source, self.name, self._count)
        self._count += 1
        if self.by is By.SUPERVISOR:
            if supervisor is None:
                raise ValueError(f"{self.name}: 統括が出すコマンドには、名乗るタスクが要る")
            issuer = Issuer.task_supervisor(supervisor)
        elif self.by is By.REACTION:
            issuer = Issuer.reaction(self.name, self.source)
        else:
            issuer = Issuer.policy(self.name, self.source)
        return {"command_id": command_id, "issuer": issuer}


#: 1 行の中身。受けたイベント・その id・id を振る道具から、出すコマンドの一覧を返す
Rule = Callable[[Any, EventId, Stamp], list[Command]]


@dataclass(frozen=True)
class Policy:
    """表の 1 行。"""

    name: str
    on: tuple[type[Event], ...]
    rule: Rule
    by: By = By.POLICY

    def receive(self, event: Event, source: EventId) -> list[Command]:
        """受けたイベントから、処理してほしいコマンドの一覧（受けないイベントなら空）。"""
        if not isinstance(event, self.on):
            return []
        return self.rule(event, source, Stamp(self.name, source, self.by))


def _task_of(source: EventId) -> TaskId:
    """イベントを出したタスク（task/<TaskId> のストリーム）。"""
    return task_of_stream(source.stream)


def _from_task(source: EventId) -> bool:
    return source.stream.is_task


def _from_git(source: EventId) -> bool:
    """git 管理タスクのストリームのイベントか。"""
    return source.stream == StreamId.task(_GIT)


def _ledger_of(task: TaskId) -> StreamId:
    return JudgeCapability.ledger_of(task)


# =====================================================================================
# Run のイベント
# =====================================================================================


def start_planning_and_git(e: RunStarted, src: EventId, stamp: Stamp) -> list[Command]:
    """計画タスクと git 管理タスクを始め、最初の仕事（概要ブランチを切る）を列に入れる。"""
    return [
        StartTask(**stamp(), task=_PLANNING),
        StartTask(**stamp(), task=_GIT),
        EnqueueGitJob(
            **stamp(),
            kind=_J.CUT_OVERVIEW,
            task=_PLANNING,
            branch=BranchName.overview(e.name),
            base=e.base,
        ),
    ]


def open_task(e: TaskStarted, src: EventId, stamp: Stamp) -> list[Command]:
    if e.reopened:
        return []
    return [
        OpenTask(
            **stamp(),
            task=e.task,
            kind=e.kind,
            spec=e.spec,
            artifacts=e.artifacts,
            blocked_by=e.blocked_by,
            branch=e.branch,
            conflicts=e.conflicts,
        )
    ]


def rescope_reopened(e: TaskStarted, src: EventId, stamp: Stamp) -> list[Command]:
    """積む列から外して始め直したタスクには、新しいブランチで範囲を変えたと知らせる。"""
    if not e.reopened:
        return []
    assert e.spec is not None, "実装タスクの TaskStarted は中身を持つ"
    return [
        ChangeScope(
            **stamp(),
            task=e.task,
            spec=e.spec,
            artifacts=e.artifacts,
            pointers=Pointers(),
            branch=e.branch,
        )
    ]


def cut_task_branch(e: TaskStarted, src: EventId, stamp: Stamp) -> list[Command]:
    if e.kind is not TaskKind.IMPLEMENTATION:
        return []
    return [EnqueueGitJob(**stamp(), kind=_J.CUT_TASK, task=e.task, branch=e.branch)]


def apply_first_plan(e: SettledPlanRecorded, src: EventId, stamp: Stamp) -> list[Command]:
    # 再計画（replan）なら、反応がラン統括を起こして apply-plan を返させる
    return [] if e.replan else [ApplyPlan(**stamp(), design=e.proposal.design)]


def start_ready(e: Event, src: EventId, stamp: Stamp) -> list[Command]:
    """始められるタスクが増えうるイベントで頼む。どれを始めるかは、Run が TaskScheduler に聞いて
    決める。増えうるのは、計画を反映した・依存先を積んだ・差し込んだ・止めた（待つ
    相手が減り、枠が空く）・呼び直された・並列の枠が空いた、のとき。"""
    return [StartReadyTasks(**stamp())]


def start_ready_after_slot_freed(e: TaskStatusChanged, src: EventId, stamp: Stamp) -> list[Command]:
    """並列の枠が空いた（running → gated など）。

    TaskGated ではなく、Run が状態を動かした TaskStatusChanged で頼む。TaskGated を受けた行どうしの
    処理の順によらず、Run が gated を当てた後に頼むためである。
    """
    if not TaskScheduler.frees_slot(e.from_status, e.to_status):
        return []
    return [StartReadyTasks(**stamp())]


def write_overview(e: TasksPlanned, src: EventId, stamp: Stamp) -> list[Command]:
    return [EnqueueGitJob(**stamp(), kind=_J.REWRITE_OVERVIEW if e.replan else _J.OPEN_OVERVIEW)]


def change_scope(e: TasksPlanned, src: EventId, stamp: Stamp) -> list[Command]:
    specs = {task.id: task.spec for task in e.tasks}
    return [
        ChangeScope(
            **stamp(), task=task, spec=specs[task], artifacts=e.artifacts, pointers=Pointers()
        )
        for task in sorted(e.scope_changed, key=str)
    ]


def withdraw_rescoped(e: TasksPlanned, src: EventId, stamp: Stamp) -> list[Command]:
    return [WithdrawRequest(**stamp(), task=task) for task in sorted(e.withdraw, key=str)]


def carry_findings(e: TasksPlanned, src: EventId, stamp: Stamp) -> list[Command]:
    return [
        CarryFinding(
            **stamp(), ledger=StreamId.review(c.from_task), finding=c.finding, to_task=c.to_task
        )
        for c in e.carry
    ]


def resume_stacking(e: TasksPlanned, src: EventId, stamp: Stamp) -> list[Command]:
    """再計画を反映したら、積む仕事をまた取り出す。取り出しは StackingResumed で頼む（take-next）。"""
    return [ResumeStacking(**stamp())]


def discard_proposal(e: ReplanRequested, src: EventId, stamp: Stamp) -> list[Command]:
    return [DiscardProposal(**stamp(), reason=e.reason)]


def cut_stack_top(e: ReplanRequested, src: EventId, stamp: Stamp) -> list[Command]:
    return [EnqueueGitJob(**stamp(), kind=_J.CUT_STACK_TOP)] if e.settled_before else []


def plan_again(e: ReplanRequested, src: EventId, stamp: Stamp) -> list[Command]:
    """計画タスクの統括: 設計が一度も確定していなければ、trees/overview のまま Prepare → Plan から組み直す。"""
    if e.settled_before:
        return []  # stack-top を切り直した WorktreeReady で組む（plan-on-worktree）
    return [
        AcceptFlow(**stamp(_PLANNING), task=_PLANNING, steps=planning_flow(settled_before=False))
    ]


def pause_stacking(e: ReplanRequested, src: EventId, stamp: Stamp) -> list[Command]:
    """再計画が進んでいる間は、積む仕事を取り出さない（Stack の規則）。"""
    return [PauseStacking(**stamp())]


def stop_tasks(e: TasksStopped, src: EventId, stamp: Stamp) -> list[Command]:
    commands: list[Command] = []
    for task in sorted(e.tasks, key=str):
        commands += [
            StopTask(**stamp(), task=task, reason="タスクを止めた"),
            WithdrawRequest(**stamp(), task=task),
        ]
    return commands


def discard_job(e: TasksDiscarded, src: EventId, stamp: Stamp) -> list[Command]:
    return [EnqueueGitJob(**stamp(), kind=_J.DISCARD, discarded=e.tasks)]


def requeue(e: TasksReturnedToQueue, src: EventId, stamp: Stamp) -> list[Command]:
    return [
        EnqueueStack(**stamp(), task=task, branch=branch)
        for task, branch in zip(e.tasks, e.branches, strict=True)
    ]


def close_task_side(e: EscalationClosed, src: EventId, stamp: Stamp) -> list[Command]:
    """Run の側で閉じたエスカレーションの、タスクの側の元を閉じる。"""
    if src.stream != StreamId.run() or e.task is None or e.source is None:
        return []
    return [CloseEscalation(**stamp(), task=e.task, escalation=e.source, reason=e.reason)]


def resolve_task_side(e: EscalationAnswered, src: EventId, stamp: Stamp) -> list[Command]:
    if e.task is None or e.source is None:
        return []
    return [
        ResolveEscalation(
            **stamp(), task=e.task, escalation=e.source, answer=e.answer, question=e.question
        )
    ]


def note_answer(e: EscalationAnswered, src: EventId, stamp: Stamp) -> list[Command]:
    """上げ元を書かずに上げたエスカレーションへの回答は、タスクの notes に残す。

    統括が応じなかった上げ（failed_notice がある）への回答は、起こし直すタスク統括への言葉で、
    受入条件の判断ではないので残さない（起こし直す知らせに載る）。
    """
    if e.task is None or e.source is not None or e.failed_notice is not None:
        return []
    origin = DecisionOrigin.USER if e.question is not None else DecisionOrigin.RUN_SUPERVISOR
    return [AddNote(**stamp(), task=e.task, decision=Decision(e.answer, origin, e.question))]


def ask_user_for_supervisor(e: EscalationRaised, src: EventId, stamp: Stamp) -> list[Command]:
    """ユーザーが直に受ける Run の上げ（ラン統括が応じなかった）を、`/autodev` への質問にする。

    回答が届くと Run がこの上げを閉じ、ラン統括を新しいセッションで、同じ知らせで起こし直す。
    タスク統括が応じなかった上げは、何度続いてもラン統括が受ける（段を飛ばさない）。
    """
    if src.stream != StreamId.run() or e.kind is not _E.SUPERVISOR_FAILED:
        return []
    route = EscalationRouter.route(src.stream, e.kind, e.task)
    if route.level is not SupervisorLevel.USER:
        return []
    body = (
        f"ラン統括が知らせ {e.failed_notice} に応じなかった（{e.reason}）。原因を取り除いてから"
        "答えると、回答を添えて、ラン統括を新しいセッションで同じ知らせから起こし直す。"
    )
    question = QuestionId(f"supervisor-failed-{src.version}")
    return [PostQuestion(**stamp(), question=question, body=body, escalation=src)]


def withdraw_questions(e: EscalationClosed, src: EventId, stamp: Stamp) -> list[Command]:
    """Run の側のエスカレーションが閉じたら、それを経路に持つ回答待ちの質問を取り下げる。

    ラン統括が ask-user の後に stop-tasks・replan で閉じると、質問が回答を待ったまま残り、driver が
    回答待ち（終了コード 4）で止まる。回答で閉じたなら質問はもう answered で、Questions は何もしない。
    """
    if src.stream != StreamId.run():
        return []
    return [WithdrawQuestions(**stamp(), escalation=e.escalation, reason=e.reason)]


def finish_job(e: RunFinished, src: EventId, stamp: Stamp) -> list[Command]:
    return [EnqueueGitJob(**stamp(), kind=_J.FINISH, ready_overview=e.ready_overview)]


def resume_tasks(e: RunResumed, src: EventId, stamp: Stamp) -> list[Command]:
    return [ResumeInterrupted(**stamp(), task=task) for task in e.tasks]


# =====================================================================================
# Task のイベント
# =====================================================================================


def hand_findings(e: StageCompleted, src: EventId, stamp: Stamp) -> list[Command]:
    if e.handoff is not Handoff.FINDINGS:
        return []
    assert e.result is not None, "受け渡すステージの StageCompleted は結果を持つ"
    return [
        RecordFindings(
            **stamp(),
            ledger=_ledger_of(e.execution.task),
            source=e.execution,
            findings=e.result.findings,
            design=e.reviewed,
        )
    ]


def hand_judgement(e: StageCompleted, src: EventId, stamp: Stamp) -> list[Command]:
    if e.handoff is not Handoff.JUDGEMENT:
        return []
    assert e.result is not None, "受け渡すステージの StageCompleted は結果を持つ"
    return [
        RecordJudgement(
            **stamp(),
            ledger=_ledger_of(e.execution.task),
            execution=e.execution,
            verdicts=e.result.verdicts,
            stall_cause=e.result.stall_cause,
            design=e.reviewed,
            design_cause=e.result.design_cause,
        )
    ]


def hand_proposal(e: StageCompleted, src: EventId, stamp: Stamp) -> list[Command]:
    if e.handoff is not Handoff.PROPOSAL:
        return []
    assert e.result is not None and e.result.proposal is not None, "提案のステージは提案を返す"
    return [
        ProposeDesign(
            **stamp(), proposal=e.result.proposal, execution=e.execution, artifacts=e.shared
        )
    ]


def hand_overview(e: StageCompleted, src: EventId, stamp: Stamp) -> list[Command]:
    if e.handoff is not Handoff.OVERVIEW:
        return []
    job = e.job
    assert e.result is not None and e.result.pr is not None, "概要 PR を作るステージは PR を返す"
    assert job is not None and job.branch is not None and job.base is not None, "概要 PR の仕事"
    entry = StackEntry(_GIT, job.branch, e.result.pr, job.base)
    return [RecordOverview(**stamp(), entry=entry, source=e.execution)]


def hand_entry(e: StageCompleted, src: EventId, stamp: Stamp) -> list[Command]:
    if e.handoff is not Handoff.ENTRY:
        return []
    job = e.job
    assert e.result is not None and e.result.pr is not None, "積むステージは PR を返す"
    assert job is not None and job.task is not None, "積む仕事は相手のタスクを持つ"
    assert job.branch is not None and job.base is not None, "取り出した積む仕事は base を持つ"
    entry = StackEntry(job.task, job.branch, e.result.pr, job.base)
    return [AppendEntry(**stamp(), entry=entry, source=e.execution)]


def hand_cut_back(e: StageCompleted, src: EventId, stamp: Stamp) -> list[Command]:
    if e.handoff is not Handoff.CUT_BACK:
        return []
    assert e.job is not None, "閉じるステージは git 管理タスクの仕事の中で走る"
    return [
        UnstackFrom(**stamp(), entry=e.job.cut_from, discarded=e.job.discarded, source=e.execution)
    ]


def record_conflict(e: StageCompleted, src: EventId, stamp: Stamp) -> list[Command]:
    if not e.conflicts or e.job is None or e.job.task is None:
        return []
    return [RecordConflict(**stamp(), task=e.job.task, files=e.conflicts)]


def count_fix(e: StageCompleted, src: EventId, stamp: Stamp) -> list[Command]:
    if e.execution.stage is not _S.FIX:
        return []
    return [CountFix(**stamp(), ledger=_ledger_of(e.execution.task), execution=e.execution)]


def comment_findings(e: StageCompleted, src: EventId, stamp: Stamp) -> list[Command]:
    if e.result is None:
        return []
    return [
        CommentFinding(
            **stamp(),
            ledger=_ledger_of(e.execution.task),
            finding=c.finding,
            body=c.body,
            author=e.execution,
        )
        for c in e.result.comments
    ]


def gate_passed(e: StageCompleted, src: EventId, stamp: Stamp) -> list[Command]:
    if e.execution.stage is not _S.GATE:
        return []
    return [RecordGateResult(**stamp(), ledger=_ledger_of(e.execution.task), execution=e.execution)]


def gate_failed(e: GateFailed, src: EventId, stamp: Stamp) -> list[Command]:
    return [
        RecordGateResult(
            **stamp(),
            ledger=_ledger_of(e.execution.task),
            execution=e.execution,
            failed=e.failed,
        )
    ]


def reject_integration(e: StageReported, src: EventId, stamp: Stamp) -> list[Command]:
    if e.kind is not _E.INTEGRATION_FAILED or e.job is None or e.job.task is None:
        return []
    return [RejectRequest(**stamp(), task=e.job.task, reason=e.reason or e.kind.value)]


def relay_to_run(e: EscalationRaised, src: EventId, stamp: Stamp) -> list[Command]:
    """計画タスクと git 管理タスクの統括: 受けたエスカレーションを、そのままラン統括へ上げる。"""
    if not _from_task(src) or _task_of(src).kind is TaskKind.IMPLEMENTATION:
        return []
    task = _task_of(src)
    return [
        EscalateToRun(
            **stamp(task),
            task=task,
            kind=e.kind,
            reason=e.reason or e.kind.value,
            pointers=e.pointers,
            hint=e.hint,
            source=src,
            question=e.question,
            answer_only=e.answer_only,
        )
    ]


def close_run_side(e: EscalationClosed, src: EventId, stamp: Stamp) -> list[Command]:
    """タスクの側で閉じたエスカレーションを中継していた、Run の側のエスカレーションも閉じる。"""
    if not _from_task(src):
        return []
    return [CloseRelayedEscalation(**stamp(), source=e.escalation, reason=e.reason)]


def resume_design(e: EscalationResolved, src: EventId, stamp: Stamp) -> list[Command]:
    if e.kind not in DESIGN_WAITS:
        return []
    return [ResumeDesign(**stamp(), kind=e.kind, answer=e.answer)]


def resume_ask(e: EscalationResolved, src: EventId, stamp: Stamp) -> list[Command]:
    """反応の続き: answers/<tool_use_id>.json に回答を書き終えてから、止まった実行を続きから再開する。"""
    if e.resume is None or e.tool_use_id is None:
        return []
    return [ResumeStage(**stamp(), task=_task_of(src), execution=e.resume)]


def finish_git_job(e: FlowFinished | FlowAbandoned, src: EventId, stamp: Stamp) -> list[Command]:
    if e.job is None:
        return []
    ending = FlowEnding.FINISHED if isinstance(e, FlowFinished) else FlowEnding.ABANDONED
    return [FinishGitJob(**stamp(), job=e.job.id, ending=ending)]


def git_finished(e: FlowFinished, src: EventId, stamp: Stamp) -> list[Command]:
    """仕上げの並びを終えた git 管理タスクを finished にする。"""
    if e.job is None or e.job.kind is not _J.FINISH:
        return []
    return [
        UpdateTaskStatus(**stamp(), task=_GIT, to_status=TaskStatus.FINISHED, cause="FlowFinished")
    ]


def enqueue_gated(e: TaskGated, src: EventId, stamp: Stamp) -> list[Command]:
    assert e.branch is not None, "実装タスクの TaskGated はブランチを持つ"
    return [EnqueueStack(**stamp(), task=_task_of(src), branch=e.branch)]


def record_cut_base(e: WorktreeReady, src: EventId, stamp: Stamp) -> list[Command]:
    """切った worktree と元のコミットを、それを使うタスクに覚えさせる（差分の起点・読む所）。

    積み直すタスクを前に積んだブランチから切り直したなら、切った元は根元ではないので覚えさせない
    （`CutPoint.roots_branch`）。worktree も同じタスクのもので変わらない。
    """
    point = e.job.cut_point if e.job is not None else None
    if e.base is None or (point is not None and not point.roots_branch):
        return []
    return [RecordBase(**stamp(), task=e.task, base=e.base, tree=e.tree)]


def record_rebased_base(e: BranchRebased, src: EventId, stamp: Stamp) -> list[Command]:
    """積み直しで根元が動いたら、載せ直した先をそのタスクに覚えさせる。"""
    return [RecordBase(**stamp(), task=e.task, base=e.onto)]


def plan_on_worktree(e: WorktreeReady, src: EventId, stamp: Stamp) -> list[Command]:
    """計画タスクの統括: 切った仕事の種類で、初回か再計画かの並びを組む。"""
    if e.task != _PLANNING or e.job is None:
        return []
    settled_before = e.job.kind is _J.CUT_STACK_TOP
    return [
        AcceptFlow(
            **stamp(_PLANNING),
            task=_PLANNING,
            steps=planning_flow(settled_before=settled_before),
        )
    ]


def status(to: TaskStatus) -> Rule:
    """Task・Stack のイベントで、Run が持つタスクの状態を動かす。"""

    def rule(e: Event, src: EventId, stamp: Stamp) -> list[Command]:
        if isinstance(e, GitJobTaken):
            if e.job.kind is not _J.STACK or e.job.task is None:
                return []
            task = e.job.task
        elif not _from_task(src):
            return []
        else:
            task = _task_of(src)
        if isinstance(e, FlowAccepted) and e.closes is None:
            return []
        return [UpdateTaskStatus(**stamp(), task=task, to_status=to, cause=type(e).__name__)]

    return rule


# =====================================================================================
# ReviewLedger のイベント
# =====================================================================================


def conclude_round(e: FindingsEvaluated, src: EventId, stamp: Stamp) -> list[Command]:
    """判定を締めた台帳の答えで、ラウンドを締める（Judge・Gate）か、設計の行き先を決める（DesignJudge）。"""
    judge = e.execution
    unresolved = tuple(f.finding for f in e.open_findings)
    if judge.stage is _S.JUDGE:
        return [
            ConcludeReviewRound(
                **stamp(),
                task=judge.task,
                judge=judge,
                unresolved=unresolved,
                stalled=e.stalled,
                cause=e.stall_cause,
            )
        ]
    if judge.stage is _S.GATE:
        return [
            ConcludeGateRound(
                **stamp(), task=judge.task, gate=judge, unresolved=unresolved, stalled=e.stalled
            )
        ]
    # 設計の台帳: 前の版に戻った・曖昧なら回答を待つ。must-fix が残れば直す。無ければ確定する
    cause = e.design_cause
    if cause is not None and cause.cause is DesignCause.REVERTED:
        assert cause.reverted_to is not None, "前の版に戻ったなら、戻った版を持つ"
        return [MarkReverted(**stamp(), to_version=cause.reverted_to, execution=judge)]
    if cause is not None and cause.cause is DesignCause.AMBIGUOUS:
        return [MarkAmbiguous(**stamp(), execution=judge)]
    if any(f.rating is Rating.MUST_FIX for f in e.open_findings):
        return [ReviseDesign(**stamp(), execution=judge)]
    assert e.design is not None, "DesignJudge の判定は、見た版を持つ"
    return [
        SettleDesign(**stamp(), execution=judge, design=e.design, open_findings=e.open_findings)
    ]


def confirm_received(e: ResultReceived, src: EventId, stamp: Stamp) -> list[Command]:
    return [ConfirmHandoff(**stamp(), task=e.source.task, execution=e.source)]


def confirm_refused(e: ResultRefused, src: EventId, stamp: Stamp) -> list[Command]:
    return [ConfirmHandoff(**stamp(), task=e.source.task, execution=e.source, refused=e.reason)]


def raise_carried(e: FindingCarried, src: EventId, stamp: Stamp) -> list[Command]:
    return [
        RaiseFinding(
            **stamp(),
            ledger=StreamId.review(e.to_task),
            rating=e.rating,
            body=e.body,
            location=e.location,
            carried_from=FindingOrigin(src.stream, e.finding),
        )
    ]


# =====================================================================================
# Design のイベント
# =====================================================================================


def track_proposal(e: DesignProposed, src: EventId, stamp: Stamp) -> list[Command]:
    return [TrackProposal(**stamp(), ledger=StreamId.design_review(), design=e.proposal.design)]


def record_settled(e: DesignSettled, src: EventId, stamp: Stamp) -> list[Command]:
    return [RecordSettledPlan(**stamp(), proposal=e.proposal, artifacts=e.artifacts)]


def conclude_settled(e: DesignSettled, src: EventId, stamp: Stamp) -> list[Command]:
    return [
        ConcludeDesignRound(**stamp(), task=_PLANNING, judge=e.execution, settled=e.proposal.design)
    ]


def reject_appendix(e: DesignSettled, src: EventId, stamp: Stamp) -> list[Command]:
    """must-fix 以外の指摘は設計ファイルの末尾に回し（反応）、確定を決めた判定で rejected にする。"""
    return [
        JudgeFinding(
            **stamp(),
            ledger=StreamId.design_review(),
            finding=f.finding,
            to=FindingStatus.REJECTED,
            comment="must-fix 以外なので、設計ファイルの末尾に書き足した",
            execution=e.execution,
        )
        for f in e.appendix
    ]


def conclude_revision(e: DesignRevisionStarted, src: EventId, stamp: Stamp) -> list[Command]:
    if e.execution is None:
        return []
    return [ConcludeDesignRound(**stamp(), task=_PLANNING, judge=e.execution)]


def escalate_design_wait(kind: EscalationKind) -> Rule:
    """設計が回答を待つ（前の版に戻った・曖昧・ラウンドを使い切った）ことを、計画タスクから上げる。"""
    cause = {_E.DESIGN_REVERTED: DesignCause.REVERTED, _E.DESIGN_AMBIGUOUS: DesignCause.AMBIGUOUS}

    def rule(
        e: DesignReverted | DesignAmbiguous | DesignRoundsExhausted, src: EventId, stamp: Stamp
    ) -> list[Command]:
        return [
            Escalate(
                **stamp(),
                task=_PLANNING,
                kind=kind,
                pointers=Pointers(),
                hint=Hint(design_cause=cause.get(kind)),
                origin=e.execution,
            )
        ]

    return rule


# =====================================================================================
# Stack のイベント
# =====================================================================================


def take_next(e: Event, src: EventId, stamp: Stamp) -> list[Command]:
    """取り出せるかは Stack が決める。取り出せなければ何もしない。"""
    return [TakeNextGitJob(**stamp())]


def flow_for_job(e: GitJobTaken, src: EventId, stamp: Stamp) -> list[Command]:
    """git 管理タスクの統括: 取り出した仕事から決まった並びを組む。"""
    return [AcceptFlow(**stamp(_GIT), task=_GIT, steps=git_job_flow(e.job), job=e.job)]


def abandon_withdrawn(e: GitJobWithdrawn, src: EventId, stamp: Stamp) -> list[Command]:
    if not e.in_progress:
        return []
    return [AbandonFlow(**stamp(), task=_GIT, reason="相手のタスクを止めた")]


def mark_stacked(e: TaskStacked, src: EventId, stamp: Stamp) -> list[Command]:
    return [MarkStacked(**stamp(), task=e.entry.task, pr=e.entry.pr)]


def return_to_queue(e: StackCutBack, src: EventId, stamp: Stamp) -> list[Command]:
    """積む列へ戻す一覧は Run が持つ（TasksDiscarded.requeue）ので、写さずに頼むだけ。"""
    return [ReturnToQueue(**stamp())]


def record_integration_failure(e: IntegrationFailed, src: EventId, stamp: Stamp) -> list[Command]:
    return [RecordIntegrationFailure(**stamp(), task=e.task, reason=e.reason, files=e.files)]


def clear_integration_failure(e: IntegrationRetried, src: EventId, stamp: Stamp) -> list[Command]:
    return [ClearIntegrationFailure(**stamp(), task=e.task)]


def regate_kept_stack_job(e: GitJobFinished, src: EventId, stamp: Stamp) -> list[Command]:
    """積む仕事のフローを捨てて仕事を残したら、相手のタスクを gated に戻す（積む列で待っている）。

    再計画で範囲が変わったら、gated のタスクとして積む列から外せる（TasksPlanned.withdraw）。
    """
    job = e.job
    if not e.outcome.keeps_job or job.kind is not _J.STACK or job.task is None:
        return []
    return [
        UpdateTaskStatus(
            **stamp(), task=job.task, to_status=TaskStatus.GATED, cause="GitJobFinished"
        )
    ]


def escalate_stuck_job(e: GitJobFinished, src: EventId, stamp: Stamp) -> list[Command]:
    """戻した回数が上限に達して止めた仕事を、git 管理タスクから上げる（ラン統括が続けるかやめるか）。

    やめるとランが終わらない仕事（`GitJobKind.can_drop` が偽）は、答え以外では閉じられない上げにする
    （`answer_only`）。ラン統括が答え以外の判断で閉じようとすると Run が拒み、理由を添えて差し戻す。
    """
    if e.outcome is not GitJobOutcome.STUCK:
        return []
    stuck = (
        f"git の仕事 {e.job.id}（{e.job.kind.value}）のフローを捨てて {MAX_JOB_RETURNS} 回戻したが"
        "、済んでいない。"
    )
    droppable = e.job.kind.can_drop
    if droppable:
        reason = stuck + "続けるなら答え、やめるなら答え以外の判断で閉じる"
    else:
        reason = (
            stuck
            + "この仕事はやめるとランが終わらないので、答えて続ける（答え以外では閉じられない）"
        )
    return [
        Escalate(
            **stamp(),
            task=_GIT,
            kind=_E.STAGE_ERRORS,
            pointers=Pointers(),
            reason=reason,
            answer_only=not droppable,
        )
    ]


def _stuck_job_escalation(kind: EscalationKind | None, origin: ExecutionId | None) -> bool:
    """止めた仕事への上げか（名前の付いた規則）。git 管理タスクの、上げ元の実行が無い stage-errors。"""
    return kind is _E.STAGE_ERRORS and origin is None


def retry_stuck_job(e: EscalationResolved, src: EventId, stamp: Stamp) -> list[Command]:
    """止めた仕事への上げに回答が届いた。続ける。"""
    if not _from_git(src) or not _stuck_job_escalation(e.kind, e.origin):
        return []
    return [RetryGitJob(**stamp())]


def drop_stuck_job(e: EscalationClosed, src: EventId, stamp: Stamp) -> list[Command]:
    """止めた仕事への上げを答え以外で閉じた。やめる。"""
    if not _from_git(src) or not _stuck_job_escalation(e.kind, e.origin):
        return []
    return [DropGitJob(**stamp())]


def retry_integration(e: EscalationResolved, src: EventId, stamp: Stamp) -> list[Command]:
    """統合の失敗に回答が届いた。同じ仕事で統合をやり直すので、失敗の印を下ろす。"""
    if not _from_git(src) or e.kind is not _E.INTEGRATION_FAILED:
        return []
    return [RetryIntegration(**stamp())]


# =====================================================================================
# Questions のイベント
# =====================================================================================


def record_answer(e: QuestionAnswered, src: EventId, stamp: Stamp) -> list[Command]:
    return [RecordAnswer(**stamp(), question=e.question, answer=e.answer, escalation=e.escalation)]


# =====================================================================================
# 表
# =====================================================================================

_SUPERVISOR = By.SUPERVISOR


def _row(
    name: str, on: type[Event] | tuple[type[Event], ...], rule: Rule, by: By = By.POLICY
) -> Policy:
    return Policy(name, on if isinstance(on, tuple) else (on,), rule, by)


#: 受け手として登録する行（この順に登録する）。行の名前と並びは test_seams.py の SEAMS と同じ
POLICIES: tuple[Policy, ...] = (
    # Run
    _row("start-planning-and-git", RunStarted, start_planning_and_git),
    _row("open-task", TaskStarted, open_task),
    _row("rescope-reopened", TaskStarted, rescope_reopened),
    _row("cut-task-branch", TaskStarted, cut_task_branch),
    _row("apply-first-plan", SettledPlanRecorded, apply_first_plan),
    _row("start-ready-after-plan", TasksPlanned, start_ready),
    _row("write-overview", TasksPlanned, write_overview),
    _row("change-scope", TasksPlanned, change_scope),
    _row("withdraw-rescoped", TasksPlanned, withdraw_rescoped),
    _row("carry-findings", TasksPlanned, carry_findings),
    _row("resume-stacking", TasksPlanned, resume_stacking),
    _row("discard-proposal", ReplanRequested, discard_proposal),
    _row("cut-stack-top", ReplanRequested, cut_stack_top),
    _row("plan-again", ReplanRequested, plan_again, _SUPERVISOR),
    _row("pause-stacking", ReplanRequested, pause_stacking),
    _row("stop-tasks", TasksStopped, stop_tasks),
    _row("discard-job", TasksDiscarded, discard_job),
    _row("requeue", TasksReturnedToQueue, requeue),
    _row("start-ready-after-stacked", TaskMarkedStacked, start_ready),
    _row("start-ready-after-inserted", TaskInserted, start_ready),
    _row("start-ready-after-stopped", TasksStopped, start_ready),
    _row("start-ready-after-slot-freed", TaskStatusChanged, start_ready_after_slot_freed),
    _row("close-task-side", EscalationClosed, close_task_side),
    _row("resolve-task-side", EscalationAnswered, resolve_task_side),
    _row("note-answer", EscalationAnswered, note_answer),
    _row("ask-user-for-supervisor", EscalationRaised, ask_user_for_supervisor),
    _row("withdraw-questions", EscalationClosed, withdraw_questions),
    _row("finish-job", RunFinished, finish_job),
    _row("resume-tasks", RunResumed, resume_tasks),
    _row("start-ready-after-resumed", RunResumed, start_ready),
    # Task
    _row("hand-findings", StageCompleted, hand_findings),
    _row("hand-judgement", StageCompleted, hand_judgement),
    _row("hand-proposal", StageCompleted, hand_proposal),
    _row("hand-overview", StageCompleted, hand_overview),
    _row("hand-entry", StageCompleted, hand_entry),
    _row("hand-cut-back", StageCompleted, hand_cut_back),
    _row("record-conflict", StageCompleted, record_conflict),
    _row("count-fix", StageCompleted, count_fix),
    _row("comment-findings", StageCompleted, comment_findings),
    _row("gate-passed", StageCompleted, gate_passed),
    _row("gate-failed", GateFailed, gate_failed),
    _row("reject-integration", StageReported, reject_integration),
    _row("relay-to-run", EscalationRaised, relay_to_run, _SUPERVISOR),
    _row("close-run-side", EscalationClosed, close_run_side),
    _row("resume-design", EscalationResolved, resume_design),
    _row("finish-git-job", (FlowFinished, FlowAbandoned), finish_git_job),
    _row("git-finished", FlowFinished, git_finished),
    _row("enqueue-gated", TaskGated, enqueue_gated),
    # 計画タスクのフローを組む前に、読む所を覚えさせる（最初のステージのプロンプトが読む）
    _row("record-cut-base", WorktreeReady, record_cut_base),
    _row("record-rebased-base", BranchRebased, record_rebased_base),
    _row("plan-on-worktree", WorktreeReady, plan_on_worktree, _SUPERVISOR),
    _row("status-gated", TaskGated, status(TaskStatus.GATED)),
    _row("status-stacking", GitJobTaken, status(TaskStatus.STACKING)),
    _row("status-escalated", EscalationRaised, status(TaskStatus.ESCALATED)),
    _row(
        "status-running",
        (EscalationResolved, EscalationClosed, ScopeChanged, FlowAccepted),
        status(TaskStatus.RUNNING),
    ),
    # ReviewLedger
    _row("conclude-round", FindingsEvaluated, conclude_round),
    _row("confirm-received", ResultReceived, confirm_received),
    _row("confirm-refused", ResultRefused, confirm_refused),
    _row("raise-carried", FindingCarried, raise_carried),
    # Design
    _row("track-proposal", DesignProposed, track_proposal),
    _row("record-settled", DesignSettled, record_settled),
    _row("conclude-settled", DesignSettled, conclude_settled),
    _row("reject-appendix", DesignSettled, reject_appendix),
    _row("conclude-revision", DesignRevisionStarted, conclude_revision),
    _row("escalate-reverted", DesignReverted, escalate_design_wait(_E.DESIGN_REVERTED)),
    _row("escalate-ambiguous", DesignAmbiguous, escalate_design_wait(_E.DESIGN_AMBIGUOUS)),
    _row(
        "escalate-rounds-exhausted",
        DesignRoundsExhausted,
        escalate_design_wait(_E.DESIGN_ROUNDS_EXHAUSTED),
    ),
    # Stack
    _row(
        "take-next",
        (
            GitJobQueued,
            GitJobFinished,
            StackingResumed,
            GitJobRetried,
            GitJobDropped,
            GitJobWithdrawn,
        ),
        take_next,
    ),
    _row("flow-for-job", GitJobTaken, flow_for_job, _SUPERVISOR),
    _row("abandon-withdrawn", GitJobWithdrawn, abandon_withdrawn),
    _row("mark-stacked", TaskStacked, mark_stacked),
    _row("return-to-queue", StackCutBack, return_to_queue),
    _row("record-integration-failure", IntegrationFailed, record_integration_failure),
    _row("clear-integration-failure", IntegrationRetried, clear_integration_failure),
    _row("regate-kept-stack-job", GitJobFinished, regate_kept_stack_job),
    _row("escalate-stuck-job", GitJobFinished, escalate_stuck_job),
    _row("retry-stuck-job", EscalationResolved, retry_stuck_job),
    _row("drop-stuck-job", EscalationClosed, drop_stuck_job),
    _row("retry-integration", EscalationResolved, retry_integration),
    # Questions
    _row("record-answer", QuestionAnswered, record_answer),
)

#: 反応が副作用を済ませた後に返すコマンドの組み方。反応はこれを呼び、返った一覧をそのまま返す
FOLLOW_UPS: Mapping[str, Policy] = MappingProxyType(
    {
        policy.name: policy
        for policy in (
            # answers/<tool_use_id>.json に EscalationResolved.answer を書いてから
            _row("resume-ask", EscalationResolved, resume_ask, By.REACTION),
        )
    }
)

#: メインループに登録する受け手（名前 → 受ける関数）。登録の順は、この並び
RECEIVERS: Mapping[str, Callable[[Event, EventId], list[Command]]] = MappingProxyType(
    {policy.name: policy.receive for policy in POLICIES}
)
