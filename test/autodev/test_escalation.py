"""停滞したときの手・再計画・回答待ち（`app/review_loop.py`・`app/replan.py`・`app/planning.py`）。

ステージは起動せず、`stage_call.call` を台本どおりに結果を返す偽物に差し替える。
ここが狂うと、直らない指摘のまま修正を回し続けるか、止まったタスクを続けられなくなる。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from autodev import main
from autodevlib.app import build, design, drive, planning, replan, review_loop, stage_call
from autodevlib.app import task as task_mod
from autodevlib.app.context import Ctx, NeedsReplan, Waiting
from autodevlib.config import paths, stages
from autodevlib.core import globs, task_order, verdict
from autodevlib.ports import evidence, files, proc, repo, review_store, runner

#: ステージ 1 回ぶんの台本。review.json を書き換えて、ステージの結果を返す。None ならエラーで終わる
Step = Callable[[Ctx, dict[str, Any]], dict[str, Any] | None]


class Script:
    """ステージの名前ごとに、呼ばれた順に台本を返す偽の `call`。台本が尽きたら空の結果を返す。"""

    def __init__(self, steps: dict[str, list[Step]]) -> None:
        self.steps = steps
        self.calls: list[tuple[str, str, str]] = []

    def __call__(
        self, ctx, stage, task, round_label, *, extra="", resume_from=None, continue_from=None
    ):
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


def replanned(tier: str = "light") -> dict[str, Any]:
    """再計画ステージの結果。止まった task1 を残し、r1 を新しいタスクへ移す。"""
    return {
        "keepCurrent": True,
        "tasks": [
            {
                "subject": "CLI",
                "tier": tier,
                "dod": "",
                "acceptance": "表示する",
                "carry": ["r1"],
            }
        ],
        "design": "## 公開インターフェース\n\n- cli(argv) -> int",
        "notes": "r1 は CLI の関心事",
        "blocked": False,
    }


def test_再計画で指摘を後ろのタスクへ移す(ctx, monkeypatch):
    raise_finding(ctx, ctx.st["tasks"][0])
    use(monkeypatch, Script({"replan": [lambda c, t: replanned()]}))
    replan.replan(ctx, NeedsReplan("task1", "範囲の外", ["r1"]))
    # 写すのは設計の確かめを通ってから（止まった task1 も新しいタスクも light なので飛ばす）
    assert [t["id"] for t in ctx.st["tasks"]] == ["task1", "task2"]
    drive.settle_proposal(ctx, {})

    ids = [t["id"] for t in ctx.st["tasks"]]
    assert ids == ["task1", "task3"]
    assert ctx.st["replans"] == 1
    moved = stored(ctx, "task1")["items"]["r1"]
    assert (moved["status"], moved["movedTo"]) == ("moved", "task3")
    carried = review_store.items(stored(ctx, "task3"))
    assert [(i["status"], i["movedFrom"]) for i in carried] == [("open", "task1/r1")]
    assert "task1 から移した指摘" in ctx.st["tasks"][1]["acceptance"]
    assert "再計画した（1 回目、設計 v1）" in ctx.st["decisions"][-1]["body"]
    assert "cli(argv)" in files.read_text(ctx.run.design)


def test_再計画で変えた検証コマンドを判断ログとブリーフに残す(ctx, monkeypatch, tmp_path):
    monkeypatch.setenv("AUTODEV_CONFIG_DIR", str(tmp_path / "config"))
    ctx.st.update(repo="/src/demo", base="main", overviewBranch="stack/demo", verify=["make all"])
    ctx.st["tasks"][0]["verify"] = ["bash -n b.sh"]
    result = {**replanned(), "verify": ["make test"], "current": {"verify": []}}
    use(monkeypatch, Script({"replan": [lambda c, t: result]}))
    replan.replan(ctx, NeedsReplan("task1", "後ろのタスクのファイルを確かめている", []))
    drive.settle_proposal(ctx, {})

    bodies = [d["body"] for d in ctx.st["decisions"]]
    assert "再計画で ラン共通 の検証コマンドを変えた → `make test`" in bodies
    assert "再計画で task1 の検証コマンドを変えた → なし" in bodies
    assert "make test" in files.read_text(ctx.run.brief)
    assert files.read_json(ctx.run.config) == {
        "verify": ["make test"],
        "testGlobs": list(globs.DEFAULT_TEST_GLOBS),
        "protected": [],
    }


def test_再計画は設計レビューが済むまで写さない(ctx, monkeypatch):
    raise_finding(ctx, ctx.st["tasks"][0])
    script = use(monkeypatch, Script({"replan": [lambda c, t: replanned(tier="standard")]}))
    replan.replan(ctx, NeedsReplan("task1", "範囲の外", ["r1"]))
    # 設計レビューが `review done` を呼ばずに終わったので、人に聞いて止まる
    with pytest.raises(Waiting):
        drive.settle_proposal(ctx, {})
    assert "design-review@1" in script.names()
    assert [t["id"] for t in ctx.st["tasks"]] == ["task1", "task2"]
    assert stored(ctx, "task1")["items"]["r1"]["status"] == "open"
    review_extra = next(extra for name, _, extra in script.calls if name == "design-review")
    assert "再計画の前提" in review_extra and "範囲の外" in review_extra


def test_再計画の直しは止まったタスクと理由を引き継いで提案し直す(ctx, monkeypatch):
    fixed = replanned(tier="standard")
    fixed["design"] = "## 公開インターフェース\n\n- cli(argv) -> str"
    script = use(
        monkeypatch,
        Script({"replan": [lambda c, t: replanned(tier="standard"), lambda c, t: fixed]}),
    )
    replan.replan(ctx, NeedsReplan("task1", "範囲の外", ["r1"]))
    replan.revise(ctx, "## 設計レビューの指摘を直す")
    proposal = ctx.st["design"]["proposal"]
    assert (proposal["version"], proposal["taskId"], proposal["reason"]) == (2, "task1", "範囲の外")
    assert "再計画の前提" in proposal["context"]
    assert script.calls[1][2] == "## 設計レビューの指摘を直す"
    # 止まった数は、直しでは増やさない
    assert ctx.st["replansSinceStack"] == 1


def test_スタックに追加しないまま再計画を続けたら人に聞く(ctx, monkeypatch):
    script = use(monkeypatch, Script({}))
    ctx.st["replansSinceStack"] = replan.REPLANS_WITHOUT_PROGRESS
    with pytest.raises(Waiting) as raised:
        replan.replan(ctx, NeedsReplan("task1", "範囲の外", ["r1"]))
    assert raised.value.questions[0]["id"] == "task1-replan-limit"
    assert script.calls == []


def test_ラン全体の再計画の回数では止めない(ctx, monkeypatch):
    use(monkeypatch, Script({"replan": [lambda c, t: replanned()]}))
    ctx.st["replans"] = 5
    ctx.st["replansSinceStack"] = 0
    replan.replan(ctx, NeedsReplan("task1", "範囲の外", ["r1"]))
    assert ctx.st["replansSinceStack"] == 1


def test_再計画の上限に回答したら数え直して再計画ステージに回答を渡す(ctx, monkeypatch):
    ctx.st["replansSinceStack"] = replan.REPLANS_WITHOUT_PROGRESS
    use(monkeypatch, Script({}))
    with pytest.raises(Waiting) as raised:
        replan.replan(ctx, NeedsReplan("task1", "範囲の外", ["r1"]))
    with pytest.raises(SystemExit):
        planning.ask_human(ctx, raised.value)
    files.write_json(
        ctx.run.answer("task1-replan-limit"), {"answer": "parser.py も範囲に入れてよい"}
    )
    planning.take_answers(ctx)
    assert ctx.st["replansSinceStack"] == 0
    assert "parser.py も範囲に入れてよい" in ctx.st["tasks"][0]["notes"][-1]

    script = use(monkeypatch, Script({"replan": [lambda c, t: replanned()]}))
    replan.replan(ctx, NeedsReplan("task1", "範囲の外", ["r1"]))
    assert script.names() == ["replan@0"]


def test_写す途中で落ちても同じ再計画を二度写さない(ctx, monkeypatch):
    """review.json への移管は先に書かれる。state.json を保存する前に落ちたら、呼び直しで同じ移管を通る。"""
    raise_finding(ctx, ctx.st["tasks"][0])
    use(monkeypatch, Script({"replan": [lambda c, t: replanned()]}))
    replan.replan(ctx, NeedsReplan("task1", "範囲の外", ["r1"]))
    before = ctx.st.copy()
    before["tasks"] = [dict(t) for t in ctx.st["tasks"]]
    replan.apply(ctx, design.pending(ctx.st) or {})
    # state.json を保存する前に落ちたことにして、写す前の state で呼び直す
    ctx.st.clear()
    ctx.st.update(before)
    replan.apply(ctx, design.pending(ctx.st) or {})
    assert [t["id"] for t in ctx.st["tasks"]] == ["task1", "task3"]
    carried = review_store.items(stored(ctx, "task3"))
    assert [i["movedFrom"] for i in carried] == ["task1/r1"]


def test_無い指摘を移すとした再計画はその指摘だけ飛ばす(ctx, monkeypatch):
    use(monkeypatch, Script({"replan": [lambda c, t: replanned()]}))
    replan.replan(ctx, NeedsReplan("task1", "範囲の外", ["r1"]))
    drive.settle_proposal(ctx, {})
    assert [t["id"] for t in ctx.st["tasks"]] == ["task1", "task3"]
    assert review_store.read(ctx.run.review("task3")) is None


# --- テスト作成 --------------------------------------------------------------


def verify_results(*outcomes: bool) -> Callable[..., tuple[verdict.VerifyResult, ...]]:
    """偽の `run_verify`。呼ばれるたびに、検証コマンド一式が通ったか（True）落ちたかを返す。"""
    queue = list(outcomes)

    def run_verify(tree: str, commands: list[str], timeout: int = 3600):
        ok = queue.pop(0)
        return (
            verdict.VerifyResult(command=commands[0], ok=ok, code=0 if ok else 1, out="", err=""),
        )

    return run_verify


@pytest.fixture
def fresh(ctx, monkeypatch) -> dict[str, Any]:
    """まだテストを書いていないタスク。検証コマンドがあり、変わったファイルは無い。"""
    ctx.st["verify"] = ["uv run pytest -q"]
    ctx.st["testGlobs"] = ["**/test_*.py"]
    monkeypatch.setattr(repo, "changed_files", lambda *a: [])
    task = ctx.st["tasks"][0]
    task.update(phase="tests", testsAt=None)
    return task


def test_設計に無い形が要るならテストを書かせず再計画に回す(ctx, fresh, monkeypatch):
    gap = {"testFiles": [], "failing": 0, "blocked": False, "designGap": "「無い」の返し方が無い"}
    use(monkeypatch, Script({"testgen": [lambda c, t: gap]}))
    with pytest.raises(NeedsReplan) as raised:
        build.make_tests(ctx, fresh, "0")
    assert (raised.value.kind, raised.value.reason) == ("design-gap", "「無い」の返し方が無い")
    assert fresh["testsAt"] is None


def test_設計の抜けが文字列のnullなら報告として扱わない(ctx, fresh, monkeypatch):
    monkeypatch.setattr(evidence, "run_verify", verify_results(False))
    use(monkeypatch, Script({"testgen": [lambda c, t: {"designGap": "null"}]}))
    build.make_tests(ctx, fresh, "0")
    assert fresh["testsAt"]


def test_実装の前に検証コマンドが落ちればそのまま進む(ctx, fresh, monkeypatch):
    monkeypatch.setattr(evidence, "run_verify", verify_results(False))
    script = use(monkeypatch, Script({}))
    build.make_tests(ctx, fresh, "0")
    assert script.names() == ["testgen@0"]


def test_実装の前の確かめでは後ろのタスクが足した検証コマンドを流さない(ctx, fresh, monkeypatch):
    fresh["verify"] = ["php -l routes/web.php"]
    ctx.st["tasks"][1]["verify"] = ["bash -n install.sh"]
    ran: list[list[str]] = []

    def run_verify(tree: str, commands: list[str], timeout: int = 3600):
        ran.append(commands)
        return (verdict.VerifyResult(command=commands[0], ok=False, code=1, out="", err=""),)

    monkeypatch.setattr(evidence, "run_verify", run_verify)
    use(monkeypatch, Script({}))
    build.make_tests(ctx, fresh, "0")
    assert ran == [["uv run pytest -q", "php -l routes/web.php"]]


def test_実装の前なのに通ったら1回だけ書き直させる(ctx, fresh, monkeypatch):
    monkeypatch.setattr(evidence, "run_verify", verify_results(True, False))
    script = use(monkeypatch, Script({}))
    build.make_tests(ctx, fresh, "0")
    assert script.names() == ["testgen@0", "testgen@0"]
    assert "実装の前なのにテストが通った" in script.calls[1][2]


def test_書き直しても通るなら人に聞く(ctx, fresh, monkeypatch):
    monkeypatch.setattr(evidence, "run_verify", verify_results(True, True))
    use(monkeypatch, Script({}))
    with pytest.raises(Waiting) as raised:
        build.make_tests(ctx, fresh, "0")
    assert raised.value.questions[0]["id"] == "task1-tests-green"


def test_実装の前から通る件に回答したら回答に沿って1回だけ書き直させて進む(ctx, fresh, monkeypatch):
    monkeypatch.setattr(evidence, "run_verify", verify_results(True, True))
    use(monkeypatch, Script({}))
    with pytest.raises(Waiting):
        build.make_tests(ctx, fresh, "0")
    # 呼び直し。確かめ直すと「このまま進めてよい」という回答でも同じ質問に戻る（台本が尽きて落ちる）
    script = use(monkeypatch, Script({}))
    build.make_tests(ctx, fresh, "0")
    assert script.names() == ["testgen@0"]
    assert "実装の前からテストが通る件の回答" in script.calls[0][2]
    assert "redCheck" not in fresh


def test_確かめの途中で落ちたらテストを書き直さずに確かめだけやり直す(ctx, fresh, monkeypatch):
    fresh.update(testsAt="sha-tests", redCheck="running")
    monkeypatch.setattr(evidence, "run_verify", verify_results(False))
    script = use(monkeypatch, Script({}))
    build.make_tests(ctx, fresh, "0")
    assert script.calls == []
    assert "redCheck" not in fresh


def test_報告を受けた設計の直しはタスクがlightでも設計レビューを通す(ctx, monkeypatch):
    use(monkeypatch, Script({"replan": [lambda c, t: replanned()]}))
    replan.replan(ctx, NeedsReplan("task1", "「無い」の形が無い", [], kind="design-gap"))
    assert ctx.st["design"]["proposal"]["skipReview"] is False


def test_2回目からのテスト作成では落ちることを確かめない(ctx, fresh, monkeypatch):
    """実装がすでにあるので、直したテストは通ってよい。"""
    fresh["testsAt"] = "sha-tests"
    monkeypatch.setattr(evidence, "run_verify", verify_results())
    use(monkeypatch, Script({}))
    build.make_tests(ctx, fresh, "1")


def test_テスト以外に触ったファイルを控えて通常レビューにだけ渡す(ctx, fresh, monkeypatch):
    monkeypatch.setattr(evidence, "run_verify", verify_results(False))
    monkeypatch.setattr(repo, "changed_files", lambda *a: ["src/cache.py", "tests/test_cache.py"])
    use(monkeypatch, Script({}))
    build.make_tests(ctx, fresh, "0")
    assert fresh["stubFiles"] == ["src/cache.py"]

    fresh["tier"] = "standard"
    script = use(monkeypatch, Script({"judge": [set_all("closed")]}))
    review_loop.review_fix_loop(ctx, fresh)
    extras = {name: extra for name, _, extra in script.calls}
    assert "`src/cache.py`" in extras["review:normal"]
    assert extras["review:adversarial"] == ""


# --- 設計の形の変更 ----------------------------------------------------------


def test_修正ステージが設計の形を変えたいと報告したら報告の時点で再計画に回す(ctx, monkeypatch):
    change = {"changeKind": "logic", "interfaceChange": "get は Miss を返す必要がある"}
    use(
        monkeypatch,
        Script(
            {
                "review:normal": [raise_finding],
                "judge": [keep_open()],
                "fix": [lambda c, t: change],
            }
        ),
    )
    with pytest.raises(NeedsReplan) as raised:
        review_loop.review_fix_loop(ctx, ctx.st["tasks"][0])
    assert (raised.value.kind, raised.value.reason) == (
        "interface-change",
        "get は Miss を返す必要がある",
    )


def test_設計が変わったらテスト作成からやり直し説明をテストと実装に渡す(ctx, monkeypatch):
    task = ctx.st["tasks"][0]
    task.update(testsAt="sha-tests", phase="review", parent="stack/demo--task-0")
    raise_finding(ctx, task)

    def design_review_done(c: Ctx, t: Any) -> dict[str, Any]:
        review_store.done(c.run.review("design"), "design-review", "1", 0)
        return {}

    # 報告を受けた設計の直しなので、light のタスクでも設計レビューを通る
    script = use(
        monkeypatch,
        Script({"replan": [lambda c, t: replanned()], "design-review": [design_review_done]}),
    )
    replan.replan(ctx, NeedsReplan("task1", "get は Miss を返す", [], kind="interface-change"))
    drive.settle_proposal(ctx, {})
    assert "design-review@1" in script.names()
    assert task["phase"] == "tests"
    assert "設計が変わった" in task["resumeNote"]

    monkeypatch.setattr(repo, "start_task_branch", lambda *a: proc.Run(0, "", ""))
    monkeypatch.setattr(repo, "changed_files", lambda *a: [])
    monkeypatch.setattr(task_mod, "review_fix_loop", lambda c, t: None)
    monkeypatch.setattr(task_mod, "gate", lambda c, t: report())
    monkeypatch.setattr(task_mod, "publish", lambda c, t: None)
    # テストはすでにあるので、落ちることは確かめない（呼ばれると台本が尽きて落ちる）
    ctx.st["verify"] = ["uv run pytest -q"]
    monkeypatch.setattr(evidence, "run_verify", verify_results())
    script = use(monkeypatch, Script({}))
    task_mod.run_task(ctx, task)
    extras = {name: extra for name, _, extra in script.calls}
    assert "get は Miss を返す" in extras["testgen"]
    assert "get は Miss を返す" in extras["impl"]
    assert "resumeNote" not in task


def test_範囲の直しではテストからやり直さない(ctx, monkeypatch):
    task = ctx.st["tasks"][0]
    raise_finding(ctx, task)
    use(monkeypatch, Script({"replan": [lambda c, t: replanned()]}))
    replan.replan(ctx, NeedsReplan("task1", "範囲の外", ["r1"]))
    drive.settle_proposal(ctx, {})
    assert task["phase"] == "review"
    assert "resumeNote" not in task


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
