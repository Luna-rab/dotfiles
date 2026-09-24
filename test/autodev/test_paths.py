"""ランごとの置き場（`config/paths.py`）。

**ここは driver が書き込む先である。** 字面がずれると、対象リポジトリに記録が混ざる
（`guard.json` を worktree の中に置く）か、書いた場所と読む場所が食い違う
（`questions/` と `answers/`）。どちらもランを 1 本潰すまで気づけないので、
パスを字面で留める。

`AUTODEV_STATE_DIR` を差し替えて確かめるので、`~/.local/state/` には触らない。
"""

from __future__ import annotations

import os

import pytest
from autodevlib.config import paths

STATE_DIR = "/tmp/st"


@pytest.fixture
def run(monkeypatch: pytest.MonkeyPatch) -> paths.Run:
    monkeypatch.setenv("AUTODEV_STATE_DIR", STATE_DIR)
    return paths.Run("demo")


# --- ランの中のパス ----------------------------------------------------------


def test_runのファイルをラン名のディレクトリの直下に置く(run: paths.Run):
    assert run.dir == "/tmp/st/demo"
    assert run.state == "/tmp/st/demo/state.json"
    assert run.config == "/tmp/st/demo/config.json"
    assert run.brief == "/tmp/st/demo/brief.md"
    assert run.map == "/tmp/st/demo/map.md"
    assert run.overview_pr_body == "/tmp/st/demo/overview-pr-body.md"
    assert run.tree == "/tmp/st/demo/tree"
    assert run.prose("plan") == "/tmp/st/demo/prose/plan.md"


def test_タスクごとのファイルをtasksの下に置く(run: paths.Run):
    assert run.task_dir("task1") == "/tmp/st/demo/tasks/task1"
    assert run.review("task1") == "/tmp/st/demo/tasks/task1/review.json"
    assert run.result("task1", "impl", "1") == "/tmp/st/demo/tasks/task1/result-impl-1.json"
    assert run.task_pr_body("task1") == "/tmp/st/demo/tasks/task1/pr-body.md"
    assert run.log("task1", "impl", "1") == "/tmp/st/demo/logs/task1/impl-1.jsonl"
    # autodev-watch がログの隣から読む
    assert run.prompt("task1", "impl", "1") == "/tmp/st/demo/logs/task1/impl-1.prompt.md"


def test_guardはworktreeの外に置く(run: paths.Run):
    """`claude --settings` で渡す。worktree の中に置くと commit に混ざる。"""
    assert run.guard == "/tmp/st/demo/guard.json"
    assert not run.guard.startswith(f"{run.tree}/")


def test_質問と回答を別のディレクトリに置く(run: paths.Run):
    """回答の実在がステージの再開の合図である。同じ場所にすると、聞いた瞬間に再開する。"""
    assert run.question("q1") == "/tmp/st/demo/questions/q1.json"
    assert run.answer("q1") == "/tmp/st/demo/answers/q1.json"


def test_置き場の根を環境変数で差し替えられる(monkeypatch: pytest.MonkeyPatch):
    """検査と試験で `~/.local/state/autodev/` を汚さないための口である。"""
    monkeypatch.setenv("AUTODEV_STATE_DIR", "st")
    assert paths.state_root() == os.path.abspath("st")


def test_一覧と実在の判定が同じファイルを見る(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """`autodev list` が並べたランを `autodev status` が「そのランが無い」と言わないこと。"""
    monkeypatch.setenv("AUTODEV_STATE_DIR", str(tmp_path))
    fresh = paths.Run("demo")
    fresh.ensure()
    assert paths.list_runs() == []
    assert fresh.exists() is False
    with open(fresh.state, "w", encoding="utf-8") as fh:
        fh.write("{}")
    assert paths.list_runs() == ["demo"]
    assert fresh.exists() is True


# --- ラン名 ------------------------------------------------------------------


@pytest.mark.parametrize("run_name", ["demo", "a", "a-1", "1", "a" * 49])
def test_英小文字と数字とハイフンのラン名を通す(run_name: str):
    assert paths.check_name(run_name) == run_name


@pytest.mark.parametrize("run_name", ["../x", "a/b", "A", "", "-a", "a b", "a_b", "a" * 50])
def test_それ以外のラン名を弾く(run_name: str):
    """ラン名は `Run.dir` とブランチ名に入る。`../x` が通れば置き場の外へ書く。"""
    with pytest.raises(ValueError):
        paths.check_name(run_name)
