#!/usr/bin/env python3
"""dynamic workflow が残した worktree の後始末。

ワークフローの worktree は `wf_<runId>-<連番>` という名前で作られる。正常に終わった run でも
残ることがあるので、リードが統合を終えてから runId で名指しして消す。

サブコマンド:
  list    ある run の worktree を挙げ、dirty（未コミットの変更）と locked（走行中）を表示する
  remove  ある run の worktree（または 1 つのパス）を、安全条件を検査してから消す
  rescue  落ちた worktree の未コミットの変更を保全 commit して push する

削除と push はどちらも後戻りできない操作なので、実行する前に安全条件を検査する。
**パターンで一括削除しない**——`.claude/worktrees/` 配下を一括 `--force` 除去して、他プロセスの
worktree まで消した事故がある。対象は必ず `git worktree list` に載っていて、かつ名前が runId で
始まるものに限る。
"""

import argparse
import os
import subprocess

from lib.gitpath import listed_worktrees, locked_worktrees, run_worktrees
from lib.shell import die, emit, out, run

RESCUE_MESSAGE = "wip: 中断時点の保全（検証未実施）"


def targets(args):
    """--run か --path から対象の worktree パスを決める。どちらか一方が必須。"""
    if args.run and args.path:
        die("--run と --path は同時に指定できません")
    if args.run:
        return run_worktrees(args.run)
    if args.path:
        return [os.path.realpath(args.path)]
    die("--run か --path のどちらかを指定してください")


def inspect(path, locks=None):
    """1 つの worktree の状態を調べる。`git worktree list` に無ければ gone。

    `locked` は「まだ生きているエージェントが掴んでいる」しるしなので、`dirty` とは別の
    キーで返す。**リードはこれを run の生死の手がかりに使う**（integration.md §4 手順 1）。
    生きている run の worktree を消すと、走っているエージェントの足元を抜くことになる。
    """
    if path not in listed_worktrees():
        return {"path": path, "state": "gone", "locked": False}
    locks = locked_worktrees() if locks is None else locks
    locked = {"locked": path in locks}
    if path in locks:
        locked["lock_reason"] = locks[path]
    if not os.path.isdir(path):
        # 登録は残っているが実体が無い（エージェントが自分のツリーを消した、外から rm された）
        return {"path": path, "state": "stale-registration", **locked}
    dirty = out(["git", "-C", path, "status", "--porcelain"])
    return {"path": path, "state": "dirty" if dirty else "clean",
            "changes": len(dirty.splitlines()), **locked}


def cmd_list(args):
    found = targets(args)
    locks = locked_worktrees()
    listed = [inspect(p, locks) for p in found]
    emit({"run": args.run, "count": len(found),
          "locked": sum(1 for w in listed if w["locked"]),
          "worktrees": listed}, pretty=True)


def cmd_remove(args):
    # 消してよいのは、統合を終えたタスクと、打ち切ったタスクだけ。スクリプトからは判別
    # できない（台帳とタスクリストにしかない）のでリードが明示する
    if not args.merged and not args.aborted:
        die("--merged（topic へ取り込み終えた）か --aborted（打ち切った）を明示してください。"
            "まだ承認を待っている worktree は消しません")
    authorized_by = "--merged" if args.merged else "--aborted"

    results, failed = [], False
    locks = locked_worktrees()
    for path in targets(args):
        info = inspect(path, locks)

        if info["state"] == "gone":
            run(["git", "worktree", "prune"])
            results.append({"removed": False, "reason": "already-gone", "path": path})
            continue

        # ロックが残っているものには触らない。`git worktree prune` もロック済みは飛ばすので、
        # 実体が無くても「消した」と報告できない
        if info["locked"]:
            results.append({
                "removed": False, "reason": "locked", "path": path,
                "detail": info.get("lock_reason", ""),
                "hint": "この worktree を使っているエージェントがまだ生きています。run の"
                        "完了通知を待ってから同じコマンドを再実行してください",
            })
            failed = True
            continue

        if info["state"] == "stale-registration":
            run(["git", "worktree", "prune"])
            results.append({"removed": True, "reason": "pruned-stale-registration",
                            "path": path, "authorized_by": authorized_by})
            continue

        if info["state"] == "dirty":
            # 未コミットの成果ごと消さない。先に rescue で保全させる
            results.append({"removed": False, "reason": "dirty", "path": path,
                            "changes": info["changes"],
                            "hint": f"worktree.py rescue --path {path} "
                                    "--branch <タスクブランチ> で先に保全してください"})
            failed = True
            continue

        # --force を付けない。上の検査を抜けた場合も git 自身に最後の砦を担わせる
        proc = subprocess.run(["git", "worktree", "remove", path],
                              capture_output=True, text=True)
        if proc.returncode != 0:
            # 生きているエージェントの worktree は git がロックしている。run がまだ終わって
            # いないので、他の失敗と区別して返す（呼び出し側は完了通知を待って再実行する）
            stderr = proc.stderr.strip()
            locked = "locked working tree" in stderr
            results.append({
                "removed": False,
                "reason": "locked" if locked else "remove-failed",
                "path": path,
                "detail": stderr,
                "hint": "この worktree を使っているエージェントがまだ生きています。run の"
                        "完了通知を待ってから同じコマンドを再実行してください"
                        if locked else "git worktree remove が失敗しました",
            })
            failed = True
            continue

        results.append({"removed": True, "path": path, "authorized_by": authorized_by})

    run(["git", "worktree", "prune"])
    emit({"run": args.run, "results": results}, pretty=True)
    raise SystemExit(1 if failed else 0)


def cmd_rescue(args):
    path = os.path.realpath(args.path)
    if path not in listed_worktrees():
        die(f"`git worktree list` に載っていません: {path}")
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
        "note": f"検証していない保全コミットです。台帳に 1 行残し、立て直すワークフローの "
                f"resumeFrom.sha に {sha[:12]} を渡すときは「未検証の保全である」と添えてください",
    }, pretty=True)


def build_parser():
    parser = argparse.ArgumentParser(prog="worktree.py", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def add_targets(p):
        p.add_argument("--run", help="ワークフローの runId（例: wf_a1b2c3d4e5f6）")
        p.add_argument("--path", help="worktree の絶対パス（1 件だけ扱うとき）")

    p = sub.add_parser("list",
                       help="ある run の worktree を挙げ、dirty と locked（走行中）を表示する")
    add_targets(p)
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("remove", help="残留 worktree を検査してから消す")
    add_targets(p)
    p.add_argument("--merged", action="store_true",
                   help="このタスクを topic へ取り込み終えたことを明示する")
    p.add_argument("--aborted", action="store_true",
                   help="このタスクを打ち切ったことを明示する")
    p.set_defaults(func=cmd_remove)

    p = sub.add_parser("rescue", help="未コミットの変更を保全 commit して push する")
    p.add_argument("--path", required=True, help="worktree の絶対パス")
    p.add_argument("--branch", required=True, help="push 先のタスクブランチ名")
    p.set_defaults(func=cmd_rescue)

    return parser


if __name__ == "__main__":
    parsed = build_parser().parse_args()
    parsed.func(parsed)
