"""autodev のランを見る画面（Textual）。2 秒ごとに `autodev status --json` を呼び直すだけで、何も書き込まない。

左ペインは「ラン → タスク → 段」のリストを 1 層ずつ出し、右ペインに選んでいる項目の詳細を出す。
ランのリストでは全ランの status を、タスクと段のリストでは選んだラン 1 つの status を呼ぶ。

| キー | すること |
| --- | --- |
| ↑ ↓ / ホイール | 項目を選ぶ（右ペインの詳細が変わる） |
| Enter / → | 1 つ深いリストに入る |
| Esc / ← / Backspace | 1 つ浅いリストに戻る |
| q | 終了 |
"""

from __future__ import annotations

import datetime as dt
import sys
from enum import Enum
from typing import ClassVar

from rich.console import Group
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Footer, OptionList, Static
from textual.widgets.option_list import Option

from hud.core import headline, pipeline, runs, stagelist
from hud.core.pipeline import Mark
from hud.ports import autodev
from hud.render import detail, navigator
from hud.render.theme import DIM

REFRESH_SECONDS = 2.0


class Level(Enum):
    RUNS = "runs"
    TASKS = "tasks"
    STAGES = "stages"


class Watch(App):
    TITLE = "autodev watch"
    CSS = """
    #left { width: 1fr; }
    #crumb { height: 1; padding: 0 1; }
    #right { width: 2fr; border-left: solid $panel; padding: 0 1; }
    """
    BINDINGS: ClassVar[list[BindingType]] = [
        # OptionList も enter を持ち、フッターに出さない設定になっている。priority で先に受け取らないと
        # フッターに「深く」が出ない
        Binding("enter,right", "enter", "深く", key_display="enter", priority=True),
        Binding("escape,left,backspace", "leave", "浅く"),
        Binding("q", "quit", "終了"),
    ]

    def __init__(self, run_name: str | None = None) -> None:
        super().__init__()
        self.level = Level.TASKS if run_name else Level.RUNS
        self.run_name = run_name
        self.task_id: str | None = None
        #: 層ごとに選んでいる項目。読み直しても同じ項目にカーソルを保つ
        self.cursor: dict[Level, str | None] = {level: None for level in Level}
        #: ランのリストでは全ラン、深い層では選んだラン 1 つだけ
        self.states: list[dict] = []
        #: status を呼べなかった理由。呼べていれば None
        self.failure: str | None = None
        self.now = dt.datetime.now().astimezone()
        self.signature: list[tuple[str, str]] = []

    def compose(self) -> ComposeResult:
        with Horizontal():
            with Vertical(id="left"):
                yield Static(id="crumb")
                yield OptionList(id="list")
            with VerticalScroll(id="right"):
                yield Static(id="info-body")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one(OptionList).focus()
        self.reload()
        self.set_interval(REFRESH_SECONDS, self.reload)

    # --- 読む ------------------------------------------------------------------

    def fetch(self) -> None:
        """今の層に要る status だけを呼ぶ。ラン 1 つが消えていたら、ランのリストに戻る。"""
        if self.level is not Level.RUNS and self.run_name is not None:
            reply = autodev.status(self.run_name)
            st, self.failure = runs.single(reply.code, reply.data, reply.message)
            if st is not None:
                self.states = [st]
                return
            if self.failure is not None:
                self.states = []
                return
            self.level, self.run_name, self.task_id = Level.RUNS, None, None
        reply = autodev.statuses()
        found = runs.listing(reply.code, reply.data, reply.message)
        self.failure = found.error
        self.states = runs.order(found.runs, self.now)

    @property
    def current_run(self) -> dict | None:
        return self.find_run(self.run_name)

    def find_run(self, name: str | None) -> dict | None:
        return next((st for st in self.states if runs.name_of(st) == name), None)

    def task(self) -> dict | None:
        st = self.current_run or {}
        return next((t for t in runs.tasks(st) if str(t.get("id")) == self.task_id), None)

    def stage_items(self) -> list[stagelist.StageItem]:
        task = self.task()
        return stagelist.for_task(task) if task else []

    def options(self) -> list[tuple[str, Text]]:
        """今の層のリストの項目（キーと行）。"""
        if self.level is Level.RUNS:
            return [(runs.name_of(st), self.run_row(st)) for st in self.states]
        st = self.current_run
        if st is None:
            return []
        if self.level is Level.TASKS:
            return [(str(t.get("id")), navigator.task_row(t)) for t in runs.tasks(st)]
        return [(item.key, navigator.stage_row(item)) for item in self.stage_items()]

    def run_row(self, st: dict) -> Text:
        if runs.error_of(st):
            return navigator.broken_run_row(runs.name_of(st))
        return navigator.run_row(headline.build(st, self.now))

    def default_key(self, keys: list[str]) -> str | None:
        """その層に初めて入ったときに選ぶ項目。走っているもの、無ければ先頭。"""
        if self.level is Level.RUNS:
            return keys[0] if keys else None
        if self.level is Level.TASKS:
            st = self.current_run or {}
            running = next((t for t in runs.tasks(st) if t.get("status") == "running"), None)
            return str(running.get("id")) if running else (keys[0] if keys else None)
        items = self.stage_items()
        current = next((i for i in items if i.mark is Mark.CURRENT), None)
        started = [i for i in items if i.mark is not Mark.NEXT]
        pick = current or (started[-1] if started else (items[0] if items else None))
        return pick.key if pick else None

    # --- 描く ------------------------------------------------------------------

    def reload(self) -> None:
        self.now = dt.datetime.now().astimezone()
        self.fetch()
        self.query_one("#crumb", Static).update(
            navigator.breadcrumb(
                self.run_name if self.level is not Level.RUNS else None,
                self.task_id if self.level is Level.STAGES else None,
            )
        )
        self.fill_list()
        self.show_detail()

    def fill_list(self) -> None:
        """リストを組み直す。**中身が変わらなければ組み直さない**（2 秒ごとにちらつかせない）。"""
        listing = self.query_one(OptionList)
        options = self.options()
        keys = [key for key, _ in options]
        if self.cursor[self.level] not in keys:
            self.cursor[self.level] = self.default_key(keys)
        signature = [(key, row.plain) for key, row in options]
        if signature != self.signature:
            self.signature = signature
            listing.clear_options()
            listing.add_options([Option(row, id=key) for key, row in options])
        if self.cursor[self.level] in keys:
            listing.highlighted = keys.index(str(self.cursor[self.level]))

    def show_detail(self) -> None:
        self.query_one("#info-body", Static).update(self.info())

    def info(self) -> Text | Group:
        if self.failure is not None:
            return detail.failure_detail(self.failure)
        key = self.cursor[self.level]
        st = self.current_run if self.level is not Level.RUNS else self.find_run(key)
        if st is None or key is None:
            return Text("autodev のランが無い", style=DIM)
        error = runs.error_of(st)
        if error is not None:
            return detail.broken_detail(runs.name_of(st), error)
        if self.level is Level.RUNS:
            return detail.run_detail(headline.build(st, self.now), st, self.now)
        return self.item_info(st, key)

    def item_info(self, st: dict, key: str) -> Text | Group:
        """タスクか段を選んだときの詳細。"""
        if self.level is Level.STAGES:
            item = next((i for i in self.stage_items() if i.key == key), None)
            return detail.stage_detail(item, self.now) if item else Text("")
        task = next((t for t in runs.tasks(st) if str(t.get("id")) == key), None)
        if task is None:
            return Text("")
        return detail.task_detail(task, pipeline.steps(task), self.now)

    # --- 操作 ------------------------------------------------------------------

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        key = event.option.id
        if key and key != self.cursor[self.level]:
            self.cursor[self.level] = key
            self.show_detail()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.action_enter()

    def action_enter(self) -> None:
        key = self.cursor[self.level]
        if key is None or self.level is Level.STAGES:
            return
        if self.level is Level.RUNS:
            st = self.find_run(key)
            # 読めないランには入らない。status --name も同じ理由で読めない
            if st is None or runs.error_of(st):
                return
            if key != self.run_name:
                self.cursor[Level.TASKS] = None
            self.run_name, self.level = key, Level.TASKS
        else:
            if key != self.task_id:
                self.cursor[Level.STAGES] = None
            self.task_id, self.level = key, Level.STAGES
        self.signature = []
        self.reload()

    def action_leave(self) -> None:
        if self.level is Level.STAGES:
            self.level = Level.TASKS
        elif self.level is Level.TASKS:
            self.level = Level.RUNS
            self.cursor[Level.RUNS] = self.run_name
        else:
            return
        self.signature = []
        self.reload()


def main() -> int:
    # install.sh が uv に依存を取り寄せさせるためだけに呼ぶ。import が通れば用は済んでいる
    if "--warm" in sys.argv[1:]:
        return 0
    Watch(sys.argv[1] if len(sys.argv) > 1 else None).run()
    return 0
