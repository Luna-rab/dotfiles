"""autodev のランディレクトリ（`~/.local/state/autodev/<ラン名>/`）を読む。**何も書き込まない。**

driver が書き換えている最中のファイルに当たることがあるので、読めないものは飛ばす。
"""

from __future__ import annotations

import json
import os
from typing import Any


def state_root() -> str:
    """ランディレクトリの置き場。**出所は `autodevlib/config/paths.py` の `state_root()`** と同じ規則。"""
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


def read_text(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return None


def read_lines(path: str) -> list[str]:
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.readlines()
    except OSError:
        return []


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


def read_overview(run_name: str) -> str | None:
    """まとめステージが書いた概要 PR の説明。計画の直後まで無い。"""
    return read_text(os.path.join(state_root(), run_name, "prose", "overview.md"))


def stage_file(run_name: str, task: str, stage: str, round_label: str, suffix: str) -> str:
    """ステージのログ（`.jsonl`）と、渡した指示（`.prompt.md`）の置き場。

    名前の付け方は `autodevlib/config/paths.py` の `Run.log()` / `Run.prompt()` と同じ。
    """
    name = f"{stage.replace(':', '-')}-{round_label}{suffix}"
    return os.path.join(state_root(), run_name, "logs", task, name)


def log_names(run_name: str, task: str) -> list[str]:
    """そのタスクのログのファイル名。書かれた順（古いものが先）。"""
    root = os.path.join(state_root(), run_name, "logs", task)
    try:
        names = [f for f in os.listdir(root) if f.endswith(".jsonl")]
    except OSError:
        return []
    return sorted(names, key=lambda f: os.path.getmtime(os.path.join(root, f)))
