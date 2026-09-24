#!/usr/bin/env python3
"""Claude Code の statusline の入口。

`statusLine.command` はコマンドを 1 つしか取れないので、**ここが入口になり、
既存の行は `statusline.sh` に委ねる**。`statusline.sh` 側は何も変わらない。

足すのは autodev の 1 行だけで、**段が走っていないときは何も出さない**。

    🤖 range-field · impl r1 4m12s · task1 2/3 · PR #4

判定に使うのは `<run>/state.json` の `running`（driver が段の開始と終了で書く）。
これが段の途中で更新される唯一の値なので、進行と生存の両方をここで見る。段が
1 つも走っていなければ、その run は表示しない。

**タイムアウトを超えても残っている段は `!` を付けて出す。** driver が段の途中で
落ちると `running` が残るので、それを「走っている」と信じ続けないようにする。
"""

from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys

#: `statusline.sh` に渡す標準入力は 1 度しか読めないので、読んだものを渡し直す
DELEGATE = os.path.expanduser("~/.claude/scripts/statusline.sh")
#: 段 1 つの制限時間（`autodevlib/config/stages.py` の `Stage.timeout`）。これを超えたら `!`
STAGE_TIMEOUT = 3600
#: これを超えたら driver が落ちたものとして表示しない
GIVE_UP = 3 * 3600

CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RESET = "\033[0m"


def state_root() -> str:
    """run の置き場。**出所は `autodevlib/config/paths.py` の `state_root()`** と同じ規則。"""
    override = os.environ.get("AUTODEV_STATE_DIR")
    if override:
        return os.path.abspath(override)
    xdg = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local/state")
    return os.path.join(xdg, "autodev")


def read_states() -> list[dict]:
    root = state_root()
    if not os.path.isdir(root):
        return []
    out = []
    for name in sorted(os.listdir(root)):
        path = os.path.join(root, name, "state.json")
        try:
            with open(path, encoding="utf-8") as fh:
                loaded = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue  # driver が書き換えている最中に当たることがある
        if isinstance(loaded, dict):
            out.append(loaded)
    return out


def elapsed_seconds(stamp: str) -> float | None:
    try:
        started = dt.datetime.fromisoformat(stamp)
    except ValueError:
        return None
    now = dt.datetime.now(started.tzinfo) if started.tzinfo else dt.datetime.now()
    return (now - started).total_seconds()


def short(seconds: float) -> str:
    total = int(seconds)
    if total < 60:
        return f"{total}s"
    if total < 3600:
        return f"{total // 60}m{total % 60:02d}s"
    return f"{total // 3600}h{total % 3600 // 60:02d}m"


def waiting_line(st: dict) -> str | None:
    """答えを待って止まっている run。**段は走っていないが、人の手を待っている。**"""
    deferred = st.get("deferred")
    if not isinstance(deferred, dict) or not deferred:
        return None
    questions = [q for q in (st.get("questions") or []) if isinstance(q, dict)]
    keys = " ".join(str(q.get("id") or "?") for q in questions) or "?"
    return (
        f"🙋 {CYAN}{st.get('work', '?')}{RESET} · "
        f"{YELLOW}{deferred.get('stage', '?')} が答え待ち{RESET} · {keys}"
    )


def line_for(st: dict) -> str | None:
    running = st.get("running") or {}
    if not isinstance(running, dict) or not running:
        return waiting_line(st)

    stages = []
    newest: float | None = None
    for name, info in sorted(running.items()):
        if not isinstance(info, dict):
            continue
        seconds = elapsed_seconds(str(info.get("at") or ""))
        if seconds is None or seconds > GIVE_UP:
            continue
        if newest is None or seconds < newest:
            newest = seconds
        stages.append((name, info, seconds))
    if not stages or newest is None:
        return None

    # 同時に走っている段は短い名前で並べる（review:normal → normal）
    label = "+".join(name.split(":")[-1] for name, _, _ in stages)
    round_label = str(stages[0][1].get("round") or "0")
    mark = f"{YELLOW}!{RESET}" if newest > STAGE_TIMEOUT else ""
    task = str(stages[0][1].get("task") or "")
    # 往復とツールは段が 1 つのときだけ出す（並んでいるとどちらの数か分からない）
    inside = ""
    if len(stages) == 1:
        info = stages[0][1]
        bits = [f"{info['turns']}往復"] if info.get("turns") else []
        if info.get("tool"):
            bits.append(str(info["tool"]))
        inside = f" {' '.join(bits)}" if bits else ""

    tasks = st.get("tasks") or []
    done = sum(1 for t in tasks if t.get("status") == "stacked")
    held = sum(1 for t in tasks if t.get("status") in ("blocked", "failed"))

    parts = [
        f"{CYAN}{st.get('work', '?')}{RESET}",
        f"{label} r{round_label} {short(newest)}{mark}{inside}",
    ]
    if tasks:
        progress = f"{task} {done}/{len(tasks)}" if task else f"{done}/{len(tasks)}"
        parts.append(f"{GREEN}{progress}{RESET}" if not held else f"{progress} 保留{held}")
    if st.get("stackPr"):
        parts.append(f"PR #{st['stackPr']}")
    return "🤖 " + " · ".join(parts)


def main() -> int:
    payload = sys.stdin.read()
    if os.access(DELEGATE, os.X_OK):
        subprocess.run([DELEGATE], input=payload, text=True, check=False)
    for st in read_states():
        line = line_for(st)
        if line:
            print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
