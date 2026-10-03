from __future__ import annotations

from .base import Text, _non_blank


class Instruction(Text):
    """`/autodev` に渡された指示の本文。"""

    def _check(self) -> None:
        _non_blank("指示", self.value)
