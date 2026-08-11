#!/usr/bin/env python3
"""ワークフローの承認の返り値を、ブランチ・コミット・PR の実物で確かめる。

実行中の run に完了通知が誤って発火し、PR 番号もマージも含む捏造レポートが届いた実績が
あるため、リードは報告に基づいて動く前にこれを通す。報告ではなく実物を見るのが要点である。

サブコマンドは無い。1 つの判定しかしないので、引数だけを渡す。

  verify.py --branch <タスクブランチ> --base <topic/作業名> --pr <PR 番号>

4 つの検査（ブランチが origin にある / base からのコミットが 1 件以上ある / PR の head と
base が報告どおり / PR が OPEN）が全部通ったときだけ終了コード 0 を返す。

**GitHub のレビュー状態は見ない。** 未解決スレッドと提出済みレビューの判定は
`gh-review.py gate` が持つ（integration.md §1）。
"""

import argparse
import json

from lib.shell import die, emit, out, run, warn


def main(args):
    run(["git", "fetch", "origin"])
    checks = []

    code, _ = run(
        ["git", "ls-remote", "--exit-code", "--heads", "origin",
         f"refs/heads/{args.branch}"],
        allow_fail=True,
    )
    branch_exists = code == 0
    checks.append({"check": "branch-exists", "ok": branch_exists,
                   "detail": args.branch})

    base_code, _ = run(
        ["git", "rev-parse", "--verify", "--quiet", f"origin/{args.base}"],
        allow_fail=True,
    )
    if base_code != 0:
        die(f"base ブランチ origin/{args.base} がありません（fetch 後に確認）")

    commits = None
    if branch_exists:
        commits = int(out(["git", "rev-list", "--count",
                           f"origin/{args.base}..origin/{args.branch}"]))
        checks.append({"check": "commits-on-branch", "ok": commits > 0,
                       "detail": commits})

    pr = json.loads(out(["gh", "pr", "view", str(args.pr),
                         "--json", "state,headRefName,baseRefName"]))
    checks.append({"check": "pr-head", "ok": pr["headRefName"] == args.branch,
                   "detail": pr["headRefName"]})
    checks.append({"check": "pr-base", "ok": pr["baseRefName"] == args.base,
                   "detail": pr["baseRefName"]})
    # 取り込み前なので OPEN であるべき。MERGED なら既に入っている可能性があるので、
    # git merge-base --is-ancestor で二重取り込みかどうかを確かめる（integration.md §3）
    checks.append({"check": "pr-open", "ok": pr["state"] == "OPEN",
                   "detail": pr["state"]})

    clean = all(c["ok"] for c in checks)
    emit({"clean": clean, "branch": args.branch, "base": args.base,
          "pr": args.pr, "commits": commits, "pr_state": pr["state"],
          "checks": checks}, pretty=True)
    if not clean:
        warn("実物と返り値が合いません。取り込まずに、resumeFrom を付けてワークフローを"
             "起動し直してください")
    raise SystemExit(0 if clean else 1)


def build_parser():
    parser = argparse.ArgumentParser(prog="verify.py", description=__doc__)
    parser.add_argument("--branch", required=True, help="タスクブランチ名")
    parser.add_argument("--base", required=True, help="base ブランチ名（topic/<作業名>）")
    parser.add_argument("--pr", required=True, help="PR 番号")
    return parser


if __name__ == "__main__":
    main(build_parser().parse_args())
