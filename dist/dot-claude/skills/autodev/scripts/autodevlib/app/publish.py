"""土台（ブランチ・worktree・draft PR）と、タスク 1 本ぶんの PR。"""

from __future__ import annotations

import os
from typing import Any

from ..config import stages
from ..core import markdown
from ..ports import console, files, forge, repo, run_store, templates
from .context import Ctx
from .stage_call import call


def ensure_tree(ctx: Ctx) -> None:
    """土台ブランチ（空コミット 1 つ）と worktree を作る。

    **計画段より先に作る。** 段はすべて worktree を cwd にして走るので、無いと起動できない。

    空コミットを載せるのは、**base と差分が 0 だと `gh pr create` が
    `No commits between …` で落ちる**ためである。
    """
    run, st = ctx.run, ctx.st
    if os.path.isdir(run.tree):
        return
    made = repo.create_stack_base(st["repo"], run.tree, st["stackBranch"], st["base"])
    if not made.ok:
        console.die(f"土台の worktree を作れなかった: {made.err or made.out}")


def ensure_stack_pr(ctx: Ctx) -> None:
    """土台の draft PR を作る。**タスクが決まってから**（本文に一覧を載せるので）。

    draft にするのは、全部積み終わるまで上のタスク PR もマージできないようにする栓である。
    """
    run, st = ctx.run, ctx.st
    if st.get("stackPr"):
        return
    pushed = repo.push(run.tree, st["stackBranch"])
    if not pushed.ok:
        console.die(f"土台ブランチを push できなかった: {pushed.err or pushed.out}")
    write_stack_body(ctx)
    number, got = forge.pr_create(
        run.tree,
        base=st["base"],
        head=st["stackBranch"],
        title=f"[autodev] {st['work']}",
        body_file=run.stack_pr_body,
        draft=True,
    )
    if not number:
        console.die(f"土台 PR を作れなかった: {got.err or got.out}")
    st["stackPr"] = number
    console.info(f"土台 PR #{number} を draft で作った")


def write_stack_body(ctx: Ctx) -> str:
    """土台 PR の本文を書き出す。

    **散文はまとめ段が書いたものを差し、進行表と残課題は state.json から毎回組み立てる。**
    こうすると、1 本積むたびに書き出しても数がずれず、モデルを呼び直さなくて済む。
    """
    run, st = ctx.run, ctx.st
    return files.write_text(
        run.stack_pr_body,
        templates.fill(
            "stack-pr-body",
            {
                "work": st["work"],
                "prose": templates.read_prose(
                    run, "stack", "（まとめ段がまだ書いていない。下のタスクの一覧を見る）"
                ),
                "instruction": (st.get("instruction") or "").strip() or "(なし)",
                "tasks": markdown.tasks_block(st),
                "held": markdown.held_block(st),
                "decisions": markdown.entries_block(st, "decisions", "自律判断の記録"),
                "deferrals": markdown.entries_block(st, "deferrals", "先送り・対象外"),
                "updated_at": st.get("updatedAt", ""),
            },
        ),
    )


def refresh_stack_pr(ctx: Ctx) -> None:
    run, st = ctx.run, ctx.st
    if not st.get("stackPr"):
        return
    write_stack_body(ctx)
    forge.pr_edit(run.tree, st["stackPr"], body_file=run.stack_pr_body)


def summarize(ctx: Ctx) -> None:
    """まとめ段に土台 PR の散文を書かせる。**呼ぶのは計画の直後と仕上げの 2 回だけ。**

    進行表は state.json から毎回組み立てるので、ここで書かせるのは「この作業で何が
    変わるか」の文章に限る。
    """
    got = call(ctx, stages.TABLE["summary"], None, "0")
    prose = str((got.result or {}).get("prose") or "").strip() if got.ok else ""
    if prose:
        templates.write_prose(ctx.run, "stack", prose)
    else:
        console.info("まとめ段が文章を返さなかったので、前の文章をそのまま使う")


def publish(ctx: Ctx, task: dict[str, Any]) -> None:
    """PR 本文を書かせ、push して PR を作り、stacked PR に連ねる。"""
    run, st = ctx.run, ctx.st
    got = call(ctx, stages.TABLE["pr-body"], task, "0")
    body = str((got.result or {}).get("body") or "") if got.ok else ""
    if not body.strip():
        console.info("PR 本文段が本文を返さなかったので、最小限の本文で作る")
        body = f"## 何が変わるか\n\n{task['subject']}\n\n## DoD\n\n{task['dod']}"
    stack_pr = st.get("stackPr")
    files.write_text(
        run.task_pr_body(task["id"]),
        templates.fill(
            "task-pr-body",
            {
                "task_id": task["id"],
                # **一覧は焼き込まない。** 本文を差し替えるのは決着した 1 回だけなので、
                # 後から積まれた PR が載らず古くなる。置き場は土台 PR 1 か所に保つ
                "stack_pr_note": f"全体の計画と進行は #{stack_pr} にある。" if stack_pr else "",
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
        title=f"[autodev #{st['stackPr']} {task['id']}] {task['subject']}",
        body_file=run.task_pr_body(task["id"]),
        draft=False,
    )
    if not number:
        console.die(f"{task['id']} の PR を作れなかった: {created.err or created.out}")

    members = [str(st["stackPr"])]
    members += [str(i["pr"]) for i in st["tasks"] if i["status"] == "stacked" and i.get("pr")]
    members.append(str(number))
    linked = forge.stack_link(run.tree, members)
    if not linked.ok:
        console.info(f"gh stack link が失敗した（PR は作れている）: {linked.err or linked.out}")

    run_store.set_task(st, task["id"], status="stacked", pr=number)
    refresh_stack_pr(ctx)
    console.info(f"{task['id']} を PR #{number} として積んだ")
