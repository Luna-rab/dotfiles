"""hud の検査で使う入力。Claude Code が渡す JSON と、autodev の ランディレクトリ。"""

from __future__ import annotations

import datetime as dt
import json


def session(now: float) -> dict:
    return {
        "model": {"display_name": "Opus 5.5"},
        "effort": {"level": "high"},
        "workspace": {"current_dir": "/nonexistent/dotfiles"},
        "context_window": {"used_percentage": 50},
        "cost": {"total_cost_usd": 3.214},
        "rate_limits": {
            # 窓の 6 割が過ぎたところで 72% 使っている → 12 ポイント使いすぎ
            "five_hour": {"used_percentage": 72, "resets_at": now + 5 * 3600 * 0.4 + 30},
            "seven_day": {"used_percentage": 31},
        },
        "pr": {"number": 22, "url": "https://example.com/pull/22", "review_state": "pending"},
    }


def state(**over) -> dict:
    """ステージ review:adversarial が 4 分 12 秒走っているラン。task2 が実行中。"""
    now = dt.datetime.now().astimezone()
    data = {
        "name": "range-field",
        "overviewPr": 4,
        "updatedAt": now.isoformat(),
        "running": {
            "review:adversarial": {
                "task": "task2",
                "round": "1",
                "at": (now - dt.timedelta(minutes=4, seconds=12)).isoformat(),
                "turns": 26,
                "tool": "Read",
            }
        },
        "tasks": [
            {"id": "task1", "subject": "パーサの土台", "status": "stacked", "pr": 5},
            {
                "id": "task2",
                "subject": "範囲指定",
                "status": "running",
                "tier": "standard",
                "acceptance": "空入力で None を返す",
                "stages": [
                    {"name": "testgen", "round": "0", "ok": True},
                    {"name": "impl", "round": "0", "ok": True},
                    {"name": "review:normal", "round": "1", "ok": True},
                ],
            },
            {"id": "task3", "subject": "CLI", "status": "pending"},
            {"id": "task4", "subject": "移行", "status": "blocked", "reason": "受入条件が曖昧"},
        ],
    }
    data.update(over)
    return data


def write_run(root, **over) -> None:
    """`root/<ラン名>/` に state.json・review.json・ステージのログを置く。"""
    st = state(**over)
    run = root / st["name"]
    (run / "tasks" / "task2").mkdir(parents=True)
    (run / "logs" / "task2").mkdir(parents=True)
    (run / "state.json").write_text(json.dumps(st), encoding="utf-8")
    review = {
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
    (run / "tasks" / "task2" / "review.json").write_text(json.dumps(review), encoding="utf-8")
    events = [
        {"type": "system", "subtype": "init"},
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "テストを読む\n続き"}]},
        },
        {
            "type": "assistant",
            "message": {
                "content": [{"type": "tool_use", "name": "Edit", "input": {"file_path": "a.py"}}]
            },
        },
    ]
    log = run / "logs" / "task2" / "review-adversarial-1.jsonl"
    log.write_text("\n".join(json.dumps(e) for e in events) + "\nnot json\n", encoding="utf-8")
    prompt = run / "logs" / "task2" / "review-adversarial-1.prompt.md"
    prompt.write_text(
        "# プロンプト\n\nあなたは敵対的レビューのステージである。\n", encoding="utf-8"
    )
    (run / "logs" / "task0").mkdir(parents=True)
    (run / "logs" / "task0" / "plan-0.jsonl").write_text("", encoding="utf-8")
    (run / "prose").mkdir()
    (run / "prose" / "overview.md").write_text("範囲を指定して切り出す。", encoding="utf-8")
