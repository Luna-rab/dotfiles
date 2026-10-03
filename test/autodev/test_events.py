"""イベントとコマンドの名前の表と、JSON との往復（`domain/events.py`・`domain/commands.py`・`domain/codec.py`）。"""

from __future__ import annotations

import inspect
import json
from typing import Any

import pytest
from autodev_samples import sample
from autodevlib.domain import codec, commands, events
from autodevlib.domain.commands import COMMAND_TYPES, COMMANDS_BY_AGGREGATE, Command
from autodevlib.domain.events import (
    EVENT_TYPES,
    EVENTS_BY_AGGREGATE,
    AllTasksSettled,
    Event,
    EventRecord,
    RunPanicked,
    TaskStarted,
    UnknownEventType,
    from_record,
    to_record,
)
from autodevlib.domain.values import (
    ArtifactKind,
    ArtifactRef,
    StreamId,
    TaskId,
    TaskKind,
)

#: 宛先の集約ごとの土台。表には入れない
COMMAND_BASES = {
    commands.Command,
    commands.RunCommand,
    commands.TaskCommand,
    commands.ReviewCommand,
    commands.DesignCommand,
    commands.StackCommand,
    commands.QuestionsCommand,
}


def defined_in(module: Any, base: type) -> set[type]:
    return {
        obj
        for obj in vars(module).values()
        if inspect.isclass(obj) and issubclass(obj, base) and obj.__module__ == module.__name__
    }


def test_eventsで定義したイベントはすべて名前の表にある():
    assert defined_in(events, Event) - {Event} == set(EVENT_TYPES.values())
    assert all(name == cls.__name__ for name, cls in EVENT_TYPES.items())


def test_commandsで定義したコマンドはすべて名前の表にある():
    assert defined_in(commands, Command) - COMMAND_BASES == set(COMMAND_TYPES.values())
    assert all(name == cls.__name__ for name, cls in COMMAND_TYPES.items())


def test_補足で足したイベントがあり名前を変えた古いイベントは無い():
    for name in (
        "TaskStatusChanged",
        "AllTasksSettled",
        "SettledPlanRecorded",
        "DesignProposalAbandoned",
        "DesignRevisionStarted",
        "RunResumed",
        "TaskOpened",
        "StageRequested",
        "ExecutionRestarted",
    ):
        assert name in EVENT_TYPES
    assert "AllTasksStacked" not in EVENT_TYPES


def test_補足で足したコマンドがある():
    for name in (
        "UpdateTaskStatus",
        "RecordSettledPlan",
        "DiscardProposal",
        "MarkInterrupted",
        "OpenTask",
        "RecordJudgement",
        "RecordFindings",
        "ConfirmHandoff",
        "ResumeInterrupted",
        "EnqueueGitJob",
        "ConcludeGateRound",
    ):
        assert name in COMMAND_TYPES
    # --resume で続けられなかったかは、証拠の事実（resumed・initialized など）から Task が決める
    assert "RestartExecution" not in COMMAND_TYPES
    # 次のステージは Task が決める。止めを外すのは ConcludeGateRound だけ
    assert "RequestStage" not in COMMAND_TYPES
    # 破棄は再計画の反映（ApplyReplan）の中だけで行う。判定の締めは判定と一緒に届く
    assert "DiscardTasks" not in COMMAND_TYPES
    assert "EvaluateStall" not in COMMAND_TYPES


def test_複数の集約に属するのはエスカレーションと結果の受け渡しの知らせだけ():
    # Run の側のエスカレーションも EscalationClosed で閉じる（ADDENDUM §8）。ステージの結果を受け取る
    # 側は、どれも同じ形で受けた／受けられないを返す
    owners: dict[type[Event], list[str]] = {}
    for aggregate, classes in EVENTS_BY_AGGREGATE.items():
        assert len(set(classes)) == len(classes), aggregate
        for cls in classes:
            owners.setdefault(cls, []).append(aggregate)
    shared = {cls.__name__: names for cls, names in owners.items() if len(names) > 1}
    receivers = ["ReviewLedger", "Design", "Stack"]
    assert shared == {
        "EscalationRaised": ["Run", "Task"],
        "EscalationClosed": ["Run", "Task"],
        "ResultReceived": receivers,
        "ResultRefused": receivers,
    }


