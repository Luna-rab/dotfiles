from __future__ import annotations

from .base import non_blank

#: autodev が作った概要 PR と分かる印。ステージには書かせず、driver が付ける
OVERVIEW_PR_MARK = "[autodev]"


def overview_pr_title(title: str) -> str:
    """WriteOverview が書いた 1 行に印を付ける。指示書に反して印まで書いてきても、二重に付けない。"""
    line = title.strip().removeprefix(OVERVIEW_PR_MARK).strip()
    non_blank("概要 PR のタイトル", line)
    return f"{OVERVIEW_PR_MARK} {line}"
