#!/usr/bin/env python3
"""supervisor スキルの GitHub レビューコメント操作。

各 subagent はこのスクリプトを引数付きで呼ぶ。GraphQL の組み立てと JSON の
エスケープを閉じ込め、エージェント側が書き間違える余地をなくす。

サブコマンド:
  post      レビューを投稿する（PENDING 作成 → スレッド追加 → submit）
  threads   レビュースレッドを列挙する（既定は未解決のみ）
  reply     スレッドに返信する（--resolve を付けると返信後に畳む）
  resolve   スレッドを畳む
  gate      未解決スレッド 0 件・PENDING 0 件・要求した役割のレビュー提出を判定する（承認の門）

すべて `gh api graphql` を経由する。REST は使わない。

このファイルが持つのは「どのサブコマンドが何を順に行うか」だけである。GraphQL の呼び方は
lib/ghapi.py、本文の書式は lib/reviewbody.py、スレッドと提出済みレビューの取得は
lib/reviewthreads.py にある。
"""

import argparse
import json
import sys

from lib.ghapi import graphql, pr_node_id, resolve_repo
from lib.reviewbody import (SEVERITIES, STATUS_BY_ROLE, VERDICTS, check_role,
                            finding_body, reply_body, summary_body, thread_meta)
from lib.reviewthreads import (delete_own_pending, fetch_threads, own_pending,
                               resolve_thread, submitted_roles)
from lib.shell import die, emit, warn


def cmd_post(args):
    role = args.role
    check_role(role)

    if args.findings == "-" and args.summary_file == "-":
        die(
            "--findings と --summary-file の両方を - にはできません"
            "（標準入力は 1 回しか読めず、後から読む方が空になります）"
        )
    raw = sys.stdin.read() if args.findings == "-" else open(args.findings).read()
    try:
        findings = json.loads(raw)
    except json.JSONDecodeError as exc:
        die(f"--findings の JSON を解釈できません: {exc}")
    if not isinstance(findings, list):
        die("--findings は配列にしてください")

    counts = {s: 0 for s in SEVERITIES}
    line_threads, file_threads = [], []
    for index, finding in enumerate(findings):
        if not finding.get("path"):
            die(f"findings[{index}] に path がありません")
        if finding.get("severity") not in SEVERITIES:
            die(
                f"findings[{index}] の severity は {' / '.join(SEVERITIES)} の"
                f"いずれかにしてください: {finding.get('severity')!r}"
            )
        counts[finding["severity"]] += 1
        body = finding_body(role, finding)
        if finding.get("line"):
            thread = {
                "path": finding["path"],
                "line": int(finding["line"]),
                "side": finding.get("side", "RIGHT"),
                "body": body,
            }
            if finding.get("startLine"):
                thread["startLine"] = int(finding["startLine"])
                thread["startSide"] = finding.get("startSide", thread["side"])
            line_threads.append(thread)
        else:
            file_threads.append({"path": finding["path"], "body": body})

    extra = ""
    if args.summary_file:
        extra = sys.stdin.read() if args.summary_file == "-" else open(args.summary_file).read()
    body = summary_body(role, args.verdict, counts, extra)

    if args.dry_run:
        emit({"dry_run": True, "counts": counts, "line_threads": line_threads,
              "file_threads": file_threads, "summary": body}, pretty=True)
        return

    owner, name = resolve_repo(args.repo)
    if not args.keep_pending:
        deleted = delete_own_pending(owner, name, args.pr)
        if deleted:
            warn(f"残っていた PENDING レビューを {deleted} 件消しました")

    review_id = graphql(
        "mutation($pr:ID!,$threads:[DraftPullRequestReviewThread!]){"
        "addPullRequestReview(input:{pullRequestId:$pr,threads:$threads}){"
        "pullRequestReview{id}}}",
        {"pr": pr_node_id(args.pr, owner, name), "threads": line_threads},
    )["addPullRequestReview"]["pullRequestReview"]["id"]

    for thread in file_threads:
        graphql(
            "mutation($rid:ID!,$path:String!,$body:String!){"
            "addPullRequestReviewThread(input:{pullRequestReviewId:$rid,path:$path,"
            "subjectType:FILE,body:$body}){thread{id}}}",
            {"rid": review_id, "path": thread["path"], "body": thread["body"]},
        )

    result = graphql(
        "mutation($rid:ID!,$body:String!){"
        "submitPullRequestReview(input:{pullRequestReviewId:$rid,event:COMMENT,body:$body}){"
        "pullRequestReview{state url}}}",
        {"rid": review_id, "body": body},
    )["submitPullRequestReview"]["pullRequestReview"]

    emit({"review_url": result["url"], "state": result["state"],
          "line_threads": len(line_threads), "file_threads": len(file_threads),
          "counts": counts, "verdict": args.verdict}, pretty=True)


def cmd_threads(args):
    owner, name = resolve_repo(args.repo)
    nodes = fetch_threads(owner, name, args.pr)
    if not args.all:
        nodes = [n for n in nodes if not n["isResolved"]]
    listed = []
    for n in nodes:
        severity, role = thread_meta(n["comments"]["nodes"])
        listed.append({
            "id": n["id"],
            "isResolved": n["isResolved"],
            "isOutdated": n["isOutdated"],
            "path": n["path"],
            "line": n["line"],
            "subjectType": n["subjectType"],
            "severity": severity,
            "role": role,
            "comments": [
                {"author": (c["author"] or {}).get("login"), "body": c["body"]}
                for c in n["comments"]["nodes"]
            ],
        })
    if args.role:
        listed = [t for t in listed if t["role"] == args.role]
    emit(listed, pretty=True)


