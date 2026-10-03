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
from autodevlib.domain.commands.run import Panic
from autodevlib.domain.commands.task import (
    AcceptFlow,
    InterruptStage,
    OpenTask,
    ReportBeginFailure,
    ReportStageResult,
    ResolveEscalation,
    ResumeStage,
)
from autodevlib.domain.events.run import EscalationRaised
from autodevlib.domain.events.task import (
    ExecutionRestarted,
    StageCompleted,
    StageDeferred,
    StageFailed,
    StageRequested,
    StageStarted,
)
from autodevlib.domain.flow.flow import FlowStep, Reviewers
from autodevlib.domain.flow.standard import git_job_flow, planning_flow
from autodevlib.domain.value_objects.artifact_kind import ArtifactKind
from autodevlib.domain.value_objects.artifact_ref import ArtifactRef
from autodevlib.domain.value_objects.branch_name import BranchName
from autodevlib.domain.value_objects.escalation_kind import EscalationKind
from autodevlib.domain.value_objects.execution_id import ExecutionId
from autodevlib.domain.value_objects.git_job import GitJob
from autodevlib.domain.value_objects.git_job_kind import GitJobKind
from autodevlib.domain.value_objects.interrupt_cause import InterruptCause
from autodevlib.domain.value_objects.issuer import Issuer
from autodevlib.domain.value_objects.stage_exit import StageExit
from autodevlib.domain.value_objects.stage_kind import StageKind
from autodevlib.domain.value_objects.stream_id import StreamId
from autodevlib.domain.value_objects.task_id import TaskId
from autodevlib.domain.value_objects.task_kind import TaskKind
from autodevlib.domain.value_objects.task_spec import TaskSpec
from autodevlib.domain.value_objects.verify_command import VerifyCommand
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


def rewrite_overview(env: Env) -> ExecutionId:
    """概要 PR を書き直す仕事を git 管理タスクに渡し、WriteOverview を始める。"""
    env.git.create_branch(OVERVIEW, "origin/main")
    env.git.add_worktree(env.paths.overview_tree, OVERVIEW)
    git = TaskId.git()
    job = GitJob(1, GitJobKind.REWRITE_OVERVIEW, branch=OVERVIEW, base=BranchName("main"))
    env.world(OpenTask(command_id=new_id(), issuer=POLICY, task=git, kind=TaskKind.GIT))
    env.world(
        AcceptFlow(
            command_id=new_id(),
            issuer=Issuer.task_supervisor(git),
            task=git,
            steps=git_job_flow(job),
            job=job,
        )
    )
    execution = ex(S.WRITE_OVERVIEW, task=git)
    env.begin(execution)
    return execution


def test_WriteOverviewのタイトルと本文を書き出す(env: Env):
    execution = rewrite_overview(env)
    env.runtime.behaviors.append(
        lambda call, p: outcome(call, {"title": "キャッシュを足す", "body": "本文"})
    )
    assert of_type(env.run(execution), StageCompleted)
    assert env.paths.overview_title.read_text(encoding="utf-8") == "キャッシュを足す"
    assert env.paths.overview_body.read_text(encoding="utf-8") == "本文"


@pytest.mark.parametrize("title", ["[autodev]", "   "])
def test_印を除くと空になるタイトルは形の誤りにして書き出さない(env: Env, title: str):
    execution = rewrite_overview(env)
    env.runtime.behaviors.append(lambda call, p: outcome(call, {"title": title, "body": "本文"}))
    env.executor.run(execution, env.world.inbox.expect(execution))
    env.executor.join()
    (report,) = reports(env)
    assert report.evidence.result_valid is False
    assert "$.title: 概要 PR のタイトル が空" in (report.evidence.error or "")
    assert not env.paths.overview_title.exists()


def test_根元が無いタスクのコミットの数は0件ではなく数えられないとして渡す(env: Env):
    planning_task(env)
    prepare = ex(S.PREPARE, task=PLANNING)
    env.begin(prepare)
    env.executor.run(prepare, env.world.inbox.expect(prepare))
    env.executor.join()
    (report,) = reports(env)
    assert report.evidence.commits is None


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
        return lost_session(call, process)

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
    """見つからないセッションの `--resume`。claude 2.1.288 は init を出さず、この result を返して
    終了コード 1 で終わる（段 6 の実測）。"""
    message = f"No conversation found with session ID: {call.session}"
    return outcome(
        call,
        None,
        subtype="error_during_execution",
        is_error=True,
        num_turns=0,
        text=message,
        exit_code=1,
        stderr=message,
        initialized=False,
    )


