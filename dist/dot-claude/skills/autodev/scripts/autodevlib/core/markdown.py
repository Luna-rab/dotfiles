"""state.json と config.json から、本文のマーカーに差す塊を組み立てる。

- 概要 PR: まとめステージが書いた本文の `<!-- autodev:tasks -->` などを `fill_markers` が置き換える
- brief: `${verify}` `${test_globs}` の塊（`bullets`）をここで組み、`ports/templates.py` が埋める
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

STATUS_LABEL = {
    "pending": "未着手",
    "running": "実行中",
    "stacked": "スタック済み",
    "dropped": "取り下げ",
    "blocked": "要確認",
    "failed": "失敗",
}


def tasks_block(st: dict[str, Any]) -> str:
    """タスクの一覧。**進行状態の唯一の出所は state.json である。**"""
    if not st.get("tasks"):
        return "タスクはありません。"
    lines = ["| # | 状態 | PR | 階層 | 内容 |", "| --- | --- | --- | --- | --- |"]
    for item in st["tasks"]:
        pr = f"#{item['pr']}" if item.get("pr") else "—"
        label = STATUS_LABEL.get(item["status"], item["status"])
        lines.append(f"| {item['id']} | {label} | {pr} | {item['tier']} | {item['subject']} |")
    return "\n".join(lines)


def held_block(st: dict[str, Any]) -> str:
    held = [i for i in st.get("tasks") or [] if i["status"] in ("blocked", "failed")]
    if not held:
        return "要対応はありません。"
    lines = []
    for item in held:
        reason = item.get("reason") or "理由の記録なし"
        lines.append(f"- {item['id']}（{STATUS_LABEL[item['status']]}）: {reason}")
    return "\n".join(lines)


def entries_block(st: dict[str, Any], key: str, empty: str) -> str:
    """`decisions` / `deferrals` の一覧。無ければ `empty` を返す。"""
    entries = st.get(key) or []
    if not entries:
        return empty
    return "\n".join(f"- {entry['body']}" for entry in entries)


#: 概要 PR の本文に置くマーカーと、差す中身を組む関数
MARKERS: dict[str, Callable[[dict[str, Any]], str]] = {
    "<!-- autodev:tasks -->": tasks_block,
    "<!-- autodev:held -->": held_block,
    "<!-- autodev:deferrals -->": lambda st: entries_block(
        st, "deferrals", "スコープ外にしたものはありません。"
    ),
    "<!-- autodev:decisions -->": lambda st: entries_block(
        st, "decisions", "判断ログはありません。"
    ),
}


def fill_markers(body: str, st: dict[str, Any]) -> str:
    """本文中の `MARKERS` を state.json の中身に置き換える。

    **`string.Template` を使わない。** 本文はステージが書いたもので、通すと `$$` が `$` になり
    `${tasks}` が消える。一覧に無いマーカーはそのまま残し、マーカーが無くても足さない。

    **1 回の走査で置き換え、差した中身は読み直さない。** マーカーを 1 つずつ `str.replace` すると、
    タスクの題や判断ログにマーカーの文字列が入っていたとき、それも置き換わる。

    **置き換えるのは 1 行に単独で置いたマーカーだけである。** 行の途中にあるもの（本文や起動時の
    指示がマーカーを `` `<!-- autodev:tasks -->` `` と説明している箇所）まで置き換えると、表の
    セルや文の中に表と箇条書きが入って本文が崩れる。
    """
    blocks: dict[str, str] = {}

    def block(found: re.Match[str]) -> str:
        marker = found.group("marker")
        if marker not in blocks:
            blocks[marker] = MARKERS[marker](st)
        return blocks[marker]

    return _MARKER_RE.sub(block, body)


_MARKER_RE = re.compile(
    r"^[ \t]*(?P<marker>" + "|".join(re.escape(marker) for marker in MARKERS) + r")[ \t]*$",
    re.MULTILINE,
)


def bullets(items: list[str], empty: str) -> str:
    return "\n".join(f"- `{item}`" for item in items) if items else empty
