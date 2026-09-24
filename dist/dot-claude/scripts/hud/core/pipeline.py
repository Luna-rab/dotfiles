"""タスク 1 本のステージの並び。済んだステージ・走っているステージ・これからのステージを決める。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from hud.core.runs import Stage

#: ステージのコード名から、画面に出す名前へ（`autodev/GLOSSARY.md` のステージ名）
FULL = {
    "plan": "計画",
    "testgen": "テスト作成",
    "impl": "実装",
    "fix": "修正",
    "review:normal": "通常レビュー",
    "review:adversarial": "敵対的レビュー",
    "judge": "ジャッジ",
    "pr-body": "PR 本文",
    "summary": "まとめ",
}
#: タスクリストの並びでは、同じラウンドに並んで走るレビュー 2 つを 1 つにまとめる
SHORT = {**FULL, "review:normal": "レビュー", "review:adversarial": "レビュー"}
#: そのステージの後に来るステージ。ジャッジで指摘が残れば修正に戻るが、戻るかどうかはジャッジが終わるまで分からない
NEXT: dict[str | None, list[str]] = {
    None: ["テスト作成", "実装", "レビュー", "ジャッジ", "PR 本文"],
    "テスト作成": ["実装", "レビュー", "ジャッジ", "PR 本文"],
    "実装": ["レビュー", "ジャッジ", "PR 本文"],
    "修正": ["レビュー", "ジャッジ", "PR 本文"],
    "レビュー": ["ジャッジ", "PR 本文"],
    "ジャッジ": ["PR 本文"],
    "PR 本文": [],
}
#: 済んだステージがこれより多いと、古いものを 1 つの「…」にまとめる
MAX_DONE = 3
#: まとめたときに残す、済んだステージの数
KEEP_DONE = 2


class Mark(Enum):
    DONE = "done"
    FAILED = "failed"
    CURRENT = "current"
    NEXT = "next"
    #: 古い済んだステージをまとめたもの
    ELIDED = "elided"


@dataclass(frozen=True)
class Step:
    name: str
    round: str
    mark: Mark

    @property
    def label(self) -> str:
        """レビュー・ジャッジ・修正は 2 ラウンド目からラウンドを添える。"""
        if self.name in ("レビュー", "ジャッジ", "修正") and self.round not in ("", "0", "1"):
            return f"{self.name} r{self.round}"
        return self.name


def short_name(stage: str) -> str:
    return SHORT.get(stage, stage)


def full_name(stage: str) -> str:
    return FULL.get(stage, stage)


def steps(task: dict, stages: list[Stage]) -> list[Step]:
    """`task["stages"]`（driver がステージの終わりに足す）と、走っているステージから並びを組む。"""
    done: list[Step] = []
    for entry in task.get("stages") or []:
        name = short_name(str(entry.get("name")))
        round_label = str(entry.get("round") or "0")
        ok = bool(entry.get("ok", True))
        # レビュー 2 体のように、同じラウンドの同じステージは 1 つにまとめる
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