@pytest.mark.parametrize(
    "stopped",
    [
        # result が来ないので kill した
        {"ending": Ending.KILLED, "exit_code": -9},
        # interrupt を送り、result が返った
        {
            "subtype": "error_during_execution",
            "is_error": True,
            "num_turns": 0,
            "exit_code": 1,
            "interrupted": "制限時間を超えた",
        },
    ],
)
def test_続けたセッションでinitの前にこちらが止めたら続けられなかったとは数えず失敗にする(
    env: Env, stopped: dict
):
    """init の前に止めたのでは、セッションが在ったかは分からない。作り直すと前の仕事を捨てる。"""
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
    env.runtime.behaviors.append(lambda call, p: outcome(call, None, initialized=False, **stopped))
    events = env.run(ex(S.IMPL))
    assert env.runtime.calls[-1].resume is True
    assert of_type(events, StageFailed) and not of_type(events, ExecutionRestarted)


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


def stuck_stop(env: Env, tree: Path) -> executor_module._Live:
    """`tree` で止めたことにした、いつまでも終わらない実行。"""
    live = executor_module._Live(tree, stopped=True)
    env.executor._stopping.append(live)
    return live


def test_走り出した直後に止めた実行は自分の終わりを待たず同じworktreeの次の仕事も待たせない(
    env: Env, monkeypatch: pytest.MonkeyPatch
):
    """run のスレッドが止めた実行を待つ前に止めると、その走りは止めている実行の一覧に入っている。"""
    impl_task(env)
    monkeypatch.setattr(executor_module, "STOP_WAIT_SECONDS", 5.0)
    env.begin(ex(S.IMPL))
    entered, release = threading.Event(), threading.Event()
    progress = env.executor._progress

    def held(context, body):
        entered.set()
        release.wait(10)
        progress(context, body)

    monkeypatch.setattr(env.executor, "_progress", held)
    env.executor.run(ex(S.IMPL), env.world.inbox.expect(ex(S.IMPL)))
    assert entered.wait(10)
    env.executor.interrupt(ex(S.IMPL))
    tree = env.paths.task_tree(T1)
    lock = tree / sh(tree, "rev-parse", "--git-path", "index.lock").strip()
    lock.write_text("", encoding="utf-8")
    begun = time.monotonic()
    release.set()
    env.executor.join(10)
    assert time.monotonic() - begun < 2
    assert env.executor._stopping == [] and env.world.submitted() == []
    # 止めた走りは何も走らせない（claude を起こさず、起こした跡も残さない）
    assert env.runtime.calls == []
    assert not env.paths.stage_log(ex(S.IMPL)).is_file()
    # 止めた当の走りは lock を片付けない。片付けるのは次の仕事
    assert lock.exists() and tree in env.executor._unswept
    begun = time.monotonic()
    env.executor.abort_rebase(T1)
    env.executor.join(10)
    assert time.monotonic() - begun < 2
    assert not lock.exists()


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


def test_止めた実行が残したindex_lockは走り終えた後の次の仕事が消し一度だけ消す(env: Env):
    """止めた走りは終わるとすぐ止めている実行の一覧から外れる。外れた後に来た次の仕事でも消す。"""
    impl_task(env)
    tree = env.paths.task_tree(T1)
    lock = tree / sh(tree, "rev-parse", "--git-path", "index.lock").strip()
    running = threading.Event()

    def killed_mid_git(call: AgentCall, process: FakeProcess):
        lock.write_text("", encoding="utf-8")  # SIGKILL で止めた git が片付けずに終わった
        running.set()
        process.stop.wait(10)
        return outcome(call, None, is_error=True, interrupted=process.interrupted)

    env.begin(ex(S.IMPL))
    env.runtime.behaviors.append(killed_mid_git)
    env.executor.run(ex(S.IMPL), env.world.inbox.expect(ex(S.IMPL)))
    assert running.wait(10)
    env.executor.interrupt(ex(S.IMPL))
    env.executor.join()
    assert env.executor._stopping == [] and lock.exists()
    env.executor.abort_rebase(T1)
    env.executor.join()
    assert not lock.exists()
    # 片付けた後に現れた lock は、止めた実行のものではないので消さない
    lock.write_text("", encoding="utf-8")
    env.executor.abort_rebase(T1)
    env.executor.join()
    assert lock.exists()


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


def test_joinの待ち時間は全体の上限で走りの数だけ延びない(env: Env):
    release = threading.Event()
    threads = [threading.Thread(target=release.wait, daemon=True) for _ in range(3)]
    for thread in threads:
        thread.start()
    env.executor._threads.extend(threads)
    began = time.monotonic()
    env.executor.join(0.5)
    assert time.monotonic() - began < 1.0
    release.set()
