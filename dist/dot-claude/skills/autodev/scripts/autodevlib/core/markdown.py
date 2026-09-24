"""state.json と config.json から、テンプレートのマーカーに差す塊を組み立てる。

埋めるのは `${tasks}` `${held}` `${decisions}` `${deferrals}` `${verify}` `${test_globs}` で、
**組み立てるのはここ、テンプレートに埋めるのは `ports/templates.py`** である。
"""

from __future__ import annotations

from typing import Any

STATUS_LABEL = {
    "pending": "未着手",
    "running": "進行中",
    "stacked": "スタック済み",
    "blocked": "要確認",
    "failed": "失敗",
}


def tasks_block(st: dict[str, Any]) -> str:
    """タスクの一覧。**進行状態の唯一の出所は state.json である。**"""
    if not st.get("tasks"):
        return "まだ割っていない。"
    lines = ["| # | 状態 | PR | 階層 | 内容 |", "| --- | --- | --- | --- | --- |"]
    for item in st["tasks"]:
        pr = f"#{item['pr']}" if item.get("pr") else "—"
        label = STATUS_LABEL.get(item["status"], item["status"])
        lines.append(f"| {item['id']} | {label} | {pr} | {item['tier']} | {item['subject']} |")
    return "\n".join(lines)


def held_block(st: dict[str, Any]) -> str:
    held = [i for i in st.get("tasks", []) if i["status"] in ("blocked", "failed")]
    if not held:
        return ""
    lines = ["## 要対応", ""]
    for item in held:
        reason = item.get("reason") or "理由の記録なし"
        lines.append(f"- {item['id']}（{STATUS_LABEL[item['status']]}）: {reason}")
    return "\n".join(lines) + "\n\n"


def entries_block(st: dict[str, Any], key: str, heading: str) -> str:
    """`decisions` / `deferrals` の一覧。無ければ節ごと出さない。"""
    entries = st.get(key) or []
    if not entries:
        return ""
    lines = [f"## {heading}", ""]
    lines += [f"- {entry['body']}" for entry in entries]
    return "\n".join(lines) + "\n\n"


def bullets(items: list[str], empty: str) -> str:
    return "\n".join(f"- `{item}`" for item in items) if items else empty
