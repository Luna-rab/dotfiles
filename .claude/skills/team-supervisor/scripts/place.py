#!/usr/bin/env python3
"""実装 subagent の作業場所と、ベース 3 資料の置き場を 1 行で答える。

サブコマンド:
  where     実装 subagent が戻るべき worktree の絶対パスか `STAY` を 1 行返す
  base-dir  brief.md / map.md / ledger.md を置くディレクトリの絶対パスを 1 行返す

どちらも stdout に 1 行（`STAY` か絶対パス）だけを出す。EnterWorktree(path:) や Read に
そのまま渡せるようにするためである。

**どちらも worktree を作らない。** 以前は subleader / review 用に `git worktree add` して
いたが、`isolation: "worktree"` で起動した subagent は Bash が起動時の worktree に固定され、
新しく作った worktree ではコマンドを 1 つも実行できないので、作った実体が使えないまま
残っていた。作用があるのは `base-dir` が `--require` 無しのときにディレクトリを作ることだけ
である。
"""

import argparse
import os

from lib.gitpath import BASE_FILES, base_dir, in_main_worktree
from lib.shell import die

# where が扱う役割。`isolation` を付けずに起動する実装 subagent だけが対象である
# （isolation 付きの subagent は再開しても自分の worktree のままなので、聞く必要がない）
WHERE_ROLES = ("impl",)


def cmd_where(args):
    """実装 subagent がどこで作業すべきかを 1 行返す。

    対象が実装 subagent だけなのは、`isolation` を付けずに起動する唯一の subagent だから
    である。`isolation: "worktree"` 付きの subagent（サブリーダー・レビュアー）は再開しても
    自分の worktree のままで、Bash がそこに固定されるので、聞く必要がない。`--role` に
    `impl` 以外を渡すと argparse が弾く。
    """
    # 実装 subagent はサブリーダーの worktree を共有する。新しくは作らない
    if not in_main_worktree():
        print("STAY")
        return
    if not args.parent_worktree:
        die(
            "メインの作業ツリーに再開されましたが --parent-worktree が渡されていません。"
            "コードを 1 行も書かずにサブリーダーへ報告して終えてください"
        )
    target = os.path.abspath(args.parent_worktree)
    if not os.path.isdir(target):
        die(f"--parent-worktree のパスがありません: {target}")
    print(target)


def cmd_base_dir(args):
    """ベース 3 資料の置き場を 1 行返す。読み手と書き手が同じ式でパスを求めるための入口。

    `--require` は 3 ファイルの実在を確かめる。ベース資料を git の外に出したので、
    `git checkout` では揃わない——揃っていないことに気づかず走り出すと、検証コマンドも
    不可侵パスも知らないまま実装が始まる。読み手（サブリーダー・レビュアー）は
    `--require` を付けて呼び、非 0 なら親へ報告して止まる。
    """
    target = base_dir(args.work)
    if args.require:
        missing = [n for n in BASE_FILES
                   if not os.path.isfile(os.path.join(target, n))]
        if missing:
            die(
                f"{target} に {', '.join(missing)} がありません。"
                "ベース資料は git の追跡対象外なので checkout では揃いません。"
                "実装・検証に入らず、リード（サブリーダーなら親）へ報告して止まってください。"
            )
    else:
        os.makedirs(target, exist_ok=True)
    print(target)


def build_parser():
    parser = argparse.ArgumentParser(prog="place.py", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser(
        "where",
        help="実装 subagent が戻るべき worktree の絶対パスか STAY を 1 行返す",
    )
    p.add_argument("--role", required=True, choices=WHERE_ROLES,
                   help="impl のみ。isolation 付きの subagent は聞く必要がない")
    p.add_argument("--parent-worktree",
                   help="サブリーダーの worktree の絶対パス（メインツリーにいるとき必須）")
    p.set_defaults(func=cmd_where)

    p = sub.add_parser("base-dir", help="ベース 3 資料を置くディレクトリを 1 行返す")
    p.add_argument("--work", required=True, help="作業名（ディレクトリ区切りを含めない）")
    p.add_argument("--require", action="store_true",
                   help="3 ファイルの実在を確かめ、欠けていたら終了コード 1")
    p.set_defaults(func=cmd_base_dir)

    return parser


if __name__ == "__main__":
    parsed = build_parser().parse_args()
    parsed.func(parsed)
