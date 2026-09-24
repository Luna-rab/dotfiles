"""run 1 回を最後まで回す。**段の順番とタスクのループはここにある。**

    前段（読む）  : config を読む / brief.md と map.md を置く
    土台          : worktree を切る（段はここを cwd にして走るので計画段より先）
    計画          : 受入条件と DoD を確定し、タスクへ割る
    後段（書く）  : 土台ブランチと draft PR
    タスクごとに  : テスト作成 → 実装 → レビュー → 裁定 → 修正 → 6 検査 → PR
    仕上げ        : 土台を ready にして人間に渡す

**タスクは番号順に 1 本ずつ回す。** `next_pending()` が `blocked` か `failed` を見つけたら
`None` を返すので、そこで止まって後続を回さない。
"""

from __future__ import annotations

from ..core import task_order
from ..ports import repo
from .context import Ctx
from .finish import finish
from .inputs import load_config, prepare_inputs
from .planning import plan
from .publish import ensure_stack_pr, ensure_tree, summarize
from .task import run_task


def drive(ctx: Ctx) -> int:
    """終了コードを返す。値の意味は `app/context.py` の `EXIT_*` にある。"""
    config = load_config(ctx.st["repo"], ctx.run)
    ctx.st.setdefault("testGlobs", config["testGlobs"])
    ctx.st.setdefault("verify", config["verify"])
    prepare_inputs(ctx.run, ctx.st, config)
    # テストへの書き込みを止めるフックは、run の頭で 1 度書いて全段に渡す
    repo.write_guard_settings(ctx.run.guard)
    ctx.save()
    # 段はすべて worktree を cwd にして走るので、計画段より先に作る
    ensure_tree(ctx)

    if not ctx.st["tasks"]:
        plan(ctx, config)
        ctx.save()
        # 土台 PR を作る前に散文を用意する（本文の「何をする作業か」に入る）
        summarize(ctx)

    ensure_stack_pr(ctx)
    ctx.save()

    while True:
        task = task_order.next_pending(ctx.st)
        if task is None:
            break
        run_task(ctx, task)
        ctx.save()

    code = finish(ctx)
    ctx.save()
    return code
