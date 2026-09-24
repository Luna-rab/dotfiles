"""autodev-watch の画面を、Textual の `run_test()` で端末なしに動かす。"""

from __future__ import annotations

import asyncio

from hud.app.watch import Level, Watch
from hud_samples import write_run


def screen_text(app: Watch) -> str:
    return app.export_screenshot().replace("&#160;", " ")


def test_ランからステージまでEnterで入りEscで戻る(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTODEV_STATE_DIR", str(tmp_path))
    write_run(tmp_path)

    async def drive() -> None:
        app = Watch()
        async with app.run_test(size=(160, 40)) as pilot:
            await pilot.pause()
            assert (app.level, app.cursor[Level.RUNS]) == (Level.RUNS, "range-field")
            # ランの詳細: ゴールと進み具合
            assert "範囲を指定して切り出す。" in screen_text(app)
            assert "1/4 スタック済み" in screen_text(app)

            await pilot.press("enter")
            await pilot.pause()
            # 実行中のタスクを選んで入る。詳細は受入条件と指摘
            assert (app.level, app.cursor[Level.TASKS]) == (Level.TASKS, "task2")
            assert "空入力で None を返す" in screen_text(app)
            assert "境界で落ちる" in screen_text(app)

            await pilot.press("enter")
            await pilot.pause()
            # 走っているステージを選ぶ。指示に続けて出力を出す
            assert (app.level, app.cursor[Level.STAGES]) == (Level.STAGES, "review:adversarial@1")
            text = screen_text(app)
            assert "あなたは敵対的レビューのステージである。" in text
            assert text.index("あなたは敵対的レビュー") < text.index("Claude Code の出力")
            assert "テストを読む" in text and "続き" in text
            assert "▸ Edit a.py" in text

            await pilot.press("escape")
            await pilot.pause()
            assert (app.level, app.cursor[Level.TASKS]) == (Level.TASKS, "task2")
            await pilot.press("escape")
            await pilot.pause()
            assert app.level is Level.RUNS

    asyncio.run(drive())


def test_読み直してもカーソルを保つ(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTODEV_STATE_DIR", str(tmp_path))
    write_run(tmp_path)

    async def drive() -> None:
        app = Watch("range-field")
        async with app.run_test(size=(160, 40)) as pilot:
            await pilot.pause()
            assert app.level is Level.TASKS
            await pilot.press("down")
            await pilot.pause()
            assert app.cursor[Level.TASKS] == "task3"
            app.reload()
            await pilot.pause()
            assert app.cursor[Level.TASKS] == "task3"

    asyncio.run(drive())


def test_準備と仕上げから計画ステージに入れる(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTODEV_STATE_DIR", str(tmp_path))
    write_run(tmp_path)

    async def drive() -> None:
        app = Watch("range-field")
        async with app.run_test(size=(160, 40)) as pilot:
            await pilot.pause()
            app.cursor[Level.TASKS] = "task0"
            app.reload()
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert (app.task_id, app.cursor[Level.STAGES]) == ("task0", "plan@0")
            # 指示の記録が無いステージは、その旨を出す
            assert "指示の記録が無い" in screen_text(app)

    asyncio.run(drive())


def test_これからのステージはまだ走っていないと出す(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTODEV_STATE_DIR", str(tmp_path))
    write_run(tmp_path)

    async def drive() -> None:
        app = Watch("range-field")
        async with app.run_test(size=(160, 40)) as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("down")
            await pilot.pause()
            assert app.cursor[Level.STAGES] == "next:ジャッジ"
            assert "まだ走っていない" in screen_text(app)

    asyncio.run(drive())
