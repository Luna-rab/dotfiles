"""autodev の ランディレクトリ（`~/.local/state/autodev/<ラン名>/`）を読む。**何も書き込まない。**

driver が書き換えている最中のファイルに当たることがあるので、読めないものは飛ばす。
"""

from __future__ import annotations

import json
import os
from typing import Any


def state_root() -> str:
    """ランディレクトリ。**出所は `autodevlib/config/paths.py` の `state_root()`** と同じ規則。"""
    override = os.environ.get("AUTODEV_STATE_DIR")
    if override:
        return os.path.abspath(override)
    xdg = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local/state")
    return os.path.join(xdg, "autodev")


def read_json(path: str) -> Any:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def read_states() -> list[dict]:
    """全ランの `state.json`。ラン名の順。"""
    root = state_root()
    if not os.path.isdir(root):
        return []
    loaded = (
        read_json(os.path.join(root, name, "state.json")) for name in sorted(os.listdir(root))
    )
    return [st for st in loaded if isinstance(st, dict)]


def read_review(run_name: str, task: str) -> Any:
    return read_json(os.path.join(state_root(), run_name, "tasks", task, "review.json"))


def log_path(run_name: str, task: str, running: list[tuple[str, str]]) -> str | None:
    """そのタスクのログ。`running`（走っているステージの名前とラウンド）のログがあればそれ、無ければ最後に書かれたもの。"""
    root = os.path.join(state_root(), run_name, "logs", task)
    for name, round_label in running:
        path = os.path.join(root, f"{name.replace(':', '-')}-{round_label}.jsonl")
        if os.path.exists(path):
            return path
    try:
        logs = [os.path.join(root, f) for f in os.listdir(root) if f.endswith(".jsonl")]
    except OSError:
        return None
    return max(logs, key=os.path.getmtime) if logs else None


def read_lines(path: str) -> list[str]:
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.readlines()
    except OSError:
        return []
