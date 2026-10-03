"""LLM の統括を、どのイベントで起こすか（`domain/supervision.py`）と、driver が終わり方を決めるのに聞く問い。"""

from __future__ import annotations

import dataclasses

import pytest
from autodev_samples import sample
from autodevlib.domain.events import (
    AllTasksSettled,
    AnswerRecorded,
    EscalationAnswered,
    EscalationRaised,
    Event,
    FlowAbandoned,
    FlowAccepted,
    FlowRejected,
    QuestionAnswered,
    QuestionPosted,
    RunFinished,
    RunStarted,
    ScopeChanged,
    SettledPlanRecorded,
    StageCompleted,
    StageRequested,
    StageStarted,
    TaskOpened,
    TasksPlanned,
    TaskStarted,
    TaskStatusChanged,
    TaskStopped,
    WorktreeReady,
)
from autodevlib.domain.flow import Flow, FlowStep
from autodevlib.domain.questions import Questions
from autodevlib.domain.run import Run
from autodevlib.domain.supervision import Notice, Supervisor, Wake, wake_for
from autodevlib.domain.task import Task
from autodevlib.domain.values import (
    MAX_SUPERVISOR_FAILURES,
    BranchName,
    CommandId,
    CommitSha,
    DesignVersion,
    EscalationKind,
    EventId,
    ExecutionId,
    GitJob,
    GitJobKind,
    Pointers,
    Proposal,
    QuestionId,
    StageKind,
    StreamId,
    TaskId,
    TaskKind,
    TaskSpec,
    TaskStatus,
)

T1 = TaskId("task1")
RUN = EventId("run#4")
TASK1 = EventId("task/task1#4")
PLANNING = EventId("task/planning#4")
GIT = EventId("task/git#4")
PROPOSAL = Proposal(DesignVersion(2), ())
CUT = GitJob(3, GitJobKind.CUT_TASK, task=T1, branch=BranchName("stack/r--task-1"))
PLANNED = sample(TasksPlanned)
FAILED_TASK1 = EscalationRaised(
    EscalationKind.SUPERVISOR_FAILED, Pointers(), task=T1, reason="x", failed_notice=GIT
)
FAILED_RUN = EscalationRaised(
    EscalationKind.SUPERVISOR_FAILED, Pointers(), reason="x", failed_notice=RUN
)


@pytest.mark.parametrize(
    ("event", "source", "wake"),
    [
        # ラン統括
        (sample(EscalationRaised), RUN, Wake(Supervisor.run(), Notice.ESCALATION)),
        (AnswerRecorded(QuestionId("q1"), "A"), RUN, Wake(Supervisor.run(), Notice.ANSWER)),
        # タスク統括が応じなかった上げはラン統括が受ける
        (FAILED_TASK1, RUN, Wake(Supervisor.run(), Notice.ESCALATION)),
        # ラン統括が応じなかった上げはユーザーが受ける（ポリシーが質問にする）。ラン統括は起こさない
        (FAILED_RUN, RUN, None),
        # 答えたら、応じなかった統括を同じ知らせで起こし直す
        (
            AnswerRecorded(QuestionId("q1"), "直した", RUN, failed_notice=TASK1),
            RUN,
            Wake(Supervisor.run(), Notice.RETRY, TASK1),
        ),
        (
            EscalationAnswered(RUN, T1, None, "続けてよい", failed_notice=GIT),
            RUN,
            Wake(Supervisor.of(T1), Notice.RETRY, GIT),
        ),
        (EscalationAnswered(RUN, T1, None, "続けてよい"), RUN, None),
        # タスク統括が応じなかった数が上限を超えても、受けるのはラン統括（段を飛ばさない）
        (
            dataclasses.replace(FAILED_TASK1, failures=MAX_SUPERVISOR_FAILURES + 1),
            RUN,
            Wake(Supervisor.run(), Notice.ESCALATION),
        ),
        (
            SettledPlanRecorded(PROPOSAL, replan=True),
            RUN,
            Wake(Supervisor.run(), Notice.SETTLED_REPLAN),
        ),
        (SettledPlanRecorded(PROPOSAL, replan=False), RUN, None),
        (PLANNED, RUN, Wake(Supervisor.run(), Notice.STILL_OPEN)),
        (dataclasses.replace(PLANNED, still_open=()), RUN, None),
        (AllTasksSettled(), RUN, Wake(Supervisor.run(), Notice.ALL_SETTLED)),
        (sample(RunStarted), RUN, None),
        # 実装タスクの統括
        (WorktreeReady(T1, "trees/task1", job=CUT), GIT, Wake(Supervisor.of(T1), Notice.STARTED)),
        (
            WorktreeReady(T1, "trees/task1", job=GitJob(4, GitJobKind.STACK, task=T1)),
            GIT,
            None,
        ),
        (
            WorktreeReady(
                TaskId.planning(), "trees/overview", job=GitJob(1, GitJobKind.CUT_OVERVIEW)
            ),
            GIT,
            None,
        ),
        (sample(EscalationRaised), TASK1, Wake(Supervisor.of(T1), Notice.ESCALATION)),
        (
            ScopeChanged(TaskSpec("t"), (), Pointers()),
            TASK1,
            Wake(Supervisor.of(T1), Notice.SCOPE_CHANGED),
        ),
        # 新しいブランチで始め直すなら、切り直した worktree を待つ
        (
            ScopeChanged(TaskSpec("t"), (), Pointers(), BranchName("stack/r--task-1-r1")),
            TASK1,
            None,
        ),
        (FlowRejected((), ("x",)), TASK1, Wake(Supervisor.of(T1), Notice.FLOW_REJECTED)),
        (FlowAbandoned(1, "閉じた"), TASK1, Wake(Supervisor.of(T1), Notice.FLOW_ABANDONED)),
        # 計画タスクと git 管理タスクの統括はプログラム（ポリシーの表）
        (sample(EscalationRaised), PLANNING, None),
        (FlowAbandoned(1, "閉じた"), GIT, None),
        # うまく進んでいる間は起こさない
        (StageCompleted(ExecutionId(T1, StageKind.IMPL, 0, 1)), TASK1, None),
    ],
)
def test_統括を起こすイベントと知らせ(event: Event, source: EventId, wake: Wake | None):
    assert wake_for(event, source) == wake


