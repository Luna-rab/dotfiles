"""サブコマンドの中身。`build_parser()` が `func` に差すのはここの関数である。

**進行の判断は持たない。** 引数を読み、起動前の確認をし、`app` と `ports` を呼んで
終了コードをそのまま返す。ラン 1 回をどう回すかは `app/drive.py`、ステージの順番と打ち切りは
`app/` の各ファイルにある。
"""

from __future__ import annotations

import argparse
import os
import secrets
import sys
from typing import Any

from .app.context import Ctx
from .app.drive import drive
from .app.planning import unanswered
from .config import paths
from .core import review_policy, task_order
from .ports import console, files, forge, proc, repo, review_store, run_store

# --- 起動前の確認 -----------------------------------------------------------


def preflight() -> list[str]:
    """足りないものを並べて返す。**1 つでもあれば走らない。**

    途中で気づくと、worktree と概要 PR だけが残る中途半端な状態になる。
    """
    problems: list[str] = []
    if not proc.run(["claude", "--version"]).ok:
        problems.append("`claude` が PATH に無い（ステージを起動できない）")
    if not proc.run(["git", "--version"]).ok:
        problems.append("`git` が PATH に無い")
    reason = forge.ready()
    if reason:
        problems.append(reason)
    return problems


def resolve_repo(value: str | None) -> str:
    start = os.path.abspath(value or os.getcwd())
    root = repo.repo_root(start)
    if not root:
        console.die(f"git のリポジトリが見つからない: {start}")
    return root


def read_instruction(value: str | None) -> str:
    """指示を受け取る。`-` なら標準入力から読む。"""
    if value == "-":
        return sys.stdin.read().strip()
    if value:
        return value.strip()
    return ""


# --- サブコマンド -----------------------------------------------------------


def start_run(args: argparse.Namespace, run: paths.Run) -> dict[str, Any]:
    """新しいランの state を作る。**ラン名の重複はここで弾く。**"""
    repo_path = resolve_repo(args.repo)
    base = args.base or repo.default_branch(repo_path)
    instruction = read_instruction(args.instruction)
    if not instruction:
        console.die("指示が要る")
    repo.fetch(repo_path)
    if repo.remote_branch_exists(repo_path, f"stack/{args.name}--task-0"):
        console.die(
            f"ラン名 {args.name} は既に使われている（origin に stack/{args.name}--task-0 がある）"
        )
    st = run_store.new_state(args.name, instruction, repo_path, base)
    st["judgeToken"] = secrets.token_hex(16)
    run.ensure()
    return st


def cmd_run(args: argparse.Namespace) -> int:
    problems = preflight()
    if problems:
        for problem in problems:
            console.info(f"足りない: {problem}")
        console.die("起動前の確認に落ちたので走らない")

    run = paths.Run(args.name)
    if run.exists():
        st = run_store.load(run.state)
        console.info(f"{args.name} を続きから始める（タスク {len(st['tasks'])} 件）")
    else:
        st = start_run(args, run)

    # 前のランがステージの途中で落ちていると、走っていないステージが残る
    st["running"] = {}
    return drive(Ctx(run=run, st=st))


def cmd_status(args: argparse.Namespace) -> int:
    run = paths.Run(args.name)
    if not run.exists():
        console.die(f"そのランが無い: {run.dir}")
    st = run_store.load(run.state)
    console.emit(
        {
            "name": st["name"],
            "outcome": task_order.outcome_of(st),
            "repo": st["repo"],
            "overviewPr": st.get("overviewPr"),
            "counts": task_order.counts(st),
            "tasks": [
                {k: t.get(k) for k in ("id", "status", "pr", "tier", "subject", "reason")}
                for t in st["tasks"]
            ],
            "questions": st.get("questions") or [],
            "decisions": [e["body"] for e in st.get("decisions") or []],
            "deferrals": [e["body"] for e in st.get("deferrals") or []],
            "logs": run.path("logs"),
            "dir": run.dir,
        },
        pretty=True,
    )
    return 0


def cmd_list(_: argparse.Namespace) -> int:
    for name in paths.list_runs():
        st = run_store.load(paths.Run(name).state)
        print(f"{name:<24} PR #{st.get('overviewPr') or '-':<6} {task_order.counts(st)}")
    return 0


def cmd_clean(args: argparse.Namespace) -> int:
    run = paths.Run(args.name)
    if not run.exists():
        console.die(f"そのランが無い: {run.dir}")
    st = run_store.load(run.state)
    if os.path.isdir(run.tree):
        got = repo.remove_worktree(st["repo"], run.tree)
        console.info(f"worktree を外した: {run.tree}" if got.ok else f"外せなかった: {got.err}")
    console.info(f"記録は残してある: {run.dir}")
    return 0


