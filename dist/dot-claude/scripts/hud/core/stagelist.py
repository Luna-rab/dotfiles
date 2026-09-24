"""autodev-watch のステージのリスト。済んだ・失敗した・走っている・これからのステージを 1 行ずつ並べる。

statusline の段の並び（`core/pipeline.py`）と違い、レビュー 2 つをまとめない。ステージを選んで
指示と出力を見るので、ログのファイルと 1 対 1 に対応させる。
"""

from __future__ import annotations

from dataclasses import dataclass

from hud.core.pipeline import FULL, NEXT, Mark, short_name
from hud.core.runs import Stage

#: どのタスクにも属さないステージ（計画・まとめ）を並べる仮のタスク
RUN_TASK = "task0"


@dataclass(frozen=True)
class StageItem:
    #: ステージのコード名（`impl`・`review:normal`）。これからのステージは None
    code: str | None
    round: str
    mark: Mark
    #: 画面に出す名前（`実装`・`通常レビュー`）
    name: str

    @property
    def key(self) -> str:
        """リストの中で項目を見分ける値。読み直してもカーソルを同じ項目に保つのに使う。"""
        return f"{self.code}@{self.round}" if self.code else f"next:{self.name}"

    @property
    def label(self) -> str:
        return f"{self.name} r{self.round}" if self.round else self.name


def for_task(task: dict, stages: list[Stage]) -> list[StageItem]:
    """タスク 1 つのステージ。`task["stages"]`（driver が終わりに足す）と、走っているステージから組む。"""
    items: list[StageItem] = []
    done: set[tuple[str, str]] = set()
    for entry in task.get("stages") or []:
        code, round_label = str(entry.get("name")), str(entry.get("round") or "0")
        mark = Mark.DONE if entry.get("ok", True) else Mark.FAILED
        items.append(StageItem(code, round_label, mark, FULL.get(code, code)))
        done.add((code, round_label))
    for stage in stages:
        if stage.task == task.get("id") and (stage.name, stage.round) not in done:
            items.append(
                StageItem(stage.name, stage.round, Mark.CURRENT, FULL.get(stage.name, stage.name))
            )
    last = short_name(items[-1].code or "") if items else None
    items += [StageItem(None, "", Mark.NEXT, name) for name in NEXT.get(last, [])]
    return items


def for_run(log_names: list[str], stages: list[Stage]) -> list[StageItem]:
    """どのタスクにも属さないステージ（計画・まとめ）。driver は記録を残さないので、ログのファイル名から組む。"""
    running = {(s.name, s.round) for s in stages if s.task == RUN_TASK}
    items: list[StageItem] = []
    for name in log_names:
        parsed = parse_log_name(name)
        if parsed is None:
            continue
        code, round_label = parsed
        mark = Mark.CURRENT if (code, round_label) in running else Mark.DONE
        items.append(StageItem(code, round_label, mark, FULL.get(code, code)))
    seen = {(i.code, i.round) for i in items}
    items += [
        StageItem(code, round_label, Mark.CURRENT, FULL.get(code, code))
        for code, round_label in sorted(running)
        if (code, round_label) not in seen
    ]
    return items


def parse_log_name(filename: str) -> tuple[str, str] | None:
    """`review-normal-1.jsonl` → (`review:normal`, `1`)。

    ラウンドが `0-2` のようにハイフンを含むので、末尾のハイフンでは分けられない。既知のステージ名で
    前から照らす（長い名前を先に試す。`review-normal` を `review` と読み違えないため）。
    """
    if not filename.endswith(".jsonl"):
        return None
    stem = filename[: -len(".jsonl")]
    for code in sorted(FULL, key=len, reverse=True):
        prefix = code.replace(":", "-") + "-"
        if stem.startswith(prefix) and len(stem) > len(prefix):
            return code, stem[len(prefix) :]
    return None
