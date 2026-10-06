"""モデルのクラスと、クラスごとのモデルと effort。"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from enum import Enum

from .base import Text, non_blank


class ModelClass(Enum):
    """ステージと統括の役割の区分。系統の違うモデルを当てたい役割（レビュー）を、ほかと分けて替えられる。"""

    #: 統括・計画・設計の書き直し・ジャッジ。判断を出し、後ろで誰も確かめない
    LEAD = "lead"
    #: 設計とコードのレビュー
    REVIEW = "review"
    #: テスト・実装・修正・衝突の解消
    IMPLEMENT = "implement"
    #: PR の題と本文
    WRITE = "write"


class Effort(Enum):
    """claude の `--effort` が受ける値。"""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"
    MAX = "max"


class ModelName(Text):
    """claude の `--model` に渡す名前。別名（opus）も正式な ID（claude-opus-5-5）も通す。"""

    def _check(self) -> None:
        non_blank("モデルの名前", self.value)


@dataclass(frozen=True)
class ModelChoice:
    model: ModelName
    effort: Effort


@dataclass(frozen=True)
class ModelClasses:
    """4 つのクラスそれぞれのモデルと effort。欄の名前は `ModelClass` の値に揃える（JSON の鍵になる）。"""

    lead: ModelChoice
    review: ModelChoice
    implement: ModelChoice
    write: ModelChoice

    @classmethod
    def default(cls) -> ModelClasses:
        opus, sonnet = ModelName("opus"), ModelName("sonnet")
        return cls(
            lead=ModelChoice(opus, Effort.HIGH),
            review=ModelChoice(opus, Effort.MEDIUM),
            implement=ModelChoice(sonnet, Effort.MEDIUM),
            write=ModelChoice(sonnet, Effort.MEDIUM),
        )

    def of(self, cls: ModelClass) -> ModelChoice:
        return getattr(self, cls.value)

    def with_choice(
        self,
        cls: ModelClass,
        *,
        model: ModelName | None = None,
        effort: Effort | None = None,
    ) -> ModelClasses:
        now = self.of(cls)
        chosen = ModelChoice(model or now.model, effort or now.effort)
        return dataclasses.replace(self, **{cls.value: chosen})
