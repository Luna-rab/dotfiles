from __future__ import annotations

from dataclasses import dataclass

from .finding_id import FindingId
from .finding_status import FindingStatus


@dataclass(frozen=True)
class FindingVerdict:
    """ジャッジの判定 1 件（JudgeFinding）。"""

    finding: FindingId
    to: FindingStatus
    comment: str