def test_LLMの統括を持つのはラン統括と実装タスクだけ():
    assert Supervisor.run().name == "run"
    assert Supervisor.of(T1).name == "task-task1"
    with pytest.raises(ValueError, match="実装タスクだけ"):
        Supervisor.of(TaskId.planning())


def test_回答を待つ質問があるかを答える():
    questions = Questions(StreamId.questions())
    assert not questions.awaiting_answer
    questions.apply(QuestionPosted(QuestionId("q1"), "A か B か"), CommandId("c1"))
    assert questions.awaiting_answer
    questions.apply(QuestionAnswered(QuestionId("q1"), "A"), CommandId("c2"))
    assert not questions.awaiting_answer


def test_ランを終えたかは仕上げの並びを終えるまで偽():
    run = Run(StreamId.run())
    started = sample(RunStarted)
    for index, event in enumerate(
        [
            started,
            TaskStarted(TaskId.planning(), TaskKind.PLANNING),
            TaskStarted(TaskId.git(), TaskKind.GIT),
            RunFinished(ready_overview=True),
        ]
    ):
        run.apply(event, CommandId(f"c{index}"))
    # RunFinished の後も、git 管理タスクは仕上げの並びを走らせている
    assert run.finished
    assert not run.complete
    run.apply(
        TaskStatusChanged(TaskId.git(), TaskStatus.RUNNING, TaskStatus.FINISHED, "FlowFinished"),
        CommandId("c9"),
    )
    assert run.complete


def test_始めていない実行は今のフローのものだけを答える():
    task = Task(StreamId.task(T1))
    flow = Flow((FlowStep(StageKind.IMPL),), 1)
    first = ExecutionId(T1, StageKind.IMPL, 0, 1)
    for index, event in enumerate(
        [
            TaskOpened(TaskKind.IMPLEMENTATION, TaskSpec("t")),
            FlowAccepted(flow),
            StageRequested(first, 0),
        ]
    ):
        task.apply(event, CommandId(f"c{index}"))
    assert task.requested_executions() == [first]
    task.apply(StageStarted(first, CommitSha("a" * 40)), CommandId("c8"))
    assert task.requested_executions() == []
    task.apply(StageRequested(ExecutionId(T1, StageKind.IMPL, 0, 2), 0), CommandId("c9"))
    task.apply(TaskStopped("止めた"), CommandId("c10"))
    assert task.requested_executions() == []
