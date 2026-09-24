#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["rich>=13"]
# ///
"""Claude Code の statusline。

    Opus 5.5 · high   ctx ━━━━━───── 47%   $3.21
    dotfiles   feature/x +2 ~1 ?3
    5h ━━━━━━━━━━━━━━━━━━━━━━━━┃━━━━╾─────────── 72% ▲12 1h47m
    7d ━━━━━━━━━━━━╾───────────────────┃─────── 31% ▼4 2d05h

autodev が走っている間は、Claude Code のタスクリストのように現在地・済んだもの・これからを
足す。**幅が足りれば右に、足りなければ下に置く。**

    autodev range-field ▸ task2 impl r0 · 4m12s 26往復 Edit · 土台 PR #4
      ✔ task1 パーサの土台を作る   #5
      ◼ task2 範囲指定を足す       testgen ✔ › impl ◼ › review › judge › PR
      ◻ task3 CLI に出す

**Claude Code は標準出力を受け取って描くだけで、端末に直結しない。** 常駐・キー入力・
アニメーションはできず、端末の幅は `tput` では取れないので `COLUMNS` を読む。幅に収まらない
行は、優先度の低い部品から落とす。

**端末の幅を変えても描き直されない**（描き直すきっかけに入っていない）。次に描き直すまで
前の幅の出力が残るので、右寄せや行末の空白で幅を埋めない。大事なものを左から並べておけば、
縮めたときに切れるのは行末だけで済む。`settings.json` の `refreshInterval` で定期的に
描き直させて、幅の変化に追いつかせる。

Nerd Font を前提にしない。区切りと棒は既定のフォントにもある文字（`│` `━` `╾` `─` `✔` `◼` `◻`）だけで描く。
"""

from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Any

from rich.console import Console
from rich.style import Style
from rich.text import Text

#: 段 1 つの制限時間（`autodevlib/config/stages.py` の `Stage.timeout`）。これを超えたら `!`
STAGE_TIMEOUT = 3600
#: これを超えて更新の無い run は、driver が落ちたものとして表示しない
GIVE_UP = 3 * 3600
BAR_WIDTH = 10
LIMIT_BAR_WIDTH = 40
#: 利用枠の窓の長さ。ペース（窓の経過に対して使いすぎているか）の計算に使う
WINDOWS = {"five_hour": 5 * 3600, "seven_day": 7 * 86400}
#: タスクがこれより多いと、完了したものを 1 行にまとめる
MAX_TASK_ROWS = 5
SUBJECT_WIDTH = 22
REASON_WIDTH = 24

DIM = Style(color="#6c7086")
ACCENT = Style(color="#89b4fa", bold=True)
MAUVE = Style(color="#cba6f7")
GREEN = Style(color="#a6e3a1")
YELLOW = Style(color="#f9e2af")
RED = Style(color="#f38ba8")
BLUE = Style(color="#74c7ec")
MARK = Style(color="#cdd6f4", bold=True)
GAP = "   "
#: 左の statusline と右のタスクリストの間
SIDE = Text("  │  ", style=DIM)
ARROW = Text(" › ", style=DIM)


@dataclass
class Part:
    """1 行を組み立てる部品。幅が足りないときは `keep` の小さいものから落とす。"""

    text: Text
    keep: int = 0


def dig(data: Any, *keys: str) -> Any:
    for key in keys:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


# --- 描き方 ------------------------------------------------------------------


def pct_color(pct: float) -> Style:
    """0% の緑から 100% の赤へ、黄を経て連続に変える。"""
    p = max(0.0, min(pct, 100.0)) / 100
    green, yellow, red = (0xA6, 0xE3, 0xA1), (0xF9, 0xE2, 0xAF), (0xF3, 0x8B, 0xA8)
    lo, hi, t = (green, yellow, p * 2) if p < 0.5 else (yellow, red, (p - 0.5) * 2)
    rgb = "".join(f"{round(a + (b - a) * t):02x}" for a, b in zip(lo, hi, strict=True))
    return Style(color=f"#{rgb}")


