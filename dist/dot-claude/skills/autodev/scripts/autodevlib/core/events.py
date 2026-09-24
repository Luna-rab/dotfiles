"""claude の `--output-format stream-json` が流すイベントの読み方。"""

from __future__ import annotations

from typing import Any


def guard_denials(event: dict[str, Any]) -> int:
    """PreToolUse のフック（`hooks/deny-writes.py`）に拒まれたツール呼び出しの数。

    **PreToolUse のフックは stream-json にイベントを出さない。** 拒まれた呼び出しは、次の
    `user` イベントの `tool_result` に `is_error: true` と、`PreToolUse:<ツール> hook error` で
    始まる本文で残る（Claude Code 2.1.281 で確認）。`hook_response` の `exit_code` を数えると、
    SessionStart のフックが失敗しただけでも拒否に数えてしまう。
    """
    if event.get("type") != "user":
        return 0
    content = (event.get("message") or {}).get("content")
    if not isinstance(content, list):
        return 0
    return sum(
        1
        for block in content
        if isinstance(block, dict)
        and block.get("type") == "tool_result"
        and block.get("is_error")
        and _text(block.get("content")).startswith("PreToolUse:")
    )


def _text(content: Any) -> str:
    """`tool_result` の本文。文字列のことも、`{"type": "text"}` の並びのこともある。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(str(b.get("text") or "") for b in content if isinstance(b, dict))
    return ""
