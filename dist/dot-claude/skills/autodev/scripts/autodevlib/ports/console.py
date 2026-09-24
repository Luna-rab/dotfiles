"""呼んだ側に見せる出力。"""

from __future__ import annotations

import json
import sys
from typing import NoReturn


def die(message: str, code: int = 1) -> NoReturn:
    """呼んだ側へ理由を出して終える。**ここから戻らない**（呼び出し側の分岐が減る）。"""
    print(f"autodev: {message}", file=sys.stderr)
    raise SystemExit(code)


def info(message: str) -> None:
    print(f"autodev: {message}", file=sys.stderr)


def emit(payload: object, pretty: bool = False) -> None:
    """機械が読む出力は標準出力へ。進行の報せは info() で標準エラーへ出す。"""
    print(json.dumps(payload, ensure_ascii=False, indent=2 if pretty else None))
