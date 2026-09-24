"""段のログ（`claude --output-format stream-json` の 1 行 1 イベント）から、何をしたかを拾う。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum

#: ツールの呼び出しで、何に向けて呼んだかとして拾う引数（先に見つかったもの）
TARGET_KEYS = ("file_path", "command", "pattern", "path", "url", "description")


class Kind(Enum):
    TOOL = "tool"
    TEXT = "text"


@dataclass(frozen=True)
class Activity:
    kind: Kind
    #: ツールなら「名前 対象」、発言なら 1 行目
    text: str


def parse(lines: list[str], limit: int) -> list[Activity]:
    """末尾の `limit` 件を返す。JSON として読めない行は飛ばす。"""
    out: list[Activity] = []
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get("type") != "assistant":
            continue
        for block in (event.get("message") or {}).get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                out.append(Activity(Kind.TOOL, _tool(block)))
            elif block.get("type") == "text" and str(block.get("text") or "").strip():
                out.append(Activity(Kind.TEXT, str(block["text"]).strip().splitlines()[0][:200]))
    return out[-limit:]


def _tool(block: dict) -> str:
    args = block.get("input") or {}
    target = ""
    for key in TARGET_KEYS:
        if isinstance(args, dict) and args.get(key):
            target = str(args[key]).splitlines()[0]
            break
    return f"{block.get('name', '?')} {target[:160]}".rstrip()
