"""設計の確かめ（`app/design.py`）と、計画を提案として持ってから写す流れ（`app/drive.py`・`app/planning.py`）。

ステージは起動せず、`stage_call.call` を台本どおりに結果を返す偽物に差し替える。
ここが狂うと、確かめていない割り方でタスクが動き出すか、設計の直しが止まらずに回り続ける。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from autodevlib.app import design, drive, planning, stage_call
from autodevlib.app.context import Ctx, Waiting
from autodevlib.config import paths, stages
from autodevlib.core import prompt, review_policy
from autodevlib.ports import files, review_store, runner

#: ステージ 1 回ぶんの台本。review.json を書き換えて、ステージの結果を返す
Step = Callable[[Ctx, str], dict[str, Any] | None]


class Script:
    """ステージの名前ごとに、呼ばれた順に台本を返す偽の `call`。台本が尽きたら空の結果を返す。"""

    def __init__(self, steps: dict[str, list[Step]]) -> None:
        self.steps = steps
        self.calls: list[tuple[str, str, str, str | None]] = []

    def __call__(
        self, ctx, stage, task, round_label, *, extra="", resume_from=None, continue_from=None
    ):
        self.calls.append((stage.name, round_label, extra, continue_from))
        queue = self.steps.get(stage.name) or []
        result = queue.pop(0)(ctx, round_label) if queue else {}
        if result is None:
            return runner.Result(stage.name, 1, None, "", {}, "error", "", error="boom")
        return runner.Result(stage.name, 0, f"s-{stage.name}", "", {}, "success", "", result=result)

    def names(self) -> list[str]:
        return [f"{name}@{label}" for name, label, _, _ in self.calls]


@pytest.fixture
def ctx(tmp_path, monkeypatch) -> Ctx:
    monkeypatch.setenv("AUTODEV_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("AUTODEV_CONFIG_DIR", str(tmp_path / "config"))
    run = paths.Run("demo")
    run.ensure()
    st: dict[str, Any] = {
        "name": "demo",
        "repo": "/src/demo",
        "base": "main",
        "overviewBranch": "stack/demo--task-0",
        "testGlobs": [],
        "verify": [],
        "tasks": [],
        "decisions": [],
        "deferrals": [],
    }
    return Ctx(run=run, st=st)


def use(monkeypatch, script: Script) -> Script:
    monkeypatch.setattr(stage_call, "call", script)
    monkeypatch.setattr(planning, "call", script)
    return script


def plan_result(
    tier: str = "standard", design_text: str = "## 公開インターフェース\n\n- f(x)"
) -> dict:
    return {
        "fitsOnePr": True,
        "tasks": [{"subject": "範囲指定", "tier": tier, "dod": "", "acceptance": "1-3 を渡す"}],
        "design": design_text,
        "verify": ["uv run pytest -q"],
        "decisions": ["1 タスクで済ませた"],
        "blocked": False,
    }


def proposal(ctx: Ctx) -> dict[str, Any]:
    got = design.pending(ctx.st)
    assert got is not None
    return got


def review_path(ctx: Ctx) -> str:
    return ctx.run.review(stage_call.DESIGN_ID)


def finding(body: str = "失敗の返し方が無い", rating: str = "must-fix") -> Step:
    """設計レビューの台本。指摘を 1 件立てて、走り終えたことを残す。"""

    def step(ctx: Ctx, label: str) -> dict[str, Any]:
        review_store.add(
            review_path(ctx),
            reviewer="design-review",
            rating=rating,
            location="設計 公開インターフェース",
            body=body,
            round_label=label,
        )
        review_store.done(review_path(ctx), "design-review", label, 1)
        return {}

    return step


def clean(ctx: Ctx, label: str) -> dict[str, Any]:
    review_store.done(review_path(ctx), "design-review", label, 0)
    return {}


def judge(status: str, escalation: dict[str, Any] | None = None) -> Step:
    """設計のジャッジの台本。未解決の指摘をすべて `status` にし、分類を返す。"""

    def step(ctx: Ctx, label: str) -> dict[str, Any]:
        with review_store.opened(review_path(ctx)) as data:
            for item in data["items"].values():
                if item["status"] == "open":
                    item["status"] = status
        return {"closed": 0, "rejected": 0, "escalation": escalation}

    return step


class Revisions:
    """偽の `revise`。呼ばれるたびに設計の新しい版を提案する。"""

    def __init__(self) -> None:
        self.extras: list[str] = []

    def __call__(self, ctx: Ctx, extra: str) -> None:
        self.extras.append(extra)
        design.propose(ctx, "plan", "s-plan", plan_result(design_text=f"v{len(self.extras) + 1}"))


# --- 提案と版 --------------------------------------------------------------------


def test_提案すると設計ファイルの版を消さずに書き出す(ctx):
    assert design.propose(ctx, "plan", "s1", plan_result(design_text="一つ目")) == 1
    assert design.propose(ctx, "plan", "s1", plan_result(design_text="二つ目")) == 2
    assert files.read_text(ctx.run.design).strip() == "二つ目"
    assert files.read_text(ctx.run.design_version(1)).strip() == "一つ目"
    assert proposal(ctx)["version"] == 2


def test_設計が空なら空であることを書き出す(ctx):
    design.propose(ctx, "plan", "s1", plan_result(design_text=""))
    assert "設計を書かなかった" in files.read_text(ctx.run.design)


def test_直した提案の設計が空なら前の版の設計を残す(ctx):
    design.propose(ctx, "plan", "s1", plan_result(design_text="f(x) -> int"))
    design.propose(ctx, "plan", "s1", plan_result(design_text=""))
    assert files.read_text(ctx.run.design).strip() == "f(x) -> int"
    assert files.read_text(ctx.run.design_version(2)).strip() == "f(x) -> int"


def test_設計レビューを飛ばすかは直す前の提案で決める(ctx):
    """直した版がたまたま light だけになっても、未解決の指摘を残したまま写さない。"""
    design.propose(ctx, "plan", "s1", plan_result(tier="standard"))
    design.propose(ctx, "plan", "s1", plan_result(tier="light"))
    assert proposal(ctx)["skipReview"] is False
    design.close(ctx)
    design.propose(ctx, "plan", "s1", plan_result(tier="light"))
    assert proposal(ctx)["skipReview"] is True


# --- 設計レビューのループ ----------------------------------------------------------


def test_タスクがすべてlightなら設計レビューを飛ばす(ctx, monkeypatch):
    script = use(monkeypatch, Script({}))
    design.propose(ctx, "plan", "s1", plan_result(tier="light"))
    design.settle(ctx, Revisions())
    assert script.calls == []
    assert design.state(ctx.st)["step"] == "settled"
    assert "設計レビューを飛ばした" in ctx.st["decisions"][-1]["body"]


def test_指摘が残れば設計を書いたステージが直してレビューし直す(ctx, monkeypatch):
    script = use(
        monkeypatch,
        Script(
            {
                "design-review": [finding(), clean],
                "design-judge": [judge("open"), judge("closed")],
            }
        ),
    )
    revisions = Revisions()
    design.propose(ctx, "plan", "s1", plan_result())
    design.settle(ctx, revisions)
    assert script.names() == [
        "design-review@1",
        "design-judge@1",
        "design-review@2",
        "design-judge@2",
    ]
    assert len(revisions.extras) == 1
    assert "失敗の返し方が無い" in revisions.extras[0]
    assert "--commenter plan" in revisions.extras[0]
    assert design.state(ctx.st)["step"] == "settled"


def test_mustfixが0件ならshouldfixを申し送って通す(ctx, monkeypatch):
    script = use(
        monkeypatch,
        Script(
            {
                "design-review": [finding("空の範囲の扱いが無い", rating="should-fix")],
                "design-judge": [judge("open")],
            }
        ),
    )
    revisions = Revisions()
    design.propose(ctx, "plan", "s1", plan_result(design_text="f(x) -> int"))
    design.settle(ctx, revisions)
    assert script.names() == ["design-review@1", "design-judge@1"]
    assert revisions.extras == []
    assert design.state(ctx.st)["step"] == "settled"
    item = review_store.read(review_path(ctx))["items"]["r1"]
    assert item["status"] == "rejected"
    assert item["handedOff"] is True
    # 設計のジャッジが判じた版のファイルは書き換えない
    written = files.read_text(ctx.run.design)
    assert written.startswith("f(x) -> int")
    assert "## 設計で残った指摘" in written
    assert "空の範囲の扱いが無い" in written
    assert "設計で残った指摘" not in files.read_text(ctx.run.design_version(1))
    assert "申し送った" in ctx.st["decisions"][-1]["body"]


def keep_newest_open(ctx: Ctx, label: str) -> dict[str, Any]:
    """設計のジャッジの台本。前のラウンドの指摘を却下し、このラウンドの指摘だけ未解決に残す。"""
    with review_store.opened(review_path(ctx)) as data:
        for item in data["items"].values():
            if item["status"] == "open" and item["round"] != label:
                item["status"] = "rejected"
                item["comments"].append({"by": "judge", "at": "", "body": "内部の欄なので却下"})
    return {"closed": 0, "rejected": 0, "escalation": None}


def test_毎ラウンド新しいmustfixが立ち続けたら上限で人に聞く(ctx, monkeypatch):
    """同じ指摘の停滞には掛からない、一段細かい所へ掘り進む往復を止める。"""
    rounds = review_policy.DESIGN_ROUNDS
    script = use(
        monkeypatch,
        Script(
            {
                "design-review": [finding(f"欄 {n} が無い") for n in range(1, rounds + 1)],
                "design-judge": [keep_newest_open] * rounds,
            }
        ),
    )
    revisions = Revisions()
    design.propose(ctx, "plan", "s1", plan_result())
    with pytest.raises(Waiting) as raised:
        design.settle(ctx, revisions)
    assert len(revisions.extras) == rounds - 1
    question = raised.value.questions[0]
    assert question["id"] == f"design-r{rounds}-rounds"
    assert f"欄 {rounds} が無い" in question["question"]
    assert "欄 1 が無い" not in question["question"]
    # 回答したら直しから続け、もう一度上限まで回せる
    d = design.state(ctx.st)
    assert (d["step"], d["proposalRounds"]) == ("fix", 0)
    # 最初の却下は r2 のジャッジなので、却下済みの一覧が載るのは r3 の設計レビューから
    third_review = script.calls[4]
    assert third_review[:2] == ("design-review", "3")
    assert "却下済みの指摘" in third_review[2]
    assert "欄 1 が無い" in third_review[2]
    assert "内部の欄なので却下" in third_review[2]


def test_提案を写したらラウンドの数を戻す(ctx):
    design.propose(ctx, "plan", "s1", plan_result())
    design.state(ctx.st)["proposalRounds"] = 3
    design.close(ctx)
    assert design.state(ctx.st)["proposalRounds"] == 0


def test_設計レビューには前の版を渡さず設計のジャッジにだけ渡す(ctx, monkeypatch):
    script = use(monkeypatch, Script({"design-review": [clean], "design-judge": [judge("closed")]}))
    design.propose(ctx, "plan", "s1", plan_result())
    design.settle(ctx, Revisions())
    review_extra, judge_extra = script.calls[0][2], script.calls[1][2]
    assert "<設計の履歴>" not in review_extra
    assert "範囲指定" in review_extra
    assert "<設計の履歴>" in judge_extra


def test_前の版に戻ったら人に聞く(ctx, monkeypatch):
    escalation = {
        "cause": "reverted",
        "items": ["r1"],
        "reason": "None に戻した",
        "revertedTo": 1,
        "questions": [],
    }
    use(
        monkeypatch,
        Script({"design-review": [finding()], "design-judge": [judge("open", escalation)]}),
    )
    design.propose(ctx, "plan", "s1", plan_result())
    with pytest.raises(Waiting) as raised:
        design.settle(ctx, Revisions())
    assert raised.value.task_id == "design"
    assert "v1" in raised.value.questions[0]["question"]
    # 回答が置かれたら、人が決めたことを渡して直しから続ける
    assert design.state(ctx.st)["step"] == "fix"


def test_直しを2回受けても直らず分類も無ければ人に聞く(ctx, monkeypatch):
    script = use(
        monkeypatch,
        Script(
            {
                "design-review": [finding(), clean, clean],
                "design-judge": [judge("open"), judge("open"), judge("open")],
            }
        ),
    )
    revisions = Revisions()
    design.propose(ctx, "plan", "s1", plan_result())
    with pytest.raises(Waiting) as raised:
        design.settle(ctx, revisions)
    assert len(revisions.extras) == 2
    assert "r1" in raised.value.questions[0]["question"]
    assert "停滞している指摘" in script.calls[-1][2]


def test_設計レビューがdoneを呼ばずに終わったら人に聞く(ctx, monkeypatch):
    script = use(monkeypatch, Script({"design-review": [lambda c, label: {}]}))
    design.propose(ctx, "plan", "s1", plan_result())
    with pytest.raises(Waiting) as raised:
        design.settle(ctx, Revisions())
    assert raised.value.questions[0]["id"] == "design-r1-review"
    assert script.names() == ["design-review@1"]


def test_設計のジャッジが2回続けて落ちたらセッションを捨てて人に聞く(ctx, monkeypatch):
    use(
        monkeypatch,
        Script({"design-review": [clean], "design-judge": [lambda c, label: None] * 2}),
    )
    design.state(ctx.st)["judgeSession"] = "s-broken"
    design.propose(ctx, "plan", "s1", plan_result())
    with pytest.raises(Waiting) as raised:
        design.settle(ctx, Revisions())
    assert raised.value.questions[0]["id"] == "design-design-judge-error"
    assert design.state(ctx.st)["judgeSession"] is None


def test_設計の直しの途中で計画ステージがblockedを返したら人に聞いて続ける(ctx, monkeypatch):
    blocked = {**plan_result(), "blocked": True, "questions": ["None か Miss か"]}
    use(monkeypatch, Script({"plan": [lambda c, label: blocked]}))
    design.propose(ctx, "plan", "s1", plan_result())
    with pytest.raises(Waiting) as raised:
        planning.revise(ctx, "## 設計レビューの指摘を直す")
    assert raised.value.task_id == "design"
    assert raised.value.questions == [{"id": "design-plan-q1", "question": "None か Miss か"}]


def test_設計のジャッジはランの間セッションを続ける(ctx):
    judge_stage = stages.TABLE["design-judge"]
    holder = stage_call.session_holder(ctx, judge_stage, None)
    assert holder is ctx.st["design"]
    holder["judgeSession"] = "s-judge"
    assert stage_call.stage_session(holder, judge_stage) == ("s-judge", True)
    assert stage_call.owner_id(judge_stage, None) == "design"
    assert stage_call.owner_id(stages.TABLE["plan"], None) == "task0"


# --- 計画を写す ------------------------------------------------------------------


def test_計画は設計の指摘が0件になってから1回だけ写す(ctx, monkeypatch):
    use(
        monkeypatch,
        Script(
            {
                # 2 回目は、設計の指摘を受けた直し
                "plan": [lambda c, label: plan_result(), lambda c, label: plan_result()],
                "design-review": [finding(), clean],
                "design-judge": [judge("open"), judge("closed")],
            }
        ),
    )
    config = {"verify": [], "testGlobs": ["**/test_*.py"], "protected": []}
    planning.plan(ctx, config)
    assert ctx.st["tasks"] == []
    drive.settle_proposal(ctx, config)
    assert [t["subject"] for t in ctx.st["tasks"]] == ["範囲指定"]
    assert ctx.st["verify"] == ["uv run pytest -q"]
    assert design.pending(ctx.st) is None
    # 直した計画の判断ログも、写すのは 1 回だけ
    assert [d["body"] for d in ctx.st["decisions"]].count("1 タスクで済ませた") == 1


def test_直しは計画ステージのセッションの続きで呼ぶ(ctx, monkeypatch):
    script = use(monkeypatch, Script({"plan": [lambda c, label: plan_result(design_text="v2")]}))
    design.propose(ctx, "plan", "s-first", plan_result())
    planning.revise(ctx, "## 設計レビューの指摘を直す")
    assert script.calls[0][3] == "s-first"
    assert proposal(ctx)["version"] == 2


def test_設計で聞いた回答は設計のステージに渡す(ctx):
    question = {"id": "design-r1-q1", "question": "None か Miss か"}
    with pytest.raises(SystemExit):
        planning.ask_human(ctx, Waiting("design", [question]))
    files.write_json(ctx.run.answer("design-r1-q1"), {"id": "design-r1-q1", "answer": "Miss"})
    planning.take_answers(ctx)
    assert design.state(ctx.st)["notes"] == ["None か Miss か → Miss"]
    assert "人の判断（design）" in ctx.st["decisions"][-1]["body"]


# --- ステージへ渡すもの ----------------------------------------------------------


def test_敵対的レビューには設計を渡さない(ctx):
    files.write_text(ctx.run.design, "設計")
    task = {
        "id": "task1",
        "tier": "standard",
        "subject": "",
        "dod": "",
        "acceptance": "",
        "scope": "",
        "entrypoints": "",
        "contracts": "",
        "branch": "b",
    }
    for name, expected in (("review:normal", True), ("review:adversarial", False)):
        values = stage_call.stage_values(ctx, stages.TABLE[name], task, "1", "")
        table = prompt._table(stages.TABLE[name], values, "/autodev.py")
        assert ("<設計>" in table) is expected, name


def test_設計のステージのレビュー記録は設計の置き場を指す(ctx):
    values = stage_call.stage_values(ctx, stages.TABLE["design-judge"], None, "1", "")
    assert values["review"] == ctx.run.review("design")
    assert values["design_history"] == ctx.run.design_history
    assert "design_history" not in stage_call.stage_values(
        ctx, stages.TABLE["design-review"], None, "1", ""
    )
