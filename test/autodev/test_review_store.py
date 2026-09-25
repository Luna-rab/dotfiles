"""`review.json` のラウンド照合（`ports/review_store.py` と `ports/evidence.py`）。

`done()` は `round` を文字列で書き、`reviewers_seen()` は読むときに文字列へ直す。
review.json は追跡しないファイルで手でも直せるので、どちらの側も型を仮定しない。
**照合が外れると完了チェック④が「r1: review:normal が走っていない」と言い、正しく解消した
タスクが全部 blocked になる。** 落ちるのはランを 1 本通したときだけで、ほかの検査は
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


def test_数値のラウンドでも走り終えたレビューステージを拾う():
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


def test_ラウンドのキーは文字列にそろえる():
    """キーは `app/review_loop.py` が作る `str(index)` と引き当てる。"""
    assert evidence._reviewers_by_round({"runs": ROUND_1}) == {
        "1": ("review:normal", "review:adversarial")
    }


def test_数値のラウンドでもレビューステージの体数が足りていると判定する():
    """証拠を集める側と読む側でラウンドの型が揃っていることを、完了チェック④の合否で固定する。"""
    report = verdict.Report()
    verdict.check_reviewer_count(
        report,
        facts_from({"items": {}, "runs": ROUND_1}),
        "standard",
        [("1", ["review:normal", "review:adversarial"])],
    )
    assert report.ok, report.lines()
    assert report.checks[0].detail == "走り終えたレビュー 2 回"


# --- driver だけが呼ぶ操作 ----------------------------------------------------


def stored(path: str) -> dict[str, Any]:
    data = review_store.read(path)
    assert data is not None, path
    return data


def test_落ちた完了チェックをmustfixの指摘にする(tmp_path, monkeypatch):
    monkeypatch.delenv(review_store.JUDGE_TOKEN_ENV, raising=False)
    path = str(tmp_path / "review.json")
    review_id = review_store.add_gate_failure(path, "verify", "落ちた: pytest", "2")
    item = stored(path)["items"][review_id]
    assert (item["status"], item["rating"], item["location"]) == (
        "open",
        "must-fix",
        "完了チェック verify",
    )


def test_移した指摘は移した先で未解決として立て直す(tmp_path, monkeypatch):
    """ジャッジトークンが無くても動く。driver が in-process で呼ぶので、ステージからは届かない。"""
    monkeypatch.delenv(review_store.JUDGE_TOKEN_ENV, raising=False)
    source, target = str(tmp_path / "t2.json"), str(tmp_path / "t5.json")
    review_id, _ = review_store.add(
        source,
        reviewer="review:normal",
        rating="should-fix",
        location="a.py:3",
        body="境界",
        round_label="1",
    )
    moved = review_store.move(source, review_id, "task5", "範囲の外")
    new_id = review_store.add_carried(target, moved, "task2")

    left = stored(source)["items"][review_id]
    assert (left["status"], left["movedTo"]) == ("moved", "task5")
    carried = stored(target)["items"][new_id]
    assert (carried["status"], carried["rating"], carried["movedFrom"]) == (
        "open",
        "should-fix",
        f"task2/{review_id}",
    )
