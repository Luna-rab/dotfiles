"""`autodev status --json` の組み立てと、走っているステージの進み具合のファイル（DOMAIN_MODEL §7.4・§13 の `StatusQuery`）。

状態を外から読むのは `status --json` だけで、HUD と `/autodev` はこれを呼ぶ（ARCHITECTURE §10）。
`events` を読むだけの接続で読み、自分で再生する。driver のメインループには関わらない。

外向けの形は、どの集約にも依らない骨組み（ラン名・最後の seq・ストリームごとの版・進み具合・
拒んだコマンド）に、集約ごとの中身を `sections` で差し込む。差し込む関数は、再生した集約を読んで
JSON にするだけにする。外向けの形を集約の形から切り離すため、集約をそのまま JSON にしない。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from ..domain.aggregate import Aggregate
from ..domain.values import ExecutionId, StreamId
from .eventstore import AggregateFactory, EventReader, decoded, replay
from .files import write_atomic
from .paths import RunPaths
from .rejections import read_rejections

#: 再生した集約から、外向けの形の 1 つの欄を作る
Section = Callable[[Mapping[StreamId, Aggregate]], Any]

#: 骨組みの欄。差し込む欄の名前とぶつけない
_FRAME_KEYS = frozenset({"name", "last_seq", "streams", "progress", "rejections"})


def build_status(
    paths: RunPaths, factory: AggregateFactory, sections: Mapping[str, Section]
) -> dict[str, Any]:
    """ランが無ければ（`events.db` が無ければ）FileNotFoundError。"""
    if clash := sorted(_FRAME_KEYS & set(sections)):
        raise ValueError(f"骨組みの欄と同じ名前の欄は差し込めない: {clash}")
    with EventReader.open(paths.events_db) as reader:
        history = decoded(reader.read_all())
    aggregates = replay(history, factory)
    status: dict[str, Any] = {
        "name": paths.name.value,
        "last_seq": history[-1][0].seq if history else 0,
        "streams": {
            stream.value: agg.version
            for stream, agg in sorted(aggregates.items(), key=lambda item: item[0].value)
        },
        "progress": read_progress(paths),
        "rejections": read_rejections(paths.rejections),
    }
    for key, section in sections.items():
        status[key] = section(aggregates)
    return status


# --- 進み具合 ---
#
# 正本ではない使い捨てのファイル。ステージ 1 回で数百の stream-json のイベントが流れるので、
# イベントにすると events が膨らむ（ARCHITECTURE §8）。消えても状態は変わらない。


def write_progress(paths: RunPaths, execution: ExecutionId, progress: Mapping[str, Any]) -> None:
    """実行器のスレッドが数秒ごとに呼ぶ。一時ファイルから置き換えるので、読む側は書きかけを見ない。"""
    write_atomic(paths.progress_of(execution), json.dumps(progress, ensure_ascii=False))


def remove_progress(paths: RunPaths, execution: ExecutionId) -> None:
    paths.progress_of(execution).unlink(missing_ok=True)


def prune_progress(paths: RunPaths, keep: Iterable[ExecutionId]) -> None:
    """`keep` に無い実行の進み具合を消す。driver の起動時に、走っていない実行の分を片付ける。

    前の driver が落ちると、実行器が消す前のファイルが残り、`status --json` が終わった実行を
    走っているかのように出す。
    """
    if not paths.progress.is_dir():
        return
    names = {str(execution) for execution in keep}
    for path in paths.progress.glob("*.json"):
        if path.stem not in names:
            path.unlink(missing_ok=True)


def read_progress(paths: RunPaths) -> dict[str, Any]:
    """`ExecutionId` の文字列 → 中身。読めないファイルは飛ばす（使い捨てなので、無くても状態は変わらない）。"""
    found: dict[str, Any] = {}
    if not paths.progress.is_dir():
        return found
    for path in sorted(paths.progress.glob("*.json")):
        try:
            found[path.stem] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # 一覧を取った後に実行器が消した、など
            continue
    return found
