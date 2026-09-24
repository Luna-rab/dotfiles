"""レビューステージの体数と打ち切りの条件（`core/review_policy.py`）。

体数が足りないと完了チェック④が落ちてランが止まり、多すぎるとトークンを無駄に使う。打ち切りが
緩むと、指摘が減らないタスクで上限までラウンドを回し続ける。
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


# --- 打ち切り ----------------------------------------------------------------


def test_ラウンドの上限は3巡():
    """1 増えると、タスク 1 本あたりレビュー・ジャッジ・修正が 1 巡増える。"""
    assert review_policy.MAX_ROUNDS == 3


def test_上限に届いたら打ち切る():
    tally = {"open": 2, "openMustFix": 1}
    reason = review_policy.stop_reason(3, tally, 5, 3)
    assert reason == "ラウンド上限（未解決 2 件 / must-fix 1 件）"


def test_総数もmustfixも減らなければ打ち切る():
    tally = {"open": 3, "openMustFix": 2}
    assert (
        review_policy.stop_reason(1, tally, 3, 2)
        == "未解決の指摘が前のラウンドから減っていない（未解決 3 件）"
    )


def test_mustfixだけ減ったら続ける():
    """must-fix が減っているなら、総数が同じでも直る見込みがある。"""
    tally = {"open": 3, "openMustFix": 1}
    assert review_policy.stop_reason(1, tally, 3, 2) is None


def test_総数だけ減ったら続ける():
    tally = {"open": 2, "openMustFix": 2}
    assert review_policy.stop_reason(1, tally, 3, 2) is None


def test_上限の前でも両方増えていれば打ち切る():
    tally = {"open": 4, "openMustFix": 3}
    assert (
        review_policy.stop_reason(2, tally, 3, 2)
        == "未解決の指摘が前のラウンドから減っていない（未解決 4 件）"
    )


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
    }


def test_指摘0件でも数が揃う():
    """指摘 0 件で終わったラウンドは 1 件も書き込まない。欠けたキーを読む側が作らない。"""
    assert review_policy.tally({"items": {}}) == {
        "total": 0,
        "open": 0,
        "openMustFix": 0,
        "closed": 0,
        "rejected": 0,
    }


def test_openでないmustfixはopenMustFixに数えない():
    data = items(("closed", "must-fix"), ("rejected", "must-fix"))
    counted = review_policy.tally(data)
    assert counted["openMustFix"] == 0
    assert counted["open"] == 0
