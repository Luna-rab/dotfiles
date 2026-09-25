"""ラン 1 つの間ずっと変わらないものと、ランの結末を表す終了コード。

終了コードが `app` にあるのは、進めなくなった理由を知っているのが進行の側だからである。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

from ..config import paths
from ..ports import run_store

#: 終了コード。**呼び出し元のエージェントがこれで分岐する**
EXIT_OK = 0
EXIT_PLAN_BLOCKED = 2
EXIT_HELD = 3
EXIT_WAITING = 4


class Waiting(Exception):
    """人の回答が要る。`drive()` が受け取り、質問を出して回答待ちで終わる。

    回答が置かれたら、タスクは `phase` から続く。どこで止まってもやり直しにはならない。
    """

    def __init__(self, task_id: str, questions: list[dict[str, str]]) -> None:
        super().__init__(task_id)
        self.task_id = task_id
        self.questions = questions


class NeedsReplan(Exception):
    """タスクの割り方を直さないと進めない。`drive()` が受け取り、再計画ステージを呼ぶ。"""

    def __init__(self, task_id: str, reason: str, items: list[str]) -> None:
        super().__init__(task_id)
        self.task_id = task_id
        self.reason = reason
        self.items = items


@dataclass
class Ctx:
    """1 つのランの間ずっと変わらないもの。ステージの呼び出しはすべてこれを持ち回る。"""

    run: paths.Run
    st: dict[str, Any]
    #: 1 ラウンド目のレビューは 2 体を同時に走らせる。同じ state を 2 つのスレッドが書くので、
    #: 書き出しは直列にする（`json.dumps` の途中で辞書が変わると落ちる）
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def save(self) -> None:
        with self._lock:
            run_store.save(self.run.state, self.st)

    def begin(self, stage: str, task_id: str, round_label: str) -> None:
        """走り始めたステージを state.json に載せる。

        **ステージの途中で state.json が更新される唯一の経路である。** これが無いと、外から
        「いまどこを走っているか」も「まだ生きているか」も読めない（タスクの境目まで
        `updatedAt` が動かない）。ステージの名前をキーにするのは、同時に走る 2 体を並べるため。
        """
        self.st.setdefault("running", {})[stage] = {
            "task": task_id,
            "round": round_label,
            "at": run_store.now(),
        }
        self.save()

    def end(self, stage: str, *, ok: bool) -> None:
        """走り終えたステージを `running` から外し、そのタスクの `stages` に足す。

        statusline はタスクごとの `stages` を読んで「済・今・これから」を描く。`running` は
        走っているステージしか持たないので、これが無いと済んだステージが外から見えない。
        """
        entry = (self.st.get("running") or {}).pop(stage, None)
        task_id = entry.get("task") if isinstance(entry, dict) else None
        for item in self.st.get("tasks") or []:
            if item.get("id") == task_id:
                done = {"name": stage, "round": entry["round"], "ok": ok}
                item.setdefault("stages", []).append(done)
        self.save()

    def progress(self, stage: str, **fields: Any) -> None:
        """走っているステージの進み具合を上書きする。**呼び出し間隔は呼ぶ側が絞る**
        （ステージ 1 回で数百イベント流れるので、毎回書くと state.json への書き出しが増える）。"""
        entry = (self.st.get("running") or {}).get(stage)
        if not isinstance(entry, dict):
            return
        entry.update(fields)
        self.save()
