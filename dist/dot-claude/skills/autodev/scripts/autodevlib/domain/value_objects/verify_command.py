from __future__ import annotations

from .base import Text, _non_blank


class VerifyCommand(Text):
    """検証コマンド 1 本。`bash -lc` で流すので、パイプやリダイレクトを含んでよい。"""

    def _check(self) -> None:
        _non_blank("検証コマンド", self.value)
