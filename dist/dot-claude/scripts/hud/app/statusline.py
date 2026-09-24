"""statusline を 1 回描く。Claude Code が標準入力に JSON を渡し、標準出力を受け取って表示する。

**Claude Code は標準出力を受け取るだけで、端末に直結しない。** 常駐・キー入力・アニメーションは
できず、端末の幅は `tput` では取れないので `COLUMNS` を読む。**端末の幅を変えても描き直されない**
ので、`settings.json` の `refreshInterval` で定期的に描き直させて幅の変化に追いつかせる。
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
import time

from rich.console import Console
from rich.text import Text

from hud.core import git, headline, pipeline, runs, session, tasklist
from hud.ports import autodev
from hud.ports import git as git_port
from hud.render import layout
from hud.render import session as session_view
from hud.render import tasklist as tasklist_view

DEFAULT_COLUMNS = 120


def autodev_block(st: dict, now: dt.datetime) -> list[Text]:
    """動いている run 1 つのタスクリスト。動いていなければ空。"""
    stages = runs.live_stages(st, now)
    if not runs.is_active(st, stages, now):
        return []
    lines = [tasklist_view.headline(headline.build(st, stages, active=True))]
    for row in tasklist.visible(runs.tasks(st)):
        if isinstance(row, tasklist.Summary):
            lines.append(tasklist_view.summary_row(row))
        else:
            lines.append(tasklist_view.task_row(row, pipeline.steps(row, stages)))
    return lines


def columns() -> int:
    try:
        return int(os.environ.get("COLUMNS") or 0) or DEFAULT_COLUMNS
    except ValueError:
        return DEFAULT_COLUMNS


def main() -> int:
    # install.sh が uv に依存を取り寄せさせるためだけに呼ぶ。import が通れば用は済んでいる
    if "--warm" in sys.argv[1:]:
        return 0
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        data = {}
    current = session.parse(data, time.time())
    output = git_port.status(current.cwd)
    rows = session_view.rows(current, git.parse(output) if output is not None else None)
    now = dt.datetime.now().astimezone()
    block = [line for st in autodev.read_states() for line in autodev_block(st, now)]
    # 標準出力は端末ではないので、色を付けるよう明示する。折り返しは Claude Code に任せない
    console = Console(
        force_terminal=True,
        color_system="truecolor",
        width=10_000,
        soft_wrap=True,
        highlight=False,
        markup=False,
        emoji=False,
    )
    for line in layout.layout(rows, block, columns()):
        console.print(line)
    return 0
