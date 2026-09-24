"""タスク 1 本を回す。テスト作成 → 実装 → レビュー → 6 検査 → PR。

**⑥は①〜⑤が通ってから流す。** `judge()` を 2 度呼ぶのはそのためである（時間のかかる
検証コマンドを、落ちると分かっている run で流さない）。
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from ..config import stages
from ..core import task_order, verdict
from ..ports import console, evidence, files, repo, run_store, runner
from .context import Ctx
from .publish import publish
from .review_loop import review_fix_loop
from .stage_call import call, record_judgements


def make_tests(ctx: Ctx, task: dict[str, Any], extra: str = "", label: str = "0") -> bool:
    """テスト作成段。**この段だけテストへ書ける**（driver が `AUTODEV_ALLOW_TESTS` を渡す）。"""
    got = call(ctx, stages.TABLE["testgen"], task, label, extra=extra)
    if not got.ok:
        run_store.set_task(ctx.st, task["id"], status="failed", reason=f"テスト作成段: {got.error}")
        return False
    result = got.result or {}
    if result.get("blocked"):
        questions = "; ".join(result.get("questions", []))
        run_store.set_task(
            ctx.st, task["id"], status="blocked", reason=f"受入条件が曖昧: {questions}"
        )
        return False
    # **テストを書いた時点のコミットを控える。** 検査⑤はここから先でテストが動いていないかを
    # 見る（テスト作成段はタスクのブランチに commit するので、parent から見ると必ず差分が出る）
    run_store.set_task(ctx.st, task["id"], testsAt=repo.head_sha(ctx.run.tree))
    return True


def implement(ctx: Ctx, task: dict[str, Any], label: str) -> runner.Result:
    """実装段。テストファイルは read-only にして走らせ、終わったら必ず戻す。

    フックは run の頭で書いた `guard.json` を全段に渡してあるので、ここで出し入れするのは
    ファイルの書き込み権だけである（フックの裏をかかれても書けないようにする二重の栓）。
    """
    repo.lock_tests(ctx.run.tree, ctx.st["testGlobs"])
    try:
        return call(ctx, stages.TABLE["impl"], task, label)
    finally:
        repo.unlock_tests(ctx.run.tree, ctx.st["testGlobs"])


def build(ctx: Ctx, task: dict[str, Any]) -> bool:
    """実装させる。テストの矛盾が申告されたら、テスト作成段を呼び直してから実装に戻る。"""
    st = ctx.st
    got = implement(ctx, task, "1")
    if not got.ok:
        run_store.set_task(st, task["id"], status="failed", reason=f"実装段: {got.error}")
        return False
    run_store.set_task(st, task["id"], implSession=got.session_id)
    result = got.result or {}

    conflict = result.get("testConflict")
    if conflict:
        # **テストを直せるのはテスト作成段だけである。** 実装段に直させると、テストを
        # 通すためにテストを緩める経路ができる
        console.info(f"テストの矛盾が申告された: {str(conflict)[:200]}")
        extra = (
            "## 実装段からの申告\n\n"
            f"{conflict}\n\n"
            "この申告を受入条件と照らして検証し、**正しければテストを直す**。"
            "誤っていれば直さず、理由を結果の `notes` に書く。"
        )
        if not make_tests(ctx, task, extra=extra, label="1"):
            return False
        got = implement(ctx, task, "2")
        if not got.ok:
            run_store.set_task(st, task["id"], status="failed", reason=f"実装段（再）: {got.error}")
            return False
        run_store.set_task(st, task["id"], implSession=got.session_id)
        result = got.result or {}

    if result.get("blocked"):
        questions = "; ".join(result.get("questions", []))
        run_store.set_task(
            st, task["id"], status="blocked", reason=f"実装段が blocked: {questions}"
        )
        return False
    record_judgements(st, result)
    return True


def run_task(ctx: Ctx, task: dict[str, Any]) -> bool:
    """タスク 1 本を回す。戻り値は「積めたか」。"""
    run, st = ctx.run, ctx.st
    parent = task_order.parent_of(st, task)
    run_store.set_task(st, task["id"], status="running", parent=parent)
    console.info(f"--- {task['id']}（{task['subject']}）を {parent} の上で始める")

    switched = repo.start_task_branch(run.tree, task["branch"], parent)
    if not switched.ok:
        run_store.set_task(
            st, task["id"], status="failed", reason=f"ブランチを作れなかった: {switched.err}"
        )
        return False

    if not make_tests(ctx, task):
        return False
    if not build(ctx, task):
        return False

    settled, rounds, reason = review_fix_loop(ctx, task)
    facts = evidence.collect(
        stage_ok=settled,
        stage_detail=reason or "レビューが全件決着した",
        tree=run.tree,
        parent=parent,
        branch=task["branch"],
        review_path=run.review(task["id"]),
        tests_since=task.get("testsAt"),
    )
    report = verdict.judge(facts, tier=task["tier"], rounds=rounds, test_globs=st["testGlobs"])
    if verdict.needs_verify(report):
        facts = replace(facts, verify=evidence.run_verify(run.tree, st["verify"]))
        report = verdict.judge(
            facts, tier=task["tier"], rounds=rounds, test_globs=st["testGlobs"], verify_ran=True
        )
    for line in report.lines():
        console.info(f"  {line}")
    files.write_json(run.result(task["id"], "gate", "0"), verdict.as_dict(report))

    if not report.ok:
        run_store.set_task(
            st,
            task["id"],
            status="blocked",
            reason="; ".join(f"{c.name}: {c.detail}" for c in report.failed),
        )
        return False

    publish(ctx, task)
    return True
