"""次に回す 1 本・積む先・run の状態・タスクの組み立て（`core/task_order.py`）。

ここが狂うと、保留を置いたまま後続を回す（壊れた土台の上に積む）か、積む先を間違えて
PR の連鎖が崩れる。
"""

from __future__ import annotations

from typing import Any

from autodevlib.core import task_order


def state(*statuses: str, **over: Any) -> dict[str, Any]:
    """`tasks` の状態だけを並べた state。番号とブランチ名は規約どおりに振る。"""
    data: dict[str, Any] = {
        "stackBranch": "stack/demo",
        "tasks": [
            {
                "id": f"task{i}",
                "status": s,
                "branch": f"stack/demo--task-{i}",
                "tier": "standard",
                "subject": f"件名 {i}",
            }
            for i, s in enumerate(statuses, 1)
        ],
    }
    data.update(over)
    return data


# --- 次に回す 1 本 -----------------------------------------------------------


def test_番号の小さいものから1本ずつ回す():
    got = task_order.next_pending(state("pending", "pending"))
    assert got is not None
    assert got["id"] == "task1"


def test_積み終わったタスクは飛ばす():
    got = task_order.next_pending(state("stacked", "stacked", "pending"))
    assert got is not None
    assert got["id"] == "task3"


def test_進行中のタスクをそのまま返す():
    """途中で落ちた run を再開すると、`running` の 1 本から続ける。"""
    got = task_order.next_pending(state("stacked", "running"))
    assert got is not None
    assert got["id"] == "task2"


def test_保留があれば後続を回さない():
    """保留の上に積むと、壊れた土台の上に PR が連なる。止めて人間に渡す。"""
    assert task_order.next_pending(state("blocked", "pending")) is None


def test_失敗があれば後続を回さない():
    assert task_order.next_pending(state("stacked", "failed", "pending")) is None


def test_全部積み終わっていればNoneを返す():
    assert task_order.next_pending(state("stacked", "stacked")) is None


def test_タスクが無ければNoneを返す():
    assert task_order.next_pending(state()) is None


# --- 積む先 ------------------------------------------------------------------


def test_1本目は土台に積む():
    data = state("pending", "pending")
    assert task_order.parent_of(data, data["tasks"][0]) == "stack/demo"


def test_2本目は直前の積み終わったブランチに積む():
    data = state("stacked", "pending")
    assert task_order.parent_of(data, data["tasks"][1]) == "stack/demo--task-1"


def test_積み終わっていない前のタスクは起点にしない():
    """順に 1 本ずつ回すので起点は動かない。積み替えが要らない。"""
    data = state("stacked", "failed", "pending")
    assert task_order.parent_of(data, data["tasks"][2]) == "stack/demo--task-1"


# --- run の状態 --------------------------------------------------------------


def test_タスクを割る前はplanning():
    assert task_order.outcome_of(state()) == "planning"


def test_回している間はrunning():
    assert task_order.outcome_of(state("stacked", "pending")) == "running"


def test_答えを待っている間はwaiting():
    """`deferred` はタスクの状態より先に見る。計画段が聞いて止まった run である。

    park するのは計画段なので、そのとき `tasks` は空である。`tasks` を先に見ると
    `planning` が返り、呼んだ側が答えを待たずに計画段をもう一度起動する。
    """
    assert task_order.outcome_of(state("pending", deferred=["q1"])) == "waiting"
    waiting = {"tasks": [], "deferred": ["q1"], "stackBranch": "stack/demo"}
    assert task_order.outcome_of(waiting) == "waiting"


def test_保留か失敗があればheld():
    assert task_order.outcome_of(state("stacked", "blocked")) == "held"
    assert task_order.outcome_of(state("failed")) == "held"


def test_全部積み終わればstacked():
    assert task_order.outcome_of(state("stacked", "stacked")) == "stacked"


# --- タスクの組み立て --------------------------------------------------------


def test_計画段の割り方を番号付きで入れる():
    data: dict[str, Any] = {"tasks": []}
    task_order.add_tasks(
        data,
        "demo",
        [
            {"subject": "土台を作る", "tier": "light", "dod": "通る", "acceptance": "A"},
            {"subject": "本体を書く", "tier": "standard", "blockedBy": ["task1"]},
        ],
    )
    assert [t["id"] for t in data["tasks"]] == ["task1", "task2"]
    assert [t["branch"] for t in data["tasks"]] == [
        "stack/demo--task-1",
        "stack/demo--task-2",
    ]
    assert data["tasks"][0]["tier"] == "light"
    assert data["tasks"][0]["status"] == "pending"
    assert data["tasks"][1]["blockedBy"] == ["task1"]


def test_知らないtierはstandardに倒す():
    """迷ったら standard。`light` に倒すと敵対的レビューの網が外れる。"""
    data: dict[str, Any] = {"tasks": []}
    task_order.add_tasks(data, "demo", [{"tier": "trivial"}, {}, {"tier": None}])
    assert [t["tier"] for t in data["tasks"]] == ["standard", "standard", "standard"]


def test_検査5の起点とレビューの記録を空で置く():
    """`testsAt` が空のまま積むと検査⑤が落ちる。作る時点で鍵を用意しておく。"""
    data: dict[str, Any] = {"tasks": []}
    task_order.add_tasks(data, "demo", [{}])
    task = data["tasks"][0]
    assert task["testsAt"] is None
    assert task["rounds"] == 0
    assert task["adversarialRan"] is False
    assert task["reviewersSeen"] == []


# --- 数 ----------------------------------------------------------------------


def test_状態ごとの数を全状態ぶん返す():
    counted = task_order.counts(state("stacked", "stacked", "pending", "blocked"))
    assert counted == {
        "pending": 1,
        "running": 0,
        "stacked": 2,
        "blocked": 1,
        "failed": 0,
    }


def test_タスクが無ければ全部0():
    assert task_order.counts(state()) == dict.fromkeys(task_order.STATUSES, 0)
