"""autodev のランを見る画面（Textual）。2 秒ごとに読み直すだけで、何も書き込まない。

左ペインは「ラン → タスク → ステージ」のリストを 1 層ずつ出し、右ペインに選んでいる項目の
詳細を出す。

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
from textual.widgets import ContentSwitcher, Footer, OptionList, RichLog, Static
from textual.widgets.option_list import Option

from hud.core import activity, headline, pipeline, review, runs, stagelist
from hud.core.pipeline import Mark
from hud.ports import autodev
from hud.render import detail, navigator
from hud.render.theme import DIM

REFRESH_SECONDS = 2.0
#: ステージの出力に出す、ログの末尾のイベント数
LOG_EVENTS = 300


class Level(Enum):
    RUNS = "runs"
    TASKS = "tasks"
    STAGES = "stages"


class Watch(App):
    TITLE = "autodev watch"
    CSS = """
    #left { width: 1fr; }
    #crumb { height: 1; padding: 0 1; }
    #right { width: 2fr; border-left: solid $panel; }
    #info { padding: 0 1; }
    #stage { padding: 0 1; }
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
        self.states: list[dict] = []
        self.now = dt.datetime.now().astimezone()
        self.signature: list[tuple[str, str]] = []
        self.log_source: tuple[str | None, int] = (None, 0)

    def compose(self) -> ComposeResult:
        with Horizontal():
            with Vertical(id="left"):
                yield Static(id="crumb")
                yield OptionList(id="list")
            with ContentSwitcher(id="right", initial="info"):
                with VerticalScroll(id="info"):
                    yield Static(id="info-body")
                yield RichLog(id="stage", markup=False, wrap=True)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one(OptionList).focus()
        self.reload()
        self.set_interval(REFRESH_SECONDS, self.reload)

    # --- 読む ------------------------------------------------------------------

    @property
    def current_run(self) -> dict | None:
        return self.find_run(self.run_name)

    def find_run(self, name: str | None) -> dict | None:
        return next((st for st in self.states if runs.name_of(st) == name), None)

    def stages_of(self, st: dict) -> list[runs.Stage]:
        return runs.live_stages(st, self.now)

    def head_of(self, st: dict) -> headline.Headline:
        stages = self.stages_of(st)
        return headline.build(st, stages, runs.is_active(st, stages, self.now))

    def task(self) -> dict | None:
        st = self.current_run or {}
        return next((t for t in runs.tasks(st) if str(t.get("id")) == self.task_id), None)

    def stage_items(self) -> list[stagelist.StageItem]:
        st = self.current_run
        if st is None or self.task_id is None:
            return []
        stages = self.stages_of(st)
        if self.task_id == stagelist.RUN_TASK:
            names = autodev.log_names(str(self.run_name), stagelist.RUN_TASK)
            return stagelist.for_run(names, stages)
        task = self.task()
        return stagelist.for_task(task, stages) if task else []

    def options(self) -> list[tuple[str, Text]]:
        """今の層のリストの項目（キーと行）。"""
        if self.level is Level.RUNS:
            return [(runs.name_of(st), navigator.run_row(self.head_of(st))) for st in self.states]
        st = self.current_run
        if st is None:
            return []
        if self.level is Level.TASKS:
            active = any(s.task == stagelist.RUN_TASK for s in self.stages_of(st))
            rows = [(stagelist.RUN_TASK, navigator.run_task_row(active))]
            return rows + [(str(t.get("id")), navigator.task_row(t)) for t in runs.tasks(st)]
        return [(item.key, navigator.stage_row(item)) for item in self.stage_items()]

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
        self.states = runs.order(autodev.read_states(), self.now)
        if self.level is not Level.RUNS and self.current_run is None:
            self.level, self.run_name, self.task_id = Level.RUNS, None, None
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
        switcher = self.query_one(ContentSwitcher)
        key = self.cursor[self.level]
        st = self.current_run if self.level is not Level.RUNS else self.find_run(key)
        if st is None or key is None:
            switcher.current = "info"
            self.query_one("#info-body", Static).update(Text("autodev のランが無い", style=DIM))
            return
        if self.level is Level.STAGES:
            item = next((i for i in self.stage_items() if i.key == key), None)
            if item is not None and item.code is not None:
                switcher.current = "stage"
                self.show_stage(item)
                return
        switcher.current = "info"
        self.query_one("#info-body", Static).update(self.info(st, key))

    def info(self, st: dict, key: str) -> Text | Group:
        if self.level is Level.RUNS:
            return detail.run_detail(self.head_of(st), st, autodev.read_overview(key))
        if self.level is Level.STAGES:
            return Text("まだ走っていない", style=DIM)
        if key == stagelist.RUN_TASK:
            head = self.head_of(st)
            return Text(navigator.RUN_TASK_LABEL + "\n\n", style="bold").append(
                f"いま: {head.doing}\nEnter で計画ステージとまとめステージの一覧に入る", style=DIM
            )
        task = next((t for t in runs.tasks(st) if str(t.get("id")) == key), None)
        if task is None:
            return Text("")
        stages = self.stages_of(st)
        mine = [s for s in stages if s.task == key]
        found = review.summarize(autodev.read_review(runs.name_of(st), key))
        return detail.task_detail(task, pipeline.steps(task, stages), mine, found)

    def show_stage(self, item: stagelist.StageItem) -> None:
        """渡した指示と出力を 1 つのペインに続けて出す。

        同じログが伸びただけなら書き直さずに足す（書き直すとスクロール位置が先頭に戻る）。
        開いたときは指示の先頭を見せ、末尾まで読み進めているときだけ新しい出力を追う。
        """
        run_name, task_id, code = str(self.run_name), str(self.task_id), str(item.code)
        path = autodev.stage_file(run_name, task_id, code, item.round, ".jsonl")
        pane = self.query_one("#stage", RichLog)
        events = activity.parse(autodev.read_lines(path), LOG_EVENTS)
        source, seen = self.log_source
        if path != source or len(events) < seen:
            pane.clear()
            pane.auto_scroll = False
            prompt = autodev.read_text(
                autodev.stage_file(run_name, task_id, code, item.round, ".prompt.md")
            )
            pane.write(detail.stage_prompt(prompt))
            pane.write(detail.stage_output_heading())
            seen = 0
        else:
            pane.auto_scroll = pane.is_vertical_scroll_end
        for event in events[seen:]:
            pane.write(detail.activity_line(event))
        self.log_source = (path, len(events))

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
