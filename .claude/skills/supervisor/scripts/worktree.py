#!/usr/bin/env python3
"""dynamic workflow が残した worktree の後始末。

ワークフローの worktree は `wf_<runId>-<連番>` という名前で作られる。run が終わっても
worktree が残ることがあるので、runId で名指しして消す。

消す機会は 2 つある。

- **走行中**: ワークフロー自身が、役目を終えたエージェントの worktree を `--role-done` で消す
  （task-workflow.js の後始末レーン）。run はまだ続いているので、対象は 1 つずつパスで名指しする。
- **run が終わってから**: リードが決着を確かめて `--settled`、打ち切って `--aborted` で消す。
  走行中に消し切れなかった分がここで片づく。**stacked PR へ積むより先に消す**——`gh stack add` は
  対象ブランチへ HEAD を移すので、worktree が握っているままだと積めない
  （../integration.md §2）。

サブコマンド:
  list    ある run の worktree を挙げ、dirty（未コミットの変更）と locked（走行中）を表示する
  remove  ある run の worktree（またはパスで名指しした分）を、安全条件を検査してから消す
  rescue  落ちた worktree の未コミットの変更を保全 commit して push する

削除と push はどちらも後戻りできない操作なので、実行する前に安全条件を検査する。
**パターンで一括削除しない**——`.claude/worktrees/` 配下を一括 `--force` 除去して、他プロセスの
worktree まで消した事故がある。対象は必ず `git worktree list` に載っていて、かつ名前が runId で
始まるものに限る。
"""

from __future__ import annotations

import argparse
import os
import subprocess
from typing import Any

from lib.gitpath import listed_worktrees, locked_worktrees, run_worktrees
from lib.shell import die, emit, git, out, run

RESCUE_MESSAGE: str = "wip: 中断時点の保全（検証未実施）"


def targets(args: argparse.Namespace) -> list[str]:
    """--run か --path から対象の worktree パスを決める。どちらか一方が必須。

    `--path` は繰り返せる。走行中のワークフローは役目を終えた worktree を数個まとめて渡すので、
    1 回の実行で片づけられるようにしてある。
    """
    if args.run and args.path:
        die("--run と --path は同時に指定できません")
    if args.run:
        return run_worktrees(args.run)
    if args.path:
        return [os.path.realpath(p) for p in args.path]
    die("--run か --path のどちらかを指定してください")


def inspect(
    path: str, locks: dict[str, str] | None = None, listed: set[str] | None = None
) -> dict[str, Any]:
    """1 つの worktree の状態を調べる。`git worktree list` に無ければ gone。

    `locked` は「まだ生きているエージェントが掴んでいる」しるしなので、`dirty` とは別の
    キーで返す。**リードはこれを run の生死の手がかりに使う**（../integration.md §2 手順 2）。
    生きている run の worktree を消すと、走っているエージェントの足元を抜くことになる。

    `locks` と `listed` は呼び出し元が 1 回だけ取って渡す（worktree ごとに
    `git worktree list --porcelain` を叩き直さないため）。
    """
    listed = listed_worktrees() if listed is None else listed
    if path not in listed:
        return {"path": path, "state": "gone", "locked": False}
    locks = locked_worktrees() if locks is None else locks
    locked = {"locked": path in locks}
    if path in locks:
        locked["lock_reason"] = locks[path]
    if not os.path.isdir(path):
        # 登録は残っているが実体が無い（エージェントが自分のツリーを消した、外から rm された）
        return {"path": path, "state": "stale-registration", **locked}
    dirty = out(["git", "-C", path, "status", "--porcelain"])
    return {
        "path": path,
        "state": "dirty" if dirty else "clean",
        "changes": len(dirty.splitlines()),
        **locked,
    }


def cmd_list(args: argparse.Namespace) -> None:
    found = targets(args)
    locks = locked_worktrees()
    known = listed_worktrees()
    rows = [inspect(p, locks, known) for p in found]
    emit(
        {
            "run": args.run,
            "count": len(found),
            "locked": sum(1 for w in rows if w["locked"]),
            "worktrees": rows,
        },
        pretty=True,
    )


LOCKED_HINT: str = (
    "この worktree を使っているエージェントがまだ生きています。"
    "run の完了通知を待ってから同じコマンドを再実行してください"
)