def cmd_reply(args):
    role = args.role
    prefix = check_role(role)
    allowed = STATUS_BY_ROLE[prefix]
    if args.status not in allowed:
        die(f"役割 '{role}' が使える状態語は {' / '.join(allowed)} です: {args.status!r}")
    # 返信を投稿する前に判定する（投稿してから拒否すると片方だけ実行された状態になる）
    if args.resolve and prefix == "impl":
        die("実装エージェントはスレッドを畳めません（自己承認になります）")

    graphql(
        "mutation($t:ID!,$b:String!){addPullRequestReviewThreadReply("
        "input:{pullRequestReviewThreadId:$t,body:$b}){comment{id}}}",
        {"t": args.thread, "b": reply_body(role, args.status, args.message, args.commit)},
    )
    result = {"replied": args.thread, "status": args.status}

    if args.resolve:
        result["resolved"] = resolve_thread(args.thread)

    emit(result)


def cmd_resolve(args):
    prefix = check_role(args.role)
    if prefix == "impl":
        die("実装エージェントはスレッドを畳めません（自己承認になります）")
    emit({"resolved": args.thread, "isResolved": resolve_thread(args.thread)})


def cmd_gate(args):
    required = [r.strip() for r in args.require_roles.split(",") if r.strip()]
    if not required:
        die(
            "--require-roles に役割を 1 つ以上指定してください"
            "（standard: review:normal,review:adversarial / light: review:normal）"
        )
    for role in required:
        check_role(role)

    owner, name = resolve_repo(args.repo)
    unresolved = [t for t in fetch_threads(owner, name, args.pr) if not t["isResolved"]]
    pending = own_pending(owner, name, args.pr)
    counts = submitted_roles(owner, name, args.pr)
    missing = [r for r in required if not counts.get(r)]
    clean = not unresolved and not pending and not missing
    emit({
        "clean": clean,
        "unresolved": len(unresolved),
        "pending_reviews": len(pending),
        "required_roles": required,
        "submitted_roles": {r: counts.get(r, 0) for r in required},
        "missing_roles": missing,
        "unresolved_threads": [
            {"id": t["id"], "path": t["path"], "line": t["line"],
             "isOutdated": t["isOutdated"]}
            for t in unresolved
        ],
    }, pretty=True)
    if missing:
        warn(
            " / ".join(missing)
            + " のレビューが提出されていません。未解決スレッドが 0 件でも承認しないで"
            "ください（レビューを走らせていない PR はスレッドが 0 件になります）"
        )
    raise SystemExit(0 if clean else 1)


def build_parser():
    parser = argparse.ArgumentParser(prog="gh-review.py", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p, need_pr=True):
        if need_pr:
            p.add_argument("--pr", required=True, help="PR 番号")
        p.add_argument("--repo", help="owner/name（省略時はカレントのリポジトリ）")

    p = sub.add_parser("post", help="レビューを投稿する")
    add_common(p)
    p.add_argument("--role", required=True, help="例: review:normal / review:adversarial")
    p.add_argument("--verdict", required=True, choices=VERDICTS)
    p.add_argument("--findings", required=True, help="findings の JSON ファイル（- で標準入力）")
    p.add_argument("--summary-file", help="サマリに足す本文（検証結果など）")
    p.add_argument("--keep-pending", action="store_true",
                   help="残っている自分の PENDING レビューを消さない")
    p.add_argument("--dry-run", action="store_true",
                   help="GitHub を呼ばず、組み立てた本文とスレッドを表示して終わる")
    p.set_defaults(func=cmd_post)

    p = sub.add_parser("threads", help="レビュースレッドを列挙する")
    add_common(p)
    p.add_argument("--all", action="store_true", help="解決済みも含める")
    p.add_argument("--role", help="この役割が立てたスレッドだけに絞る（例: review:normal）")
    p.set_defaults(func=cmd_threads)

    p = sub.add_parser("reply", help="スレッドに返信する")
    add_common(p, need_pr=False)
    p.add_argument("--thread", required=True, help="スレッドの ID")
    p.add_argument("--role", required=True)
    p.add_argument("--status", required=True)
    p.add_argument("--message", required=True)
    p.add_argument("--commit", help="対応したコミットの SHA")
    p.add_argument("--resolve", action="store_true", help="返信したあとに畳む")
    p.set_defaults(func=cmd_reply)

    p = sub.add_parser("resolve", help="スレッドを畳む")
    add_common(p, need_pr=False)
    p.add_argument("--thread", required=True)
    p.add_argument("--role", required=True)
    p.set_defaults(func=cmd_resolve)

    p = sub.add_parser(
        "gate",
        help="未解決スレッド 0 件・PENDING 0 件・要求した役割のレビュー提出を判定する",
    )
    add_common(p)
    # 任意にすると渡し忘れた時点で「レビュー 0 件でも通る門」に戻るので必須にする
    p.add_argument(
        "--require-roles", required=True,
        help="提出されていることを要求するレビューの役割をカンマ区切りで指定する。"
             "standard: review:normal,review:adversarial / light: review:normal",
    )
    p.set_defaults(func=cmd_gate)

    return parser


if __name__ == "__main__":
    parsed = build_parser().parse_args()
    parsed.func(parsed)
