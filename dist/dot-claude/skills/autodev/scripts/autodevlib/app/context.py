"""run 1 つの間ずっと変わらないものと、run の結末を表す終了コード。

終了コードが `app` にあるのは、進めなくなった理由を知っているのが進行の側だからである。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

from ..config import paths
from ..ports import run_store

#: 終了コード。**呼んだ側がこれで分岐する**
EXIT_OK = 0
EXIT_PLAN_BLOCKED = 2
EXIT_HELD = 3
EXIT_WAITING = 4


@dataclass
class Ctx:
    """1 つの run の間ずっと変わらないもの。段の呼び出しはすべてこれを持ち回る。"""

    run: paths.Run
    st: dict[str, Any]
    #: 1 巡目のレビューは 2 体を同時に走らせる。同じ state を 2 つのスレッドが書くので、
    #: 書き出しは直列にする（`json.dumps` の途中で辞書が変わると落ちる）
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def save(self) -> None:
        with self._lock:
            run_store.save(self.run.state, self.st)

    def begin(self, stage: str, task_id: str, round_label: str) -> None:
        """走り始めた段を state.json に載せる。

        **段の途中で state.json が更新される唯一の経路である。** これが無いと、外から
        「いまどこを走っているか」も「まだ生きているか」も読めない（タスクの境目まで
        `updatedAt` が動かない）。段の名前を鍵にするのは、同時に走る 2 体を並べるため。
        """
        self.st.setdefault("running", {})[stage] = {
            "task": task_id,
            "round": round_label,
            "at": run_store.now(),
        }
        self.save()

    def end(self, stage: str) -> None:
        (self.st.get("running") or {}).pop(stage, None)
        self.save()

    def progress(self, stage: str, **fields: Any) -> None:
        """走っている段の進み具合を上書きする。**呼び出し間隔は呼ぶ側が絞る**
        （段 1 回で数百イベント流れるので、毎回書くと state.json への書き出しが増える）。"""
        entry = (self.st.get("running") or {}).get(stage)
        if not isinstance(entry, dict):
            return
        entry.update(fields)
        self.save()
