"""autodev の run を見る画面（Textual）。2 秒ごとに読み直すだけで、何も書き込まない。

| キー | すること |
| --- | --- |
| ↑ ↓ / ホイール | タスクを選ぶ（詳細とログのペインはホイールでスクロール） |
| Tab | 表・詳細・ログの間でフォーカスを移す |
| [ ] | run を切り替える |
| l | ログのペインを出す・隠す |
| q | 終了 |
"""

from __future__ import annotations

import datetime as dt
import os
import sys
from typing import ClassVar

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import DataTable, Footer, RichLog, Static

from hud.core import activity, headline, pipeline, review, runs
from hud.ports import autodev
from hud.render import detail
from hud.render import tasklist as tasklist_view
from hud.render.theme import DIM, STATUS_LABEL, status_mark

REFRESH_SECONDS = 2.0
#: ログのペインに出す、段のログの末尾のイベント数
LOG_EVENTS = 60


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
        self.stages: list[runs.Stage] = []
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
        now = dt.datetime.now().astimezone()
        self.states = runs.order(autodev.read_states(), now)
        if self.current is None:
            self.work = self.states[0].get("work") if self.states else None
        st = self.current
        headline_widget = self.query_one("#headline", Static)
        if st is None:
            headline_widget.update(Text("autodev の run が無い", style=DIM))
            return
        self.stages = runs.live_stages(st, now)
        head = headline.build(st, self.stages, runs.is_active(st, self.stages, now))
        position = f"  [{self.states.index(st) + 1}/{len(self.states)}]"
        headline_widget.update(tasklist_view.headline(head).append(position, style=DIM))
        self.fill_table(runs.tasks(st))
        self.show_task()

    def fill_table(self, tasks: list[dict]) -> None:
        table = self.query_one(DataTable)
        ids = [str(t.get("id")) for t in tasks]
        if self.selected not in ids:
            running = next((t for t in tasks if t.get("status") == "running"), None)
            self.selected = str(running.get("id")) if running else (ids[0] if ids else None)
        table.clear()
        for task in tasks:
            status = str(task.get("status"))
            mark, mark_style, _ = status_mark(status)
            table.add_row(
                Text(mark, style=mark_style),
                str(task.get("id")),
                str(task.get("subject") or ""),
                STATUS_LABEL.get(status, status),
                f"#{task['pr']}" if task.get("pr") else "",
                key=str(task.get("id")),
            )
        if self.selected in ids:
            table.move_cursor(row=ids.index(self.selected), animate=False)

    def show_task(self) -> None:
        st = self.current or {}
        task = next((t for t in runs.tasks(st) if str(t.get("id")) == self.selected), None)
        pane = self.query_one("#detail", Static)
        if task is None:
            pane.update(Text("（計画中。タスクはまだ無い）", style=DIM))
            return
        work, task_id = str(st.get("work")), str(task.get("id"))
        mine = [s for s in self.stages if s.task == task_id]
        found = review.summarize(autodev.read_review(work, task_id))
        pane.update(detail.task_detail(task, pipeline.steps(task, self.stages), mine, found))
        self.show_log(autodev.log_path(work, task_id, [(s.name, s.round) for s in mine]))

    def show_log(self, path: str | None) -> None:
        """ログのペイン。同じファイルが伸びただけなら、書き直さずに足す。"""
        log = self.query_one(RichLog)
        events = activity.parse(autodev.read_lines(path), LOG_EVENTS) if path else []
        source, seen = self.log_source
        if path != source or len(events) < seen:
            log.clear()
            seen = 0
            if path:
                log.write(Text(os.path.basename(path), style="bold"))
        for event in events[seen:]:
            log.write(detail.activity_line(event))
        self.log_source = (path, len(events))

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
