"""タスク 1 本の段の並び。済んだ段・走っている段・これからの段を決める。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from hud.core.runs import Stage

#: 段の名前を、タスクリストに出す短い名前へ。レビュー 2 体は 1 つにまとめる
SHORT = {
    "testgen": "testgen",
    "impl": "impl",
    "fix": "fix",
    "review:normal": "review",
    "review:adversarial": "review",
    "judge": "judge",
    "pr-body": "PR",
}
#: その段の後に来る段。裁定で指摘が残れば fix に戻るが、戻るかどうかは裁定が終わるまで分からない
NEXT: dict[str | None, list[str]] = {
    None: ["testgen", "impl", "review", "judge", "PR"],
    "testgen": ["impl", "review", "judge", "PR"],
    "impl": ["review", "judge", "PR"],
    "fix": ["review", "judge", "PR"],
    "review": ["judge", "PR"],
    "judge": ["PR"],
    "PR": [],
}
#: 済んだ段がこれより多いと、古いものを 1 つの「…」にまとめる
MAX_DONE = 3
#: まとめたときに残す、済んだ段の数
KEEP_DONE = 2


class Mark(Enum):
    DONE = "done"
    FAILED = "failed"
    CURRENT = "current"
    NEXT = "next"
    #: 古い済んだ段をまとめたもの
    ELIDED = "elided"


@dataclass(frozen=True)
class Step:
    name: str
    round: str
    mark: Mark

    @property
    def label(self) -> str:
        """レビュー・裁定・修正は 2 巡目から巡目を添える。"""
        if self.name in ("review", "judge", "fix") and self.round not in ("", "0", "1"):
            return f"{self.name} r{self.round}"
        return self.name


def short_name(stage: str) -> str:
    return SHORT.get(stage, stage)


def steps(task: dict, stages: list[Stage]) -> list[Step]:
    """`task["stages"]`（driver が段の終わりに足す）と、走っている段から並びを組む。"""
    done: list[Step] = []
    for entry in task.get("stages") or []:
        name = short_name(str(entry.get("name")))
        round_label = str(entry.get("round") or "0")
        ok = bool(entry.get("ok", True))
        # レビュー 2 体のように、同じ巡目の同じ段は 1 つにまとめる
        if done and (done[-1].name, done[-1].round) == (name, round_label):
            ok = ok and done[-1].mark is Mark.DONE
            done.pop()
        done.append(Step(name, round_label, Mark.DONE if ok else Mark.FAILED))

    now = [
        Step(short_name(s.name), s.round, Mark.CURRENT) for s in stages if s.task == task.get("id")
    ]
    current = now[0] if now else None
    if current and done and (done[-1].name, done[-1].round) == (current.name, current.round):
        done.pop()  # 2 体のうち 1 体だけ終わったレビュー
    last = current.name if current else (done[-1].name if done else None)

    out: list[Step] = []
    if len(done) > MAX_DONE:
        out.append(Step("…", "", Mark.ELIDED))
        done = done[-KEEP_DONE:]
    out += done
    if current:
        out.append(current)
    out += [Step(name, "", Mark.NEXT) for name in NEXT.get(last, [])]
    return out
