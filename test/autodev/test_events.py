"""フックに拒まれたツール呼び出しの数え方（`core/events.py`）。

driver はこれが 10 回に達した段を打ち切る。数え損なうと、契約を読み違えた段が往復の上限まで
走り続け、数えすぎると、SessionStart のフックが失敗しただけの段を打ち切る。
"""

from __future__ import annotations

from autodevlib.core import events


def tool_result(content, *, is_error: bool = True) -> dict:
    return {
        "type": "user",
        "message": {"content": [{"type": "tool_result", "is_error": is_error, "content": content}]},
    }


def test_PreToolUseの拒否を数える():
    event = tool_result("PreToolUse:Write hook error: [deny.sh]: 書き込みは禁止されている\n")
    assert events.guard_denials(event) == 1


def test_本文がtextの並びでも数える():
    event = tool_result([{"type": "text", "text": "PreToolUse:Edit hook error: tests/ は書けない"}])
    assert events.guard_denials(event) == 1


def test_SessionStartのフックの失敗は数えない():
    event = {
        "type": "system",
        "subtype": "hook_response",
        "hook_event": "SessionStart",
        "exit_code": 127,
    }
    assert events.guard_denials(event) == 0


def test_ツール自身のエラーは数えない():
    assert events.guard_denials(tool_result("File does not exist.")) == 0
    assert events.guard_denials(tool_result("PreToolUse:Write hook error", is_error=False)) == 0
