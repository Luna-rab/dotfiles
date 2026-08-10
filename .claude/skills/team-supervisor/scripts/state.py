#!/usr/bin/env python3
"""リードと SubagentStop フックが共有する状態ファイルの読み書き。

置き場は `.git/team-supervisor/` で、`scripts/subagent-stop.sh` が同じ式で求める。
リードがシェルで直に書かずこのスクリプトを通すのは、パスの求め方を 2 か所に書かないため、
および `resume` の上限判定をリードの手順書ではなく終了コードで効かせるためである。

サブコマンド:
  init    状態ディレクトリ直下のファイルを消す（ベース資料の base/ は残す）
  branch  branch-<agentId> を書く（SubagentStop フックが読むブランチ名）
  resume  resume-count-task<番号> を加算する（上限を超えたら終了コード 1）
  clear   resume-count-task<番号> を消す
  block   blocked-<agentId> を作る（押し戻しと再開を止める目印）
"""

import argparse
import os

from lib.gitpath import ensure_state_dir, state_dir
from lib.shell import emit, warn

# リードが同じサブリーダーを SendMessage で再開してよい回数（SKILL.md §7）
RESUME_LIMIT = 3


def cmd_init(args):
    sd = ensure_state_dir()
    # 前回の実行が残したカウンタ・目印・ブランチ登録が新しい実行の判定を狂わせないよう、
    # タスクを登録する前に消す。**ディレクトリごと消してはならない**——同じ場所の
    # base/<作業名>/ にベース 3 資料があり、init はそれを書いた後に走る（SKILL.md §6）
    removed = []
    for name in sorted(os.listdir(sd)):
        path = os.path.join(sd, name)
        if os.path.isfile(path):
            os.remove(path)
            removed.append(name)
    emit({"state_dir": sd, "removed": removed,
          "kept": [n for n in sorted(os.listdir(sd)) if os.path.isdir(os.path.join(sd, n))]})


def cmd_branch(args):
    target = os.path.join(ensure_state_dir(), f"branch-{args.agent}")
    with open(target, "w", encoding="utf-8") as fh:
        fh.write(args.branch)
    emit({"wrote": target, "branch": args.branch})


def cmd_resume(args):
    """再開する前に呼ぶ。上限を超えていたら終了コード 1 で止める。

    加算だけをシェルで行っていた形では、上限との比較がリードの手順書にしかなく飛ばせた。
    """
    target = os.path.join(ensure_state_dir(), f"resume-count-task{args.task}")
    try:
        with open(target, encoding="utf-8") as fh:
            current = int(fh.read().strip())
    except (OSError, ValueError):
        current = 0
    count = current + 1
    with open(target, "w", encoding="utf-8") as fh:
        fh.write(str(count))

    over = count > RESUME_LIMIT
    emit({"task": args.task, "resume_count": count, "limit": RESUME_LIMIT,
          "over_limit": over})
    if over:
        warn(
            f"task{args.task} の再開は {count} 回目で上限 {RESUME_LIMIT} を超えました。"
            "再開せず、台帳でそのタスクを blocked にし、block を実行してユーザーへ"
            "上げてください。他のタスクは止めません"
        )
        raise SystemExit(1)


def cmd_clear(args):
    target = os.path.join(state_dir(), f"resume-count-task{args.task}")
    existed = os.path.exists(target)
    if existed:
        os.remove(target)
    emit({"cleared": target, "existed": existed})


def cmd_block(args):
    target = os.path.join(ensure_state_dir(), f"blocked-{args.agent}")
    with open(target, "a", encoding="utf-8"):
        pass
    emit({"blocked": target})


def build_parser():
    parser = argparse.ArgumentParser(prog="state.py", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="状態ディレクトリ直下のファイルを消す（base/ は残す）")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("branch", help="branch-<agentId> を書く")
    p.add_argument("--agent", required=True)
    p.add_argument("--branch", required=True)
    p.set_defaults(func=cmd_branch)

    p = sub.add_parser("resume", help="再開回数を加算し、上限超過なら終了コード 1")
    p.add_argument("--task", required=True)
    p.set_defaults(func=cmd_resume)

    p = sub.add_parser("clear", help="再開回数を消す")
    p.add_argument("--task", required=True)
    p.set_defaults(func=cmd_clear)

    p = sub.add_parser("block", help="blocked-<agentId> を作る")
    p.add_argument("--agent", required=True)
    p.set_defaults(func=cmd_block)

    return parser


if __name__ == "__main__":
    parsed = build_parser().parse_args()
    parsed.func(parsed)