def authorization(args: argparse.Namespace) -> str:
    """どの資格で消すのかを 1 つに確定する。

    消してよいのは、役目を終えたエージェントの worktree（走行中）と、決着したタスク・
    打ち切ったタスクの worktree（run の後）だけである。スクリプトからはどれなのか判別できない
    （state.json とタスクリストにしかない）ので、呼ぶ側が明示する。
    """
    given = [
        name
        for name, on in (
            ("--settled", args.settled),
            ("--aborted", args.aborted),
            ("--role-done", args.role_done),
        )
        if on
    ]
    if len(given) != 1:
        die(
            "--role-done（このエージェントが結果を返して役目を終えた）か "
            "--settled（precheck が通ってレビューが全件決着した）か --aborted（打ち切った）を"
            " 1 つだけ指定してください。まだ決着を待っている worktree は消しません"
        )
    if not args.branch:
        # --settled / --aborted でも省かせない。省くと unpushed の検査（`blocking()`）が
        # 働かず、push だけ失敗した clean な worktree の唯一のコミットを黙って消す
        die(
            "remove には --branch <タスクブランチ> が必要です。"
            "HEAD がそのブランチのリモート側に含まれていること（この worktree にしか無い"
            "コミットが 1 つも無いこと）を確かめてから消します"
        )
    return given[0]


def ancestor(path: str, ref: str) -> bool:
    """path の HEAD が ref に含まれているか。ref が無いときも False を返す。"""
    code, _ = git(path, ["merge-base", "--is-ancestor", "HEAD", ref], allow_fail=True)
    return code == 0


def pushed(path: str, branch: str) -> bool:
    """この worktree の HEAD が `origin/<branch>` に含まれているか。

    含まれていれば、この worktree にしか無いコミットは 1 つも無い——消しても失われるものが
    無い、という判定である。remote-tracking ref は worktree 間で共有されるので、エージェント
    自身の `git push origin HEAD:refs/heads/<branch>` で更新済みになっている。含まれていない
    ときだけ 1 度 fetch して見直す（push が別経路で行われた場合に備える）。
    """
    ref = f"refs/remotes/origin/{branch}"
    if ancestor(path, ref):
        return True
    git(path, ["fetch", "origin", branch], allow_fail=True)
    return ancestor(path, ref)


def refuse(reason: str, path: str, hint: str, **extra: Any) -> dict[str, Any]:
    """消さなかったことと、その理由・次の手を返す。"""
    return {"removed": False, "reason": reason, "path": path, "hint": hint, **extra}


def blocking(
    path: str, info: dict[str, Any], authorized_by: str, branch: str | None
) -> dict | None:
    """消してはいけない条件に当たっていないかを見る。当たらなければ None を返す。

    条件はどれも「消すと失われるものがある」か「まだ使われている」のどちらかである。
    """
    # ロックが残っているものには触らない。`git worktree prune` もロック済みは飛ばすので、
    # 実体が無くても「消した」と報告できない
    if info["locked"]:
        return refuse("locked", path, LOCKED_HINT, detail=info.get("lock_reason", ""))

    # 走行中に消すのはワークフローが作った worktree（`wf_<runId>-<連番>`）だけである。
    # エージェントは自分の worktree のパスを自己申告で返すので、`isolation: 'worktree'` が
    # 効かずにメイン側で走った体はスタックツリーのパスを返す。そのまま消すと state.json と
    # 引き継ぎノートが道連れになる（design-notes.md「なぜ worktree をワークフローの中で消すか」）
    if authorized_by == "--role-done" and not os.path.basename(path).startswith("wf_"):
        return refuse(
            "not-a-workflow-tree",
            path,
            "走行中に消せるのは名前が wf_ で始まる worktree だけです。"
            "エージェントが自分の worktree ではないパスを返した可能性があります",
        )

    if info["state"] == "dirty":
        # 未コミットの成果ごと消さない。先に rescue で保全させる
        return refuse(
            "dirty",
            path,
            f"worktree.py rescue --path {path} --branch <タスクブランチ> で先に保全してください",
            changes=info["changes"],
        )

    if info["state"] == "clean" and branch and not pushed(path, branch):
        # この worktree にしか無いコミットがある。push 漏れか、起点が origin/<branch> の系列に
        # 無いかのどちらかで、どちらも「消すと追えなくなる」側に転ぶ。--settled / --aborted でも
        # 消さない——push だけ失敗して clean になった worktree の唯一のコミットを守る
        return refuse(
            "unpushed",
            path,
            f"HEAD が origin/{branch} に含まれていません。"
            f"worktree.py list --path {path} で中身を見て、残すコミットなら "
            f"`git -C {path} push origin HEAD:refs/heads/{branch}` で push してから"
            "消してください",
            head=out(["git", "-C", path, "rev-parse", "HEAD"])[:12],
        )

    return None


