"""`status --json` の組み立てと進み具合のファイル（`infra/status.py`）・ランディレクトリの置き場（`infra/paths.py`）。"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from autodev_fakes import Queue, enqueue, execution, factory, task
from autodevlib.adapters import children
from autodevlib.app.mainloop import MainLoop
from autodevlib.domain.events import (
    DesignAmbiguous,
    DesignProposed,
    DesignSettled,
    EscalationRaised,
    Event,
    FlowAccepted,
    OverviewRecorded,
    QuestionPosted,
    RunFinished,
    RunPanicked,
    RunResumed,
    RunStarted,
    StageCompleted,
    StageRequested,
    StageStarted,
    TaskMarkedStacked,
    TaskOpened,
    TasksPlanned,
    TasksStopped,
    TaskStacked,
    TaskStarted,
    TaskStatusChanged,
)
from autodevlib.domain.flow import Cursor, Flow, FlowStep
from autodevlib.domain.values import (
    ArtifactKind,
    ArtifactRef,
    BranchName,
    CommandId,
    CommitSha,
    DesignVersion,
    EscalationKind,
    EventId,
    ExecutionId,
    Instruction,
    ParallelLimit,
    PlannedTask,
    Pointers,
    PrNumber,
    Proposal,
    QuestionId,
    Repository,
    RunName,
    StackEntry,
    StageKind,
    StreamId,
    TaskId,
    TaskKind,
    TaskSpec,
    TaskStatus,
)
from autodevlib.infra import status_sections
from autodevlib.infra.eventstore import EventStore
from autodevlib.infra.lock import DriverLock
from autodevlib.infra.paths import RunPaths, state_root
from autodevlib.infra.rejections import Rejection, RejectionLog, read_rejections
from autodevlib.infra.status import (
    all_statuses,
    build_status,
    prune_progress,
    read_progress,
    remove_progress,
    run_status,
    write_progress,
)
from autodevlib.infra.status_sections import Replayed


@pytest.fixture
def paths(tmp_path: Path) -> RunPaths:
    return RunPaths.of(RunName("demo"), {"AUTODEV_STATE_DIR": str(tmp_path)})


def waiting(view: Replayed) -> list[str]:
    queue = view.aggregates.get(StreamId.stack())
    return [t.value for t in queue.waiting] if isinstance(queue, Queue) else []


def test_再生した集約に差し込んだ欄と進み具合と拒んだ記録を混ぜて返す(paths: RunPaths):
    loop = MainLoop(
        EventStore.open(paths.events_db), factory, [], on_rejected=RejectionLog(paths.rejections)
    )
    loop.process(enqueue(1, "c1"))
    loop.process(enqueue(2, "c2"))
    loop.process(enqueue(1, "c3"))
    write_progress(paths, execution(1), {"turns": 3, "tool": "Bash"})
    # driver の書く接続を開いたまま読む
    status = build_status(
        paths,
        factory,
        {"stack": waiting, "progress": lambda view: dict(view.progress)},
    )
    assert status["format"] == 1
    assert status["name"] == "demo"
    assert status["last_seq"] == 2
    assert status["updated_at"]
    assert status["stack"] == ["task1", "task2"]
    assert status["progress"] == {"task1-Impl-r0-a1": {"turns": 3, "tool": "Bash"}}
    assert [(r["command_id"], r["reason"]) for r in status["rejections"]] == [
        ("c3", "すでに列にある")
    ]


def test_拒んだ記録はcommand_idと理由の組で重ねない(paths: RunPaths):
    def rejection(command_id: str, reason: str) -> Rejection:
        return Rejection("x", "EnqueueStack", command_id, {"kind": "policy"}, reason)

    log = RejectionLog(paths.rejections)
    log(rejection("c1", "すでに列にある"))
    log(rejection("c1", "すでに列にある"))
    # 落ちた後の配り直しで、開き直した driver がもう一度拒む
    again = RejectionLog(paths.rejections)
    again(rejection("c1", "すでに列にある"))
    again(rejection("c2", "別の理由"))
    assert [r["command_id"] for r in read_rejections(paths.rejections)] == ["c1", "c2"]


def test_同じidの判断が別の理由で拒まれたら行を足す(paths: RunPaths):
    # 統括の判断の id は知らせ 1 つに 1 つ。同じ知らせで差し戻されて出し直した判断は同じ id になる
    def rejection(reason: str) -> Rejection:
        return Rejection("x", "AnswerEscalation", "d1", {"kind": "run-supervisor"}, reason)

    log = RejectionLog(paths.rejections)
    log(rejection("1 回目の理由"))
    log(rejection("2 回目の理由"))
    RejectionLog(paths.rejections)(rejection("2 回目の理由"))
    assert [r["reason"] for r in read_rejections(paths.rejections)] == [
        "1 回目の理由",
        "2 回目の理由",
    ]


def test_走っていない実行の進み具合を片付ける(paths: RunPaths):
    prune_progress(paths, [])
    for number in (1, 2):
        write_progress(paths, execution(number), {"turns": number})
    prune_progress(paths, [execution(2)])
    assert list(read_progress(paths)) == ["task2-Impl-r0-a1"]


def test_ランが無ければ作らずに落ちる(paths: RunPaths):
    with pytest.raises(FileNotFoundError):
        build_status(paths, factory, {})
    assert not paths.root.exists()


def test_骨組みの欄と同じ名前は差し込めない(paths: RunPaths):
    with pytest.raises(ValueError, match="rejections"):
        build_status(paths, factory, {"rejections": waiting})


def test_進み具合は書き直せて消せて読めないものは飛ばす(paths: RunPaths):
    write_progress(paths, execution(1), {"turns": 1})
    write_progress(paths, execution(1), {"turns": 2})
    write_progress(paths, execution(2), {"turns": 5})
    (paths.progress / "broken.json").write_text("{", encoding="utf-8")
    assert read_progress(paths) == {
        "task1-Impl-r0-a1": {"turns": 2},
        "task2-Impl-r0-a1": {"turns": 5},
    }
    remove_progress(paths, execution(1))
    remove_progress(paths, execution(1))
    assert list(read_progress(paths)) == ["task2-Impl-r0-a1"]
    # 一時ファイルを残していない
    assert sorted(p.name for p in paths.progress.iterdir()) == [
        "broken.json",
        "task2-Impl-r0-a1.json",
    ]


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({"AUTODEV_STATE_DIR": "/srv/runs", "XDG_STATE_HOME": "/x"}, "/srv/runs"),
        ({"XDG_STATE_HOME": "/x/state"}, "/x/state/autodev"),
        # 相対パスの XDG_STATE_HOME は無効（XDG の仕様）
        ({"XDG_STATE_HOME": "rel"}, str(Path.home() / ".local/state/autodev")),
        ({}, str(Path.home() / ".local/state/autodev")),
    ],
)
def test_ランの置き場はXDG_STATE_HOMEを見て差し替えられる(env: dict[str, str], expected: str):
    assert state_root(env) == Path(expected)


def test_ランディレクトリの中のパス():
    paths = RunPaths.of(RunName("demo"), {"AUTODEV_STATE_DIR": "/s"})
    assert paths.events_db == Path("/s/demo/events.db")
    assert paths.progress_of(execution(3)) == Path("/s/demo/progress/task3-Impl-r0-a1.json")
    assert paths.answer("toolu_1") == Path("/s/demo/answers/toolu_1.json")
    assert paths.task_tree(task(2)) == Path("/s/demo/trees/task2")
    assert paths.task_results(task(2)) == Path("/s/demo/tasks/task2/results")


# --- 本物の集約で再生した欄（status_sections.SECTIONS） ---

RUN = StreamId.run()
PLANNING = TaskId.planning()
GIT = TaskId.git()
T1 = TaskId.numbered(1)
T2 = TaskId.numbered(2)
HEAD = CommitSha("a" * 40)
OVERVIEW = StackEntry(GIT, BranchName("autodev/demo"), PrNumber(4), BranchName("main"))
PLAN = (
    PlannedTask(T1, TaskSpec("パーサを足す")),
    PlannedTask(T2, TaskSpec("CLI に出す"), frozenset({T1})),
)
ARTIFACTS = (ArtifactRef(ArtifactKind.BRIEF, "brief.md"), ArtifactRef(ArtifactKind.DESIGN, "1"))


def ex(task: TaskId, stage: StageKind, round: int = 0) -> ExecutionId:
    return ExecutionId(task, stage, round, 1)


class Seed:
    """イベントの列を、driver を通さずにそのまま events.db へ書く。"""

    def __init__(self, paths: RunPaths) -> None:
        self.store = EventStore.open(paths.events_db)
        self.versions: dict[StreamId, int] = {}
        self.commands = 0

    def __call__(self, stream: StreamId, *events: Event) -> None:
        version = self.versions.get(stream, 0)
        self.commands += 1
        self.store.append(CommandId(f"c{self.commands}"), stream, version, events)
        self.versions[stream] = version + len(events)


def started(name: str = "demo") -> RunStarted:
    return RunStarted(
        RunName(name),
        Instruction("足す"),
        Repository("/repo"),
        BranchName("main"),
        ParallelLimit(2),
    )


def seed_running(seed: Seed) -> None:
    """計画を反映し、task1 が Impl を走らせ、計画タスクが ask で回答を待っているラン。"""
    seed(RUN, started(), TaskStarted(PLANNING, TaskKind.PLANNING), TaskStarted(GIT, TaskKind.GIT))
    seed(StreamId.design(), DesignProposed(Proposal(DesignVersion(1), PLAN)))
    seed(
        StreamId.design(),
        DesignSettled(Proposal(DesignVersion(1), PLAN), ex(PLANNING, StageKind.DESIGN_JUDGE, 1)),
    )
    seed(RUN, TasksPlanned(DesignVersion(1), PLAN, (), ARTIFACTS, replan=False))
    seed(
        RUN,
        TaskStarted(
            T1, TaskKind.IMPLEMENTATION, spec=PLAN[0].spec, branch=BranchName("stack/demo--task-1")
        ),
    )
    seed(StreamId.stack(), OverviewRecorded(OVERVIEW))

    t1 = StreamId.task(T1)
    flow = Flow(
        (
            FlowStep(StageKind.TEST_GEN),
            FlowStep(StageKind.RESOLVE_CONFLICT),
            FlowStep(StageKind.IMPL),
            FlowStep(StageKind.GATE),
        ),
        1,
    )
    seed(
        t1,
        TaskOpened(TaskKind.IMPLEMENTATION, PLAN[0].spec, branch=BranchName("stack/demo--task-1")),
        FlowAccepted(flow),
        StageRequested(ex(T1, StageKind.TEST_GEN), 0),
        StageStarted(ex(T1, StageKind.TEST_GEN), HEAD),
        # ResolveConflict を飛ばした先へ進む
        StageCompleted(ex(T1, StageKind.TEST_GEN), cursor=Cursor(2)),
        StageRequested(ex(T1, StageKind.IMPL), 2),
        StageStarted(ex(T1, StageKind.IMPL), HEAD),
    )

    planning = StreamId.task(PLANNING)
    seed(
        planning,
        TaskOpened(TaskKind.PLANNING),
        FlowAccepted(Flow((FlowStep(StageKind.REPLAN),), 1)),
        StageRequested(ex(PLANNING, StageKind.REPLAN), 0),
        StageStarted(ex(PLANNING, StageKind.REPLAN), HEAD),
        EscalationRaised(
            EscalationKind.ASK, Pointers(), task=PLANNING, origin=ex(PLANNING, StageKind.REPLAN)
        ),
    )
    seed(
        RUN,
        TaskStatusChanged(PLANNING, TaskStatus.RUNNING, TaskStatus.ESCALATED, "EscalationRaised"),
        EscalationRaised(
            EscalationKind.ASK, Pointers(), task=PLANNING, source=EventId("task/planning#5")
        ),
    )
    seed(
        StreamId.questions(),
        QuestionPosted(QuestionId("q1"), "どちらの形にするか", EventId("run#7")),
    )
    seed(
        StreamId.design(),
        DesignProposed(Proposal(DesignVersion(2), PLAN)),
        DesignAmbiguous(ex(PLANNING, StageKind.DESIGN_JUDGE, 1)),
    )


def test_走っているランのタスクと実行と回答待ちを見せる(paths: RunPaths):
    seed_running(Seed(paths))
    write_progress(paths, ex(T1, StageKind.IMPL), {"turns": 7, "lastTool": "Edit"})

    status = run_status(paths)

    assert status["run"].pop("started_at") <= status["updated_at"]
    assert status["run"] == {
        "phase": "running",
        "awaiting_answer": True,
        "repository": "/repo",
        "base": "main",
        "limit": 2,
        "resumes": 0,
        "panic_cause": None,
        "directory": str(paths.root.absolute()),
        "driver_running": False,
        "live_children": [],
    }
    assert [(t["id"], t["kind"], t["status"]) for t in status["tasks"]] == [
        ("planning", "planning", "escalated"),
        ("git", "git", "running"),
        ("task1", "implementation", "running"),
        ("task2", "implementation", "pending"),
    ]
    task1 = status["tasks"][2]
    assert task1["title"] == "パーサを足す"
    assert task1["branch"] == "stack/demo--task-1"
    assert task1["pr"] is None
    assert [(s["stage"], s["state"]) for s in task1["flow"]["steps"]] == [
        ("TestGen", "done"),
        ("ResolveConflict", "skipped"),
        ("Impl", "current"),
        ("Gate", "pending"),
    ]
    assert [(e["id"], e["status"]) for e in task1["executions"]] == [
        ("task1-TestGen-r0-a1", "completed"),
        ("task1-Impl-r0-a1", "running"),
    ]
    running = task1["executions"][1]
    assert running["progress"] == {"turns": 7, "lastTool": "Edit"}
    assert running["started_at"]
    assert status["tasks"][3]["blocked_by"] == ["task1"]
    assert status["tasks"][3]["flow"] is None
    assert status["tasks"][0]["escalations"] == [
        {"id": "task/planning#5", "kind": "ask", "origin": "planning-Replan-r0-a1"}
    ]

    assert status["escalations"] == [
        {
            "id": "run#7",
            "kind": "ask",
            "task": "planning",
            "source": "task/planning#5",
            "for_user": False,
            "answer_only": False,
            "failures": 0,
        }
    ]
    assert status["questions"] == [
        {"id": "q1", "body": "どちらの形にするか", "escalation": "run#7"}
    ]
    assert status["stack"]["overview"] == {
        "task": "git",
        "branch": "autodev/demo",
        "pr": 4,
        "base": "main",
    }
    assert status["stack"]["top"] == "autodev/demo"
    assert status["plan"] == {
        "planning": False,
        "planned": True,
        "applied_design": 1,
        "replans_without_stack": 0,
        "design": {
            "versions": [1, 2],
            "settled": 1,
            "proposal": {
                "version": 2,
                "state": "awaiting",
                "round": 1,
                "awaiting": "design-ambiguous",
            },
        },
    }
    json.dumps(status)


def test_積んだタスクと止めたタスクとランの終わりを見分けられる(paths: RunPaths):
    seed = Seed(paths)
    seed(RUN, started(), TaskStarted(PLANNING, TaskKind.PLANNING), TaskStarted(GIT, TaskKind.GIT))
    seed(RUN, TasksPlanned(DesignVersion(1), PLAN, (), ARTIFACTS, replan=False))
    seed(RUN, TaskStarted(T1, TaskKind.IMPLEMENTATION, spec=PLAN[0].spec))
    seed(
        RUN,
        TaskStatusChanged(T1, TaskStatus.RUNNING, TaskStatus.GATED, "TaskGated"),
        TaskStatusChanged(T1, TaskStatus.GATED, TaskStatus.STACKING, "GitJobTaken"),
        TaskMarkedStacked(T1, PrNumber(5)),
        TasksStopped(frozenset({T2})),
        RunFinished(ready_overview=True),
    )
    entry = StackEntry(T1, BranchName("stack/demo--task-1"), PrNumber(5), OVERVIEW.branch)
    seed(StreamId.stack(), OverviewRecorded(OVERVIEW), TaskStacked(entry))

    status = run_status(paths)
    # git 管理タスクの仕上げが残っている
    assert status["run"]["phase"] == "finishing"
    by_id = {t["id"]: t for t in status["tasks"]}
    assert (by_id["task1"]["status"], by_id["task1"]["pr"]) == ("stacked", 5)
    assert (by_id["task2"]["status"], by_id["task2"]["terminal"]) == ("dropped", True)
    assert by_id["planning"]["status"] == "finished"
    assert status["stack"]["entries"] == [
        {"task": "task1", "branch": "stack/demo--task-1", "pr": 5, "base": "autodev/demo"}
    ]

    seed(RUN, TaskStatusChanged(GIT, TaskStatus.RUNNING, TaskStatus.FINISHED, "FlowFinished"))
    assert run_status(paths)["run"]["phase"] == "finished"


def test_パニックしたランは終えた後でもパニックと見せる(paths: RunPaths):
    seed = Seed(paths)
    seed(RUN, started(), TaskStarted(GIT, TaskKind.GIT))
    assert run_status(paths)["run"]["phase"] == "planning"
    assert run_status(paths)["run"]["panic_cause"] is None
    seed(RUN, RunFinished(ready_overview=False), RunPanicked("利用枠"))
    run = run_status(paths)["run"]
    assert (run["phase"], run["panic_cause"]) == ("panicked", "利用枠")
    # 呼び直したら、もうパニックしていない
    seed(RUN, RunResumed((GIT,), after_panic=True))
    run = run_status(paths)["run"]
    assert (run["phase"], run["panic_cause"]) == ("finishing", None)


def test_StartRunを拒まれてイベントが無いランも読める(paths: RunPaths):
    EventStore.open(paths.events_db).close()
    status = run_status(paths)
    assert (status["last_seq"], status["updated_at"]) == (0, None)
    assert status["run"]["phase"] == "not-started"
    assert (status["tasks"], status["stack"]["overview"], status["plan"]["design"]["proposal"]) == (
        [],
        None,
        None,
    )


def test_全ランをラン名の順に返し読めないランはerrorで残す(tmp_path: Path):
    env = {"AUTODEV_STATE_DIR": str(tmp_path)}
    for name in ("zeta", "alpha"):
        Seed(RunPaths.of(RunName(name), env))(RUN, started(name))
    # events.db の無い所はランではない
    (tmp_path / "pr-body-markers").mkdir()
    (tmp_path / "Bad_Name").mkdir()
    (tmp_path / "Bad_Name" / "events.db").write_bytes(b"")
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "events.db").write_bytes(b"not sqlite")

    found = all_statuses(env)
    assert [(s["name"], "error" in s) for s in found] == [
        ("Bad_Name", True),
        ("alpha", False),
        ("broken", True),
        ("zeta", False),
    ]
    assert found[0]["error"].startswith("InvalidValue: ")
    assert all_statuses({"AUTODEV_STATE_DIR": str(tmp_path / "none")}) == []


def test_欄を作る所の不具合では全ランをerrorで残して警告する(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    env = {"AUTODEV_STATE_DIR": str(tmp_path)}
    for name in ("alpha", "beta"):
        Seed(RunPaths.of(RunName(name), env))(RUN, started(name))

    def broken_section(view: Replayed) -> None:
        raise KeyError("x")

    monkeypatch.setitem(status_sections.SECTIONS, "run", broken_section)

    assert all_statuses(env) == [
        {"name": "alpha", "error": "KeyError: 'x'"},
        {"name": "beta", "error": "KeyError: 'x'"},
    ]
    assert [r.levelname for r in caplog.records] == ["WARNING", "WARNING"]


def test_型の違う集約を空の集約に置き換えない(paths: RunPaths):
    loop = MainLoop(
        EventStore.open(paths.events_db), factory, [], on_rejected=RejectionLog(paths.rejections)
    )
    loop.process(enqueue(1, "c1"))
    with pytest.raises(TypeError, match="Queue"):
        build_status(paths, factory, {"stack": lambda view: view.stack})


def test_書き直す前のフローで走っている実行はフローの版で見分けられる(paths: RunPaths):
    seed = Seed(paths)
    seed(RUN, started(), TaskStarted(T1, TaskKind.IMPLEMENTATION, spec=PLAN[0].spec))
    old = ex(T1, StageKind.IMPL)
    seed(
        StreamId.task(T1),
        TaskOpened(TaskKind.IMPLEMENTATION, PLAN[0].spec),
        FlowAccepted(
            Flow((FlowStep(StageKind.TEST_GEN), FlowStep(StageKind.IMPL)), 1), cursor=Cursor(1)
        ),
        StageRequested(old, 1),
        StageStarted(old, HEAD),
        FlowAccepted(Flow((FlowStep(StageKind.TEST_GEN),), 2)),
        StageRequested(ex(T1, StageKind.TEST_GEN), 0),
        StageCompleted(ex(T1, StageKind.TEST_GEN), cursor=Cursor(1)),
    )
    # 完了した実行の進み具合のファイルが残っていても見せない
    write_progress(paths, old, {"turns": 3})
    write_progress(paths, ex(T1, StageKind.TEST_GEN), {"turns": 9})

    (task1,) = run_status(paths)["tasks"]
    assert [
        (e["id"], e["flow_version"], e["step"], e["progress"]) for e in task1["executions"]
    ] == [
        ("task1-Impl-r0-a1", 1, 1, {"turns": 3}),
        ("task1-TestGen-r0-a1", 2, 0, None),
    ]
    assert task1["flow"]["version"] == 2


#: 錠を握ったら `held` を 1 行書き、標準入力が閉じるまで握り続ける
_HOLD_LOCK = """
import fcntl, os, sys
fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o644)
fcntl.flock(fd, fcntl.LOCK_EX)
print("held", flush=True)
sys.stdin.read()
"""


@pytest.fixture
def holder(paths: RunPaths) -> Iterator[subprocess.Popen[str]]:
    """`driver.lock` を握っている、別のプロセス（同じプロセスの flock では driver の代わりにならない）。"""
    paths.root.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(
        [sys.executable, "-c", _HOLD_LOCK, str(paths.driver_lock)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None and process.stdout.readline() == "held\n"
    yield process
    assert process.stdin is not None
    process.stdin.close()
    process.wait()


def test_ほかのプロセスが錠を握っていればdriverが走っていると見せる(
    paths: RunPaths, holder: subprocess.Popen[str]
):
    Seed(paths)(RUN, started())
    assert run_status(paths)["run"]["driver_running"] is True
    assert holder.stdin is not None
    holder.stdin.close()
    holder.wait()
    assert run_status(paths)["run"]["driver_running"] is False


def test_錠を確かめても錠のファイルを作らず錠を残さない(paths: RunPaths):
    Seed(paths)(RUN, started())
    assert run_status(paths)["run"]["driver_running"] is False
    assert not paths.driver_lock.exists()
    with DriverLock(paths.driver_lock):
        pass
    assert run_status(paths)["run"]["driver_running"] is False
    with DriverLock(paths.driver_lock):
        pass


@pytest.fixture
def sleeper(paths: RunPaths) -> Iterator[int]:
    """前の driver が控えを置いたまま残した子。"""
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    children.track_in(paths.children)
    try:
        children.record(child.pid, ["python", "-c", "sleep"])
    finally:
        children.track_in(None)
    yield child.pid
    child.kill()
    child.wait()


def test_driverが止まっていて前の子が生きていればそのpidを見せる(paths: RunPaths, sleeper: int):
    Seed(paths)(RUN, started())
    run = run_status(paths)["run"]
    assert (run["driver_running"], run["live_children"]) == (False, [sleeper])


def test_driverが走っていれば生きている子を前の子に数えない(
    paths: RunPaths, sleeper: int, holder: subprocess.Popen[str]
):
    Seed(paths)(RUN, started())
    run = run_status(paths)["run"]
    assert (run["driver_running"], run["live_children"]) == (True, [])
