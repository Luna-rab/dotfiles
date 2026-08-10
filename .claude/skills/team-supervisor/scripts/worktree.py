#!/usr/bin/env python3
"""落ちた・役目を終えた subagent の worktree の後始末。

どちらも後戻りできない操作（作業ツリーの削除・未検証コミットの push）なので、実行する前に
安全条件を検査する。

サブコマンド:
  remove  残留 worktree を、安全条件を検査してから消す
  rescue  落ちた worktree の未コミットの変更を保全 commit して push する
"""

import argparse
import os
import subprocess

from lib.gitpath import agent_worktree, listed_worktrees, state_dir
from lib.shell import die, emit, out, run

RESCUE_MESSAGE = "wip: 中断時点の保全（検証未実施）"


def cmd_remove(args):
    path = agent_worktree(args.agent)
    # 判定は `git worktree list` で行う。ディレクトリの実在だけを見ると、パスを取り違えて
    # いても「もう無い」と読めてしまい、消えていないのに後始末が済んだように見える
    if os.path.realpath(path) not in listed_worktrees():
        run(["git", "worktree", "prune"])
        emit({"removed": False, "reason": "already-gone", "path": path})
        return

    # 消してよいのは、統合を終えたタスクと、再開を打ち切ったタスクだけ。前者はスクリプト
    # からは分からない（台帳とタスクリストにしかない）のでリードが --merged で明示する
    blocked = os.path.exists(os.path.join(state_dir(), f"blocked-{args.agent}"))
    if not blocked and not args.merged:
        die(
            f"agent-{args.agent} は打ち切り済み（blocked-{args.agent} が無い）でも"
            "統合済み（--merged が無い）でもありません。まだ再開しうる worktree は"
            "消しません"
        )

    if not os.path.isdir(path):
        # 登録は残っているが実体が消えている（エージェントが自分のツリーを消した、
        # 外から rm された）。未コミットの変更を確かめる相手がいないので prune で片づける
        run(["git", "worktree", "prune"])
        emit({"removed": True, "reason": "pruned-stale-registration", "path": path,
              "authorized_by": "blocked-marker" if blocked else "--merged"})
        return

    dirty = out(["git", "-C", path, "status", "--porcelain"])
    if dirty:
        die(
            f"{path} に未コミットの変更が {len(dirty.splitlines())} 件あります。先に "
            f"worktree.py rescue --agent {args.agent} --branch <タスクブランチ> で"
            "保全してください"
        )

    # --force を付けない。上の検査を抜けた場合も git 自身に最後の砦を担わせる
    proc = subprocess.run(["git", "worktree", "remove", path],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        # 生きている subagent の worktree は git がロックしている。統合を終えたタスクでも
        # サブリーダーが報告を終えてまだ終了していないことがあるので、他の失敗と区別して
        # 返す（呼び出し側は終了通知を待って再実行すればよい）
        stderr = proc.stderr.strip()
        locked = "locked working tree" in stderr
        emit({
            "removed": False,
            "reason": "locked" if locked else "remove-failed",
            "path": path,
            "detail": stderr,
            "hint": "この worktree を使っている subagent がまだ生きています。終了通知を"
                    "待ってから同じコマンドを再実行してください"
                    if locked else "git worktree remove が失敗しました",
        })
        raise SystemExit(1)
    run(["git", "worktree", "prune"])
    emit({"removed": True, "path": path,
          "authorized_by": "blocked-marker" if blocked else "--merged"})


def cmd_rescue(args):
    path = agent_worktree(args.agent)
    if not os.path.isdir(path):
        die(f"worktree がありません: {path}")

    dirty = out(["git", "-C", path, "status", "--porcelain"])
    if not dirty:
        emit({"rescued": False, "reason": "clean", "path": path})
        return

    run(["git", "-C", path, "add", "-A"])
    run(["git", "-C", path, "commit", "-m", RESCUE_MESSAGE])
    sha = out(["git", "-C", path, "rev-parse", "HEAD"])
    run(["git", "-C", path, "push", "origin", f"HEAD:refs/heads/{args.branch}"])
    emit({
        "rescued": True,
        "path": path,
        "branch": args.branch,
        "commit": sha,
        "files": len(dirty.splitlines()),
        "note": f"検証していない保全コミットです。台帳に 1 行残し、再開するサブリーダーへ"
                f"「前コミット {sha[:12]} は未検証の保全である」と伝えてください",
    }, pretty=True)


def build_parser():
    parser = argparse.ArgumentParser(prog="worktree.py", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("remove", help="残留 worktree を検査してから消す")
    p.add_argument("--agent", required=True, help="サブリーダーの agentId")
    p.add_argument("--merged", action="store_true",
                   help="このタスクを topic へ取り込み終えたことを明示する")
    p.set_defaults(func=cmd_remove)

    p = sub.add_parser("rescue", help="未コミットの変更を保全 commit して push する")
    p.add_argument("--agent", required=True)
    p.add_argument("--branch", required=True, help="push 先のタスクブランチ名")
    p.set_defaults(func=cmd_rescue)

    return parser


if __name__ == "__main__":
    parsed = build_parser().parse_args()
    parsed.func(parsed)
