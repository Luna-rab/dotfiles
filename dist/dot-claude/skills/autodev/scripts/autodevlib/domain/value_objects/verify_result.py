from __future__ import annotations

from dataclasses import dataclass

from .verify_command import VerifyCommand


@dataclass(frozen=True)
class VerifyResult:
    command: VerifyCommand
    exit_code: int
    #: 出力の末尾
    tail: str = ""

    @property
    def passed(self) -> bool:
        return self.exit_code == 0
