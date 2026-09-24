"""ラン 1 回を最後まで回す。**ステージの順番とタスクのループはここにある。**

    準備          : config を読み、worktree を切り、ブリーフとコードマップを置く
                    （ステージは worktree を cwd にして走るので計画ステージより先）
    計画          : 受入条件と DoD を確定し、タスクに分ける
    公開          : 概要ブランチと概要 PR（draft）を作る
    タスクごとに  : テスト作成 → 実装 → レビュー → ジャッジ → 修正 → 完了チェック →
                    公開（タスク PR を作ってスタックに追加する）
    仕上げ        : 概要 PR の draft を外して人間に渡す

**タスクは番号順に 1 本ずつ回す。** タスクが進めなくなったら、次の 2 つのどちらかになる。

- タスクの割り方が合っていない → 再計画ステージが割り直し、ループを続ける
- 人の判断が要る → 質問を出して回答待ち（終了コード 4）で終わる。回答を置いて呼び直すと、
  そのタスクの続きから進む
"""

from __future__ import annotations

from ..core import task_order
from ..ports import repo
from .context import Ctx, NeedsReplan, Waiting
from .finish import finish
from .inputs import load_config, prepare_inputs
from .planning import ask_human, plan, take_answers
from .publish import ensure_overview_pr, ensure_tree, summarize
from .replan import replan
from .task import run_task


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

    if not ctx.st["tasks"]:
        plan(ctx, config)
        ctx.save()
        # 概要 PR を作る前に自由記述を用意する（本文の「何をする作業か」に入る）
        summarize(ctx, "0")

    ensure_overview_pr(ctx)
    take_answers(ctx)
    ctx.save()

    while True:
        task = task_order.next_pending(ctx.st)
        if task is None:
            break
        try:
            try:
                run_task(ctx, task)
            except NeedsReplan as need:
                replan(ctx, need)
        except Waiting as wait:
            ask_human(ctx, wait)
        ctx.save()

    code = finish(ctx)
    ctx.save()
    return code
