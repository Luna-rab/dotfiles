"""ランの仕上げ。スタックに追加した結果を人間に渡し、結末を終了コードで返す。

**要対応があるあいだ概要 PR の draft は外さない。** 外すと上のタスク PR がマージできて
しまう。
"""

from __future__ import annotations

from ..ports import console, forge
from .context import EXIT_HELD, EXIT_OK, Ctx
from .publish import refresh_overview_pr, summarize


def finish(ctx: Ctx) -> int:
    run, st = ctx.run, ctx.st
    # **人間が最初に読む文章を、実際にスタックに追加した結果から書き直す。** 計画の直後に書いたものは
    # 予定なので、blocked で終わったタスクやスコープ外が反映されていない
    summarize(ctx)
    refresh_overview_pr(ctx)
    stacked = [i for i in st["tasks"] if i["status"] == "stacked"]
    held = [i for i in st["tasks"] if i["status"] in ("blocked", "failed")]

    if stacked and not held:
        forge.pr_ready(run.tree, st["overviewPr"])
        console.info(f"概要 PR #{st['overviewPr']} の draft を外した")
    elif held:
        console.info(f"概要 PR #{st['overviewPr']} は draft のまま残す（要対応 {len(held)} 件）")

    print()
    print(f"ラン名: {st['name']}  概要 PR: #{st['overviewPr']}")
    for item in st["tasks"]:
        pr = f"#{item['pr']}" if item.get("pr") else "—"
        print(f"  {item['id']:<8} {item['status']:<8} {pr:<6} {item['subject']}")
        if item.get("reason"):
            print(f"           理由: {item['reason']}")
    print()
    if held:
        print("要対応があるので、概要 PR は draft のままにしてある。")
        print(f"詳しくは `autodev status --name {st['name']}` を読んでください。")
        return EXIT_HELD
    print(f"レビューが済んだら `gh stack merge` で下からマージしてください（worktree: {run.tree}）")
    return EXIT_OK
