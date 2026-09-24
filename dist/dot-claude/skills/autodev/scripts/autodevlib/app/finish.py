"""run の仕上げ。積んだ結果を人間に渡し、結末を終了コードで返す。

**残課題があるあいだ土台 PR の draft は外さない。** 外すと上のタスク PR がマージできて
しまう。
"""

from __future__ import annotations

from ..ports import console, forge
from .context import EXIT_HELD, EXIT_OK, Ctx
from .publish import refresh_stack_pr, summarize


def finish(ctx: Ctx) -> int:
    run, st = ctx.run, ctx.st
    # **人間が最初に読む文章を、実際に積んだ結果から書き直す。** 計画の直後に書いたものは
    # 予定なので、blocked で終わったタスクや先送りが反映されていない
    summarize(ctx)
    refresh_stack_pr(ctx)
    stacked = [i for i in st["tasks"] if i["status"] == "stacked"]
    held = [i for i in st["tasks"] if i["status"] in ("blocked", "failed")]

    if stacked and not held:
        forge.pr_ready(run.tree, st["stackPr"])
        console.info(f"土台 PR #{st['stackPr']} の draft を外した")
    elif held:
        console.info(f"土台 PR #{st['stackPr']} は draft のまま残す（残課題 {len(held)} 件）")

    print()
    print(f"作業名: {st['work']}  土台 PR: #{st['stackPr']}")
    for item in st["tasks"]:
        pr = f"#{item['pr']}" if item.get("pr") else "—"
        print(f"  {item['id']:<8} {item['status']:<8} {pr:<6} {item['subject']}")
        if item.get("reason"):
            print(f"           理由: {item['reason']}")
    print()
    if held:
        print("残課題があるので、土台 PR は draft のままにしてある。")
        print(f"詳しくは `autodev status --work {st['work']}` を読んでください。")
        return EXIT_HELD
    print(f"レビューが済んだら `gh stack merge` で下からマージしてください（worktree: {run.tree}）")
    return EXIT_OK
