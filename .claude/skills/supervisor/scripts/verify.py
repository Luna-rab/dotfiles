#!/usr/bin/env python3
"""ワークフローの返り値を、ブランチとコミットの実物で確かめる。

実行中の run に完了通知が誤って発火し、PR 番号もマージも含む捏造レポートが届いた実績が
あるため、リードは報告に基づいて動く前にこれを通す。報告ではなく実物を見るのが要点である。

サブコマンドは無い。1 つの判定しかしないので、引数だけを渡す。

  verify.py --branch <タスクブランチ> --base <起点にしたブランチ>

2 つの検査（ブランチが origin にある / 起点からのコミットが 1 件以上ある）が
両方通ったときだけ終了コード 0 を返す。

`--base` に渡すのは、そのブランチを切った起点である（stacked PR の土台 `stack/<作業名>--task-0` か、
起動時の stacked PR の先頭。state.json の `parent`）。stacked PR へ積んだ後は起点が動くので、この数え方は
積む前にだけ意味がある（../integration.md §1）。

**PR は見ない。** PR は全レビューが決着してからリードが作るので、この検査を叩く時点では
まだ存在しない（../integration.md §2）。**レビューの決着も見ない。** そちらは
`review.py list --dir <ベース>/notes/task<番号> --require-empty` が判定する。
"""

from __future__ import annotations

import argparse
from typing import Any

from lib.shell import die, emit, out, run, warn


def main(args: argparse.Namespace) -> None:
    # fetch は要る 2 ref に絞る。全ブランチの fetch にすると、呼び出し元の stack.py precheck が
    # 直前に fetch したばかりのリポジトリで、無関係ブランチの転送をもう 1 回繰り返すことになる。
    # タスクブランチはまだ origin に無いことがある（それ自体が branch-exists の検査対象）ので、
    # 実在を ls-remote で確かめてから fetch する
    run(["git", "fetch", "origin", args.base])
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
        run(["git", "fetch", "origin", args.branch])
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
        "--base", required=True, help="起点にしたブランチ名（state.json の parent）"
    )
    return parser


if __name__ == "__main__":
    main(build_parser().parse_args())
