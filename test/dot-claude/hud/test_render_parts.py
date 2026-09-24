"""行の部品（`hud/render/parts.py`）とタスクリストの描き方（`hud/render/tasklist.py`）。"""

from __future__ import annotations

import pytest
from hud.core.pipeline import Mark, Step
from hud.core.tasklist import Summary
from hud.render import parts, tasklist
from rich.text import Text


@pytest.mark.parametrize(
    ("pct", "mark", "want"),
    [(50, None, "━━──"), (10, None, "╾───"), (100, None, "━━━━"), (50, 75, "━━─┃"), (0, 0, "┃───")],
)
def test_棒は罫線で0_5マスまで刻む(pct, mark, want):
    assert parts.bar(pct, 4, mark).plain == want


@pytest.mark.parametrize(("pct", "rgb"), [(0, "#a6e3a1"), (50, "#f9e2af"), (100, "#f38ba8")])
def test_使用率の色は緑から黄を経て赤へ変わる(pct, rgb):
    color = parts.pct_color(pct).color
    assert color is not None and color.get_truecolor().hex == rgb


def test_幅に収まらなければkeepの小さい部品から落とす():
    row = [
        parts.Part(Text("aaaa"), keep=2),
        parts.Part(Text("bb"), keep=0),
        parts.Part(Text("cc"), keep=1),
    ]
    assert parts.fit(row, 20).plain == "aaaa   bb   cc"
    assert parts.fit(row, 9).plain == "aaaa   cc"
    assert parts.fit(row, 3).plain == "aaaa"


def test_ステージの並びを記号つきで描く():
    steps = [
        Step("…", "", Mark.ELIDED),
        Step("修正", "2", Mark.FAILED),
        Step("レビュー", "2", Mark.CURRENT),
        Step("ジャッジ", "", Mark.NEXT),
    ]
    assert tasklist.pipeline(steps).plain == "… › 修正 r2 ✘ › レビュー r2 ◼ › ジャッジ"


def test_タスク行は右に続くものがあるときだけ件名の幅をそろえる():
    running = tasklist.task_row(
        {"id": "task2", "subject": "範囲", "status": "running"}, [Step("実装", "0", Mark.CURRENT)]
    )
    pending = tasklist.task_row({"id": "task3", "subject": "CLI", "status": "pending"}, [])
    assert running.plain == "  ◼ task2 範囲" + " " * 18 + "  実装 ◼"
    assert pending.plain == "  ◻ task3 CLI"


def test_まとめた行():
    assert tasklist.summary_row(Summary("stacked", 3)).plain == "  ✔ 3 件完了"
    assert tasklist.summary_row(Summary("pending", 2)).plain == "  ◻ 他 2 件"
