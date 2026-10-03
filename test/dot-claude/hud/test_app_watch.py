"""autodev-watch の画面を、Textual の `run_test()` で端末なしに動かす。autodev の入口は偽物に替える。"""

from __future__ import annotations

import asyncio

from hud.app.watch import Level, Watch
from hud_samples import calls, status, write_fake_entry


def screen_text(app: Watch) -> str:
    return app.export_screenshot().replace("&#160;", " ")


def fake(tmp_path, monkeypatch, statuses: list[dict]) -> None:
    for key, value in write_fake_entry(tmp_path, statuses).items():
        monkeypatch.setenv(key, value)


def test_ランから段までEnterで入りEscで戻る(tmp_path, monkeypatch):
    fake(tmp_path, monkeypatch, [status()])

    async def drive() -> None:
        app = Watch()
        async with app.run_test(size=(160, 40)) as pilot:
            await pilot.pause()
            assert (app.level, app.cursor[Level.RUNS]) == (Level.RUNS, "add-cache")
            # ランの詳細: 進み具合と回答を待っている質問
            text = screen_text(app)
            assert "1/4 スタック済み" in text
            assert "空の入力は弾くか" in text
            assert "深く" in text

            await pilot.press("enter")
            await pilot.pause()
            # 実行中のタスクを選んで入る。詳細は段の並びと走っている実行
            assert (app.level, app.cursor[Level.TASKS]) == (Level.TASKS, "task1")
            text = screen_text(app)
            # スクリーンショットは色の変わり目で文字列が切れるので、色の揃った部分だけを探す
            assert "ジャッジ r2 ◼" in text and "完了チェック" in text
            assert "ジャッジ r2 a1" in text and "7ターン Read" in text

            await pilot.press("enter")
            await pilot.pause()
            # 今の段を選ぶ。その段の実行を出す
            assert (app.level, app.cursor[Level.STAGES]) == (Level.STAGES, "step:1")
            assert "実行中" in screen_text(app)

            await pilot.press("escape")
            await pilot.pause()
            assert (app.level, app.cursor[Level.TASKS]) == (Level.TASKS, "task1")
            await pilot.press("escape")
            await pilot.pause()
            assert app.level is Level.RUNS

    asyncio.run(drive())


def test_深い層では選んだランのstatusだけを呼ぶ(tmp_path, monkeypatch):
    fake(tmp_path, monkeypatch, [status()])

    async def drive() -> None:
        app = Watch("add-cache")
        async with app.run_test(size=(160, 40)) as pilot:
            await pilot.pause()
            app.reload()
            await pilot.pause()
            assert set(calls(tmp_path)) == {"status --json --name add-cache"}

    asyncio.run(drive())


def test_読み直してもカーソルを保つ(tmp_path, monkeypatch):
    fake(tmp_path, monkeypatch, [status()])

    async def drive() -> None:
        app = Watch("add-cache")
        async with app.run_test(size=(160, 40)) as pilot:
            await pilot.pause()
            assert (app.level, app.cursor[Level.TASKS]) == (Level.TASKS, "task1")
            await pilot.press("down")
            await pilot.pause()
            assert app.cursor[Level.TASKS] == "task2"
            app.reload()
            await pilot.pause()
            assert app.cursor[Level.TASKS] == "task2"

    asyncio.run(drive())


def test_これからの段はまだ走っていないと出す(tmp_path, monkeypatch):
    fake(tmp_path, monkeypatch, [status()])

    async def drive() -> None:
        app = Watch("add-cache")
        async with app.run_test(size=(160, 40)) as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("down")
            await pilot.pause()
            assert app.cursor[Level.STAGES] == "step:2"
            assert "まだ走っていない" in screen_text(app)

    asyncio.run(drive())


def test_読めないランは理由を出し中には入らない(tmp_path, monkeypatch):
    fake(tmp_path, monkeypatch, [{"name": "old", "error": "ValueError: 版が違う"}, status()])

    async def drive() -> None:
        app = Watch()
        async with app.run_test(size=(160, 40)) as pilot:
            await pilot.pause()
            await pilot.press("down")
            await pilot.pause()
            assert app.cursor[Level.RUNS] == "old"
            assert "ValueError: 版が違う" in screen_text(app)
            await pilot.press("enter")
            await pilot.pause()
            assert app.level is Level.RUNS

    asyncio.run(drive())


def test_ランが無くなったらランのリストに戻る(tmp_path, monkeypatch):
    fake(tmp_path, monkeypatch, [status()])

    async def drive() -> None:
        app = Watch("nope")
        async with app.run_test(size=(160, 40)) as pilot:
            await pilot.pause()
            assert (app.level, app.cursor[Level.RUNS]) == (Level.RUNS, "add-cache")

    asyncio.run(drive())


def test_statusが返らなければそう出す(tmp_path, monkeypatch):
    entry = tmp_path / "broken-autodev.py"
    entry.write_text("import sys; sys.stderr.write('autodev: まだ動かない\\n'); sys.exit(1)")
    monkeypatch.setenv("AUTODEV_ENTRY", str(entry))

    async def drive() -> None:
        app = Watch()
        async with app.run_test(size=(160, 40)) as pilot:
            await pilot.pause()
            text = screen_text(app)
            assert "autodev status を読めない" in text and "まだ動かない" in text

    asyncio.run(drive())
