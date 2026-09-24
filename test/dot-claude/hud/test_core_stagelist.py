"""autodev-watch のステージのリスト（`hud/core/stagelist.py`）。"""

from __future__ import annotations

import pytest
from hud.core import stagelist
from hud.core.pipeline import Mark
from hud.core.runs import Stage


def stage(name: str, round_label: str, task: str = "task1") -> Stage:
    return Stage(name=name, task=task, round=round_label, seconds=10.0, turns=0, tool="")


def rows(items: list[stagelist.StageItem]) -> list[tuple[str, str]]:
    return [(i.label, i.mark.value) for i in items]


def test_レビュー2つをまとめずにステージを1行ずつ並べる():
    task = {
        "id": "task1",
        "stages": [
            {"name": "testgen", "round": "0", "ok": True},
            {"name": "impl", "round": "0", "ok": True},
            {"name": "review:normal", "round": "1", "ok": True},
        ],
    }
    got = stagelist.for_task(task, [stage("review:adversarial", "1")])
    assert rows(got) == [
        ("テスト作成 r0", "done"),
        ("実装 r0", "done"),
        ("通常レビュー r1", "done"),
        ("敵対的レビュー r1", "current"),
        ("ジャッジ", "next"),
        ("PR 本文", "next"),
    ]


def test_これからのステージはコードを持たずキーで見分ける():
    got = stagelist.for_task({"id": "task1", "stages": []}, [])
    assert [i.code for i in got] == [None] * 5
    assert got[0].key == "next:テスト作成"


def test_失敗したステージを印で分ける():
    task = {"id": "task1", "stages": [{"name": "impl", "round": "0", "ok": False}]}
    assert stagelist.for_task(task, [])[0].mark is Mark.FAILED


def test_タスクに属さないステージはログのファイル名から組む():
    got = stagelist.for_run(
        ["plan-0.jsonl", "summary-0.jsonl"], [stage("summary", "0", task="task0")]
    )
    assert rows(got) == [("計画 r0", "done"), ("まとめ r0", "current")]


@pytest.mark.parametrize(
    ("name", "want"),
    [
        ("review-normal-1.jsonl", ("review:normal", "1")),
        ("impl-0-2.jsonl", ("impl", "0-2")),
        ("pr-body-0.jsonl", ("pr-body", "0")),
        ("impl-0.jsonl.err", None),
        ("unknown-1.jsonl", None),
    ],
)
def test_ログのファイル名からステージとラウンドを読む(name, want):
    assert stagelist.parse_log_name(name) == want