def bar(pct: float, width: int = BAR_WIDTH, mark: float | None = None) -> Text:
    """0.5 マス刻みの棒。`mark` を渡すと、その位置に目盛り `┃` を置く。

    罫線（`━` `╾` `─`）で描く。マスの上下中央に引かれるので、棒を縦に並べても上下の行と
    接しない（ブロック要素 `█` はマスの高さいっぱいを塗るので、行どうしがくっつく）。
    """
    halves = round(max(0.0, min(pct, 100.0)) * width * 2 / 100)
    cells = ["━"] * (halves // 2) + ["╾"] * (halves % 2)
    filled = len(cells)
    cells += ["─"] * (width - filled)
    at = None if mark is None else min(width - 1, int(max(0.0, mark) * width / 100))
    out = Text()
    for i, cell in enumerate(cells):
        if i == at:
            out.append("┃", style=MARK)
        else:
            out.append(cell, style=pct_color(pct) if i < filled else DIM)
    return out


def joined(parts: list[Part], sep: str = GAP) -> Text:
    out = Text()
    for i, part in enumerate(parts):
        if i:
            out.append(sep)
        out.append_text(part.text)
    return out


def natural_width(parts: list[Part]) -> int:
    """何も落とさずに並べたときの幅。"""
    return joined([p for p in parts if p.text.plain]).cell_len


def fit(parts: list[Part], width: int) -> Text:
    """左から並べる。`width` に収まらなければ `keep` の小さい部品から落とす。"""
    live = [p for p in parts if p.text.plain]
    while natural_width(live) > width and len(live) > 1:
        drop = min(range(len(live)), key=lambda i: (live[i].keep, -i))
        del live[drop]
    return joined(live)


def clip(text: Text, width: int, *, pad: bool = False) -> Text:
    out = text.copy()
    out.truncate(width, overflow="ellipsis", pad=pad)
    return out


# --- セッション（2 行） ---------------------------------------------------------


def git_summary(cwd: str) -> Text:
    """ブランチと、staged / 変更 / 未追跡の件数。`git status` 1 回で取る。"""
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain=v2", "--branch"],
            cwd=cwd or None,
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return Text()
    if out.returncode != 0:
        return Text()
    branch, staged, modified, untracked = "", 0, 0, 0
    for line in out.stdout.splitlines():
        if line.startswith("# branch.head "):
            branch = line.split(" ", 2)[2]
        elif line.startswith(("1 ", "2 ", "u ")):
            xy = line.split(" ", 2)[1]
            staged += xy[0] != "."
            modified += xy[1] != "."
        elif line.startswith("? "):
            untracked += 1
    text = Text(branch, style=GREEN)
    marks = ((staged, "+", GREEN), (modified, "~", YELLOW), (untracked, "?", BLUE))
    for count, mark, style in marks:
        if count:
            text.append(f" {mark}{count}", style=style)
    return text


def until(seconds: float) -> str:
    """リセットまでの残り。1 日以上は日と時、それ未満は時と分。"""
    total = max(0, int(seconds))
    if total >= 86400:
        return f"{total // 86400}d{total % 86400 // 3600:02d}h"
    if total >= 3600:
        return f"{total // 3600}h{total % 3600 // 60:02d}m"
    return f"{total // 60}m"


def elapsed_pct(remaining: float, window: int) -> float:
    """利用枠の窓の時間が、どれだけ過ぎたか（0〜100）。"""
    return (1 - max(0.0, min(remaining, window)) / window) * 100


def pace(used: float, remaining: float, window: int) -> int:
    """使った割合から、窓の時間が過ぎた割合を引いたもの。正なら、このままでは窓の途中で尽きる。"""
    return round(used - elapsed_pct(remaining, window))


def limit_gauge(data: dict, key: str, label: str, now: float) -> Text:
    """利用枠の棒。目盛り `┃` は窓の時間が過ぎた位置で、棒がこれを越えていれば使いすぎ。"""
    used = dig(data, "rate_limits", key, "used_percentage")
    if used is None:
        return Text()
    used = float(used)
    resets = dig(data, "rate_limits", key, "resets_at")
    remaining = None if resets is None else float(resets) - now
    mark = None if remaining is None else elapsed_pct(remaining, WINDOWS[key])
    text = Text(f"{label} ", style=DIM)
    text.append_text(bar(used, LIMIT_BAR_WIDTH, mark))
    text.append(f" {used:.0f}%", style=pct_color(used))
    if remaining is not None:
        delta = pace(used, remaining, WINDOWS[key])
        if delta >= 1:
            text.append(f" ▲{delta}", style=RED)
        elif delta <= -1:
            text.append(f" ▼{-delta}", style=GREEN)
        text.append(f" {until(remaining)}", style=DIM)
    return text


def session_rows(data: dict, now: float) -> list[list[Part]]:
    """1 行目は使っている量、2 行目はどこで、3・4 行目は利用枠。"""
    model = Text(str(dig(data, "model", "display_name") or "?"), style=ACCENT)
    if dig(data, "effort", "level"):
        model.append(f" · {dig(data, 'effort', 'level')}", style=MAUVE)
    pct = float(dig(data, "context_window", "used_percentage") or 0)
    ctx = Text("ctx ", style=DIM)
    ctx.append_text(bar(pct))
    ctx.append(f" {pct:.0f}%", style=pct_color(pct))
    cost = float(dig(data, "cost", "total_cost_usd") or 0)
    usage = [
        Part(model, keep=3),
        Part(ctx, keep=2),
        Part(Text(f"${cost:.2f}", style=DIM), keep=1),
    ]

    cwd = str(dig(data, "workspace", "current_dir") or "")
    # サブディレクトリで動いていてもリポジトリの名前を出す
    repo = str(dig(data, "workspace", "project_dir") or cwd)
    worktree = dig(data, "worktree", "name")
    where = [
        Part(Text(os.path.basename(repo.rstrip("/")) or repo, style=Style(bold=True)), keep=3),
        Part(git_summary(cwd), keep=2),
        Part(Text(f"worktree {worktree}", style=BLUE) if worktree else Text(), keep=1),
    ]

    # 利用枠は Pro / Max のサブスクリプションでしか渡らない。渡らなければ行ごと出さない
    limits = [
        [Part(limit_gauge(data, key, label, now))]
        for key, label in (("five_hour", "5h"), ("seven_day", "7d"))
    ]
    return [usage, where] + [row for row in limits if row[0].text.plain]


# --- autodev のタスクリスト -----------------------------------------------------

#: 段の名前を、タスクリストに出す短い名前へ。レビュー 2 体は 1 つにまとめる
SHORT = {
    "testgen": "testgen",
    "impl": "impl",
    "fix": "fix",
    "review:normal": "review",
    "review:adversarial": "review",
    "judge": "judge",
    "pr-body": "PR",
}
#: その段の後に来る段。裁定で指摘が残れば fix に戻るが、戻るかどうかは裁定が終わるまで分からない
NEXT = {
    None: ["testgen", "impl", "review", "judge", "PR"],
    "testgen": ["impl", "review", "judge", "PR"],
    "impl": ["review", "judge", "PR"],
    "fix": ["review", "judge", "PR"],
    "review": ["judge", "PR"],
    "judge": ["PR"],
    "PR": [],
}
#: タスクに属さない段。タスクリストの見出しにだけ出す
RUN_STAGES = {"plan": "計画中", "summary": "まとめ"}


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


def age(stamp: Any) -> float | None:
    try:
        at = dt.datetime.fromisoformat(str(stamp))
    except ValueError:
        return None
    now = dt.datetime.now(at.tzinfo) if at.tzinfo else dt.datetime.now()
    return (now - at).total_seconds()


def short(seconds: float) -> str:
    total = int(seconds)
    if total < 60:
        return f"{total}s"
    if total < 3600:
        return f"{total // 60}m{total % 60:02d}s"
    return f"{total // 3600}h{total % 3600 // 60:02d}m"


def live_stages(st: dict) -> list[tuple[str, dict, float]]:
    """走っている段。driver が段の途中で落ちると `running` が残るので、`GIVE_UP` を超えたものは外す。"""
    running = st.get("running")
    if not isinstance(running, dict):
        return []
    stages = []
    for name, info in sorted(running.items()):
        if not isinstance(info, dict):
            continue
        seconds = age(info.get("at"))
        if seconds is not None and seconds <= GIVE_UP:
            stages.append((name, info, seconds))
    return stages


def is_active(st: dict, stages: list) -> bool:
    """表示する run か。**段と段の間**（検証・push・PR 作成）も、タスクが running なら出す。"""
    if stages or st.get("deferred"):
        return True
    fresh = (age(st.get("updatedAt")) or GIVE_UP + 1) <= GIVE_UP
    return fresh and any(t.get("status") == "running" for t in st.get("tasks") or [])


def headline(st: dict, stages: list[tuple[str, dict, float]]) -> Text:
    line = Text("autodev ", style=DIM)
    line.append(str(st.get("work", "?")), style=ACCENT)
    line.append(" ▸ ", style=DIM)
    deferred = st.get("deferred")
    if isinstance(deferred, dict) and deferred:
        questions = [q for q in (st.get("questions") or []) if isinstance(q, dict)]
        keys = " ".join(str(q.get("id") or "?") for q in questions) or "?"
        line.append(f"{deferred.get('stage', '?')} が答え待ち · {keys}", style=YELLOW)
    elif stages:
        name, info, _ = stages[0]
        newest = min(seconds for _, _, seconds in stages)
        if name in RUN_STAGES:
            line.append(RUN_STAGES[name])
        else:
            label = "+".join(dict.fromkeys(SHORT.get(n, n) for n, _, _ in stages))
            line.append(f"{info.get('task', '?')} {label} r{info.get('round') or '0'}")
        line.append(f" · {short(newest)}", style=DIM)
        if newest > STAGE_TIMEOUT:
            line.append("!", style=RED)
        # 往復とツールは段が 1 つのときだけ出す（並んでいるとどちらの数か分からない）
        if len(stages) == 1 and info.get("turns"):
            line.append(f" {info['turns']}往復 {info.get('tool') or ''}".rstrip(), style=DIM)
    else:
        current = next((t for t in st.get("tasks") or [] if t.get("status") == "running"), {})
        line.append(f"{current.get('id', '?')} 検査と PR", style=DIM)
    if st.get("stackPr"):
        line.append(f" · 土台 PR #{st['stackPr']}", style=DIM)
    return line


def round_suffix(name: str, round_label: str) -> str:
    """レビュー・裁定・修正は 2 巡目から巡目を添える。"""
    return (
        f" r{round_label}"
        if name in ("review", "judge", "fix") and round_label not in ("0", "1")
        else ""
    )


def pipeline(task: dict, stages: list[tuple[str, dict, float]]) -> Text:
    """済んだ段・走っている段・これからの段を 1 行に並べる。"""
    done: list[tuple[str, str, bool]] = []
    for entry in task.get("stages") or []:
        name = SHORT.get(str(entry.get("name")), str(entry.get("name")))
        round_label, ok = str(entry.get("round") or "0"), bool(entry.get("ok", True))
        # レビュー 2 体のように、同じ巡目の同じ段は 1 つにまとめる
        if done and done[-1][:2] == (name, round_label):
            done[-1] = (name, round_label, done[-1][2] and ok)
        else:
            done.append((name, round_label, ok))

    now = [
        (SHORT.get(n, n), str(info.get("round") or "0"))
        for n, info, _ in stages
        if info.get("task") == task.get("id")
    ]
    current = now[0] if now else None
    if current and done and done[-1][:2] == current:
        done.pop()  # 2 体のうち 1 体だけ終わったレビュー
    last = current[0] if current else (done[-1][0] if done else None)

    tokens: list[Text] = []
    if len(done) > 3:
        tokens.append(Text("…", style=DIM))
        done = done[-2:]
    for name, round_label, ok in done:
        token = Text(f"{name}{round_suffix(name, round_label)} ", style=DIM)
        token.append("✔" if ok else "✘", style=GREEN if ok else RED)
        tokens.append(token)
    if current:
        token = Text(f"{current[0]}{round_suffix(*current)} ", style=ACCENT)
        token.append("◼", style=ACCENT)
        tokens.append(token)
    tokens += [Text(name, style=DIM) for name in NEXT.get(last, [])]

    out = Text()
    for i, token in enumerate(tokens):
        if i:
            out.append_text(ARROW)
        out.append_text(token)
    return out


def task_row(task: dict, stages: list[tuple[str, dict, float]]) -> Text:
    status = task.get("status")
    marks = {
        "stacked": ("✔", GREEN, DIM),
        "running": ("◼", ACCENT, Style(bold=True)),
        "blocked": ("✘", RED, RED),
        "failed": ("✘", RED, RED),
    }
    mark, mark_style, body_style = marks.get(str(status), ("◻", DIM, Style()))
    row = Text("  ")
    row.append(mark, style=mark_style)
    row.append(f" {task.get('id', '?')} ", style=DIM)
    detail = Text()
    if status == "stacked" and task.get("pr"):
        detail = Text(f"#{task['pr']}", style=DIM)
    elif status == "running":
        detail = pipeline(task, stages)
    elif status in ("blocked", "failed") and task.get("reason"):
        detail = clip(Text(str(task["reason"]), style=RED), REASON_WIDTH)
    subject = Text(str(task.get("subject") or ""), style=body_style)
    # 右に何か続くときだけ件名の幅をそろえる（続かない行の末尾に空白を残さない）
    row.append_text(clip(subject, SUBJECT_WIDTH, pad=bool(detail.plain)))
    if detail.plain:
        row.append("  ")
        row.append_text(detail)
    return row


def autodev_block(st: dict) -> list[Text]:
    stages = live_stages(st)
    if not is_active(st, stages):
        return []
    lines = [headline(st, stages)]
    tasks = [t for t in st.get("tasks") or [] if isinstance(t, dict)]
    if len(tasks) > MAX_TASK_ROWS:
        stacked = [t for t in tasks if t.get("status") == "stacked"]
        if stacked:
            lines.append(Text("  ✔ ", style=GREEN).append(f"{len(stacked)} 件完了", style=DIM))
            tasks = [t for t in tasks if t.get("status") != "stacked"]
    lines += [task_row(t, stages) for t in tasks]
    return lines


# --- 組み立て ------------------------------------------------------------------


def layout(rows: list[list[Part]], block: list[Text], columns: int) -> list[Text]:
    """タスクリストは、幅が足りれば statusline の右に、足りなければ下に置く。"""
    if not block:
        return [fit(row, columns) for row in rows]
    left_width = max(natural_width(row) for row in rows)
    right_width = max(line.cell_len for line in block)
    if left_width + SIDE.cell_len + right_width <= columns:
        left = [fit(row, left_width) for row in rows]
        out = []
        for i in range(max(len(left), len(block))):
            line = clip(left[i], left_width, pad=True) if i < len(left) else Text(" " * left_width)
            line.append_text(SIDE)
            if i < len(block):
                line.append_text(block[i])
            out.append(line)
        return out
    return [fit(row, columns) for row in rows] + [clip(line, columns) for line in block]


def main() -> int:
    # install.sh が uv に依存を取り寄せさせるためだけに呼ぶ。import が通れば用は済んでいる
    if "--warm" in sys.argv[1:]:
        return 0
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        data = {}
    try:
        columns = int(os.environ.get("COLUMNS") or 0) or 120
    except ValueError:
        columns = 120
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
    rows = session_rows(data if isinstance(data, dict) else {}, time.time())
    block = [line for st in read_states() for line in autodev_block(st)]
    for line in layout(rows, block, columns):
        console.print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
