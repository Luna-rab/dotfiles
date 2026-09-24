"""タスクのレビュー記録（`tasks/<タスク>/review.json`）の要約。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Finding:
    """未解決の指摘 1 件。"""

    key: str
    rating: str
    location: str
    #: 指摘の本文の 1 行目
    summary: str


@dataclass(frozen=True)
class Review:
    #: status ごとの件数（open / closed / rejected）
    counts: dict[str, int]
    open: list[Finding]


def summarize(data: Any) -> Review | None:
    """指摘が 1 件も無ければ None。"""
    items = (data or {}).get("items") if isinstance(data, dict) else None
    if not isinstance(items, dict) or not items:
        return None
    counts: dict[str, int] = {}
    found: list[Finding] = []
    for key, item in items.items():
        status = str(item.get("status", "?"))
        counts[status] = counts.get(status, 0) + 1
        if status == "open":
            lines = str(item.get("review") or "").strip().splitlines()
            found.append(
                Finding(
                    key=str(key),
                    rating=str(item.get("rating", "?")),
                    location=str(item.get("location", "")),
                    summary=lines[0] if lines else "",
                )
            )
    return Review(counts=dict(sorted(counts.items())), open=found)
