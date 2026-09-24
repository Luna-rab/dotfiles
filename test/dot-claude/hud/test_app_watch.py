"""autodev-watch の画面を、Textual の `run_test()` で端末なしに動かす。"""

from __future__ import annotations

import asyncio

from hud.app.watch import Watch
from hud_samples import write_run


def test_実行中のタスクを選んで開き詳細とログを出し矢印キーで選び直せる(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTODEV_STATE_DIR", str(tmp_path))
    write_run(tmp_path)

    async def drive() -> None:
        app = Watch()
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


def test_runを切り替える(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTODEV_STATE_DIR", str(tmp_path))
    write_run(tmp_path, work="aaa-stopped", running={}, tasks=[])
    write_run(tmp_path, work="zzz-running")

    async def drive() -> None:
        app = Watch()
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            assert app.work == "zzz-running"
            await pilot.press("]")
            await pilot.pause()
            assert app.work == "aaa-stopped"

    asyncio.run(drive())
