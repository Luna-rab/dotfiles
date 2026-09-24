"""ファイルの読み書き。"""

from __future__ import annotations

import contextlib
import json
import os


def write_text(path: str, body: str) -> str:
    """親ディレクトリを作ってから書く。途中で落ちても壊れた中身が残らないよう一時ファイル経由。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(body)
    os.replace(tmp, path)
    return path


def read_text(path: str, default: str | None = None) -> str:
    if not os.path.exists(path) and default is not None:
        return default
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def write_json(path: str, payload: object) -> str:
    return write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def remove(path: str) -> None:
    """あれば消す。無いときは何もしない。"""
    with contextlib.suppress(FileNotFoundError):
        os.remove(path)


def read_json(path: str, default: object = None) -> object:
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)
