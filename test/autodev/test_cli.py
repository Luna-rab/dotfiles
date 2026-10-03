"""CLI（`autodevlib/cli.py`）: 本物の組み立て・実行器・git で、claude と gh だけを偽物にして通す。

偽の `claude`（`fake_claude_run.py`）と偽の `gh`（`test_adapter_forge.FakeGh`）を PATH の先頭に置く。
対象リポジトリは一時ディレクトリの git リポジトリ（bare の origin を clone したもの）。
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from autodevlib import cli
from autodevlib.domain.commands import AnswerQuestion
from autodevlib.domain.events import QuestionPosted, QuestionWithdrawn, RunStarted
from autodevlib.domain.values import (
    BranchName,
    CommandId,
    EventId,
    Instruction,
    ParallelLimit,
    QuestionId,
    Repository,
    RunName,
    StreamId,
)
from autodevlib.infra import status as status_module
from autodevlib.infra.eventstore import EventStore
from autodevlib.infra.lock import DriverLock
from autodevlib.infra.paths import RunPaths
from autodevlib.infra.repo_config import config_path
from autodevlib.infra.requests import RequestBox
from conftest import SCRIPTS_ROOT, SKILL_ROOT
from executor_fakes import commit, make_repo, sh
from fake_claude_run import ASK_ID, QUESTION
from test_adapter_forge import FakeGh

FAKE_CLAUDE = Path(__file__).with_name("fake_claude_run.py")
NAME = "add-ttl"


@dataclass
class World:
    tmp: Path
    repo: Path
    gh: FakeGh
    capsys: pytest.CaptureFixture[str]

    @property
    def paths(self) -> RunPaths:
        return RunPaths.of(RunName(NAME))

    def cli(self, *args: str) -> tuple[int, str, str]:
        self.capsys.readouterr()
        code = cli.main(list(args))
        out, err = self.capsys.readouterr()
        return code, out, err

    def claude_calls(self) -> list[dict[str, Any]]:
        log = self.tmp / "claude.log"
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


@pytest.fixture
def world(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> World:
    repo = make_repo(tmp_path, monkeypatch)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = FakeGh(bin_dir, monkeypatch)
    gh.reply(["auth", "status"])
    gh.reply(["extension", "list"], out="gh stack\tgithub/gh-stack\tv0.1\n")
    gh.reply(["pr", "list"], out="[]")
    gh.reply(["pr", "create"], out="https://github.com/o/r/pull/7\n")
    gh.reply(
        ["pr", "view"],
        out=json.dumps(
            {
                "number": 7,
                "headRefName": f"stack/{NAME}--task-0",
                "baseRefName": "main",
                "state": "OPEN",
                "isDraft": True,
            }
        ),
    )
    claude = bin_dir / "claude"
    claude.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE_CLAUDE}" "$@"\n', "utf-8")
    claude.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("AUTODEV_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("FAKE_CLAUDE_SCHEMAS", str(SKILL_ROOT / "schemas"))
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(tmp_path / "claude.log"))
    return World(tmp_path, repo, gh, capsys)


def start(world: World, *extra: str) -> tuple[int, str, str]:
    return world.cli(
        "run", "--name", NAME, "--instruction", "TTL を足す", "--repo", str(world.repo), *extra
    )


# --- run ---


def test_runは計画のaskで回答待ちの4で止まり回答を置いて呼び直すと仕上げて0で終える(
    world: World,
):
    code, _, err = start(world)
    assert code == 4, err
    # ラン統括がユーザーに聞いた質問が questions/ にある
    question = json.loads(world.paths.question(QuestionId(QUESTION)).read_text("utf-8"))
    assert question["status"] == "open"

    code, out, err = world.cli(
        "answer", "--name", NAME, "--question", QUESTION, "--answer", "300 秒"
    )
    assert code == 0, err
    assert json.loads(out)["question"] == QUESTION

    code, _, err = world.cli("run", "--name", NAME)
    assert code == 0, err
    # 回答は ask の回答のファイルを通って、defer で止まった Plan に届いた
    answer = json.loads(world.paths.answer(ASK_ID).read_text("utf-8"))
    assert answer == {"answer": "300 秒"}
    plans = [call for call in world.claude_calls() if call["role"] == "plan"]
    assert [call["resumed"] for call in plans] == [False, True]
    assert all(Path(call["cwd"]) == world.paths.overview_tree for call in plans)
    # 概要 PR を draft で作り、PR は残したまま（gh pr ready は呼ばない）
    calls = [call["args"][:2] for call in world.gh.calls()]
    assert ["pr", "create"] in calls
    assert ["pr", "ready"] not in calls
    assert sh(world.repo, "ls-remote", "origin", f"stack/{NAME}--task-0").strip()

    # 終えたランの events を JSON で書き出せる
    code, out, _ = world.cli("events", "--name", NAME)
    assert code == 0
    types = [row["type"] for row in json.loads(out)]
    assert types[0] == "RunStarted"
    assert "RunFinished" in types


def test_既にあるランに指示を付けたら1で止める(world: World):
    assert start(world)[0] == 4
    code, _, err = start(world)
    assert code == 1
    assert "--instruction を付けない" in err


def test_既にあるランにrepoとbaseを違う値で渡したら1で止める(world: World, tmp_path: Path):
    assert start(world)[0] == 4
    other = tmp_path / "other"
    sh(tmp_path, "init", "-q", str(other))
    code, _, err = world.cli("run", "--name", NAME, "--repo", str(other))
    assert (code, "--repo と違う" in err) == (1, True)
    code, _, err = world.cli("run", "--name", NAME, "--base", "develop")
    assert (code, "--base と違う" in err) == (1, True)


def test_新しいランに指示かrepoが無ければ1で止める(world: World):
    code, _, err = world.cli("run", "--name", NAME, "--repo", str(world.repo))
    assert (code, "--instruction" in err) == (1, True)
    code, _, err = world.cli("run", "--name", NAME, "--instruction", "x")
    assert (code, "--repo が要る" in err) == (1, True)
    code, _, err = world.cli("run", "--name", "Bad_Name", "--instruction", "x")
    assert (code, "ラン名が使えない" in err) == (1, True)
    assert not world.paths.root.exists()


def test_originに概要ブランチがあるラン名は使われているとして1で止める(world: World):
    sh(world.repo, "push", "-q", "origin", f"main:stack/{NAME}--task-0")
    code, _, err = start(world)
    assert code == 1
    assert "使われている" in err
    assert not world.paths.events_db.exists()


def test_道具が足りなければ走らずに1で止める(world: World):
    # 偽の gh は先に足した返事から当てるので、認証の返事を差し替える
    world.gh.replies[0] = {"prefix": ["auth", "status"], "out": "", "err": "", "code": 1}
    world.gh._save()
    code, _, err = start(world)
    assert code == 1
    assert "gh が認証されていない" in err
    assert world.claude_calls() == []


def test_崩れたリポジトリの設定では走らず直し方を出して1で止める(world: World):
    path = config_path(Repository(str(world.repo)))
    path.parent.mkdir(parents=True)
    path.write_text('{"verify": ["uv run pytest"], "testGlob": []}', "utf-8")
    code, _, err = start(world)
    assert code == 1
    assert str(path) in err
    assert "知らない欄がある: testGlob" in err
    assert world.claude_calls() == []


def test_リポジトリの設定は実行器に渡りブリーフに載る(world: World):
    path = config_path(Repository(str(world.repo)))
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "verify": ["uv run pytest -q"],
                "testGlobs": ["spec/**"],
                "protected": ["uv.lock"],
                "untested": ["docs/**"],
            }
        ),
        "utf-8",
    )
    code, _, err = start(world)
    assert code == 4
    assert "リポジトリの設定が無い" not in err
    brief = world.paths.brief.read_text("utf-8")
    for written in ("`uv run pytest -q`", "`spec/**`", "`uv.lock`", "`docs/**`"):
        assert written in brief
    # testGlobs は既定に足さず置き換える
    assert "`**/test_*.py`" not in brief


def test_同じランのdriverが走っている間はrunを1で止める(world: World):
    assert start(world)[0] == 4
    with DriverLock(world.paths.driver_lock):
        code, _, err = world.cli("run", "--name", NAME)
    assert code == 1
    assert "ほかに走っている" in err


def test_引数の誤りは2ではなく1で返す(world: World):
    with pytest.raises(SystemExit) as exited:
        world.cli("run")
    assert exited.value.code == 1


# --- answer・ask ---


def seed_questions(paths: RunPaths, *, withdrawn: bool) -> None:
    """driver を通さずに、質問を 1 つ出した（取り下げた）記録を作る。"""
    escalation = EventId("run#2")
    store = EventStore.open(paths.events_db)
    try:
        store.append(
            CommandId("start"),
            StreamId.run(),
            0,
            [
                RunStarted(
                    RunName(NAME),
                    Instruction("x"),
                    Repository("/r"),
                    BranchName("main"),
                    ParallelLimit(3),
                )
            ],
        )
        events: list[Any] = [QuestionPosted(QuestionId(QUESTION), "どちら?", escalation)]
        if withdrawn:
            events.append(QuestionWithdrawn(QuestionId(QUESTION), escalation, "タスクを止めた"))
        store.append(CommandId("q"), StreamId.questions(), 0, events)
    finally:
        store.close()


def test_answerは取り下げた質問なら理由を添えて1で落ち要求を置かない(world: World):
    seed_questions(world.paths, withdrawn=True)
    code, _, err = world.cli("answer", "--name", NAME, "--question", QUESTION, "--answer", "A")
    assert code == 1
    assert "取り下げた理由: タスクを止めた" in err
    with RequestBox.open(world.paths.events_db) as box:
        assert box.pending() == []


def test_answerは回答を待つ質問への回答をrequestsに置くだけでdriverを起こさない(
    world: World, tmp_path: Path
):
    seed_questions(world.paths, withdrawn=False)
    body = tmp_path / "answer.md"
    body.write_text("A にする\n", "utf-8")
    code, out, _ = world.cli(
        "answer", "--name", NAME, "--question", QUESTION, "--answer-file", str(body)
    )
    assert code == 0
    with RequestBox.open(world.paths.events_db) as box:
        (request,) = box.pending()
    assert request.id == json.loads(out)["request"]
    command = request.to_command()
    assert isinstance(command, AnswerQuestion)
    assert (command.question, command.answer) == (QuestionId(QUESTION), "A にする\n")
    assert world.claude_calls() == []
    # 無い質問・空の回答は、Questions が拒む理由で落ちる
    code, _, err = world.cli("answer", "--name", NAME, "--question", "q-none", "--answer", "A")
    assert (code, "q-none という質問は無い" in err) == (1, True)
    code, _, err = world.cli("answer", "--name", NAME, "--question", QUESTION, "--answer", " ")
    assert (code, "回答が空" in err) == (1, True)


def test_answerは無いランに要求を置かない(world: World):
    code, _, err = world.cli("answer", "--name", NAME, "--question", QUESTION, "--answer", "A")
    assert (code, "そのランが無い" in err) == (1, True)
    assert not world.paths.root.exists()


def test_askはフックが足した回答のファイルの中身を出す(world: World, tmp_path: Path):
    answer = tmp_path / "toolu_1.json"
    answer.write_text(json.dumps({"answer": "300 秒"}), "utf-8")
    code, out, _ = world.cli("ask", "--question", "どちら?", "--answer-file", str(answer))
    assert (code, out) == (0, "300 秒\n")


def test_askは回答のファイルが無ければ単独で呼び直せと言って1で落ちる(world: World):
    code, _, err = world.cli("ask", "--question", "どちら?")
    assert code == 1
    assert "単独に呼び直す" in err


# --- status ---


def test_statusはnameがあればrun_statusを無ければall_statusesを呼んでJSONを出す(
    world: World, monkeypatch: pytest.MonkeyPatch
):
    seen: list[Any] = []
    monkeypatch.setattr(
        status_module,
        "run_status",
        lambda paths: seen.append(paths) or {"name": paths.name.value},
        raising=False,
    )
    monkeypatch.setattr(status_module, "all_statuses", lambda: [{"name": "x"}], raising=False)
    code, out, _ = world.cli("status", "--json", "--name", NAME)
    assert (code, json.loads(out)) == (0, {"name": NAME})
    assert seen == [world.paths]
    code, out, _ = world.cli("status", "--json")
    assert (code, json.loads(out)) == (0, [{"name": "x"}])


# --- clean・purge ---


def test_cleanはworktreeを外して記録を残す(world: World):
    assert start(world)[0] == 4
    assert world.paths.overview_tree.is_dir()
    code, out, _ = world.cli("clean", "--name", NAME)
    assert code == 0
    assert json.loads(out)["removed"] == [str(world.paths.overview_tree)]
    assert not world.paths.overview_tree.exists()
    assert world.paths.events_db.is_file()
    assert str(world.paths.overview_tree) not in sh(world.repo, "worktree", "list")


def test_purgeはoriginに無いコミットがあれば何も消さずforceなら消す(world: World):
    assert start(world)[0] == 4
    branch = f"stack/{NAME}--task-0"
    # 概要ブランチは、計画の後に概要 PR を作るときに push する。ここでは先に push しておく
    sh(world.paths.overview_tree, "push", "-q", "origin", f"{branch}:{branch}")
    commit(world.paths.overview_tree, "b.txt", "x\n", "push していない")
    code, _, err = world.cli("purge", "--name", NAME)
    assert code == 1
    assert f"{branch} に origin に無いコミットがある（1 件）" in err
    assert world.paths.overview_tree.is_dir()

    code, out, err = world.cli("purge", "--name", NAME, "--force")
    assert code == 0, err
    assert json.loads(out)["branches"] == [branch]
    assert not world.paths.root.exists()
    assert sh(world.repo, "branch", "--list", branch).strip() == ""
    # リモートのブランチと PR には触らない
    assert sh(world.repo, "ls-remote", "origin", branch).strip()
    assert not any(call["args"][:2] == ["pr", "close"] for call in world.gh.calls())


def test_purgeはdriverが落ちて記録の上で走っている実行が残っていれば消さない(
    world: World, monkeypatch: pytest.MonkeyPatch
):
    # 入口（scripts/autodev.py）を別のプロセスで起こし、Plan が走っている間に driver ごと落とす
    monkeypatch.setenv("FAKE_CLAUDE_HANG", "plan")
    driver = subprocess.Popen(
        [
            sys.executable,
            str(SCRIPTS_ROOT / "autodev.py"),
            *("run", "--name", NAME, "--instruction", "TTL を足す", "--repo", str(world.repo)),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 30
        while not world.claude_calls() and time.monotonic() < deadline:
            time.sleep(0.05)
        (plan,) = world.claude_calls()
    finally:
        os.killpg(driver.pid, signal.SIGKILL)
        driver.wait()
    # claude は別のプロセスグループで起こすので、driver を落としても残る
    os.kill(plan["pid"], signal.SIGKILL)

    code, _, err = world.cli("purge", "--name", NAME)
    assert code == 1
    assert "記録の上で走っている実行がある: planning-Plan" in err
    assert world.paths.events_db.is_file()


def test_purgeとcleanはdriverが走っている間はforceでも消さない(world: World):
    assert start(world)[0] == 4
    with DriverLock(world.paths.driver_lock):
        assert world.cli("purge", "--name", NAME, "--force")[0] == 1
        assert world.cli("clean", "--name", NAME)[0] == 1
    assert world.paths.overview_tree.is_dir()


def test_無いランのcleanとpurgeとeventsは1で止める(world: World):
    for command in ("clean", "purge", "events"):
        code, _, err = world.cli(command, "--name", NAME)
        assert (command, code, "そのランが無い" in err) == (command, 1, True)