def remove_one(
    path: str,
    locks: dict[str, str],
    listed: set[str],
    authorized_by: str,
    branch: str | None,
) -> dict:
    """1 つの worktree を、安全条件を検査してから消す。

    返り値の `removed` が False なら消していない。**呼び出し元は `--force` で押し切らない**——
    拒んだ理由は `reason` と `hint` に入る。
    """
    info = inspect(path, locks, listed)

    if info["state"] == "gone":
        run(["git", "worktree", "prune"])
        return {"removed": False, "reason": "already-gone", "path": path}

    refused = blocking(path, info, authorized_by, branch)
    if refused:
        return refused

    if info["state"] == "stale-registration":
        run(["git", "worktree", "prune"])
        return {
            "removed": True,
            "reason": "pruned-stale-registration",
            "path": path,
            "authorized_by": authorized_by,
        }

    # --force を付けない。上の検査を抜けた場合も git 自身に最後の砦を担わせる
    # check=False は意図的である。git が拒んだ理由（ロック中か、それ以外か）を
    # stderr から読んで返し分けるので、例外にせず終了コードで受ける
    proc = subprocess.run(
        ["git", "worktree", "remove", path], capture_output=True, text=True, check=False
    )
    if proc.returncode != 0:
        # 生きているエージェントの worktree は git がロックしている。run がまだ終わって
        # いないので、他の失敗と区別して返す（呼び出し側は完了通知を待って再実行する）
        stderr = proc.stderr.strip()
        locked = "locked working tree" in stderr
        return refuse(
            "locked" if locked else "remove-failed",
            path,
            LOCKED_HINT if locked else "git worktree remove が失敗しました",
            detail=stderr,
        )

    return {"removed": True, "path": path, "authorized_by": authorized_by}


def cmd_remove(args: argparse.Namespace) -> None:
    authorized_by = authorization(args)
    locks = locked_worktrees()
    listed = listed_worktrees()
    results: list[dict[str, Any]] = [
        remove_one(path, locks, listed, authorized_by, args.branch) for path in targets(args)
    ]

    run(["git", "worktree", "prune"])
    emit({"run": args.run, "results": results}, pretty=True)
    # 「消せなかった」を終了コードに出す。already-gone は数えない（結果は同じである）
    failed = [r for r in results if not r["removed"] and r["reason"] != "already-gone"]
    raise SystemExit(1 if failed else 0)


def cmd_rescue(args: argparse.Namespace) -> None:
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
    emit(
        {
            "rescued": True,
            "path": path,
            "branch": args.branch,
            "commit": sha,
            "files": len(dirty.splitlines()),
            "note": f"検証していない保全コミットです。台帳に 1 行残し、立て直すワークフローの "
            f"resumeFrom.sha に {sha[:12]} を渡すときは「未検証の保全である」と添えてください",
        },
        pretty=True,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="worktree.py", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def add_targets(p: argparse.ArgumentParser) -> None:
        p.add_argument("--run", help="ワークフローの runId（例: wf_a1b2c3d4e5f6）")
        p.add_argument(
            "--path", action="append", help="worktree の絶対パス（繰り返すと複数を扱う）"
        )

    p = sub.add_parser(
        "list", help="ある run の worktree を挙げ、dirty と locked（走行中）を表示する"
    )
    add_targets(p)
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("remove", help="残留 worktree を検査してから消す")
    add_targets(p)
    p.add_argument(
        "--role-done",
        action="store_true",
        help="この worktree を使ったエージェントが結果を返して役目を終えたことを明示する"
        "（run はまだ続いている）。--branch が必須",
    )
    p.add_argument(
        "--settled",
        action="store_true",
        help="このタスクの precheck が通り、レビューが全件決着したことを明示する",
    )
    p.add_argument("--aborted", action="store_true", help="このタスクを打ち切ったことを明示する")
    p.add_argument(
        "--branch",
        help="タスクブランチ名。HEAD がその origin 側に含まれていることを確かめる（必須。"
        "無いと unpushed の検査が働かず、push できていないコミットごと消してしまう）",
    )
    p.set_defaults(func=cmd_remove)

    p = sub.add_parser("rescue", help="未コミットの変更を保全 commit して push する")
    p.add_argument("--path", required=True, help="worktree の絶対パス")
    p.add_argument("--branch", required=True, help="push 先のタスクブランチ名")
    p.set_defaults(func=cmd_rescue)

    return parser


if __name__ == "__main__":
    parsed = build_parser().parse_args()
    parsed.func(parsed)
