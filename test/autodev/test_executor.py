"""実行器（`app/executor.py`）。偽の AgentRuntime と、tmp_path の本物の git リポジトリで確かめる。

実行器が返したコマンドを本物の Task 集約に通し、集めた証拠から Task が決めた結果（完了・失敗・
エスカレーション）までを見る。実行器は判断しないので、結果を変えるのは証拠だけである。
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest
from autodev_harness import POLICY, SESSION, new_id, of_type
from autodevlib.adapters.agent_runtime import AgentCall, DeferredToolUse, Ending, Progress
from autodevlib.adapters.guard import GUARD_ENV
from autodevlib.app import executor as executor_module
from autodevlib.app.stage_context import ResumeMode
from autodevlib.domain.commands import (
    AcceptFlow,
    InterruptStage,
    OpenTask,
    Panic,
    ReportBeginFailure,
    ReportStageResult,
    ResolveEscalation,
    ResumeStage,
)
from autodevlib.domain.events import (
    EscalationRaised,
    ExecutionRestarted,
    StageCompleted,
    StageDeferred,
    StageFailed,
    StageRequested,
    StageStarted,
)
from autodevlib.domain.flow import FlowStep, Reviewers, planning_flow
from autodevlib.domain.values import (
    ArtifactKind,
    ArtifactRef,
    BranchName,
    EscalationKind,
    ExecutionId,
    InterruptCause,
    Issuer,
    StageExit,
    StageKind,
    StreamId,
    TaskId,
    TaskKind,
    TaskSpec,
    VerifyCommand,
)
from conftest import SKILL_ROOT
from executor_fakes import Env, FakeProcess, commit, make_env, outcome, sh

S = StageKind
A = ArtifactKind
T1 = TaskId("task1")
BRANCH = BranchName("stack/r--task-1")
OVERVIEW = BranchName("stack/r--task-0")
PLANNING = TaskId.planning()
IMPL_FLOW = (
    FlowStep(S.IMPL),
    FlowStep(S.REVIEW_LOOP, reviewers=Reviewers((S.REVIEW,))),
    FlowStep(S.GATE),
    FlowStep(S.WRITE_PR_BODY),
)
IMPL_RESULT = {
    "report": None,
    "reportReason": None,
    "unchanged": False,
    "comments": [],
    "notes": "",
}
PLAN_RESULT = {
    "design": "# 設計\n",
    "codemap": "# コードマップ\n",
    "tasks": [],
    "verify": [],
    "decisions": [],
    "deferrals": [],
}


def ex(stage: StageKind, round: int = 0, attempt: int = 1, task: TaskId = T1) -> ExecutionId:
    return ExecutionId(task, stage, round, attempt)


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Env:
    return make_env(tmp_path, monkeypatch)


def impl_task(
    env: Env,
    *steps: FlowStep,
    verify: tuple[str, ...] = (),
    artifacts: tuple[ArtifactRef, ...] = (),
) -> list:
    """trees/task1 を切り、実装タスクを開いてフローを受け入れる。"""
    env.git.create_branch(BRANCH, "main")
    env.git.add_worktree(env.paths.task_tree(T1), BRANCH)
    env.world(
        OpenTask(
            command_id=new_id(),
            issuer=POLICY,
            task=T1,
            kind=TaskKind.IMPLEMENTATION,
            spec=TaskSpec("キャッシュ", verify=tuple(VerifyCommand(v) for v in verify)),
            artifacts=(ArtifactRef(A.DESIGN, "1"), *artifacts),
            branch=BRANCH,
        )
    )
    return env.world(
        AcceptFlow(
            command_id=new_id(),
            issuer=Issuer.task_supervisor(T1, SESSION),
            task=T1,
            steps=steps or IMPL_FLOW,
        )
    )


def planning_task(env: Env) -> list:
    env.git.create_branch(OVERVIEW, "origin/main")
    env.git.add_worktree(env.paths.overview_tree, OVERVIEW)
    env.world(OpenTask(command_id=new_id(), issuer=POLICY, task=PLANNING, kind=TaskKind.PLANNING))
    return env.world(
        AcceptFlow(
            command_id=new_id(),
            issuer=Issuer.task_supervisor(PLANNING),
            task=PLANNING,
            steps=planning_flow(settled_before=False),
        )
    )


def commits_and_returns(result: dict, path: str = "src/cache.py"):
    def behave(call: AgentCall, process: FakeProcess):
        commit(Path(call.cwd), path, f"{time.time()}\n")
        return outcome(call, result)

    return behave


def reports(env: Env) -> list[ReportStageResult]:
    return [c for c in env.world.submitted() if isinstance(c, ReportStageResult)]


# --- LLM のステージ ---


def test_Implはworktreeで起動しコミットを実物にして完了する(env: Env):
    events = impl_task(env)
    assert [e.execution for e in of_type(events, StageRequested)] == [ex(S.IMPL)]
    head = sh(env.paths.task_tree(T1), "rev-parse", "HEAD").strip()
    started = of_type(env.begin(ex(S.IMPL)), StageStarted)
    assert started[0].session == env.sessions[0]
    assert str(started[0].start_commit) == head

    env.runtime.behaviors.append(commits_and_returns(IMPL_RESULT))
    events = env.run(ex(S.IMPL))

    (completed,) = of_type(events, StageCompleted)
    new_head = sh(env.paths.task_tree(T1), "rev-parse", "HEAD").strip()
    assert completed.produced == (ArtifactRef(A.IMPL, new_head),)
    (call,) = env.runtime.calls
    assert call.cwd == str(env.paths.task_tree(T1))
    assert call.resume is False and call.session == env.sessions[0]
    assert call.prompt == f"プロンプト {ex(S.IMPL)}" and call.system_append == "必須ルール"
    assert json.loads(call.json_schema or "") == json.loads(
        (SKILL_ROOT / "schemas" / "impl.json").read_text(encoding="utf-8")
    )
    assert call.settings == str(env.paths.guard) and env.paths.guard.is_file()
    guard = json.loads(call.env[GUARD_ENV])
    assert guard["guard"]["writes"] == "non-tests"
    assert guard["context"]["tree"] == str(env.paths.task_tree(T1))
    assert call.withhold_github is True
    assert call.model == "opus"
    # 結果の JSON は results/ に残り、進み具合は消える
    assert (env.paths.task_results(T1) / f"{ex(S.IMPL)}.json").is_file()
    assert not any(env.paths.progress.glob("*.json"))


def test_コミットしなかったImplは実物が無いので失敗になる(env: Env):
    impl_task(env)
    env.begin(ex(S.IMPL))
    env.runtime.behaviors.append(lambda call, p: outcome(call, IMPL_RESULT))
    (failed,) = of_type(env.run(ex(S.IMPL)), StageFailed)
    assert "impl の実物が無い" in failed.reason


def test_報告の欄の文字列のnullはnullに直してから形を確かめる(env: Env):
    impl_task(env)
    env.begin(ex(S.IMPL))
    env.runtime.behaviors.append(
        commits_and_returns({**IMPL_RESULT, "report": "null", "reportReason": "none"})
    )
    assert of_type(env.run(ex(S.IMPL)), StageCompleted)


def test_スキーマの形に合わない結果は形の誤りとして渡す(env: Env):
    impl_task(env)
    env.begin(ex(S.IMPL))
    broken = {k: v for k, v in IMPL_RESULT.items() if k != "notes"}
    env.runtime.behaviors.append(commits_and_returns(broken))
    env.executor.run(ex(S.IMPL), env.world.inbox.expect(ex(S.IMPL)))
    env.executor.join()
    (report,) = reports(env)
    assert report.evidence.result_valid is False and report.result is None
    assert "notes が無い" in (report.evidence.error or "")
    # 形が違っても、調べる先として結果は残す
    assert (env.paths.task_results(T1) / f"{ex(S.IMPL)}.json").is_file()


def test_利用枠の上限に当たったら証拠ではなくPanicを返す(env: Env):
    impl_task(env)
    env.begin(ex(S.IMPL))
    env.runtime.behaviors.append(
        lambda call, p: outcome(call, None, is_error=True, rate_limited=True, text="usage limit")
    )
    env.executor.run(ex(S.IMPL), env.world.inbox.expect(ex(S.IMPL)))
    env.executor.join()
    (panic,) = env.world.submitted()
    assert isinstance(panic, Panic) and "利用枠の上限" in panic.cause


def test_フックに止められ続けたら打ち切ってエラーの証拠にする(env: Env):
    impl_task(env)
    env.begin(ex(S.IMPL))

    def behave(call: AgentCall, process: FakeProcess):
        assert process.on_progress is not None
        process.on_progress(Progress(turns=3, last_tool="Bash", hook_denials=11, events=40))
        return outcome(
            call,
            None,
            is_error=True,
            subtype="error_during_execution",
            interrupted=process.interrupted,
            hook_denials=11,
        )

    env.runtime.behaviors.append(behave)
    env.executor.run(ex(S.IMPL), env.world.inbox.expect(ex(S.IMPL)))
    env.executor.join()
    (report,) = reports(env)
    assert report.evidence.exit is StageExit.ERROR
    assert report.evidence.hook_denials == 11
    assert "フックに 11 回止められた" in (report.evidence.error or "")


# --- 計画ステージの ask（defer）と再開 ---


def test_deferで止まった計画ステージは回答の後にプロンプト無しで続ける(env: Env):
    planning_task(env)
    env.begin(ex(S.PREPARE, task=PLANNING))
    events = env.run(ex(S.PREPARE, task=PLANNING))
    assert of_type(events, StageCompleted)[0].produced == (ArtifactRef(A.BRIEF, "brief.md"),)
    assert "キャッシュを足す" in env.paths.brief.read_text(encoding="utf-8")
    plan = ex(S.PLAN, task=PLANNING)
    env.begin(plan)

    ask = DeferredToolUse(
        "toolu_01", "Bash", {"command": "/s/autodev.py ask --question 'どちら？'"}
    )
    env.runtime.behaviors.append(
        lambda call, p: outcome(call, None, stop_reason="tool_deferred", deferred=ask)
    )
    events = env.run(plan)
    (deferred,) = of_type(events, StageDeferred)
    assert deferred.tool_use_id == "toolu_01"
    (raised,) = of_type(events, EscalationRaised)
    assert raised.kind is EscalationKind.ASK and raised.reason == "どちら？"

    escalation = next(iter(env.world.task(StreamId.task(PLANNING)).escalations))
    env.world(
        ResolveEscalation(
            command_id=new_id(), issuer=POLICY, task=PLANNING, escalation=escalation, answer="A"
        )
    )
    env.world(
        ResumeStage(
            command_id=new_id(),
            issuer=Issuer.reaction("r", escalation),
            task=PLANNING,
            execution=plan,
        )
    )
    env.runtime.behaviors.append(lambda call, p: outcome(call, PLAN_RESULT))
    events = env.run(plan)

    first, second = env.runtime.calls
    assert first.resume is False and first.prompt is not None
    assert second.resume is True and second.prompt is None and second.session == first.session
    assert first.cwd == str(env.paths.overview_tree)
    (completed,) = of_type(events, StageCompleted)
    assert set(completed.produced) == {
        ArtifactRef(A.PROPOSAL, "1"),
        ArtifactRef(A.CODEMAP, "codemap.md"),
    }
    assert (env.paths.root / "design" / "v1.md").read_text(encoding="utf-8") == "# 設計\n"
    assert env.paths.codemap.read_text(encoding="utf-8") == "# コードマップ\n"
    # 書く範囲が無い計画ステージにも ask の許しが渡る
    guard = json.loads(second.env[GUARD_ENV])["guard"]
    assert guard == {"writes": "none", "judge": False, "readsDesign": False, "canAsk": True}


def test_止めた実行は結果を返さず再開すると続きの指示を渡す(env: Env):
    impl_task(env)
    env.begin(ex(S.IMPL))
    running = threading.Event()

    def blocks(call: AgentCall, process: FakeProcess):
        running.set()
        process.stop.wait(10)
        return outcome(call, None, is_error=True, interrupted=process.interrupted)

    env.runtime.behaviors.append(blocks)
    env.executor.run(ex(S.IMPL), env.world.inbox.expect(ex(S.IMPL)))
    assert running.wait(10)
    env.world(
        InterruptStage(
            command_id=new_id(),
            issuer=POLICY,
            task=T1,
            execution=ex(S.IMPL),
            cause=InterruptCause.PANIC,
        )
    )
    env.executor.interrupt(ex(S.IMPL))
    env.executor.join()
    assert env.runtime.processes[0].interrupted == "driver が止めた"
    assert env.world.submitted() == []
    assert env.world.inbox.outstanding == 0

    env.world(
        ResumeStage(command_id=new_id(), issuer=Issuer.driver(), task=T1, execution=ex(S.IMPL))
    )
    env.runtime.behaviors.append(commits_and_returns(IMPL_RESULT))
    assert of_type(env.run(ex(S.IMPL)), StageCompleted)
    resumed = env.runtime.calls[1]
    assert resumed.resume is True and resumed.prompt == "止めたところから続けて"
    assert resumed.session == env.runtime.calls[0].session
    assert env.prompts.calls[-1][1].resume is ResumeMode.INTERRUPTED


def test_再開に失敗したら作り直しを頼みworktreeを始めた時点に戻す(env: Env):
    impl_task(env)
    (started,) = of_type(env.begin(ex(S.IMPL)), StageStarted)
    tree = env.paths.task_tree(T1)
    env.world(
        InterruptStage(
            command_id=new_id(),
            issuer=POLICY,
            task=T1,
            execution=ex(S.IMPL),
            cause=InterruptCause.PANIC,
        )
    )
    env.world(
        ResumeStage(command_id=new_id(), issuer=Issuer.driver(), task=T1, execution=ex(S.IMPL))
    )

    def lost(call: AgentCall, process: FakeProcess):
        commit(tree, "half.py", "途中\n")
        (tree / "junk.txt").write_text("x", encoding="utf-8")
        return outcome(call, None, ending=Ending.NO_RESULT, exit_code=1, stderr="No conversation")

    env.runtime.behaviors.append(lost)
    events = env.run(ex(S.IMPL))
    (restarted,) = of_type(events, ExecutionRestarted)
    assert restarted.start_commit == started.start_commit
    assert [e.execution for e in of_type(events, StageRequested)] == [ex(S.IMPL, attempt=2)]

    env.executor.restart(ex(S.IMPL), restarted.start_commit)
    (again,) = of_type(env.begin(ex(S.IMPL, attempt=2)), StageStarted)
    assert again.start_commit == started.start_commit
    assert not (tree / "junk.txt").exists() and not (tree / "half.py").exists()
    # 続けられなかったセッションは使わない
    assert again.session == env.sessions[1]


def lost_session(call: AgentCall, process: FakeProcess):
    return outcome(call, None, ending=Ending.NO_RESULT, exit_code=1, stderr="No conversation")


def test_続けたセッションで始めた後に落ちたら続けられなかったとは数えず失敗にする(env: Env):
    impl_task(env)
    env.begin(ex(S.IMPL))
    env.world(
        InterruptStage(
            command_id=new_id(),
            issuer=POLICY,
            task=T1,
            execution=ex(S.IMPL),
            cause=InterruptCause.PANIC,
        )
    )
    env.world(
        ResumeStage(command_id=new_id(), issuer=Issuer.driver(), task=T1, execution=ex(S.IMPL))
    )
    env.runtime.behaviors.append(
        lambda call, p: outcome(
            call, None, ending=Ending.NO_RESULT, exit_code=1, stderr="落ちた", initialized=True
        )
    )
    events = env.run(ex(S.IMPL))
    assert env.runtime.calls[-1].resume is True
    assert of_type(events, StageFailed) and not of_type(events, ExecutionRestarted)


def test_作り直しの反応が戻す前に落ちても次の試みは始める前に戻す(env: Env):
    impl_task(env)
    (started,) = of_type(env.begin(ex(S.IMPL)), StageStarted)
    tree = env.paths.task_tree(T1)
    env.stage_log(ex(S.IMPL)).parent.mkdir(parents=True)
    env.stage_log(ex(S.IMPL)).write_text("{}\n", encoding="utf-8")

    def lost(call: AgentCall, process: FakeProcess):
        commit(tree, "half.py", "途中\n")
        return lost_session(call, process)

    env.runtime.behaviors.append(lost)
    assert of_type(env.run(ex(S.IMPL)), ExecutionRestarted)
    # restart を呼ばない（反応が戻す前に driver が落ちた）
    (again,) = of_type(env.begin(ex(S.IMPL, attempt=2)), StageStarted)
    assert again.start_commit == started.start_commit
    assert not (tree / "half.py").exists()


def test_もう起こした跡がある初めての実行は続きとして起こし続けられなければ作り直す(env: Env):
    impl_task(env)
    env.begin(ex(S.IMPL))
    # 前の driver がこの実行の claude をもう起こしていた（配り直した StageStarted で走らせ直す）
    log_path = env.stage_log(ex(S.IMPL))
    log_path.parent.mkdir(parents=True)
    log_path.write_text("{}\n", encoding="utf-8")
    env.runtime.behaviors.append(lost_session)
    events = env.run(ex(S.IMPL))
    (call,) = env.runtime.calls
    assert call.resume is True and call.prompt == "止めたところから続けて"
    # Task の resumed_from は無いが、走ったままにせず作り直す
    assert of_type(events, ExecutionRestarted)
    assert [e.execution for e in of_type(events, StageRequested)] == [ex(S.IMPL, attempt=2)]


def test_前のセッションを続ける初めての実行が続けられなければ作り直す(env: Env):
    impl_task(env)
    env.begin(ex(S.IMPL))
    env.runtime.behaviors.append(lambda call, p: outcome(call, {"broken": True}))
    assert of_type(env.run(ex(S.IMPL)), StageFailed)
    (started,) = of_type(env.begin(ex(S.IMPL, attempt=2)), StageStarted)
    assert started.session == env.sessions[0]
    env.runtime.behaviors.append(lost_session)
    events = env.run(ex(S.IMPL, attempt=2))
    assert env.runtime.calls[1].resume is True and env.runtime.calls[1].prompt is not None
    assert of_type(events, ExecutionRestarted)
    (third,) = of_type(env.begin(ex(S.IMPL, attempt=3)), StageStarted)
    assert third.session == env.sessions[1]


def test_プロンプトを組めなくても札を返し失敗はTaskが決める(env: Env):
    impl_task(env)
    env.begin(ex(S.IMPL))

    def broken(context, aggregates):
        raise ValueError("設計ファイルが読めない")

    env.prompts.prompt = broken  # ty: ignore[invalid-assignment]
    events = env.run(ex(S.IMPL))
    (failed,) = of_type(events, StageFailed)
    assert "設計ファイルが読めない" in failed.reason
    assert env.world.inbox.outstanding == 0
    assert env.executor._live == {}
    assert env.runtime.calls == []


def test_事実を写し取れなければ札を返す(env: Env, monkeypatch: pytest.MonkeyPatch):
    impl_task(env)

    def broken(*args, **kwargs):
        raise RuntimeError("壊れた集約")

    monkeypatch.setattr("autodevlib.app.executor.snapshot", broken)
    ticket = env.world.inbox.expect(ex(S.IMPL))
    env.executor.begin(ex(S.IMPL), ticket)
    assert ticket.used and env.world.submitted() == []
    ticket = env.world.inbox.expect(ex(S.IMPL))
    env.executor.run(ex(S.IMPL), ticket)
    assert ticket.used and env.world.submitted() == []
    assert env.world.inbox.outstanding == 0


def test_止めた決定的なステージは検証コマンドまで止め結果を返さない(env: Env):
    tree = env.paths.task_tree(T1)
    impl_task(
        env,
        FlowStep(S.CONFIRM_RED),
        *IMPL_FLOW,
        verify=("touch started; sleep 30",),
        artifacts=(ArtifactRef(A.TESTS, "a" * 40),),
    )
    env.begin(ex(S.CONFIRM_RED))
    begun = time.monotonic()
    env.executor.run(ex(S.CONFIRM_RED), env.world.inbox.expect(ex(S.CONFIRM_RED)))
    while not (tree / "started").exists():
        assert time.monotonic() - begun < 10
        time.sleep(0.01)
    env.world(
        InterruptStage(
            command_id=new_id(),
            issuer=POLICY,
            task=T1,
            execution=ex(S.CONFIRM_RED),
            cause=InterruptCause.REQUESTED,
        )
    )
    env.executor.interrupt(ex(S.CONFIRM_RED))
    env.executor.join(15)
    assert time.monotonic() - begun < 15
    assert env.world.submitted() == [] and env.world.inbox.outstanding == 0
    assert env.executor._live == {}


def test_同じworktreeの次のbeginは止めた実行が終わるまで待つ(env: Env):
    impl_task(env)
    tree = env.paths.task_tree(T1)
    running = threading.Event()
    finished: list[float] = []

    def blocks(call: AgentCall, process: FakeProcess):
        running.set()
        process.stop.wait(10)
        time.sleep(0.3)  # 止めてから終わるまでに間がある
        finished.append(time.monotonic())
        return outcome(call, None, is_error=True, interrupted=process.interrupted)

    env.begin(ex(S.IMPL))
    env.runtime.behaviors.append(blocks)
    env.executor.run(ex(S.IMPL), env.world.inbox.expect(ex(S.IMPL)))
    assert running.wait(10)
    env.executor.interrupt(ex(S.IMPL))
    env.executor._wait_stopped(tree)
    waited = time.monotonic()
    assert finished and finished[0] <= waited
    assert ex(S.IMPL) not in env.executor._live


def stuck_stop(env: Env, tree: Path, *, finished: bool = False) -> executor_module._Live:
    """`tree` で止めたことにした実行（`finished` でなければ、いつまでも終わらない）。"""
    live = executor_module._Live(tree, stopped=True)
    if finished:
        live.done.set()
    env.executor._stopping.append(live)
    return live


def test_止めた実行を待つのはそのworktreeだけでほかのworktreeのbeginは待たせない(
    env: Env, monkeypatch: pytest.MonkeyPatch
):
    impl_task(env)
    monkeypatch.setattr(executor_module, "STOP_WAIT_SECONDS", 5.0)
    stuck_stop(env, env.paths.task_tree(TaskId("task2")))
    begun = time.monotonic()
    assert of_type(env.begin(ex(S.IMPL)), StageStarted)
    assert time.monotonic() - begun < 2


def test_止めた実行が終わらなければbeginは始めずに待ち直し使い切ったら始められなかったと渡す(
    env: Env, monkeypatch: pytest.MonkeyPatch
):
    impl_task(env)
    monkeypatch.setattr(executor_module, "STOP_WAIT_SECONDS", 0.05)
    stuck_stop(env, env.paths.task_tree(T1))
    ticket = env.world.inbox.expect(ex(S.IMPL))
    env.executor.begin(ex(S.IMPL), ticket)
    deadline = time.monotonic() + 5
    while not ticket.used:
        assert time.monotonic() < deadline
        time.sleep(0.01)
    (command,) = env.world.submitted()
    assert isinstance(command, ReportBeginFailure)
    assert "止めた実行" in command.error
    events = env.world(command)
    assert not of_type(events, StageStarted)
    (failed,) = of_type(events, StageFailed)
    assert failed.reason.startswith("始められなかった")
    assert [e.execution for e in of_type(events, StageRequested)] == [ex(S.IMPL, attempt=2)]


def test_止めた実行が終わったと確かめたら残ったindex_lockを消す(env: Env):
    impl_task(env)
    tree = env.paths.task_tree(T1)
    lock = tree / sh(tree, "rev-parse", "--git-path", "index.lock").strip()
    lock.write_text("", encoding="utf-8")
    stuck_stop(env, tree, finished=True)
    assert of_type(env.begin(ex(S.IMPL)), StageStarted)
    assert not lock.exists()


def test_止めた実行が無ければindex_lockを消さない(env: Env):
    impl_task(env)
    tree = env.paths.task_tree(T1)
    lock = tree / sh(tree, "rev-parse", "--git-path", "index.lock").strip()
    lock.write_text("", encoding="utf-8")
    assert of_type(env.begin(ex(S.IMPL)), StageStarted)
    assert lock.exists()


def test_beginでHEADを取れなければ始められなかったとTaskに渡しTaskがやり直す(
    env: Env, monkeypatch: pytest.MonkeyPatch
):
    impl_task(env)

    def broken(context):
        raise RuntimeError("worktree が壊れた")

    monkeypatch.setattr(env.executor, "_head", broken)
    events = env.begin(ex(S.IMPL))
    (failed,) = of_type(events, StageFailed)
    assert "worktree が壊れた" in failed.reason
    assert [e.execution for e in of_type(events, StageRequested)] == [ex(S.IMPL, attempt=2)]
    assert env.world.inbox.outstanding == 0


def test_止めた実行が走り終える前に同じ実行を再開しても走らせる(env: Env):
    impl_task(env)
    env.begin(ex(S.IMPL))
    running = threading.Event()
    ended: list[float] = []

    def blocks(call: AgentCall, process: FakeProcess):
        running.set()
        process.stop.wait(10)
        time.sleep(0.2)  # 止めてから終わるまでに間がある
        ended.append(time.monotonic())
        return outcome(call, None, is_error=True, interrupted=process.interrupted)

    env.runtime.behaviors.append(blocks)
    env.executor.run(ex(S.IMPL), env.world.inbox.expect(ex(S.IMPL)))
    assert running.wait(10)
    env.world(
        InterruptStage(
            command_id=new_id(),
            issuer=POLICY,
            task=T1,
            execution=ex(S.IMPL),
            cause=InterruptCause.PANIC,
        )
    )
    env.executor.interrupt(ex(S.IMPL))
    # 止めた走りが終わるのを待たずに再開する
    env.world(
        ResumeStage(command_id=new_id(), issuer=Issuer.driver(), task=T1, execution=ex(S.IMPL))
    )
    started: list[float] = []

    def resumed(call: AgentCall, process: FakeProcess):
        started.append(time.monotonic())
        return commits_and_returns(IMPL_RESULT)(call, process)

    env.runtime.behaviors.append(resumed)
    events = env.run(ex(S.IMPL))
    assert of_type(events, StageCompleted)
    # 同じ worktree で並べて走らせない
    assert ended and started and ended[0] <= started[0]


def test_配り直しで済んだ実行をもう一度頼まれても走らせない(env: Env):
    impl_task(env)
    env.begin(ex(S.IMPL))
    ticket = env.world.inbox.expect(ex(S.IMPL))
    env.executor.begin(ex(S.IMPL), ticket)
    env.executor.join()
    assert ticket.used and env.world.submitted() == []


def test_ConfirmRedはタスクのverifyが落ちれば完了し全部通ればred_check_failedを上げる(env: Env):
    steps = (FlowStep(S.CONFIRM_RED), *IMPL_FLOW)
    env.git.create_branch(BRANCH, "main")
    env.git.add_worktree(env.paths.task_tree(T1), BRANCH)
    for verify, expected in (("exit 1", StageCompleted), ("true", EscalationRaised)):
        task = TaskId("task1") if verify == "exit 1" else TaskId("task2")
        if task != T1:
            env.git.create_branch(BranchName("stack/r--task-2"), "main")
            env.git.add_worktree(env.paths.task_tree(task), BranchName("stack/r--task-2"))
        env.world(
            OpenTask(
                command_id=new_id(),
                issuer=POLICY,
                task=task,
                kind=TaskKind.IMPLEMENTATION,
                spec=TaskSpec("x", verify=(VerifyCommand(verify),)),
                artifacts=(ArtifactRef(A.DESIGN, "1"), ArtifactRef(A.TESTS, "a" * 40)),
                branch=BRANCH,
            )
        )
        env.world(
            AcceptFlow(
                command_id=new_id(),
                issuer=Issuer.task_supervisor(task, SESSION),
                task=task,
                steps=steps,
            )
        )
        execution = ex(S.CONFIRM_RED, task=task)
        assert of_type(env.begin(execution), StageStarted)[0].session is None
        events = env.run(execution)
        assert of_type(events, expected), events
        if expected is EscalationRaised:
            assert of_type(events, EscalationRaised)[0].kind is EscalationKind.RED_CHECK_FAILED
