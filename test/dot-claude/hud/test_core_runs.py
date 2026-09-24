"""ランが動いているかの判定と、見出しの決め方（`hud/core/runs.py`・`hud/core/headline.py`）。"""

from __future__ import annotations

import datetime as dt

from hud.core import headline, runs
from hud_samples import state

NOW = dt.datetime.now().astimezone()


def ago(**kw) -> str:
    return (NOW - dt.timedelta(**kw)).isoformat()


def test_走っているステージを経過秒数つきで取り出す():
    stages = runs.live_stages(state(), NOW)
    assert [(s.name, s.task, s.round, s.turns, s.tool) for s in stages] == [
        ("review:adversarial", "task2", "1", 26, "Read")
    ]
    assert 250 <= stages[0].seconds <= 254


def test_3時間を超えて残っているステージは落ちたものとして外す():
    st = state(running={"impl": {"task": "task2", "round": "0", "at": ago(hours=4)}})
    assert runs.live_stages(st, NOW) == []


def test_ステージとステージの間でもタスクが実行中なら動いている():
    st = state(running={})
    assert runs.is_active(st, [], NOW)
    assert not runs.is_active(state(running={}, updatedAt=ago(hours=4)), [], NOW)


def test_動いているランを先に並べる():
    stopped = state(name="aaa", running={}, tasks=[])
    active = state(name="zzz")
    assert [st["name"] for st in runs.order([stopped, active], NOW)] == ["zzz", "aaa"]


def test_見出しは走っているステージを短い名前で出す():
    st = state()
    head = headline.build(st, runs.live_stages(st, NOW), active=True)
    assert (head.state, head.doing, head.turns, head.tool, head.overview_pr) == (
        headline.State.RUNNING,
        "task2 レビュー r1",
        26,
        "Read",
        4,
    )


def test_回答を待つステージは質問IDを並べる():
    st = state(running={}, deferred={"stage": "plan"}, questions=[{"id": "range-empty"}])
    head = headline.build(st, [], active=True)
    assert (head.state, head.doing) == (headline.State.WAITING, "計画が回答待ち · range-empty")


def test_止まったランはスタック済みの数と要対応の数を持つ():
    head = headline.build(state(running={}), [], active=False)
    assert (head.state, head.stacked, head.total, head.held) == (headline.State.STOPPED, 1, 4, 1)
