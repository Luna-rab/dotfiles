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


def test_ランディレクトリを読む(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTODEV_STATE_DIR", str(tmp_path))
    write_run(tmp_path)
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "state.json").write_text("{", encoding="utf-8")
    assert [st["name"] for st in autodev.read_states()] == ["range-field"]
    assert autodev.read_review("range-field", "task2")["items"]["r1"]["rating"] == "must-fix"
    assert autodev.read_review("range-field", "task9") is None
    assert autodev.read_overview("range-field") == "範囲を指定して切り出す。"


def test_ステージのログと指示をコード名とラウンドで引く(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTODEV_STATE_DIR", str(tmp_path))
    write_run(tmp_path)
    log = autodev.stage_file("range-field", "task2", "review:adversarial", "1", ".jsonl")
    assert log.endswith("logs/task2/review-adversarial-1.jsonl")
    assert len(autodev.read_lines(log)) == 4
    prompt = autodev.stage_file("range-field", "task2", "review:adversarial", "1", ".prompt.md")
    assert "敵対的レビュー" in str(autodev.read_text(prompt))
    assert autodev.read_text(prompt.replace("-1.", "-9.")) is None


def test_ログのファイル名を書かれた順に並べる(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTODEV_STATE_DIR", str(tmp_path))
    write_run(tmp_path)
    assert autodev.log_names("range-field", "task0") == ["plan-0.jsonl"]
    assert autodev.log_names("range-field", "task9") == []
