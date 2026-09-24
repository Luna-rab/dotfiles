"""autodev の run（`state.json` の中身）が、いま動いているか。

判定に使うのは `running`（driver がステージの開始と終了で書き、走行中は 5 秒ごとにターン数と直前の
ツールを上書きする）。**ステージの途中で更新される値はこれだけ**なので、進行と生存の両方をここで見る。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

#: ステージ 1 つの制限時間（`autodevlib/config/stages.py` の `Stage.timeout`）。これを超えたら `!`
STAGE_TIMEOUT = 3600
#: これを超えて更新の無いラン・ステージは、driver が落ちたものとして扱う
GIVE_UP = 3 * 3600


@dataclass(frozen=True)
class Stage:
    """走っているステージ 1 つ。"""

    name: str
    task: str
    round: str
    #: 走り始めてからの秒数
    seconds: float
    turns: int
    tool: str


def age(stamp: Any, now: dt.datetime) -> float | None:
    """ISO 8601 の時刻から `now` までの秒数。読めなければ None。"""
    try:
        at = dt.datetime.fromisoformat(str(stamp))
    except ValueError:
        return None
    if (at.tzinfo is None) != (now.tzinfo is None):
        now = now.replace(tzinfo=None) if at.tzinfo is None else now.astimezone()
    return (now - at).total_seconds()


def live_stages(st: dict, now: dt.datetime) -> list[Stage]:
    """走っているステージ。driver がステージの途中で落ちると `running` が残るので、`GIVE_UP` を超えたものは外す。"""
    running = st.get("running")
    if not isinstance(running, dict):
        return []
    stages = []
    for name, info in sorted(running.items()):
        if not isinstance(info, dict):
            continue
        seconds = age(info.get("at"), now)
        if seconds is None or seconds > GIVE_UP:
            continue
        stages.append(
            Stage(
                name=name,
                task=str(info.get("task") or ""),
                round=str(info.get("round") or "0"),
                seconds=seconds,
                turns=int(info.get("turns") or 0),
                tool=str(info.get("tool") or ""),
            )
        )
    return stages


def is_active(st: dict, stages: list[Stage], now: dt.datetime) -> bool:
    """表示するランか。**ステージとステージの間**（検証・push・PR 作成）も、タスクが running なら出す。"""
    if stages or st.get("deferred"):
        return True
    updated = age(st.get("updatedAt"), now)
    fresh = updated is not None and updated <= GIVE_UP
    return fresh and any(t.get("status") == "running" for t in tasks(st))


def order(states: list[dict], now: dt.datetime) -> list[dict]:
    """動いているランを先に、あとは更新の新しい順に並べる。"""

    def key(st: dict) -> tuple[bool, float]:
        active = is_active(st, live_stages(st, now), now)
        updated = age(st.get("updatedAt"), now)
        return (not active, updated if updated is not None else float("inf"))

    return sorted(states, key=key)


def tasks(st: dict) -> list[dict]:
    return [t for t in st.get("tasks") or [] if isinstance(t, dict)]


def short(seconds: float) -> str:
    """経過時間の表記。1 分未満は秒、1 時間未満は分と秒、それ以上は時と分。"""
    total = int(seconds)
    if total < 60:
        return f"{total}s"
    if total < 3600:
        return f"{total // 60}m{total % 60:02d}s"
    return f"{total // 3600}h{total % 3600 // 60:02d}m"
