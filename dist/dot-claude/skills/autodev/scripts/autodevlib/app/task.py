"""タスク 1 本を回す。テスト作成 → 実装 → レビュー → 完了チェック → PR。

**進み具合は `task["phase"]` に残す。** 回答待ちで終わっても、driver が落ちても、呼び直せば
その phase から続く（テスト作成からやり直さない）。

    tests  → build  → review ⇄ gate → スタックに追加

**完了チェックが落ちたら、落ちた項目を must-fix の指摘にしてレビューのループへ戻す。**
タスクを要確認で止めない。

**⑥は①〜⑤が通ってから流す。** `judge()` を 2 度呼ぶのはそのためである（時間のかかる
検証コマンドを、落ちると分かっているランで流さない）。
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from ..core import task_order, verdict
from ..ports import console, evidence, files, repo, review_store, run_store
from .build import build, make_tests
from .context import Ctx, Waiting
from .publish import publish
from .review_loop import review_fix_loop

#: 修正ステージがコードを直せば解ける完了チェック。①はレビューのループを抜けた時点で必ず通り、
#: ④はレビューステージが走り終えないときに落ちる（直すのはコードではない）
FIXABLE = ("commits", "reviews-settled", "tests-untouched", "verify")


def advance(ctx: Ctx, task: dict[str, Any], phase: str) -> None:
    task["phase"] = phase
    ctx.save()


def gate(ctx: Ctx, task: dict[str, Any]) -> verdict.Report:
    """完了チェック①〜⑥。結果は `result-gate-<ラウンド>.json` に残す。"""
    run, st = ctx.run, ctx.st
    rounds = [(str(label), list(expected)) for label, expected in task.get("reviewRounds") or []]
    facts = evidence.collect(
        stage_ok=True,
        stage_detail="レビューが全件解消した",
        tree=run.tree,
        parent=task["parent"],
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
    files.write_json(
        run.result(task["id"], "gate", str(task.get("rounds") or 0)), verdict.as_dict(report)
    )
    return report


def run_task(ctx: Ctx, task: dict[str, Any]) -> None:
    """タスク 1 本をスタックに追加するまで回す。進めなくなったら `Waiting` か `NeedsReplan` を投げる。"""
    run, st = ctx.run, ctx.st
    if task["status"] != "running":
        run_store.set_task(st, task["id"], status="running", parent=task_order.parent_of(st, task))
        console.info(f"--- {task['id']}（{task['subject']}）を {task['parent']} の上で始める")
    else:
        console.info(
            f"--- {task['id']}（{task['subject']}）を {task.get('phase') or 'tests'} から続ける"
        )

    switched = repo.start_task_branch(run.tree, task["branch"], task["parent"])
    if not switched.ok:
        raise Waiting(
            task["id"],
            [
                {
                    "id": f"{task['id']}-branch",
                    "question": f"ブランチ {task['branch']} に切り替えられなかった: {switched.err}。"
                    "worktree の状態を直したら、続けてよいと回答してください。",
                }
            ],
        )

    phase = task.get("phase") or "tests"
    if phase == "tests":
        make_tests(ctx, task, "0")
        advance(ctx, task, phase := "build")
    if phase == "build":
        build(ctx, task)
        advance(ctx, task, phase := "review")
    while True:
        if phase == "review":
            review_fix_loop(ctx, task)
            advance(ctx, task, phase := "gate")
        report = gate(ctx, task)
        if report.ok:
            break
        failed = [c for c in report.failed if c.detail != verdict.VERIFY_SKIPPED]
        fixable = [c for c in failed if c.name in FIXABLE]
        if not fixable:
            raise Waiting(
                task["id"],
                [
                    {
                        "id": f"{task['id']}-gate",
                        "question": "完了チェックがコードの直しでは解けない理由で落ちた: "
                        + "; ".join(f"{c.name}: {c.detail}" for c in failed)
                        + "。原因を取り除いたら、続けてよいと回答してください。",
                    }
                ],
            )
        label = str(task.get("rounds") or 0)
        for check in fixable:
            review_store.add_gate_failure(run.review(task["id"]), check.name, check.detail, label)
        console.info(f"  完了チェックの失敗 {len(fixable)} 件を指摘にして、修正に戻る")
        advance(ctx, task, phase := "review")

    publish(ctx, task)
