"""レビューステージの体数、停滞の検知、次の手の選び方（`core/review_policy.py`）。

体数が足りないと完了チェック④が落ち、多すぎるとトークンを無駄に使う。ラウンドに上限が無いので、
停滞を見逃すと直らない指摘のまま修正を回し続ける。
"""

from __future__ import annotations

from typing import Any

from autodevlib.core import review_policy

NORMAL = "review:normal"
ADVERSARIAL = "review:adversarial"


def items(*pairs: tuple[str, str]) -> dict[str, Any]:
    """`(status, rating)` の並びから review.json の `items` を組む。"""
    return {"items": {str(i): {"status": s, "rating": r} for i, (s, r) in enumerate(pairs, 1)}}


# --- 体数 --------------------------------------------------------------------


def test_lightは通常レビュー1体():
    assert review_policy.expected_reviewers("light", "code", 1) == [NORMAL]


def test_standardの1ラウンド目は通常と敵対的の2体():
    assert review_policy.expected_reviewers("standard", "code", 1) == [NORMAL, ADVERSARIAL]


def test_standardの2ラウンド目以降は通常1体():
    """見る差分が「open を直した分」だけになるので、敵対的をもう一度回さない。"""
    assert review_policy.expected_reviewers("standard", "code", 2) == [NORMAL]
    assert review_policy.expected_reviewers("standard", "code", 3) == [NORMAL]


def test_docsだけの変更は通常1体():
    assert review_policy.expected_reviewers("standard", "docs", 1) == [NORMAL]


# --- 停滞 --------------------------------------------------------------------


def test_修正のたびに未解決の指摘だけ数を足す():
    data = {
        "items": {"r1": {"status": "open"}, "r2": {"status": "closed"}, "r3": {"status": "open"}}
    }
    assert review_policy.bump_attempts({"r1": 1}, data) == {"r1": 2, "r3": 1}


def test_修正を2回受けても未解決なら停滞():
    data = {
        "items": {"r1": {"status": "open"}, "r2": {"status": "open"}, "r3": {"status": "closed"}}
    }
    assert review_policy.stale({"r1": 2, "r2": 1, "r3": 5}, data) == ["r1"]


def test_ラウンドごとに新しく立った指摘は停滞に数えない():
    """新しい指摘が出続けても、それぞれは修正を受けて閉じていくので回し続ける。"""
    data = {"items": {"r1": {"status": "closed"}, "r2": {"status": "open"}}}
    assert review_policy.stale({"r1": 1}, data) == []


def test_ジャッジの分類から手を選ぶ():
    assert review_policy.route({"cause": "tests"}, []) == "tests"
    assert review_policy.route({"cause": "approach"}, ["r1"]) == "approach"
    assert review_policy.route({"cause": "scope"}, []) == "replan"
    assert review_policy.route({"cause": "ambiguous"}, []) == "ask"


def test_分類が無ければ修正を続ける():
    assert review_policy.route(None, []) == "fix"


def test_停滞しているのに分類が無ければ再計画に回す():
    """再計画は回数に上限があり、超えたら人に聞く。分類が無いまま修正を繰り返させない。"""
    assert review_policy.route(None, ["r3"]) == "replan"
    assert review_policy.route({"cause": "unknown"}, ["r3"]) == "replan"


# --- 数え方 ------------------------------------------------------------------


def test_openとmustfixとclosedとrejectedを数える():
    data = items(
        ("open", "must-fix"),
        ("open", "nit"),
        ("closed", "must-fix"),
        ("closed", "should-fix"),
        ("rejected", "nit"),
    )
    assert review_policy.tally(data) == {
        "total": 5,
        "open": 2,
        "openMustFix": 1,
        "closed": 2,
        "rejected": 1,
        "moved": 0,
    }


def test_移した指摘は未解決に数えない():
    """移した先のタスクで未解決として立て直すので、移した元は解消できる。"""
    counted = review_policy.tally(items(("moved", "must-fix"), ("open", "nit")))
    assert (counted["open"], counted["openMustFix"], counted["moved"]) == (1, 0, 1)


def test_指摘0件でも数が揃う():
    """指摘 0 件で終わったラウンドは 1 件も書き込まない。欠けたキーを読む側が作らない。"""
    assert review_policy.tally({"items": {}}) == {
        "total": 0,
        "open": 0,
        "openMustFix": 0,
        "closed": 0,
        "rejected": 0,
        "moved": 0,
    }


def test_openでないmustfixはopenMustFixに数えない():
    data = items(("closed", "must-fix"), ("rejected", "must-fix"))
    counted = review_policy.tally(data)
    assert counted["openMustFix"] == 0
    assert counted["open"] == 0
