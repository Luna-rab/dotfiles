"""概要ブランチ・worktree・概要 PR と、タスク 1 本ぶんのタスク PR。"""

from __future__ import annotations

import os
from typing import Any

from ..config import stages
from ..core import markdown
from ..ports import console, files, forge, repo, run_store, templates
from .context import Ctx
from .stage_call import call


def ensure_tree(ctx: Ctx) -> None:
    """概要ブランチ（空コミット 1 つ）と worktree を作る。

    **計画ステージより先に作る。** ステージはすべて worktree を cwd にして走るので、無いと起動できない。

    空コミットを載せるのは、**base と差分が 0 だと `gh pr create` が
    `No commits between …` で落ちる**ためである。
    """
    run, st = ctx.run, ctx.st
    if os.path.isdir(run.tree):
        return
    made = repo.create_overview_branch(st["repo"], run.tree, st["overviewBranch"], st["base"])
    if not made.ok:
        console.die(f"worktree を作れなかった: {made.err or made.out}")


def ensure_overview_pr(ctx: Ctx) -> None:
    """概要 PR を draft で作る。**タスクが決まってから**（本文に一覧を載せるので）。

    draft にするのは、全部スタックに追加し終わるまで上のタスク PR もマージできないようにする栓である。
    """
    run, st = ctx.run, ctx.st
    if st.get("overviewPr"):
        return
    pushed = repo.push(run.tree, st["overviewBranch"])
    if not pushed.ok:
        console.die(f"概要ブランチを push できなかった: {pushed.err or pushed.out}")
    write_overview_body(ctx)
    number, got = forge.pr_create(
        run.tree,
        base=st["base"],
        head=st["overviewBranch"],
        title=f"[autodev] {st['name']}",
        body_file=run.overview_pr_body,
        draft=True,
    )
    if not number:
        console.die(f"概要 PR を作れなかった: {got.err or got.out}")
    st["overviewPr"] = number
    console.info(f"概要 PR #{number} を draft で作った")


def write_overview_body(ctx: Ctx) -> str:
    """概要 PR の本文を書き出す。

    **自由記述はまとめステージが書いたものを差し、進行表と要対応は state.json から毎回組み立てる。**
    こうすると、1 本スタックに追加するたびに書き出しても数がずれず、モデルを呼び直さなくて済む。
    """
    run, st = ctx.run, ctx.st
    return files.write_text(
        run.overview_pr_body,
        templates.fill(
            "overview-pr-body",
            {
                "run_name": st["name"],
                "prose": templates.read_prose(
                    run, "overview", "（まとめステージがまだ書いていない。下のタスクの一覧を見る）"
                ),
                "instruction": (st.get("instruction") or "").strip() or "(なし)",
                "tasks": markdown.tasks_block(st),
                "held": markdown.held_block(st),
                "decisions": markdown.entries_block(st, "decisions", "判断ログ"),
                "deferrals": markdown.entries_block(st, "deferrals", "スコープ外"),
                "updated_at": st.get("updatedAt", ""),
            },
        ),
    )


def refresh_overview_pr(ctx: Ctx) -> None:
    run, st = ctx.run, ctx.st
    if not st.get("overviewPr"):
        return
    write_overview_body(ctx)
    forge.pr_edit(run.tree, st["overviewPr"], body_file=run.overview_pr_body)


def summarize(ctx: Ctx) -> None:
    """まとめステージに概要 PR の自由記述を書かせる。**呼ぶのは計画の直後と仕上げの 2 回だけ。**

    進行表は state.json から毎回組み立てるので、ここで書かせるのは「この作業で何が
    変わるか」の文章に限る。
    """
    got = call(ctx, stages.TABLE["summary"], None, "0")
    prose = str((got.result or {}).get("prose") or "").strip() if got.ok else ""
    if prose:
        templates.write_prose(ctx.run, "overview", prose)
    else:
        console.info("まとめステージが文章を返さなかったので、前の文章をそのまま使う")


def publish(ctx: Ctx, task: dict[str, Any]) -> None:
    """PR 本文を書かせ、push して PR を作り、stacked PR に連ねる。"""
    run, st = ctx.run, ctx.st
    got = call(ctx, stages.TABLE["pr-body"], task, "0")
    body = str((got.result or {}).get("body") or "") if got.ok else ""
    if not body.strip():
        console.info("PR 本文ステージが本文を返さなかったので、最小限の本文で作る")
        body = f"## 何が変わるか\n\n{task['subject']}\n\n## DoD\n\n{task['dod']}"
    overview_pr = st.get("overviewPr")
    files.write_text(
        run.task_pr_body(task["id"]),
        templates.fill(
            "task-pr-body",
            {
                "task_id": task["id"],
                # **一覧は焼き込まない。** 本文を差し替えるのは指摘が解消した 1 回だけなので、
                # 後からスタックに追加された PR が載らず古くなる。置き場は概要 PR 1 か所に保つ
                "overview_pr_note": f"全体の計画と進行は概要 PR #{overview_pr} にある。"
                if overview_pr
                else "",
                "prose": body.strip(),
            },
        ),
    )

    pushed = repo.push(run.tree, task["branch"])
    if not pushed.ok:
        console.die(f"{task['id']} を push できなかった: {pushed.err or pushed.out}")

    number, created = forge.pr_create(
        run.tree,
        base=task["parent"],
        head=task["branch"],
        title=f"[autodev #{st['overviewPr']} {task['id']}] {task['subject']}",
        body_file=run.task_pr_body(task["id"]),
        draft=False,
    )
    if not number:
        console.die(f"{task['id']} の PR を作れなかった: {created.err or created.out}")

    members = [str(st["overviewPr"])]
    members += [str(i["pr"]) for i in st["tasks"] if i["status"] == "stacked" and i.get("pr")]
    members.append(str(number))
    linked = forge.stack_link(run.tree, members)
    if not linked.ok:
        console.info(f"gh stack link が失敗した（PR は作れている）: {linked.err or linked.out}")

    run_store.set_task(st, task["id"], status="stacked", pr=number)
    refresh_overview_pr(ctx)
    console.info(f"{task['id']} を PR #{number} としてスタックに追加した")
