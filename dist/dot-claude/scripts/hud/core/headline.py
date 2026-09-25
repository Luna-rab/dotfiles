"""ラン 1 つの見出し。いま何をしているかを 1 つに決める。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum

from hud.core.pipeline import full_name, short_name
from hud.core.runs import STAGE_TIMEOUT, Stage, name_of, tasks

#: タスクに属さないステージ。見出しにだけ出す
RUN_STAGES = {"plan": "計画中", "summary": "まとめ"}


class State(Enum):
    #: ステージが回答を待って止まっている（`autodev answer` を待つ）
    WAITING = "waiting"
    #: ステージが走っている
    RUNNING = "running"
    #: ステージとステージの間。driver が検証・push・PR 作成をしている
    BETWEEN = "between"
    #: 動いていない
    STOPPED = "stopped"


@dataclass(frozen=True)
class Headline:
    run_name: str
    state: State
    #: 何をしているか（「task2 レビュー r1」「計画中」「計画が回答待ち · range-empty」など）
    doing: str
    #: いちばん新しいステージが走り始めてからの秒数
    elapsed: float | None = None
    #: ステージの制限時間を超えている
    overdue: bool = False
    #: ターン数と直前のツール。ステージが 1 つのときだけ入れる（並んでいるとどちらの数か分からない）
    turns: int = 0
    tool: str = ""
    overview_pr: int | None = None
    stacked: int = 0
    total: int = 0
    held: int = 0


def build(st: dict, stages: list[Stage], active: bool) -> Headline:
    # 再計画で取り下げたタスクはスタックに追加しないので、進み具合の分母に入れない
    items = [t for t in tasks(st) if t.get("status") != "dropped"]
    stopped = Headline(
        run_name=name_of(st),
        state=State.STOPPED,
        doing="止まっている",
        overview_pr=st.get("overviewPr") or None,
        stacked=sum(1 for t in items if t.get("status") == "stacked"),
        total=len(items),
        held=sum(1 for t in items if t.get("status") in ("blocked", "failed")),
    )
    deferred = st.get("deferred")
    if isinstance(deferred, dict) and deferred:
        questions = [q for q in (st.get("questions") or []) if isinstance(q, dict)]
        keys = " ".join(str(q.get("id") or "?") for q in questions) or "?"
        doing = f"{full_name(str(deferred.get('stage', '?')))}が回答待ち · {keys}"
        return replace(stopped, state=State.WAITING, doing=doing)
    if stages:
        first = stages[0]
        newest = min(s.seconds for s in stages)
        if first.name in RUN_STAGES:
            doing = RUN_STAGES[first.name]
        else:
            label = "+".join(dict.fromkeys(short_name(s.name) for s in stages))
            doing = f"{first.task} {label} r{first.round}"
        single = len(stages) == 1
        return replace(
            stopped,
            state=State.RUNNING,
            doing=doing,
            elapsed=newest,
            overdue=newest > STAGE_TIMEOUT,
            turns=first.turns if single else 0,
            tool=first.tool if single else "",
        )
    if active:
        current = next((t for t in items if t.get("status") == "running"), {})
        return replace(
            stopped, state=State.BETWEEN, doing=f"{current.get('id', '?')} 完了チェックと公開"
        )
    return stopped


def outcome(head: Headline) -> str:
    """ランの状態の表示名（`autodev/GLOSSARY.md` の「ランの状態」）。"""
    if head.state is State.WAITING:
        return "回答待ち"
    if head.state in (State.RUNNING, State.BETWEEN):
        return "計画中" if head.total == 0 else "実行中"
    if head.held:
        return "要対応"
    if head.total and head.stacked == head.total:
        return "完了"
    return "止まっている"
