"""レビュー記録の要約（`hud/core/review.py`）と段のログの読み方（`hud/core/activity.py`）。"""

from __future__ import annotations

import json

from hud.core import activity, review


def test_レビューは件数と未解決の指摘を返す():
    data = {
        "items": {
            "r1": {
                "status": "open",
                "rating": "must-fix",
                "location": "a.py:3",
                "review": "境界で落ちる\n詳細",
            },
            "r2": {"status": "closed", "rating": "nit", "location": "b.py:1", "review": "名前"},
        }
    }
    got = review.summarize(data)
    assert got == review.Review(
        counts={"closed": 1, "open": 1},
        open=[review.Finding("r1", "must-fix", "a.py:3", "境界で落ちる")],
    )


def test_指摘が無ければNone():
    assert review.summarize({"items": {}}) is None
    assert review.summarize(None) is None


def test_ログからツールの呼び出しと発言の1行目を拾う():
    events = [
        {"type": "system", "subtype": "init"},
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "テストを読む\n続き"}]},
        },
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "name": "Bash", "input": {"command": "uv run pytest\n-q"}}
                ]
            },
        },
    ]
    lines = [json.dumps(e) for e in events] + ["not json"]
    assert activity.parse(lines, limit=10) == [
        activity.Activity(activity.Kind.TEXT, "テストを読む"),
        activity.Activity(activity.Kind.TOOL, "Bash uv run pytest"),
    ]


def test_ログは末尾の件数だけ返す():
    lines = [
        json.dumps(
            {"type": "assistant", "message": {"content": [{"type": "text", "text": str(i)}]}}
        )
        for i in range(5)
    ]
    assert [a.text for a in activity.parse(lines, limit=2)] == ["3", "4"]