def cmd_purge(args: argparse.Namespace) -> int:
    """ランの手元の跡を全部消す。worktree・`stack/<ラン名>--task-*` のブランチ・ランディレクトリ。

    **GitHub の PR とリモートのブランチには触らない。** 公開したものを消すと戻せないので、
    残っている PR を出すだけにする。消す前に全部確かめ、1 つでも引っかかれば何も消さない。
    """
    run = paths.Run(args.name)
    if not run.exists():
        console.die(f"そのランが無い: {run.dir}")
    st = run_store.load(run.state)
    target = st.get("repo") or ""
    branches = repo.run_branches(target, run.run_name) if target else []
    if not args.force:
        problems = []
        if st.get("running"):
            problems.append(f"ステージが走っている記録がある: {', '.join(st['running'])}")
        for branch in branches:
            lost = repo.unpushed_count(target, branch)
            if lost != 0:
                problems.append(f"{branch} に origin に無いコミットがある（{lost} 件）")
        if problems:
            for problem in problems:
                console.info(problem)
            console.die("何も消していない。それでも消すなら --force を付ける")

    if os.path.isdir(run.tree) and target:
        got = repo.remove_worktree(target, run.tree)
        if not got.ok:
            console.die(f"worktree を外せなかった: {got.err}")
    if target:
        repo.prune_worktrees(target)
    for branch in branches:
        got = repo.delete_branch(target, branch)
        console.info(
            f"ブランチを消した: {branch}" if got.ok else f"消せなかった: {branch}: {got.err}"
        )
    files.remove_tree(run.dir)
    console.info(f"ランディレクトリを消した: {run.dir}")
    prs = [
        f"#{n}" for n in [st.get("overviewPr"), *(t.get("pr") for t in st.get("tasks") or [])] if n
    ]
    if prs:
        console.info(f"GitHub の PR は残してある: {' '.join(prs)}")
    return 0


def _body(args: argparse.Namespace) -> str:
    if getattr(args, "body_file", None):
        source = args.body_file
        return sys.stdin.read() if source == "-" else files.read_text(source)
    return getattr(args, "body", "") or ""


def _review_action(args: argparse.Namespace, path: str) -> dict[str, Any]:
    if args.action == "init":
        return {"wrote": path, "created": review_store.init(path)}
    if args.action == "new":
        review_id, tally = review_store.add(
            path,
            reviewer=args.reviewer,
            rating=args.rating,
            location=args.location,
            body=_body(args),
            round_label=args.round,
        )
        return {"id": review_id, "counts": tally}
    if args.action == "comment":
        return {
            "id": args.id,
            "counts": review_store.comment(path, args.id, args.commenter, _body(args)),
        }
    if args.action == "status":
        return {
            "id": args.id,
            "to": args.to,
            "counts": review_store.set_status(path, args.id, args.to, _body(args)),
        }
    if args.action == "done":
        return {
            "reviewer": args.reviewer,
            "counts": review_store.done(path, args.reviewer, args.round, args.found),
        }
    data = review_store.read(path)
    if data is None:
        raise review_store.Refused(f"review.json が無い: {path}")
    return {
        "counts": review_policy.tally(data),
        "items": review_store.items(data, only_open=not args.all),
    }


def cmd_ask(args: argparse.Namespace) -> int:
    """ステージが呼ぶ。回答が置かれていればそれを出す。

    **普通はフックが `defer` を返すので、このコマンドは走らない。** 走るのは、ほかのツールと
    同じターンで呼ばれて `defer` が効かなかったときである。そのときは単独で呼び直させる。
    """
    root = os.environ.get("AUTODEV_RUN_DIR") or ""
    if not root:
        return console.die("AUTODEV_RUN_DIR が無い（ステージの中から呼んでください）", code=3)
    loaded = files.read_json(os.path.join(root, "answers", f"{args.id}.json"))
    if isinstance(loaded, dict):
        console.emit(loaded, pretty=True)
        return 0
    return console.die(
        "回答がまだ無い。**このコマンドは 1 つのターンで単独に呼ぶこと**"
        "（ほかのツールと一緒に呼ぶと、回答を待って止まれない）。",
        code=3,
    )


def cmd_answer(args: argparse.Namespace) -> int:
    """呼び出し元のエージェントが呼ぶ。回答を置くと、次の `autodev run` でステージが続きから進む。"""
    run = paths.Run(args.name)
    if not run.exists():
        console.die(f"そのランが無い: {run.dir}")
    body = _body(args).strip()
    if not body:
        console.die("回答の本文が空")
    files.write_json(run.answer(args.id), {"id": args.id, "answer": body})
    remaining = [q["id"] for q in unanswered(run) if q.get("id")]
    console.emit({"wrote": run.answer(args.id), "remaining": remaining}, pretty=True)
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    """ステージが呼ぶ。`--dir` はタスクのディレクトリ（プレースホルダ表の `<レビュー>` の親）。"""
    path = args.dir if args.dir.endswith(".json") else os.path.join(args.dir, "review.json")
    try:
        console.emit(_review_action(args, path), pretty=True)
    except review_store.Refused as refused:
        console.die(str(refused))
    return 0
