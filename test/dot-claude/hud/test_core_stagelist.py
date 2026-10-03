"""autodev-watch の段のリスト（`hud/core/stagelist.py`）。"""

from __future__ import annotations

import copy

from hud.core import stagelist
from hud.core.pipeline import Mark
from hud_samples import ADDENDUM_TASK


def rows(items: list[stagelist.StageItem]) -> list[tuple[str, str, int, bool]]:
    return [(i.label, i.mark.value, len(i.runs), i.older) for i in items]


def test_今のフローの段ごとに実行を持たせる():
    got = stagelist.for_task(ADDENDUM_TASK)
    assert rows(got) == [
        ("テスト作成", "done", 0, False),
        ("ジャッジ r2", "current", 1, False),
        ("完了チェック", "next", 0, False),
    ]
    assert got[1].runs[0]["id"] == "task1-Judge-r2-a1"


def test_前の版のフローの実行は今の段に重ねず後ろに並べる():
    """`step` が 2 でも、書き直す前のフローの添字なので、今の Gate の段ではない。"""
    task = copy.deepcopy(ADDENDUM_TASK)
    task["flow"]["version"] = 2
    task["executions"][0]["step"] = 2
    got = stagelist.for_task(task)
    assert [len(i.runs) for i in got[:3]] == [0, 0, 0]
    assert rows(got[3:]) == [("ジャッジ r2", "current", 1, True)]
    assert got[3].key == "exec:task1-Judge-r2-a1"


def test_フローが無くても実行は見せる():
    task = copy.deepcopy(ADDENDUM_TASK)
    task["flow"] = None
    task["executions"][0]["status"] = "failed"
    got = stagelist.for_task(task)
    assert [(i.older, i.mark) for i in got] == [(True, Mark.FAILED)]


def test_段のキーは添字で決まり読み直しても変わらない():
    assert [i.key for i in stagelist.for_task(ADDENDUM_TASK)] == ["step:0", "step:1", "step:2"]
