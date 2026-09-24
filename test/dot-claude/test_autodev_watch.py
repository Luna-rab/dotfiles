"""`autodev-watch.py` の検査。画面は Textual の `run_test()` で端末なしに動かす。"""

from __future__ import annotations

import asyncio
import datetime as dt
import importlib.util
import json
import sys

import pytest
from conftest import CLAUDE_SCRIPTS

SCRIPT = CLAUDE_SCRIPTS / "autodev-watch.py"


@pytest.fixture(scope="module")
def watch():
    # ファイル名にハイフンがあるので import 文では読めない
    sys.path.insert(0, str(CLAUDE_SCRIPTS))
    spec = importlib.util.spec_from_file_location("autodev_watch", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_run(root, work: str = "range-field", *, running: bool = True) -> None:
    now = dt.datetime.now().astimezone()
    run = root / work
    (run / "tasks" / "task2").mkdir(parents=True)
    (run / "logs" / "task2").mkdir(parents=True)
    state = {
        "work": work,
        "stackPr": 4,
        "updatedAt": now.isoformat(),
        "running": {"impl": {"task": "task2", "round": "0", "at": now.isoformat(), "turns": 3}}
        if running
        else {},
        "tasks": [
            {"id": "task1", "subject": "パーサ", "status": "stacked", "pr": 5},
            {
                "id": "task2",
                "subject": "範囲指定",
                "status": "running" if running else "blocked",
                "reason": None if running else "受入条件が曖昧",
                "tier": "standard",
                "acceptance": "空入力で None を返す",
                "stages": [{"name": "testgen", "round": "0", "ok": True}],
            },
            {"id": "task3", "subject": "CLI", "status": "pending"},
        ],
    }
    (run / "state.json").write_text(json.dumps(state), encoding="utf-8")
    review = {
        "items": {
            "r1": {
                "status": "open",
                "rating": "must-fix",
                "location": "a.py:3",
                "review": "境界で落ちる\n詳細",
            },
            "r2": {"status": "closed", "rating": "nit", "location": "b.py:1", "review": "名前"},
        }
    }
    (run / "tasks" / "task2" / "review.json").write_text(json.dumps(review), encoding="utf-8")
    events = [
        {"type": "system", "subtype": "init"},
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "テストを読む\n続き"}]},
        },
        {
            "type": "assistant",
            "message": {
                "content": [{"type": "tool_use", "name": "Edit", "input": {"file_path": "a.py"}}]
            },
        },
    ]
    log = run / "logs" / "task2" / "impl-0.jsonl"
    log.write_text("\n".join(json.dumps(e) for e in events) + "\nnot json\n", encoding="utf-8")


def test_ログからツールの呼び出しと発言の1行目を拾う(watch, tmp_path, monkeypatch):
    monkeypatch.setenv("AUTODEV_STATE_DIR", str(tmp_path))
    write_run(tmp_path)
    got = [t.plain for t in watch.activity(str(tmp_path / "range-field/logs/task2/impl-0.jsonl"))]
    assert got == ["  テストを読む", "▸ Edit a.py"]


def test_走っている段のログを選ぶ(watch, tmp_path, monkeypatch):
    monkeypatch.setenv("AUTODEV_STATE_DIR", str(tmp_path))
    write_run(tmp_path)
    st = watch.runs()[0]
    path = watch.log_path(st, st["tasks"][1])
    assert path is not None and path.endswith("logs/task2/impl-0.jsonl")


def test_レビューは件数と未解決の指摘を出す(watch, tmp_path, monkeypatch):
    monkeypatch.setenv("AUTODEV_STATE_DIR", str(tmp_path))
    write_run(tmp_path)
    got = watch.review_summary(str(tmp_path / "range-field/tasks/task2/review.json")).plain
    assert got.splitlines() == ["closed 1 · open 1", "r1 must-fix   a.py:3", "    境界で落ちる"]


def test_動いているrunを先に並べる(watch, tmp_path, monkeypatch):
    monkeypatch.setenv("AUTODEV_STATE_DIR", str(tmp_path))
    write_run(tmp_path, "aaa-stopped", running=False)
    write_run(tmp_path, "zzz-running")
    assert [st["work"] for st in watch.runs()] == ["zzz-running", "aaa-stopped"]


def test_画面は実行中のタスクを選んで開き矢印キーで選び直せる(watch, tmp_path, monkeypatch):
    monkeypatch.setenv("AUTODEV_STATE_DIR", str(tmp_path))
    write_run(tmp_path)

    async def drive() -> None:
        app = watch.Watch()
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            assert app.selected == "task2"
            screen = app.export_screenshot()
            assert "空入力で&#160;None&#160;を返す" in screen
            assert "境界で落ちる" in screen
            assert "Edit&#160;a.py" in screen
            await pilot.press("down")
            await pilot.pause()
            assert app.selected == "task3"
            await pilot.press("l")
            assert not app.query_one("#log").display

    asyncio.run(drive())


def test_runを切り替える(watch, tmp_path, monkeypatch):
    monkeypatch.setenv("AUTODEV_STATE_DIR", str(tmp_path))
    write_run(tmp_path, "aaa-stopped", running=False)
    write_run(tmp_path, "zzz-running")

    async def drive() -> None:
        app = watch.Watch()
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            assert app.work == "zzz-running"
            await pilot.press("]")
            await pilot.pause()
            assert app.work == "aaa-stopped"

    asyncio.run(drive())
