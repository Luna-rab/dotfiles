"""外とのやり取り（`hud/ports/git.py`・`hud/ports/autodev.py`）。本物の git と一時ディレクトリで確かめる。"""

from __future__ import annotations

import json
import subprocess

from hud.ports import autodev, git, usage
from hud_samples import usage as sample_usage
from hud_samples import write_run, write_usage


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


def usage_env(tmp_path, monkeypatch, fetched) -> list[str]:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    (tmp_path / "claude").mkdir()
    creds = {"claudeAiOauth": {"accessToken": "tok"}}
    (tmp_path / "claude" / ".credentials.json").write_text(json.dumps(creds), encoding="utf-8")
    calls: list[str] = []
    monkeypatch.setattr(usage, "fetch", lambda token: calls.append(token) or fetched)
    return calls


def test_キャッシュが新しければ取りに行かない(tmp_path, monkeypatch):
    calls = usage_env(tmp_path, monkeypatch, {"other": 1})
    write_usage(tmp_path / "cache" / "claude-hud" / "usage.json", 1000.0)
    assert usage.usage(1000.0 + usage.TTL - 1) == sample_usage()
    assert calls == []


def test_キャッシュが古ければ取りに行って書き込む(tmp_path, monkeypatch):
    calls = usage_env(tmp_path, monkeypatch, {"fresh": 1})
    write_usage(tmp_path / "cache" / "claude-hud" / "usage.json", 1000.0)
    assert usage.usage(1000.0 + usage.TTL) == {"fresh": 1}
    assert calls == ["tok"]
    assert usage.usage(1000.0 + usage.TTL + 1) == {"fresh": 1}
    assert calls == ["tok"]


def test_取れなければ前のものを返し次のTTL秒は取りに行かない(tmp_path, monkeypatch):
    calls = usage_env(tmp_path, monkeypatch, None)
    write_usage(tmp_path / "cache" / "claude-hud" / "usage.json", 1000.0)
    assert usage.usage(2000.0) == sample_usage()
    assert usage.usage(2001.0) == sample_usage()
    assert calls == ["tok"]


def test_トークンが無ければ取りに行かない(tmp_path, monkeypatch):
    calls = usage_env(tmp_path, monkeypatch, {"fresh": 1})
    (tmp_path / "claude" / ".credentials.json").unlink()
    assert usage.usage(1000.0) is None
    assert calls == []
