#!/usr/bin/env python3
"""ベース資料と引き継ぎノートの置き場を 1 行で答える。

サブコマンド:
  base-dir  brief.md / map.md / ledger.md と notes/ を置くディレクトリの絶対パスを 1 行返す

stdout に絶対パスを 1 行だけ出す。Read や `mkdir -p` にそのまま渡せるようにするためである。

**worktree は作らない。** ワークフローの `agent()` は `isolation: 'worktree'` で自分の worktree を
割り当てられ、その中に固定される。作用があるのは `--require` 無しのときで、ディレクトリと、
それを git から隠す `.gitignore` を作る。
"""

from __future__ import annotations

import argparse
import os

from lib.gitpath import (
    BASE_FILES,
    GITIGNORE,
    base_dir,
    integration_tree,
    listed_worktrees,
)
from lib.shell import die


def cmd_base_dir(args: argparse.Namespace) -> None:
    """ベース資料の置き場を 1 行返す。読み手と書き手が同じ式でパスを求めるための入口。

    `--require` は 3 ファイルの実在を確かめる。ベース資料を git の外に出したので、
    `git checkout` では揃わない——揃っていないことに気づかず走り出すと、検証コマンドも
    不可侵パスも知らないまま実装が始まる。読み手（実装・レビュー・裁定）は `--require` を
    付けて呼び、非 0 なら親へ報告して止まる。
    """
    target = base_dir(args.work)
    if args.require:
        missing = [n for n in BASE_FILES if not os.path.isfile(os.path.join(target, n))]
        if missing:
            die(
                f"{target} に {', '.join(missing)} がありません。"
                "ベース資料は git の追跡対象外なので checkout では揃いません。"
                "実装・検証に入らず、blocked で返して止まってください。"
            )
    else:
        tree = integration_tree(args.work)
        if os.path.realpath(tree) not in listed_worktrees():
            die(
                f"{tree} が `git worktree list` にありません。ベース資料は統合ツリーの中に置くので、"
                "先に統合ツリーを作ってください（SKILL.md 「1. topic と統合ツリーを作る」）。"
                "先にこのディレクトリを作ると、あとの `git worktree add` が既存ディレクトリで失敗します。"
            )
        os.makedirs(target, exist_ok=True)
        ignore = os.path.join(target, ".gitignore")
        if not os.path.exists(ignore):
            with open(ignore, "w", encoding="utf-8") as fh:
                fh.write(GITIGNORE)
    print(target)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="place.py", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("base-dir", help="ベース資料と notes/ を置くディレクトリを 1 行返す")
    p.add_argument("--work", required=True, help="作業名（ディレクトリ区切りを含めない）")
    p.add_argument(
        "--require", action="store_true", help="3 ファイルの実在を確かめ、欠けていたら終了コード 1"
    )
    p.set_defaults(func=cmd_base_dir)

    return parser


if __name__ == "__main__":
    parsed = build_parser().parse_args()
    parsed.func(parsed)
