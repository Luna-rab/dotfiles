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

    **本文はまとめステージが書いたもの（`prose/overview.md`）で、その中のマーカーを state.json から
    毎回置き換える。** こうすると、1 本スタックに追加するたびに書き出しても数がずれず、モデルを
    呼び直さなくて済む。まだ書いていなければ `overview-pr-body-minimal` を使う。

    本文は `templates.fill` に通さない（`$$` が `$` になる）。`fill` で埋めるのは末尾の署名だけ。
    """
    run, st = ctx.run, ctx.st
    body = templates.read_prose(run, "overview", templates.template("overview-pr-body-minimal"))
    signature = templates.fill(
        "overview-pr-body",
        {"run_name": st["name"], "updated_at": st.get("updatedAt", "")},
    )
    return files.write_text(
        run.overview_pr_body,
        f"{markdown.fill_markers(body.strip(), st)}\n\n---\n\n{signature.strip()}\n",
    )


def refresh_overview_pr(ctx: Ctx) -> None:
    run, st = ctx.run, ctx.st
    if not st.get("overviewPr"):
        return
    write_overview_body(ctx)
    forge.pr_edit(run.tree, st["overviewPr"], body_file=run.overview_pr_body)


def summarize(ctx: Ctx, round_label: str) -> None:
    """まとめステージに概要 PR の本文を書かせる。**呼ぶのは計画の直後（r0）と仕上げ（r1）の 2 回だけ。**

    ラウンドを分けないと、2 回目のログと指示の記録が 1 回目のものを上書きする。
    本文は**マーカー入りのまま**保存する。ここで置き換えると、後から 1 本スタックに追加しても
    表が古いまま残る。
    """
    got = call(ctx, stages.TABLE["summary"], None, round_label)
    body = str((got.result or {}).get("body") or "").strip() if got.ok else ""
    if body:
        templates.write_prose(ctx.run, "overview", body)
    else:
        console.info("まとめステージが本文を返さなかったので、前の本文をそのまま使う")


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
    linked = forge.stack_link(run.tree, st["base"], members)
    if not linked.ok:
        console.info(f"gh stack link が失敗した（PR は作れている）: {linked.err or linked.out}")

    run_store.set_task(st, task["id"], status="stacked", pr=number)
    # 進んだので、続けて再計画した回数を数え直す（`app/replan.py` の `REPLANS_WITHOUT_PROGRESS`）
    st["replansSinceStack"] = 0
    refresh_overview_pr(ctx)
    console.info(f"{task['id']} を PR #{number} としてスタックに追加した")
    check_overview_base(ctx)


def check_overview_base(ctx: Ctx) -> None:
    """概要 PR の base がランの base のままかを確かめ、ずれていたら止める。

    base がずれたまま `gh stack merge` を流すと、別のブランチへマージされる。スタックに入った PR は
    `gh pr edit --base` で直せないので、人が `gh stack unstack` で解いて組み直す。
    止める前に state.json を書き出す。書き出さないと、呼び直したときに作り済みのタスク PR を
    もう一度作ろうとする。
    """
    run, st = ctx.run, ctx.st
    view = forge.pr_view(run.tree, st["overviewPr"])
    if view is None:
        console.info(f"概要 PR #{st['overviewPr']} の base を確かめられなかった")
        return
    actual = view.get("baseRefName")
    if actual == st["base"]:
        return
    ctx.save()
    console.die(
        f"概要 PR #{st['overviewPr']} の base が {st['base']} ではなく {actual} になっている。"
        f"`gh stack unstack` で解き、`gh stack link --base {st['base']} ...` で組み直してください"
    )
