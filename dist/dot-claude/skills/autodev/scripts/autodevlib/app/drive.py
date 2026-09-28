"""ラン 1 回を最後まで回す。**ステージの順番とタスクのループはここにある。**

    準備          : config を読み、worktree を切り、ブリーフとコードマップを置く
                    （ステージは worktree を cwd にして走るので計画ステージより先）
    計画          : 受入条件と DoD を確定し、タスクに分け、設計ファイルを書く
    設計の確かめ  : 設計レビュー → 設計のジャッジ → 計画ステージの直し。指摘が 0 件になったら
                    計画を state.json に写す（タスクがすべて light なら飛ばす）
    公開          : 概要ブランチと概要 PR（draft）を作る
    タスクごとに  : テスト作成 → 実装 → レビュー → ジャッジ → 修正 → 完了チェック →
                    公開（タスク PR を作ってスタックに追加する）
    仕上げ        : 概要 PR の draft を外して人間に渡す

**タスクは番号順に 1 本ずつ回す。** タスクが進めなくなったら、次の 2 つのどちらかになる。

- タスクの割り方か設計が合っていない → 再計画ステージが割り方と設計ファイルを直し、設計の
  確かめを通してから写して、ループを続ける
- 人の判断が要る → 質問を出して回答待ち（終了コード 4）で終わる。回答を置いて呼び直すと、
  そのタスクの続きから進む
"""

from __future__ import annotations

from typing import Any

from ..core import task_order
from ..ports import repo
from . import design, planning, replan
from .context import Ctx, NeedsReplan, Waiting
from .finish import finish
from .inputs import load_config, prepare_inputs
from .planning import ask_human, plan, take_answers
from .publish import ensure_overview_pr, ensure_tree, summarize
from .task import run_task

#: 提案を出したステージごとの、直させ方（`design.settle()` に渡す）
REVISE = {"plan": planning.revise, "replan": replan.revise}


def settle_proposal(ctx: Ctx, config: dict[str, Any]) -> None:
    """まだ写していない提案があれば、設計の指摘を 0 件にしてから state.json に写す。

    回答待ちから呼び直したときも、ここで続きから進む（`design.step` を見る）。
    """
    first = design.pending(ctx.st)
    if first is None:
        return
    design.settle(ctx, REVISE[first["stage"]])
    # 直すたびに提案は差し替わるので、写すのは最後の版である
    proposal = design.pending(ctx.st) or first
    if proposal["stage"] == "plan":
        planning.apply_plan(ctx, config, proposal["result"])
    else:
        replan.apply(ctx, proposal)
    design.close(ctx)


def drive(ctx: Ctx) -> int:
    """終了コードを返す。値の意味は `app/context.py` の `EXIT_*` にある。"""
    config = load_config(ctx.st["repo"], ctx.run)
    ctx.st.setdefault("testGlobs", config["testGlobs"])
    ctx.st.setdefault("verify", config["verify"])
    prepare_inputs(ctx.run, ctx.st, config)
    # テストへの書き込みを止めるフックは、ランの頭で 1 度書いて全ステージに渡す
    repo.write_guard_settings(ctx.run.guard)
    ctx.save()
    # ステージはすべて worktree を cwd にして走るので、計画ステージより先に作る
    ensure_tree(ctx)

    deferred = ctx.st.get("deferred") or {}
    try:
        # 計画ステージが聞いて止まっていたら、提案の直しの途中でも plan() が再開する
        if not ctx.st["tasks"] and (
            design.pending(ctx.st) is None or deferred.get("stage") == "plan"
        ):
            plan(ctx, config)
            ctx.save()
        take_answers(ctx)
        settle_proposal(ctx, config)
    except Waiting as wait:
        ask_human(ctx, wait)
    ctx.save()

    if not ctx.st.get("overviewPr"):
        # 概要 PR を作る前に、まとめステージに本文を書かせる
        summarize(ctx, "0")
    ensure_overview_pr(ctx)
    ctx.save()

    while True:
        task = task_order.next_pending(ctx.st)
        if task is None:
            break
        try:
            try:
                run_task(ctx, task)
            except NeedsReplan as need:
                replan.replan(ctx, need)
                settle_proposal(ctx, config)
        except Waiting as wait:
            ask_human(ctx, wait)
        ctx.save()

    code = finish(ctx)
    ctx.save()
    return code
