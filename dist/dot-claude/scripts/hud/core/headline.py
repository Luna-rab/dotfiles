"""run 1 つの見出し。いま何をしているかを 1 つに決める。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum

from hud.core.pipeline import short_name
from hud.core.runs import STAGE_TIMEOUT, Stage, tasks

#: タスクに属さない段。見出しにだけ出す
RUN_STAGES = {"plan": "計画中", "summary": "まとめ"}


class State(Enum):
    #: 段が答えを待って止まっている（`autodev answer` を待つ）
    WAITING = "waiting"
    #: 段が走っている
    RUNNING = "running"
    #: 段と段の間。driver が検証・push・PR 作成をしている
    BETWEEN = "between"
    #: 動いていない
    STOPPED = "stopped"


@dataclass(frozen=True)
class Headline:
    work: str
    state: State
    #: 何をしているか（「task2 review r1」「計画中」「plan が答え待ち · range-empty」など）
    doing: str
    #: いちばん新しい段が走り始めてからの秒数
    elapsed: float | None = None
    #: 段の制限時間を超えている
    overdue: bool = False
    #: 往復数と直前のツール。段が 1 つのときだけ入れる（並んでいるとどちらの数か分からない）
    turns: int = 0
    tool: str = ""
    stack_pr: int | None = None
    stacked: int = 0
    total: int = 0
    held: int = 0


def build(st: dict, stages: list[Stage], active: bool) -> Headline:
    items = tasks(st)
    stopped = Headline(
        work=str(st.get("work", "?")),
        state=State.STOPPED,
        doing="止まっている",
        stack_pr=st.get("stackPr") or None,
        stacked=sum(1 for t in items if t.get("status") == "stacked"),
        total=len(items),
        held=sum(1 for t in items if t.get("status") in ("blocked", "failed")),
    )
    deferred = st.get("deferred")
    if isinstance(deferred, dict) and deferred:
        questions = [q for q in (st.get("questions") or []) if isinstance(q, dict)]
        keys = " ".join(str(q.get("id") or "?") for q in questions) or "?"
        doing = f"{deferred.get('stage', '?')} が答え待ち · {keys}"
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
        return replace(stopped, state=State.BETWEEN, doing=f"{current.get('id', '?')} 検査と PR")
    return stopped
