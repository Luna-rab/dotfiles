"""段の並び（`hud/core/pipeline.py`）とタスクの窓切り（`hud/core/tasklist.py`）。"""

from __future__ import annotations

from hud.core import pipeline, tasklist
from hud.core.pipeline import Mark
from hud.core.runs import Stage


def stage(name: str, round_label: str, task: str = "task1") -> Stage:
    return Stage(name=name, task=task, round=round_label, seconds=10.0, turns=0, tool="")


def plain(steps: list[pipeline.Step]) -> list[tuple[str, str]]:
    return [(s.label, s.mark.value) for s in steps]


def test_済んだ段と走っている段とこれからの段を並べる():
    task = {"id": "task1", "stages": [{"name": "testgen", "round": "0", "ok": True}]}
    assert plain(pipeline.steps(task, [stage("impl", "0")])) == [
        ("testgen", "done"),
        ("impl", "current"),
        ("review", "next"),
        ("judge", "next"),
        ("PR", "next"),
    ]


def test_修正の巡目を添え古い段は省く():
    task = {
        "id": "task1",
        "stages": [
            {"name": "testgen", "round": "0", "ok": True},
            {"name": "impl", "round": "0", "ok": True},
            {"name": "review:normal", "round": "1", "ok": True},
            {"name": "judge", "round": "1", "ok": True},
            {"name": "fix", "round": "2", "ok": False},
        ],
    }
    got = pipeline.steps(task, [stage("review:normal", "2")])
    assert [s.label for s in got] == ["…", "judge", "fix r2", "review r2", "judge", "PR"]
    assert [s.mark for s in got][:4] == [Mark.ELIDED, Mark.DONE, Mark.FAILED, Mark.CURRENT]


def test_レビュー2体の片方だけ終わったら走っている方にまとめる():
    task = {"id": "task1", "stages": [{"name": "review:normal", "round": "1", "ok": True}]}
    got = pipeline.steps(task, [stage("review:adversarial", "1")])
    assert plain(got) == [("review", "current"), ("judge", "next"), ("PR", "next")]


def test_ほかのタスクで走っている段は含めない():
    task = {"id": "task1", "stages": [{"name": "testgen", "round": "0", "ok": True}]}
    got = pipeline.steps(task, [stage("impl", "0", task="task2")])
    assert plain(got)[:2] == [("testgen", "done"), ("impl", "next")]


def test_タスクが多いと今のタスクの前後に窓を切る():
    statuses = [
        "stacked",
        "stacked",
        "blocked",
        "running",
        "pending",
        "pending",
        "pending",
        "pending",
    ]
    tasks = [{"id": f"task{i}", "status": s} for i, s in enumerate(statuses, 1)]
    got = tasklist.visible(tasks)
    assert got[0] == tasklist.Summary("stacked", 2)
    assert [t["id"] for t in got[1:5] if isinstance(t, dict)] == [
        "task3",
        "task4",
        "task5",
        "task6",
    ]
    assert got[5] == tasklist.Summary("pending", 2)


def test_タスクが少なければ全部出す():
    tasks = [{"id": f"task{i}", "status": "pending"} for i in range(5)]
    assert tasklist.visible(tasks) == tasks
