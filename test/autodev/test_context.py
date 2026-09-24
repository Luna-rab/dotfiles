"""ステージの開始と終了の記録（`app/context.py`）。

statusline はタスクごとの `stages` を読んで「済・今・これから」を描く。ここが記録を落とすと、
済んだステージが外から見えなくなる。
"""

from __future__ import annotations

import json

from autodevlib.app.context import Ctx
from autodevlib.config import paths


def ctx(tmp_path, monkeypatch) -> Ctx:
    monkeypatch.setenv("AUTODEV_STATE_DIR", str(tmp_path))
    run = paths.Run("demo")
    run.ensure()
    return Ctx(run=run, st={"tasks": [{"id": "task1", "status": "running"}], "running": {}})


def test_終えたステージをタスクのstagesに成否つきで足す(tmp_path, monkeypatch):
    c = ctx(tmp_path, monkeypatch)
    c.begin("impl", "task1", "0")
    c.end("impl", ok=True)
    c.begin("review:normal", "task1", "1")
    c.end("review:normal", ok=False)

    saved = json.loads((tmp_path / "demo" / "state.json").read_text(encoding="utf-8"))
    assert saved["running"] == {}
    assert saved["tasks"][0]["stages"] == [
        {"name": "impl", "round": "0", "ok": True},
        {"name": "review:normal", "round": "1", "ok": False},
    ]


def test_タスクに属さないステージはstagesに足さない(tmp_path, monkeypatch):
    c = ctx(tmp_path, monkeypatch)
    c.begin("plan", "task0", "0")
    c.end("plan", ok=True)
    assert "stages" not in c.st["tasks"][0]
