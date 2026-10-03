"""ストリームと集約の対応。driver（書く側）と `status --json`（読む側）が、同じ表で再生する。"""

from __future__ import annotations

from .aggregate import Aggregate
from .design import Design
from .questions import Questions
from .review import ReviewLedger
from .run import Run
from .stack import Stack
from .task import Task
from .value_objects.stream_id import StreamId


def aggregate_for(stream: StreamId) -> Aggregate:
    """ストリーム → イベントが無いときの集約。"""
    if stream == StreamId.run():
        return Run(stream)
    if stream == StreamId.design():
        return Design(stream)
    if stream == StreamId.stack():
        return Stack(stream)
    if stream == StreamId.questions():
        return Questions(stream)
    if stream.is_review:
        return ReviewLedger(stream)
    return Task(stream)
