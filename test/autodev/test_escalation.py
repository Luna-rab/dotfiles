"""停滞したときの手・再計画・回答待ち（`app/review_loop.py`・`app/replan.py`・`app/planning.py`）。

ステージは起動せず、`stage_call.call` を台本どおりに結果を返す偽物に差し替える。
ここが狂うと、直らない指摘のまま修正を回し続けるか、止まったタスクを続けられなくなる。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from autodev import main
from autodevlib.app import planning, replan, review_loop, stage_call
from autodevlib.app import task as task_mod
from autodevlib.app.context import Ctx, NeedsReplan, Waiting
from autodevlib.config import paths, stages
from autodevlib.core import task_order, verdict
from autodevlib.ports import files, proc, repo, review_store, runner

#: ステージ 1 回ぶんの台本。review.json を書き換えて、ステージの結果を返す。None ならエラーで終わる
Step = Callable[[Ctx, dict[str, Any]], dict[str, Any] | None]


class Script:
    """ステージの名前ごとに、呼ばれた順に台本を返す偽の `call`。台本が尽きたら空の結果を返す。"""

    def __init__(self, steps: dict[str, list[Step]]) -> None:
        self.steps = steps
        self.calls: list[tuple[str, str, str]] = []

    def __call__(self, ctx, stage, task, round_label, *, extra="", resume_from=None):
        self.calls.append((stage.name, round_label, extra))
        queue = self.steps.get(stage.name) or []
        result = queue.pop(0)(ctx, task) if queue else {}
        if result is None:
            return runner.Result(stage.name, 1, None, "", {}, "error", "", error="boom")
        return runner.Result(stage.name, 0, f"s-{stage.name}", "", {}, "success", "", result=result)

    def names(self) -> list[str]:
        return [f"{name}@{label}" for name, label, _ in self.calls]


@pytest.fixture
def ctx(tmp_path, monkeypatch) -> Ctx:
    monkeypatch.setenv("AUTODEV_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(repo, "lock_tests", lambda *a: None)
    monkeypatch.setattr(repo, "unlock_tests", lambda *a: None)
    shas = iter(f"sha{i}" for i in range(100))
    monkeypatch.setattr(repo, "head_sha", lambda tree: next(shas))
    run = paths.Run("demo")
    run.ensure()
    st: dict[str, Any] = {
        "name": "demo",
        "testGlobs": [],
        "tasks": [],
        "decisions": [],
        "deferrals": [],
    }
    task_order.add_tasks(st, "demo", [{"subject": "範囲指定", "tier": "light"}, {"subject": "CLI"}])
    st["tasks"][0].update(status="running", phase="review")
    return Ctx(run=run, st=st)


def use(monkeypatch, script: Script) -> Script:
    monkeypatch.setattr(stage_call, "call", script)
    return script


def stored(ctx: Ctx, task_id: str) -> dict[str, Any]:
    data = review_store.read(ctx.run.review(task_id))
    assert data is not None, task_id
    return data


def raise_finding(ctx: Ctx, task: dict[str, Any]) -> dict[str, Any]:
    review_store.add(
        ctx.run.review(task["id"]),
        reviewer="review:normal",
        rating="should-fix",
        location="a.py:3",
        body="境界で落ちる",
        round_label="1",
    )
    return {}


def set_all(status: str, escalation: dict[str, Any] | None = None) -> Step:
    """ジャッジの台本。未解決の指摘をすべて `status` にし、分類を返す。"""

    def step(ctx: Ctx, task: dict[str, Any]) -> dict[str, Any]:
        with review_store.opened(ctx.run.review(task["id"])) as data:
            for item in data["items"].values():
                if item["status"] == "open":
                    item["status"] = status
        return {"closed": 0, "rejected": 0, "escalation": escalation}

    return step


def keep_open(escalation: dict[str, Any] | None = None) -> Step:
    return set_all("open", escalation)


# --- 停滞したときの手 ----------------------------------------------------------


def test_テストが誤りならテスト作成に直させて修正を続ける(ctx, monkeypatch):
    script = use(
        monkeypatch,
        Script(
            {
                "review:normal": [raise_finding],
                "judge": [
                    keep_open({"cause": "tests", "items": ["r1"], "reason": "期待値が逆"}),
                    set_all("closed"),
                ],
            }
        ),
    )
    task = ctx.st["tasks"][0]
    review_loop.review_fix_loop(ctx, task)
    assert script.names() == [
        "review:normal@1",
        "judge@1",
        "testgen@1",
        "fix@1",
        "review:normal@2",
        "judge@2",
    ]
    assert "期待値が逆" in script.calls[2][2]
    assert task["rounds"] == 2


def test_堂々巡りなら実装を新しいセッションでやり直す(ctx, monkeypatch):
    script = use(
        monkeypatch,
        Script(
            {
                "review:normal": [raise_finding],
                "judge": [
                    keep_open({"cause": "approach", "items": ["r1"], "reason": "同じ直し方"}),
                    set_all("closed"),
                ],
            }
        ),
    )
    task = ctx.st["tasks"][0]
    task["implSession"] = "old"
    review_loop.review_fix_loop(ctx, task)
    assert "impl@1" in script.names()
    assert "fix@1" not in script.names()
    # 偽の call はセッションを書かないので、driver が捨てたままになっている
    assert task["implSession"] is None


def test_修正を2回受けても直らず分類も無ければ再計画に回す(ctx, monkeypatch):
    script = use(
        monkeypatch,
        Script(
            {"review:normal": [raise_finding], "judge": [keep_open(), keep_open(), keep_open()]}
        ),
    )
    task = ctx.st["tasks"][0]
    with pytest.raises(NeedsReplan) as raised:
        review_loop.review_fix_loop(ctx, task)
    assert raised.value.items == ["r1"]
    assert [n for n in script.names() if n.startswith("fix")] == ["fix@1", "fix@2"]
    # 3 ラウンド目のジャッジには、停滞している指摘を渡してある
    judge_extras = [extra for name, _, extra in script.calls if name == "judge"]
    assert judge_extras[0] == ""
    assert "停滞している指摘" in judge_extras[2] and "r1" in judge_extras[2]


def test_範囲の外が要るなら停滞を待たずに再計画に回す(ctx, monkeypatch):
    use(
        monkeypatch,
        Script(
            {
                "review:normal": [raise_finding],
                "judge": [
                    keep_open({"cause": "scope", "items": ["r1"], "reason": "parser を変える"})
                ],
            }
        ),
    )
    with pytest.raises(NeedsReplan) as raised:
        review_loop.review_fix_loop(ctx, ctx.st["tasks"][0])
    assert raised.value.reason == "parser を変える"


def test_受入条件が曖昧なら人に聞く(ctx, monkeypatch):
    escalation = {
        "cause": "ambiguous",
        "items": ["r1"],
        "reason": "空の扱い",
        "questions": ["空は None か 0 か"],
    }
    use(monkeypatch, Script({"review:normal": [raise_finding], "judge": [keep_open(escalation)]}))
    with pytest.raises(Waiting) as raised:
        review_loop.review_fix_loop(ctx, ctx.st["tasks"][0])
    assert raised.value.questions == [{"id": "task1-r1-q1", "question": "空は None か 0 か"}]


def test_ジャッジは同じタスクの間セッションを続ける():
    task = {"judgeSession": "s1"}
    assert stage_call.stage_session(task, stages.TABLE["judge"]) == ("s1", True)
    assert stage_call.stage_session(task, stages.TABLE["review:normal"]) == (None, False)


# --- 完了チェック --------------------------------------------------------------


def report(*failed: tuple[str, str]) -> verdict.Report:
    out = verdict.Report()
    out.add("stage-finished", True, "")
    for name, detail in failed:
        out.add(name, False, detail)
    return out


def test_完了チェックが落ちたら指摘にして修正のループへ戻る(ctx, monkeypatch):
    reports = iter([report(("verify", "落ちた: pytest")), report()])
    monkeypatch.setattr(task_mod, "gate", lambda c, t: next(reports))
    published: list[str] = []
    monkeypatch.setattr(task_mod, "publish", lambda c, t: published.append(t["id"]))
    monkeypatch.setattr(repo, "start_task_branch", lambda *a: proc.Run(0, "", ""))
    use(monkeypatch, Script({"judge": [set_all("closed")]}))
    task = ctx.st["tasks"][0]
    task.update(phase="gate", parent="stack/demo--task-0")

    task_mod.run_task(ctx, task)
    items = stored(ctx, "task1")["items"]
    assert [(i["location"], i["status"]) for i in items.values()] == [
        ("完了チェック verify", "closed")
    ]
    assert published == ["task1"]


def test_コードの直しで解けない完了チェックなら人に聞く(ctx, monkeypatch):
    failed = report(
        ("reviewer-count", "r1: review:normal が走っていない"), ("verify", verdict.VERIFY_SKIPPED)
    )
    monkeypatch.setattr(task_mod, "gate", lambda c, t: failed)
    monkeypatch.setattr(repo, "start_task_branch", lambda *a: proc.Run(0, "", ""))
    task = ctx.st["tasks"][0]
    task.update(phase="gate", parent="stack/demo--task-0")
    with pytest.raises(Waiting) as raised:
        task_mod.run_task(ctx, task)
    question = raised.value.questions[0]["question"]
    assert "reviewer-count" in question
    assert verdict.VERIFY_SKIPPED not in question


# --- エラーの呼び直し ----------------------------------------------------------


def test_エラーは1回だけ呼び直す(ctx, monkeypatch):
    script = use(
        monkeypatch, Script({"fix": [lambda c, t: None, lambda c, t: {"changeKind": "logic"}]})
    )
    got = stage_call.call_or_wait(ctx, stages.TABLE["fix"], ctx.st["tasks"][0], "1")
    assert got.ok
    assert script.names() == ["fix@1", "fix@1"]


def test_2回続けてエラーなら人に聞く(ctx, monkeypatch):
    use(monkeypatch, Script({"fix": [lambda c, t: None, lambda c, t: None]}))
    with pytest.raises(Waiting) as raised:
        stage_call.call_or_wait(ctx, stages.TABLE["fix"], ctx.st["tasks"][0], "1")
    assert raised.value.questions[0]["id"] == "task1-fix-error"


def test_同じラウンドで2度呼んだらログの名前をずらす(ctx):
    log = ctx.run.log("task1", "testgen", "1")
    files.write_text(log, "")
    assert stage_call.log_label(ctx.run, "task1", stages.TABLE["testgen"], "1") == "1-2"


# --- 再計画 ------------------------------------------------------------------


def test_再計画で指摘を後ろのタスクへ移す(ctx, monkeypatch):
    raise_finding(ctx, ctx.st["tasks"][0])
    plan = {
        "keepCurrent": True,
        "tasks": [
            {
                "subject": "CLI",
                "tier": "standard",
                "dod": "",
                "acceptance": "表示する",
                "carry": ["r1"],
            }
        ],
        "notes": "r1 は CLI の関心事",
        "blocked": False,
    }
    use(monkeypatch, Script({"replan": [lambda c, t: plan]}))
    replan.replan(ctx, NeedsReplan("task1", "範囲の外", ["r1"]))

    ids = [t["id"] for t in ctx.st["tasks"]]
    assert ids == ["task1", "task3"]
    assert ctx.st["replans"] == 1
    moved = stored(ctx, "task1")["items"]["r1"]
    assert (moved["status"], moved["movedTo"]) == ("moved", "task3")
    carried = review_store.items(stored(ctx, "task3"))
    assert [(i["status"], i["movedFrom"]) for i in carried] == [("open", "task1/r1")]
    assert "task1 から移した指摘" in ctx.st["tasks"][1]["acceptance"]
    assert "再計画した（1 回目）" in ctx.st["decisions"][-1]["body"]


def test_再計画が上限に達したら人に聞く(ctx, monkeypatch):
    script = use(monkeypatch, Script({}))
    ctx.st["replans"] = replan.MAX_REPLANS
    with pytest.raises(Waiting) as raised:
        replan.replan(ctx, NeedsReplan("task1", "範囲の外", ["r1"]))
    assert raised.value.questions[0]["id"] == "task1-replan-limit"
    assert script.calls == []


# --- 回答待ち ----------------------------------------------------------------


def test_回答が揃ったらタスクの人の判断に写して続ける(ctx):
    question = {"id": "task1-r1-q1", "question": "空は None か 0 か"}
    with pytest.raises(SystemExit) as exited:
        planning.ask_human(ctx, Waiting("task1", [question]))
    assert exited.value.code == 4
    assert task_order.outcome_of(ctx.st) == "waiting"

    files.write_json(ctx.run.answer("task1-r1-q1"), {"id": "task1-r1-q1", "answer": "None"})
    planning.take_answers(ctx)
    assert ctx.st["deferred"] is None
    assert ctx.st["tasks"][0]["notes"] == ["空は None か 0 か → None"]
    assert "人の判断（task1）" in ctx.st["decisions"][-1]["body"]


def test_回答が揃っていなければまだ待つ(ctx):
    files.write_json(ctx.run.question("task1-gate"), {"id": "task1-gate", "question": "?"})
    ctx.st["deferred"] = {"stage": "task", "task": "task1"}
    with pytest.raises(SystemExit) as exited:
        planning.take_answers(ctx)
    assert exited.value.code == 4


def test_続きから始めるときに指示を渡すとエラーにする(ctx, capsys):
    ctx.save()
    with pytest.raises(SystemExit):
        main(["run", "--name", "demo", "--instruction", "足したい指示"])
    assert "--instruction を付けない" in capsys.readouterr().err
