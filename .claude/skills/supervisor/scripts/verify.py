#!/usr/bin/env python3
"""ワークフローの返り値を、ブランチとコミットの実物で確かめる。

実行中の run に完了通知が誤って発火し、PR 番号もマージも含む捏造レポートが届いた実績が
あるため、リードは報告に基づいて動く前にこれを通す。報告ではなく実物を見るのが要点である。

サブコマンドは無い。1 つの判定しかしないので、引数だけを渡す。

  verify.py --branch <タスクブランチ> --base <topic/作業名>

2 つの検査（ブランチが origin にある / base からのコミットが 1 件以上ある）が
両方通ったときだけ終了コード 0 を返す。

**PR は見ない。** PR はレビューが全件決着してからリードが作るので、この時点では存在しない
（integration.md）。**レビューの決着も見ない。** そちらは
`review.py list --dir <ベース>/notes/task<番号> --require-empty` が判定する。
"""

from __future__ import annotations

import argparse
from typing import Any

from lib.shell import die, emit, out, run, warn


def main(args: argparse.Namespace) -> None:
    run(["git", "fetch", "origin"])
    checks: list[dict[str, Any]] = []

    code, _ = run(
        ["git", "ls-remote", "--exit-code", "--heads", "origin", f"refs/heads/{args.branch}"],
        allow_fail=True,
    )
    branch_exists = code == 0
    checks.append({"check": "branch-exists", "ok": branch_exists, "detail": args.branch})

    base_code, _ = run(
        ["git", "rev-parse", "--verify", "--quiet", f"origin/{args.base}"],
        allow_fail=True,
    )
    if base_code != 0:
        die(f"base ブランチ origin/{args.base} がありません（fetch 後に確認）")

    commits = None
    if branch_exists:
        commits = int(
            out(["git", "rev-list", "--count", f"origin/{args.base}..origin/{args.branch}"])
        )
        checks.append({"check": "commits-on-branch", "ok": commits > 0, "detail": commits})

    clean = all(c["ok"] for c in checks)
    emit(
        {
            "clean": clean,
            "branch": args.branch,
            "base": args.base,
            "commits": commits,
            "checks": checks,
        },
        pretty=True,
    )
    if not clean:
        warn(
            "実物と返り値が合いません。積まずに、resumeFrom を付けてワークフローを"
            "起動し直してください"
        )
    raise SystemExit(0 if clean else 1)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="verify.py", description=__doc__)
    parser.add_argument("--branch", required=True, help="タスクブランチ名")
    parser.add_argument(
        "--base", required=True, help="起点にした base ブランチ名（topic/<作業名>）"
    )
    return parser


if __name__ == "__main__":
    main(build_parser().parse_args())