def test_コマンドは宛先の集約と出してよい者を持つ():
    for aggregate, classes in COMMANDS_BY_AGGREGATE.items():
        assert aggregate in EVENTS_BY_AGGREGATE
        for cls in classes:
            assert aggregate == cls.AGGREGATE, cls.__name__
            assert cls.ISSUERS, f"{cls.__name__} を出してよい者が無い"


@pytest.mark.parametrize("cls", list(EVENT_TYPES.values()), ids=list(EVENT_TYPES))
def test_イベントはJSONを通して往復する(cls: type[Event]):
    event = sample(cls)
    record = to_record(event)
    stored = json.loads(json.dumps(record.data, ensure_ascii=False))
    assert from_record(EventRecord(record.type, record.v, stored)) == event


@pytest.mark.parametrize("cls", list(COMMAND_TYPES.values()), ids=list(COMMAND_TYPES))
def test_コマンドはJSONを通して往復し宛先のストリームを持つ(cls: type[Command]):
    command = sample(cls)
    stored = json.loads(json.dumps(codec.to_json(command), ensure_ascii=False))
    assert codec.from_json(cls, stored) == command
    assert isinstance(command.target, StreamId)


def test_包んだ値は素の値になり集合は並べて書く():
    event = TaskStarted(
        TaskId("task2"),
        TaskKind.IMPLEMENTATION,
        blocked_by=frozenset({TaskId("task10"), TaskId("task1")}),
        artifacts=(ArtifactRef(ArtifactKind.DESIGN, "2"),),
    )
    data = to_record(event).data
    assert data["task"] == "task2"
    assert data["kind"] == "implementation"
    assert data["blocked_by"] == ["task1", "task10"]
    assert data["artifacts"] == [{"kind": "design", "at": "2"}]


def test_知らないキーと欠けたキーを読み飛ばさない():
    with pytest.raises(codec.DecodeError):
        from_record(EventRecord("RunPanicked", 1, {"cause": "429", "extra": 1}))
    with pytest.raises(codec.DecodeError):
        from_record(EventRecord("RunPanicked", 1, {}))
    with pytest.raises(codec.DecodeError):
        from_record(EventRecord("RunFinished", 1, {"ready_overview": "yes"}))


def test_値の検査に落ちた値はどこで落ちたかを付けて拒む():
    data = to_record(sample(TaskStarted)).data
    with pytest.raises(codec.DecodeError, match=r"TaskStarted\.task: TaskId の形が違う"):
        from_record(EventRecord("TaskStarted", 1, {**data, "task": "task0"}))
    with pytest.raises(codec.DecodeError, match=r"TaskStarted\.spec: タスクの件名"):
        from_record(EventRecord("TaskStarted", 1, {**data, "spec": {**data["spec"], "title": ""}}))
    with pytest.raises(codec.DecodeError, match=r"TaskMarkedStacked\.pr: int が要るところに bool"):
        from_record(EventRecord("TaskMarkedStacked", 1, {"task": "task1", "pr": True}))


def test_集合に同じ値が2つある入力を拒む():
    with pytest.raises(codec.DecodeError, match="集合に同じ値が 2 つある"):
        from_record(EventRecord("TasksStopped", 1, {"tasks": ["task1", "task1"]}))


def test_知らないイベントと新しすぎる版は読めない():
    with pytest.raises(UnknownEventType):
        from_record(EventRecord("NoSuchEvent", 1, {}))
    with pytest.raises(UnknownEventType):
        from_record(EventRecord("RunPanicked", 2, {"cause": "429"}))
    with pytest.raises(UnknownEventType):
        from_record(EventRecord("RunPanicked", 0, {"reason": "429"}))


def test_アップキャスタで古い版を今の版に読み替える():
    upcasters = {
        ("RunPanicked", 0): lambda data: ("RunPanicked", 1, {"cause": data["reason"]}),
        # 名前を変えたイベントも読み替えられる
        ("AllTasksStacked", 1): lambda data: ("AllTasksSettled", 1, data),
    }
    assert from_record(EventRecord("RunPanicked", 0, {"reason": "429"}), upcasters) == RunPanicked(
        "429"
    )
    assert from_record(EventRecord("AllTasksStacked", 1, {}), upcasters) == AllTasksSettled()


def test_輪になった読み替えで止まらなくならない():
    upcasters = {
        ("Old", 1): lambda data: ("Older", 1, data),
        ("Older", 1): lambda data: ("Old", 1, data),
    }
    with pytest.raises(UnknownEventType):
        from_record(EventRecord("Old", 1, {}), upcasters)
