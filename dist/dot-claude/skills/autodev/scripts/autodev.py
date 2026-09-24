#!/usr/bin/env python3
"""autodev——指示 1 つを stacked PR まで無人で持っていく driver。

**進行は driver が持つ。** run 1 回をどう回すかは `autodevlib/app/drive.py`、どの段を
何回呼ぶか・いつ打ち切るか・git と gh をいつ叩くかは `autodevlib/app/` の各ファイルに
ある。モデルが決めるのは各段の中身だけである。

    skill が 1 回起動
      └ 前段（読む）  : worktree を切る / brief.md と map.md を置く
      └ 計画段        : 受入条件と DoD を確定し、1 PR に収まるか判定して割る
      └ 後段（書く）  : 土台ブランチと draft PR を作る
      └ タスクごとに順に:
           テスト作成 → 実装 → レビュー → 裁定 → 修正 → …（上限 3 / 無進捗で打ち切り）
           6 検査 → PR 本文 → push → gh pr create → gh stack link
      └ 全部積んだら土台を ready にして人間に渡す

**マージはしない。** 人間がレビューして `gh stack merge` で下から行う。

**進めなくなったら、理由を終了コードと `autodev status` に載せて終わる。** 計画を引き直して
呼び直すかどうかを決めるのは、この driver を起動した側（`/autodev` skill を務める
エージェント）である。**driver は自分が呼び直されるかどうかを決めない。**

    0  全部積んだ
    1  走れなかった（起動前の確認・git・gh の失敗）
    2  計画段が blocked。前提が崩れているので指示を書き直す
    3  残課題がある（blocked / failed のタスクがある）
    4  段が答えを待って止まっている。`autodev answer` で答えを置いてから呼び直す

skill が呼ぶサブコマンド（`~/.claude/skills/autodev/scripts/autodev.py` として起動する）:

    autodev.py run --work add-cache --repo ~/ghq/github.com/foo/bar --instruction "…"
    autodev.py run --work add-cache          # 続きから（state.json があれば再開）
    autodev.py status --work add-cache
    autodev.py list
    autodev.py clean --work add-cache

段が呼ぶサブコマンド:

    autodev.py review init|new|comment|status|list|done --dir <タスクのディレクトリ> …
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from autodevlib import cli
from autodevlib.ports import review_store


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="autodev",
        description="指示 1 つを stacked PR まで無人で持っていく driver",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run_cmd = sub.add_parser("run", help="run を始める・続ける")
    run_cmd.add_argument("--work", required=True, help="作業名（英小文字・数字・ハイフン）")
    run_cmd.add_argument("--repo", help="対象リポジトリ（既定はカレント）")
    run_cmd.add_argument("--base", help="base ブランチ（既定は origin の既定ブランチ）")
    run_cmd.add_argument("--instruction", help="指示。`-` で標準入力")
    run_cmd.set_defaults(func=cli.cmd_run)

    status_cmd = sub.add_parser("status", help="run の状態を出す")
    status_cmd.add_argument("--work", required=True)
    status_cmd.set_defaults(func=cli.cmd_status)

    sub.add_parser("list", help="run を一覧する").set_defaults(func=cli.cmd_list)

    clean_cmd = sub.add_parser("clean", help="worktree を外す（記録は残す）")
    clean_cmd.add_argument("--work", required=True)
    clean_cmd.set_defaults(func=cli.cmd_clean)

    ask_cmd = sub.add_parser("ask", help="段が使う。答えが置かれるまで段が止まる")
    ask_cmd.add_argument("--id", required=True, help="質問の鍵（英数字・ハイフン）")
    ask_cmd.add_argument("--question", help="聞きたいこと")
    ask_cmd.set_defaults(func=cli.cmd_ask)

    answer_cmd = sub.add_parser("answer", help="止まっている段に答えを置く")
    answer_cmd.add_argument("--work", required=True)
    answer_cmd.add_argument("--id", required=True, help="質問の鍵")
    answer_cmd.add_argument("--body", help="答え")
    answer_cmd.add_argument("--body-file", help="答えのファイル。`-` で標準入力")
    answer_cmd.set_defaults(func=cli.cmd_answer)

    review_cmd = sub.add_parser("review", help="段が使うレビュー記録の読み書き")
    review_cmd.add_argument("action", choices=("init", "new", "comment", "status", "list", "done"))
    review_cmd.add_argument("--dir", required=True, help="タスクのディレクトリ")
    review_cmd.add_argument("--reviewer", choices=review_store.REVIEWERS)
    review_cmd.add_argument("--commenter", choices=review_store.COMMENTERS)
    review_cmd.add_argument("--rating", choices=review_store.RATINGS)
    review_cmd.add_argument("--location", help="path:line の形")
    review_cmd.add_argument("--id", help="レビューの id（r1 など）")
    review_cmd.add_argument("--to", choices=review_store.STATUSES)
    review_cmd.add_argument("--round", default="1")
    review_cmd.add_argument("--found", type=int, default=0)
    review_cmd.add_argument("--body", help="本文")
    review_cmd.add_argument("--body-file", help="本文のファイル。`-` で標準入力")
    review_cmd.add_argument("--all", action="store_true", help="list で決着済みも出す")
    review_cmd.set_defaults(func=cli.cmd_review)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
