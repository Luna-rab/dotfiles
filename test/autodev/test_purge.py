"""`autodev purge`。本物の git リポジトリに worktree とブランチを作って消させる。"""

from __future__ import annotations

import json
import subprocess

import pytest
from autodev import main


def git(cwd, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def run(tmp_path, monkeypatch):
    """origin に push 済みの概要ブランチと、その上のタスクのブランチ、worktree を持つラン。"""
    monkeypatch.setenv("AUTODEV_STATE_DIR", str(tmp_path / "state"))
    for key, value in {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com"}.items():
        monkeypatch.setenv(key, value)
        monkeypatch.setenv(key.replace("AUTHOR", "COMMITTER"), value)
    origin, repo = tmp_path / "origin.git", tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    git(repo, "remote", "add", "origin", str(origin))
    git(repo, "commit", "-q", "--allow-empty", "-m", "init")
    git(repo, "branch", "stack/demo--task-0")
    git(repo, "branch", "stack/demo--task-1")
    git(repo, "branch", "stack/other--task-0")
    git(repo, "push", "-q", "origin", "main", "stack/demo--task-0", "stack/demo--task-1")
    run_dir = tmp_path / "state" / "demo"
    git(repo, "worktree", "add", "-q", str(run_dir / "tree"), "stack/demo--task-1")
    state = {"name": "demo", "repo": str(repo), "overviewPr": 4, "tasks": [{"pr": 5}]}
    (run_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")
    return repo, run_dir


def branches(repo) -> list[str]:
    return git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads/").split()


def test_worktreeとブランチとランディレクトリを消す(run, capsys):
    repo, run_dir = run
    assert main(["purge", "--name", "demo"]) == 0
    assert not run_dir.exists()
    assert branches(repo) == ["main", "stack/other--task-0"]
    assert str(run_dir) not in git(repo, "worktree", "list")
    assert "GitHub の PR は残してある: #4 #5" in capsys.readouterr().err


def test_pushしていないコミットがあれば何も消さない(run):
    repo, run_dir = run
    git(run_dir / "tree", "commit", "-q", "--allow-empty", "-m", "local only")
    with pytest.raises(SystemExit):
        main(["purge", "--name", "demo"])
    assert (run_dir / "tree").is_dir()
    assert "stack/demo--task-1" in branches(repo)

    assert main(["purge", "--name", "demo", "--force"]) == 0
    assert not run_dir.exists()
    assert "stack/demo--task-1" not in branches(repo)


def test_ステージが走っている記録があれば何も消さない(run):
    _, run_dir = run
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    state["running"] = {"impl": {"task": "task1"}}
    (run_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(SystemExit):
        main(["purge", "--name", "demo"])
    assert run_dir.exists()
