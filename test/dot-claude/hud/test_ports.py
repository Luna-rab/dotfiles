"""外とのやり取り（`hud/ports/git.py`・`hud/ports/autodev.py`）。本物の git と一時ディレクトリで確かめる。"""

from __future__ import annotations

import subprocess

from hud.ports import autodev, git
from hud_samples import write_run


def test_git_statusの出力を返しgitの外ならNone(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q", "-b", "main"], check=True)
    (repo / "b").write_text("new")
    out = git.status(str(repo))
    assert out is not None and "# branch.head main" in out and "? b" in out
    assert git.status(str(tmp_path)) is None


def test_runの置き場を読む(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTODEV_STATE_DIR", str(tmp_path))
    write_run(tmp_path)
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "state.json").write_text("{", encoding="utf-8")
    assert [st["work"] for st in autodev.read_states()] == ["range-field"]
    assert autodev.read_review("range-field", "task2")["items"]["r1"]["rating"] == "must-fix"
    assert autodev.read_review("range-field", "task9") is None


def test_走っている段のログを選び無ければ最後に書かれたものを選ぶ(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTODEV_STATE_DIR", str(tmp_path))
    write_run(tmp_path)
    running = autodev.log_path("range-field", "task2", [("review:adversarial", "1")])
    assert running is not None and running.endswith("logs/task2/review-adversarial-1.jsonl")
    assert autodev.log_path("range-field", "task2", []) == running
    assert autodev.log_path("range-field", "task9", []) is None
    assert len(autodev.read_lines(running)) == 4
