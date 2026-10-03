from __future__ import annotations

from dataclasses import dataclass

from .base import _non_blank


@dataclass(frozen=True)
class DeferredCall:
    """計画ステージの ask が PreToolUse のフックの defer で止まった呼び出し。"""

    tool_use_id: str
    question: str

    def __post_init__(self) -> None:
        _non_blank("tool_use_id", self.tool_use_id)
