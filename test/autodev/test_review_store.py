"""`review.json` のラウンド照合（`ports/review_store.py` と `ports/evidence.py`）。

`done()` は `round` を文字列で書き、`reviewers_seen()` は読むときに文字列へ直す。
review.json は追跡しないファイルで手でも直せるので、どちらの側も型を仮定しない。
**照合が外れると検査④が「r1: review:normal が走っていない」と言い、正しく決着した
タスクが全部 blocked になる。** 落ちるのは run を 1 本通したときだけで、ほかの検査は
全部通る。
"""

from __future__ import annotations

from typing import Any

from autodevlib.core import verdict
from autodevlib.core.verdict import Evidence
from autodevlib.ports import evidence, review_store

ROUND_1: list[dict[str, Any]] = [
    {"reviewer": "review:normal", "round": 1},
    {"reviewer": "review:adversarial", "round": 1},
]


def facts_from(review: dict[str, Any]) -> Evidence:
    """`ports/evidence.collect()` が組むのと同じ証拠。①〜③⑤は通る値で埋める。"""
    return Evidence(
        stage_ok=True,
        stage_detail="",
        parent="main",
        branch="stack/demo--task-1",
        commits=1,
        tests_since="abc1234",
        changed_since_tests=(),
        review_path="/state/autodev/demo/tasks/task1/review.json",
        review=review,
        reviewers_by_round=evidence._reviewers_by_round(review),
        adversarial_ran=review_store.adversarial_ran(review),
        review_runs=len(review["runs"]),
    )


def test_数値のラウンドでも走り終えたレビュアーを拾う():
    data = {"runs": [{"reviewer": "review:normal", "round": 1}]}
    assert review_store.reviewers_seen(data, "1") == ["review:normal"]


def test_文字列のラウンドを拾う():
    data = {"runs": [{"reviewer": "review:normal", "round": "1"}]}
    assert review_store.reviewers_seen(data, "1") == ["review:normal"]


def test_別のラウンドは混ぜない():
    data = {
        "runs": [
            {"reviewer": "review:adversarial", "round": 1},
            {"reviewer": "review:normal", "round": 2},
        ]
    }
    assert review_store.reviewers_seen(data, "2") == ["review:normal"]


def test_ラウンドを渡さなければ全部数える():
    data = {"runs": ROUND_1}
    assert review_store.reviewers_seen(data) == ["review:normal", "review:adversarial"]


def test_ラウンドの鍵は文字列にそろえる():
    """鍵は `app/review_loop.py` が作る `str(index)` と引き当てる。"""
    assert evidence._reviewers_by_round({"runs": ROUND_1}) == {
        "1": ("review:normal", "review:adversarial")
    }


def test_数値のラウンドでもレビュアーの体数が足りていると判定する():
    """証拠を集める側と読む側でラウンドの型が揃っていることを、検査④の合否で固定する。"""
    report = verdict.Report()
    verdict.check_reviewer_count(
        report,
        facts_from({"items": {}, "runs": ROUND_1}),
        "standard",
        [("1", ["review:normal", "review:adversarial"])],
    )
    assert report.ok, report.lines()
    assert report.checks[0].detail == "走り終えたレビュー 2 回"
