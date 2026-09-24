#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["textual>=0.80"]
# ///
"""autodev の run を別のペインで見る画面。

    ~/.claude/scripts/autodev-watch.py [作業名]

statusline は入力を受け取れないので、タスクが多いと窓を切った要約しか出せない。ここでは
全タスクを表にし、選んだタスクの段の履歴・レビューの指摘・受入条件と、走っている段のログの
末尾を出す。2 秒ごとに `state.json` を読み直す。**何も書き込まない。**

| キー | すること |
| --- | --- |
| ↑ ↓ / ホイール | タスクを選ぶ（詳細とログのペインはホイールでスクロール） |
| Tab | 表・詳細・ログの間でフォーカスを移す |
| [ ] | run を切り替える |
| l | ログのペインを出す・隠す |
| q | 終了 |
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, ClassVar

# タスクリストの描き方と state.json の読み方は statusline と同じものを使う。ty は PEP 723 の
# スクリプトを別の環境で検査し、pyproject.toml の extra-paths を見ないので、この import を解決できない
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import statusline as sl  # ty: ignore[unresolved-import]
from rich.console import Group
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import DataTable, Footer, RichLog, Static

REFRESH_SECONDS = 2.0
#: ログのペインに出す、段のログの末尾のイベント数
LOG_EVENTS = 60
STATUS_LABEL = {
    "stacked": "積んだ",
    "running": "実行中",
    "pending": "未着手",
    "blocked": "保留",
    "failed": "失敗",
}
#: 記号は statusline のタスクリストと同じにする
STATUS_MARK = {
    "stacked": Text("✔", style=sl.GREEN),
    "running": Text("◼", style=sl.ACCENT),
    "blocked": Text("✘", style=sl.RED),
    "failed": Text("✘", style=sl.RED),
}


# --- 読む ----------------------------------------------------------------------


def read_json(path: str) -> Any:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None  # driver が書き換えている最中に当たることがある


def runs() -> list[dict]:
    """全 run。動いているものを先に、あとは更新の新しい順に並べる。"""
    states = sl.read_states()

    def order(st: dict) -> tuple[bool, float]:
        active = sl.is_active(st, sl.live_stages(st))
        return (not active, sl.age(st.get("updatedAt")) or float("inf"))

    return sorted(states, key=order)


def log_path(st: dict, task: dict) -> str | None:
    """そのタスクのログ。走っている段があればそれ、無ければ最後に書かれたもの。"""
    root = os.path.join(sl.state_root(), str(st.get("work")), "logs", str(task.get("id")))
    for name, info in sorted((st.get("running") or {}).items()):
        if isinstance(info, dict) and info.get("task") == task.get("id"):
            path = os.path.join(root, f"{name.replace(':', '-')}-{info.get('round') or '0'}.jsonl")
            if os.path.exists(path):
                return path
    try:
        logs = [os.path.join(root, f) for f in os.listdir(root) if f.endswith(".jsonl")]
    except OSError:
        return None
    return max(logs, key=os.path.getmtime) if logs else None


def activity(path: str, limit: int = LOG_EVENTS) -> list[Text]:
    """段のログ（`claude --output-format stream-json`）から、ツールの呼び出しと発言を拾う。"""
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return []
    out: list[Text] = []
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get("type") != "assistant":
            continue
        for block in (event.get("message") or {}).get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                out.append(Text("▸ ", style=sl.BLUE).append(tool_line(block)))
            elif block.get("type") == "text" and str(block.get("text") or "").strip():
                first = str(block["text"]).strip().splitlines()[0]
                out.append(Text(f"  {first[:200]}", style=sl.DIM))
    return out[-limit:]


def tool_line(block: dict) -> str:
    """ツール名と、何に向けて呼んだかを 1 行にする。"""
    args = block.get("input") or {}
    target = ""
    for key in ("file_path", "command", "pattern", "path", "url", "description"):
        if isinstance(args, dict) and args.get(key):
            target = str(args[key]).splitlines()[0]
            break
    return f"{block.get('name', '?')} {target[:160]}".rstrip()


# --- 描く ----------------------------------------------------------------------


def headline(st: dict) -> Text:
    stages = sl.live_stages(st)
    if sl.is_active(st, stages):
        return sl.headline(st, stages)
    tasks = st.get("tasks") or []
    stacked = sum(1 for t in tasks if t.get("status") == "stacked")
    held = sum(1 for t in tasks if t.get("status") in ("blocked", "failed"))
    line = Text("autodev ", style=sl.DIM).append(str(st.get("work", "?")), style=sl.ACCENT)
    line.append(f" ▸ 止まっている · 積んだ {stacked}/{len(tasks)}", style=sl.DIM)
    if held:
        line.append(f" · 保留 {held}", style=sl.YELLOW)
    return line


def stage_history(st: dict, task: dict) -> Text:
    """済んだ段を 1 行ずつ。走っている段は往復数と直前のツールを添えて最後に置く。"""
    out = Text()
    for entry in task.get("stages") or []:
        ok = bool(entry.get("ok", True))
        out.append("✔ " if ok else "✘ ", style=sl.GREEN if ok else sl.RED)
        out.append(f"{entry.get('name')} r{entry.get('round')}\n")
    for name, info, seconds in sl.live_stages(st):
        if info.get("task") != task.get("id"):
            continue
        out.append("◼ ", style=sl.ACCENT)
        out.append(f"{name} r{info.get('round') or '0'}", style=sl.ACCENT)
        out.append(f"  {sl.short(seconds)}", style=sl.DIM)
        if info.get("turns"):
            out.append(f" {info['turns']}往復 {info.get('tool') or ''}".rstrip(), style=sl.DIM)
        out.append("\n")
    return out if out.plain else Text("（まだ走っていない）\n", style=sl.DIM)


def review_summary(path: str) -> Text:
    """指摘の件数と、未解決の指摘の中身。"""
    data = read_json(path)
    items = (data or {}).get("items") or {}
    if not items:
        return Text("（指摘なし）\n", style=sl.DIM)
    counts: dict[str, int] = {}
    for item in items.values():
        counts[item.get("status", "?")] = counts.get(item.get("status", "?"), 0) + 1
    out = Text(" · ".join(f"{k} {v}" for k, v in sorted(counts.items())) + "\n", style=sl.DIM)
    colors = {"must-fix": sl.RED, "should-fix": sl.YELLOW, "nit": sl.DIM}
    for key, item in items.items():
        if item.get("status") != "open":
            continue
        rating = str(item.get("rating", "?"))
        out.append(f"{key} ", style=sl.DIM).append(
            f"{rating:<10}", style=colors.get(rating, sl.DIM)
        )
        out.append(f" {item.get('location', '')}\n")
        first = str(item.get("review") or "").strip().splitlines()
        if first:
            out.append(f"    {first[0][:200]}\n", style=sl.DIM)
    return out


def task_detail(st: dict, task: dict) -> Group:
    status = str(task.get("status"))
    title = Text(f"{task.get('id')} ", style=sl.DIM).append(
        str(task.get("subject") or ""), style="bold"
    )
    meta = Text(f"{STATUS_LABEL.get(status, status)} · {task.get('tier', '?')}", style=sl.DIM)
    if task.get("pr"):
        meta.append(f" · PR #{task['pr']}", style=sl.DIM)
    if task.get("branch"):
        meta.append(f" · {task['branch']}", style=sl.DIM)
    parts: list[Any] = [title, meta, Text()]
    if task.get("reason"):
        parts += [Text(str(task["reason"]), style=sl.RED), Text()]
    if status == "running":
        parts += [sl.pipeline(task, sl.live_stages(st)), Text()]
    review = os.path.join(
        sl.state_root(), str(st.get("work")), "tasks", str(task.get("id")), "review.json"
    )
    parts += [
        Text("段の履歴", style="bold"),
        stage_history(st, task),
        Text("レビュー", style="bold"),
        review_summary(review),
    ]
    for key, label in (("acceptance", "受入条件"), ("dod", "DoD"), ("scope", "範囲")):
        if task.get(key):
            parts += [Text(label, style="bold"), Text(f"{task[key]}\n")]
    return Group(*parts)


# --- 画面 ----------------------------------------------------------------------


class Watch(App):
    TITLE = "autodev watch"
    CSS = """
    #headline { height: 1; padding: 0 1; }
    #tasks { width: 1fr; }
    #detail-pane { width: 1fr; padding: 0 1; border-left: solid $panel; }
    #log { height: 12; border-top: solid $panel; }
    """
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("[", "switch_run(-1)", "前の run"),
        Binding("]", "switch_run(1)", "次の run"),
        Binding("l", "toggle_log", "ログ"),
        Binding("q", "quit", "終了"),
    ]

    def __init__(self, work: str | None = None) -> None:
        super().__init__()
        self.work = work
        self.selected: str | None = None
        self.states: list[dict] = []
        self.log_source: tuple[str | None, int] = (None, 0)

    def compose(self) -> ComposeResult:
        yield Static(id="headline")
        with Horizontal():
            yield DataTable(id="tasks", cursor_type="row", zebra_stripes=True)
            with VerticalScroll(id="detail-pane"):
                yield Static(id="detail")
        yield RichLog(id="log", max_lines=LOG_EVENTS * 2, markup=False)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns("", "id", "件名", "状態", "PR")
        # 指定しないとログのペインに当たり、↑↓ でタスクを選べない
        table.focus()
        self.reload()
        self.set_interval(REFRESH_SECONDS, self.reload)

    @property
    def current(self) -> dict | None:
        return next((st for st in self.states if st.get("work") == self.work), None)

    def reload(self) -> None:
        self.states = runs()
        if self.current is None:
            self.work = self.states[0].get("work") if self.states else None
        st = self.current
        headline_widget = self.query_one("#headline", Static)
        if st is None:
            headline_widget.update(Text("autodev の run が無い", style=sl.DIM))
            return
        position = f"  [{self.states.index(st) + 1}/{len(self.states)}]"
        headline_widget.update(headline(st).append(position, style=sl.DIM))
        self.fill_table(st)
        self.show_task()

    def fill_table(self, st: dict) -> None:
        table = self.query_one(DataTable)
        tasks = [t for t in st.get("tasks") or [] if isinstance(t, dict)]
        ids = [str(t.get("id")) for t in tasks]
        if self.selected not in ids:
            running = next((t for t in tasks if t.get("status") == "running"), None)
            self.selected = str(running.get("id")) if running else (ids[0] if ids else None)
        table.clear()
        for task in tasks:
            status = str(task.get("status"))
            table.add_row(
                STATUS_MARK.get(status, Text("◻", style=sl.DIM)),
                str(task.get("id")),
                str(task.get("subject") or ""),
                STATUS_LABEL.get(status, status),
                f"#{task['pr']}" if task.get("pr") else "",
                key=str(task.get("id")),
            )
        if self.selected in ids:
            table.move_cursor(row=ids.index(self.selected), animate=False)

    def selected_task(self) -> dict | None:
        st = self.current or {}
        return next((t for t in st.get("tasks") or [] if str(t.get("id")) == self.selected), None)

    def show_task(self) -> None:
        st, task = self.current, self.selected_task()
        detail = self.query_one("#detail", Static)
        if st is None or task is None:
            detail.update(Text("（計画中。タスクはまだ無い）", style=sl.DIM))
            return
        detail.update(task_detail(st, task))
        self.show_log(log_path(st, task))

    def show_log(self, path: str | None) -> None:
        """ログのペイン。同じファイルが伸びただけなら、書き直さずに足す。"""
        log = self.query_one(RichLog)
        lines = activity(path) if path else []
        source, seen = self.log_source
        if path != source or len(lines) < seen:
            log.clear()
            seen = 0
            if path:
                log.write(Text(os.path.basename(path), style="bold"))
        for line in lines[seen:]:
            log.write(line)
        self.log_source = (path, len(lines))

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        key = event.row_key.value
        if key and key != self.selected:
            self.selected = key
            self.show_task()

    def action_switch_run(self, step: int) -> None:
        if not self.states:
            return
        index = next((i for i, st in enumerate(self.states) if st.get("work") == self.work), 0)
        self.work = self.states[(index + step) % len(self.states)].get("work")
        self.selected = None
        self.reload()

    def action_toggle_log(self) -> None:
        log = self.query_one(RichLog)
        log.display = not log.display


def main() -> int:
    # install.sh が uv に依存を取り寄せさせるためだけに呼ぶ。import が通れば用は済んでいる
    if "--warm" in sys.argv[1:]:
        return 0
    Watch(sys.argv[1] if len(sys.argv) > 1 else None).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
