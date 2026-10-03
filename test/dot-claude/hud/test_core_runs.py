"""status の読み方と、見出しの決め方（`hud/core/runs.py`・`hud/core/headline.py`）。"""

from __future__ import annotations

import datetime as dt

import pytest
from hud.core import headline, runs
from hud_samples import quiet, stamp, status


def now() -> dt.datetime:
    # 見本は呼んだ時点の時刻で組むので、モジュールを読んだ時刻ではなく今の時刻と比べる
    return dt.datetime.now().astimezone()


def ago(**kw) -> str:
    return stamp(now() - dt.timedelta(**kw))


def test_一覧は終了コード0の配列だけを受け取る():
    assert runs.listing(0, [status(), "x"], "").runs[0]["name"] == "add-cache"
    assert runs.listing(1, None, "autodev: 動かない") == runs.Listing([], "autodev: 動かない")
    assert runs.listing(None, None, "").error == "終了コード None"


def test_終了コード0でも中身が違えばそう理由を出す():
    assert runs.listing(0, None, "").error == "JSON の配列が返らなかった"
    assert runs.listing(0, {"name": "a"}, "警告").error == "JSON の配列が返らなかった · 警告"
    assert runs.single(0, [], "") == (None, "JSON のオブジェクトが返らなかった")


def test_1つのランは終了コード1でも読み損じとして理由を返す():
    """終了コード 1 は、CLI の捕まえていない例外でも返る。ランが無いとは決めない。"""
    assert runs.single(0, {"name": "a"}, "") == ({"name": "a"}, None)
    assert runs.single(1, None, "Traceback") == (None, "Traceback")


def test_ランが消えたとみなすのは一覧が読めてそこにも無いときだけ():
    found = runs.Listing([status()])
    assert runs.gone("nope", found)
    assert not runs.gone("add-cache", found)
    assert not runs.gone("nope", runs.Listing([], "終了コード 1"))


@pytest.mark.parametrize(
    "broken",
    [
        {"tasks": 5},
        {"questions": "q1"},
        {"run": "running"},
        {"stack": []},
    ],
)
def test_型の違う欄があっても見出しを組める(broken):
    head = headline.build(status(**broken), now())
    assert head.run_name == "add-cache"


@pytest.mark.parametrize(
    ("field", "value"),
    [("round", "x"), ("round", None), ("progress", "broken"), ("progress", {"turns": "many"})],
)
def test_型の違う実行の欄は0や空として読む(field, value):
    st = status()
    st["tasks"][1]["executions"][0][field] = value
    got = runs.running(st, now())
    assert len(got) == 1 and isinstance(got[0].round, int) and isinstance(got[0].turns, int)


def test_number_は整数だけを通す():
    assert [runs.number(v) for v in (3, "3", None, True, 2.5)] == [3, 0, 0, 0, 0]


def test_Zで終わる時刻を読む():
    at = dt.datetime(2026, 10, 3, 1, 2, 3, tzinfo=dt.timezone.utc)
    assert runs.age("2026-10-03T01:02:03.000000Z", at + dt.timedelta(seconds=5)) == 5
    assert runs.age("…", at) is None
    assert runs.age(None, at) is None


def test_走っている実行を経過秒数と進み具合つきで取り出す():
    got = runs.running(status(), now())
    assert [(r.task, r.stage, r.round, r.turns, r.tool) for r in got] == [
        ("task1", "Judge", 2, 7, "Read")
    ]
    assert got[0].seconds is not None and 250 <= got[0].seconds <= 254


def test_statuslineに出すのは終えていないラン():
    assert runs.shown(status(), now())
    assert not runs.shown(status(run={**status()["run"], "phase": "finished"}), now())
    assert not runs.shown({"name": "old", "error": "ValueError: x"}, now())


def test_最後のイベントが古いランは回答待ちかパニックのときだけ出す():
    assert not runs.shown(quiet(updated_at=ago(hours=4)), now())
    assert runs.shown(status(updated_at=ago(hours=4)), now())
    panicked = quiet(updated_at=ago(days=2))
    panicked["run"]["phase"] = "panicked"
    assert runs.shown(panicked, now())


def test_終えていないランを先に並べ読めないランは最後():
    done = status(name="aaa", run={**status()["run"], "phase": "finished"})
    broken = {"name": "bbb", "error": "ValueError: x"}
    older = status(name="ccc", updated_at=ago(hours=1))
    got = runs.order([broken, done, older, status(name="zzz")], now())
    assert [runs.name_of(st) for st in got] == ["zzz", "ccc", "aaa", "bbb"]


def test_見出しは走っている実行を短い名前とラウンドで出す():
    head = headline.build(status(), now())
    assert (head.state, head.doing, head.turns, head.tool, head.overview_pr) == (
        headline.State.RUNNING,
        "task1 ジャッジ r2",
        7,
        "Read",
        4,
    )
    assert head.waiting == ("q1",)


def test_同じタスクで並んで走るレビューは1つにまとめターン数は出さない():
    st = status()
    review = {**st["tasks"][1]["executions"][0], "stage": "Review", "round": 1}
    st["tasks"][1]["executions"] = [review, {**review, "stage": "AdversarialReview"}]
    head = headline.build(st, now())
    assert (head.doing, head.turns) == ("task1 レビュー r1", 0)


def test_走っている実行が無ければフェーズだけを出し止まっているとは言わない():
    head = headline.build(quiet(), now())
    assert (head.state, head.doing, head.waiting) == (headline.State.QUIET, "実行中", ())


def test_パニックは走っている実行より先に見せる():
    st = status(run={**status()["run"], "phase": "panicked"})
    assert headline.build(st, now()).state is headline.State.PANICKED


def test_積んだ数の分母に止めた_引き継がれた_破棄したタスクを入れない():
    st = status()
    st["tasks"][3]["status"] = "dropped"
    head = headline.build(st, now())
    assert (head.stacked, head.total, head.escalated) == (1, 3, 1)


@pytest.mark.parametrize(
    ("task", "want"),
    [
        ({"title": "パーサ", "kind": "implementation"}, "パーサ"),
        ({"title": None, "kind": "planning"}, "計画"),
        ({"title": None, "kind": "git"}, "git 管理"),
    ],
)
def test_件名の無いタスクは種類の名前で呼ぶ(task, want):
    assert runs.task_label(task) == want
