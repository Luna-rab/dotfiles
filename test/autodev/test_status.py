"""`status --json` の組み立てと進み具合のファイル（`infra/status.py`）・ランディレクトリの置き場（`infra/paths.py`）。"""

from __future__ import annotations

from pathlib import Path

import pytest
from autodev_fakes import Queue, enqueue, execution, factory, task
from autodevlib.app.mainloop import MainLoop
from autodevlib.domain.values import RunName, StreamId
from autodevlib.infra.eventstore import EventStore
from autodevlib.infra.paths import RunPaths, state_root
from autodevlib.infra.rejections import Rejection, RejectionLog, read_rejections
from autodevlib.infra.status import (
    build_status,
    prune_progress,
    read_progress,
    remove_progress,
    write_progress,
)


@pytest.fixture
def paths(tmp_path: Path) -> RunPaths:
    return RunPaths.of(RunName("demo"), {"AUTODEV_STATE_DIR": str(tmp_path)})


def waiting(aggregates) -> list[str]:
    queue = aggregates.get(StreamId.stack())
    return [t.value for t in queue.waiting] if isinstance(queue, Queue) else []


def test_再生した集約に差し込んだ欄と進み具合と拒んだ記録を混ぜて返す(paths: RunPaths):
    loop = MainLoop(
        EventStore.open(paths.events_db), factory, [], on_rejected=RejectionLog(paths.rejections)
    )
    loop.process(enqueue(1, "c1"))
    loop.process(enqueue(2, "c2"))
    loop.process(enqueue(1, "c3"))
    write_progress(paths, execution(1), {"turns": 3, "tool": "Bash"})
    # driver の書く接続を開いたまま読む
    status = build_status(paths, factory, {"stack": waiting})
    assert status["name"] == "demo"
    assert status["last_seq"] == 2
    assert status["streams"] == {"stack": 2}
    assert status["stack"] == ["task1", "task2"]
    assert status["progress"] == {"task1-Impl-r0-a1": {"turns": 3, "tool": "Bash"}}
    assert [(r["command_id"], r["reason"]) for r in status["rejections"]] == [
        ("c3", "すでに列にある")
    ]


def test_拒んだ記録はcommand_idと理由の組で重ねない(paths: RunPaths):
    def rejection(command_id: str, reason: str) -> Rejection:
        return Rejection("x", "EnqueueStack", command_id, {"kind": "policy"}, reason)

    log = RejectionLog(paths.rejections)
    log(rejection("c1", "すでに列にある"))
    log(rejection("c1", "すでに列にある"))
    # 落ちた後の配り直しで、開き直した driver がもう一度拒む
    again = RejectionLog(paths.rejections)
    again(rejection("c1", "すでに列にある"))
    again(rejection("c2", "別の理由"))
    assert [r["command_id"] for r in read_rejections(paths.rejections)] == ["c1", "c2"]


def test_同じidの判断が別の理由で拒まれたら行を足す(paths: RunPaths):
    # 統括の判断の id は知らせ 1 つに 1 つ。同じ知らせで差し戻されて出し直した判断は同じ id になる
    def rejection(reason: str) -> Rejection:
        return Rejection("x", "AnswerEscalation", "d1", {"kind": "run-supervisor"}, reason)

    log = RejectionLog(paths.rejections)
    log(rejection("1 回目の理由"))
    log(rejection("2 回目の理由"))
    RejectionLog(paths.rejections)(rejection("2 回目の理由"))
    assert [r["reason"] for r in read_rejections(paths.rejections)] == [
        "1 回目の理由",
        "2 回目の理由",
    ]


def test_走っていない実行の進み具合を片付ける(paths: RunPaths):
    prune_progress(paths, [])
    for number in (1, 2):
        write_progress(paths, execution(number), {"turns": number})
    prune_progress(paths, [execution(2)])
    assert list(read_progress(paths)) == ["task2-Impl-r0-a1"]


def test_ランが無ければ作らずに落ちる(paths: RunPaths):
    with pytest.raises(FileNotFoundError):
        build_status(paths, factory, {})
    assert not paths.root.exists()


def test_骨組みの欄と同じ名前は差し込めない(paths: RunPaths):
    with pytest.raises(ValueError, match="progress"):
        build_status(paths, factory, {"progress": waiting})


def test_進み具合は書き直せて消せて読めないものは飛ばす(paths: RunPaths):
    write_progress(paths, execution(1), {"turns": 1})
    write_progress(paths, execution(1), {"turns": 2})
    write_progress(paths, execution(2), {"turns": 5})
    (paths.progress / "broken.json").write_text("{", encoding="utf-8")
    assert read_progress(paths) == {
        "task1-Impl-r0-a1": {"turns": 2},
        "task2-Impl-r0-a1": {"turns": 5},
    }
    remove_progress(paths, execution(1))
    remove_progress(paths, execution(1))
    assert list(read_progress(paths)) == ["task2-Impl-r0-a1"]
    # 一時ファイルを残していない
    assert sorted(p.name for p in paths.progress.iterdir()) == [
        "broken.json",
        "task2-Impl-r0-a1.json",
    ]


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({"AUTODEV_STATE_DIR": "/srv/runs", "XDG_STATE_HOME": "/x"}, "/srv/runs"),
        ({"XDG_STATE_HOME": "/x/state"}, "/x/state/autodev"),
        # 相対パスの XDG_STATE_HOME は無効（XDG の仕様）
        ({"XDG_STATE_HOME": "rel"}, str(Path.home() / ".local/state/autodev")),
        ({}, str(Path.home() / ".local/state/autodev")),
    ],
)
def test_ランの置き場はXDG_STATE_HOMEを見て差し替えられる(env: dict[str, str], expected: str):
    assert state_root(env) == Path(expected)


def test_ランディレクトリの中のパス():
    paths = RunPaths.of(RunName("demo"), {"AUTODEV_STATE_DIR": "/s"})
    assert paths.events_db == Path("/s/demo/events.db")
    assert paths.progress_of(execution(3)) == Path("/s/demo/progress/task3-Impl-r0-a1.json")
    assert paths.answer("toolu_1") == Path("/s/demo/answers/toolu_1.json")
    assert paths.task_tree(task(2)) == Path("/s/demo/trees/task2")
    assert paths.task_results(task(2)) == Path("/s/demo/tasks/task2/results")
