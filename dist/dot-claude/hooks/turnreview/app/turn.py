"""2 つの hook に共通する流れ。この turn に足した行を集め、起動から出力までを回す。

**2 周目（`stop_hook_active`）は黙って通す。** これを見ないと、残す判断をしたもので永久に止まる。
**何が起きても Claude の停止は妨げない。** 例外は標準エラーに 1 行出して終了コード 0 で抜ける。
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from pathlib import Path

from turnreview.core import turn
from turnreview.core.report import hook_output, notes_for
from turnreview.core.review import Review
from turnreview.ports import files, hookio, transcript
from turnreview.render.review import render

#: `additionalContext` がこれを超えると Claude には先頭の 2KB しか渡らない（上限は 10KB、実測）。
#: 超えそうなら色を落とし、それでも超えるなら一覧を `path:line` だけにする。一覧を途中で切ると、
#: 切った分は見直されないまま報告済みになる
MAX_CONTEXT_CHARS = 9_000
FALLBACKS = ({"color": True}, {"color": False}, {"color": False, "compact": True})
DEFAULT_COLUMNS = 100
MAX_WIDTH = 120
#: `Stop hook feedback:` の欄は 2 桁下げて描かれ、右端で折り返されると枠が崩れる
INDENT = 4


def width() -> int:
    """描く幅。hook の子プロセスは端末に直結しないので `COLUMNS` を読む。"""
    try:
        columns = int(os.environ.get("COLUMNS") or 0) or DEFAULT_COLUMNS
    except ValueError:
        columns = DEFAULT_COLUMNS
    return max(40, min(columns, MAX_WIDTH) - INDENT)


def fitted(review: Review) -> str:
    """上限に収まる描き方のうち、いちばん見やすいもの。どれも収まらなければ最後の形で返す。"""
    message = ""
    for options in FALLBACKS:
        message = render(review, width(), **options)
        if len(message) <= MAX_CONTEXT_CHARS:
            break
    return message


def added_in_turn(payload: dict) -> tuple[dict[Path, set[str]], Path]:
    """この turn に編集ツールで足した行をファイルごとに返す。2 つ目は相対パスの起点。"""
    cwd = Path(str(payload.get("cwd") or os.getcwd()))
    path = Path(str(payload.get(turn.transcript_field(payload)) or ""))
    if not path.is_file():
        return {}, cwd
    edits = turn.edits_in_last_turn(transcript.entries(path))
    return turn.added_lines_by_file(edits, cwd, files.temp_roots()), cwd


def run(
    name: str,
    review: Callable[[dict], Review | None],
    *,
    skip_env: str,
    warm: Callable[[], None],
) -> int:
    """入口の `main`。`--warm` なら依存の取得と文法のロードだけして終わる（install.sh が呼ぶ）。"""
    try:
        if "--warm" in sys.argv[1:]:
            warm()
            return 0
        if os.environ.get(skip_env) == "1":
            return 0
        payload = hookio.read_payload()
        if payload is None or payload.get("stop_hook_active"):
            return 0
        found = review(payload)
        if found is not None:
            message = fitted(found._replace(notes=notes_for(payload)))
            hookio.write(hook_output(payload, message))
    except Exception as exc:
        print(f"{name}: skipped ({exc!r})", file=sys.stderr)
    return 0
