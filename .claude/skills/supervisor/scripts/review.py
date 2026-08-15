#!/usr/bin/env python3
"""supervisor スキルのレビュー記録（review.json）の読み書き。

各 subagent はこのスクリプトを引数付きで呼ぶ。JSON を直接書かせないのは、書式の
書き間違い（rating の綴り、許されない status 遷移、コメント無しの status 変更）を
その場で拒むためである。落ちた指摘が静かに消えると、次のラウンドで誰も気づけない。

**GitHub を呼ばない。** レビューは `<ベース>/notes/task<番号>/review.json` に閉じており、
PR は全レビューが closed か rejected になってから作る（PR 作成はリードが行う）。

サブコマンド:
  new      レビューを立てる（レビューエージェントだけ）
  comment  レビューにコメントを足す（実装・レビュー・裁定）
  status   status を動かす（裁定だけ。コメント必須）
  list     レビューを一覧する（既定は open のみ。--all で全件）

役割ごとの使い分け:
  review:normal / review:adversarial  new / comment / list（open のみ）
  impl:a / impl:b                     comment / list（open のみ）
  judge                               comment / status / list --all
"""

import argparse
import sys

from lib import reviewstore as store
from lib.shell import die, emit, warn


def read_source(value):
    """`-` なら標準入力、それ以外はファイルとして読む。"""
    return sys.stdin.read() if value == "-" else open(value).read()


def text_arg(inline, source, label):
    """`--x TEXT` と `--x-file PATH`（`-` で標準入力）のどちらかを受ける。"""
    if inline is not None and source is not None:
        die(f"{label} は本文とファイルのどちらか一方だけを渡してください")
    if source is not None:
        return store.check_text(read_source(source), label)
    return store.check_text(inline, label)


def cmd_new(args):
    """レビューを立てる。review-id はスクリプトが振る。"""
    store.check_reviewer(args.reviewer)
    store.check_rating(args.rating)
    location = store.check_location(args.location)
    body = text_arg(args.review, args.review_file, "review")

    path = store.review_path(args.dir)
    with store.with_lock(path) as data:
        review_id = store.new_review_id(data)
        data[review_id] = {
            "reviewer": args.reviewer,
            "rating": args.rating,
            "location": location,
            "review": body,
            "status": "open",
            "threads": [],
        }
        counts = store.tally(data)
    emit({"wrote": path, "id": review_id, "reviewer": args.reviewer,
          "rating": args.rating, "status": "open", "counts": counts}, pretty=True)


def cmd_comment(args):
    """レビューにコメントを足す。status は動かさない。"""
    store.check_commenter(args.commenter)
    body = text_arg(args.comment, args.comment_file, "comment")

    path = store.review_path(args.dir)
    with store.with_lock(path) as data:
        review = store.get_review(data, args.id)
        thread = store.target_thread(review, force_new=args.new_thread)
        thread["comments"].append({"commenter": args.commenter, "comment": body})
        thread_id = thread["thread_id"]
        status = review.get("status")
    emit({"wrote": path, "id": args.id, "thread": thread_id,
          "commenter": args.commenter, "status": status}, pretty=True)


def cmd_status(args):
    """status を動かす。裁定だけが呼べ、コメントを必ず伴う。"""
    store.check_judge(args.commenter)
    store.check_status(args.to)
    # コメント無しで畳めると「なぜ閉じたか」が残らない。次のラウンドの裁定も、
    # 残件を拾うリードも、判断の根拠を追えなくなる
    body = text_arg(args.comment, args.comment_file, "comment")

    path = store.review_path(args.dir)
    with store.with_lock(path) as data:
        review = store.get_review(data, args.id)
        current = review.get("status")
        store.check_transition(current, args.to)
        thread = store.target_thread(review, force_new=args.new_thread)
        thread["comments"].append({"commenter": store.JUDGE, "comment": body})
        thread["transition"] = {"from": current, "to": args.to}
        review["status"] = args.to
        thread_id = thread["thread_id"]
        counts = store.tally(data)
    emit({"wrote": path, "id": args.id, "thread": thread_id,
          "from": current, "to": args.to, "counts": counts}, pretty=True)


def cmd_list(args):
    """レビューを一覧する。既定は open のみ。"""
    path = store.review_path(args.dir)
    data = store.read_only(path)
    counts = store.tally(data)

    selected = {
        key: value for key, value in data.items()
        if (args.all or value.get("status") == "open")
        and (not args.reviewer or value.get("reviewer") == args.reviewer)
        and (not args.rating or value.get("rating") == args.rating)
    }
    emit({"path": path, "counts": counts, "total": len(selected),
          "reviews": store.listed(selected)}, pretty=True)

    if args.require_empty and counts["open"]:
        warn(
            f"open のレビューが {counts['open']} 件あります。"
            "全件が closed か rejected になるまで PR を作らないでください"
        )
        raise SystemExit(1)


def build_parser():
    parser = argparse.ArgumentParser(prog="review.py", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def add_dir(p):
        p.add_argument("--dir", required=True,
                       help="review.json の置き場（<ベース>/notes/task<番号>）")

    def add_comment_args(p):
        p.add_argument("--comment", help="コメント本文")
        p.add_argument("--comment-file", help="コメント本文のファイル（- で標準入力）")
        p.add_argument("--new-thread", action="store_true",
                       help="判定がまだ付いていないスレッドがあっても新しいスレッドを立てる")

    p = sub.add_parser("new", help="レビューを立てる（レビューエージェントだけ）")
    add_dir(p)
    p.add_argument("--reviewer", required=True,
                   help=" / ".join(store.REVIEWERS))
    p.add_argument("--rating", required=True, help=" / ".join(store.RATINGS))
    p.add_argument("--location", required=True,
                   help="指摘の場所。`src/core/parser.rs:42` または `src/core/parser.rs`")
    p.add_argument("--review", help="指摘の本文")
    p.add_argument("--review-file", help="指摘の本文のファイル（- で標準入力）")
    p.set_defaults(func=cmd_new)

    p = sub.add_parser("comment", help="レビューにコメントを足す")
    add_dir(p)
    p.add_argument("--id", required=True, help="review-id（例: r1）")
    p.add_argument("--commenter", required=True, help=" / ".join(store.COMMENTERS))
    add_comment_args(p)
    p.set_defaults(func=cmd_comment)

    p = sub.add_parser("status", help="status を動かす（裁定だけ。コメント必須）")
    add_dir(p)
    p.add_argument("--id", required=True, help="review-id（例: r1）")
    p.add_argument("--commenter", required=True,
                   help=f"呼び手の役割。{store.JUDGE} 以外は拒む")
    p.add_argument("--to", required=True, choices=store.STATUSES)
    add_comment_args(p)
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("list", help="レビューを一覧する（既定は open のみ）")
    add_dir(p)
    p.add_argument("--all", action="store_true", help="closed / rejected も含める")
    p.add_argument("--reviewer", help="この役割が立てた分だけに絞る")
    p.add_argument("--rating", help="この rating だけに絞る")
    p.add_argument("--require-empty", action="store_true",
                   help="open が 1 件でもあれば終了コード 1 を返す（リードが取り込む前に使う）")
    p.set_defaults(func=cmd_list)

    return parser


if __name__ == "__main__":
    parsed = build_parser().parse_args()
    parsed.func(parsed)
