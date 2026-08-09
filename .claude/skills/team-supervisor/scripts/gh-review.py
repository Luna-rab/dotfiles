#!/usr/bin/env python3
"""team-supervisor スキルの GitHub レビューコメント操作。

各 subagent はこのスクリプトを引数付きで呼ぶ。GraphQL の組み立てと JSON の
エスケープをここに閉じ込め、エージェント側が書き間違える余地をなくす。

サブコマンド:
  post      レビューを投稿する（PENDING 作成 → スレッド追加 → submit）
  threads   レビュースレッドを列挙する（既定は未解決のみ）
  reply     スレッドに返信する（--resolve を付けると返信後に畳む）
  resolve   スレッドを畳む
  pending   自分の PENDING レビューを数える / 消す
  gate      未解決スレッドと PENDING が 0 件かを判定する（承認の門）

すべて `gh api graphql` を経由する。REST は使わない。
"""

import argparse
import json
import subprocess
import sys

SEVERITIES = ("must-fix", "should-fix", "nit")
VERDICTS = ("approved", "changes-requested")

# 役割の接頭辞ごとに使ってよい状態語
STATUS_BY_ROLE = {
    "impl": ("fixed", "partial", "wont-fix", "disputed", "deferred"),
    "subleader": ("upheld", "overruled"),
    "review": ("still-open", "resolved"),
    "lead": ("note",),
}


def die(message):
    print(f"gh-review: {message}", file=sys.stderr)
    sys.exit(1)


def graphql(query, variables):
    """GraphQL を叩いて data を返す。errors があれば止める。"""
    body = json.dumps({"query": query, "variables": variables})
    proc = subprocess.run(
        ["gh", "api", "graphql", "--input", "-"],
        input=body,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0 and not proc.stdout.strip():
        die(f"gh api graphql が失敗しました\n{proc.stderr.strip()}")
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        die(f"GraphQL の応答を解釈できません\n{proc.stdout[:500]}")
    if payload.get("errors"):
        messages = "\n".join(e.get("message", str(e)) for e in payload["errors"])
        die(f"GraphQL がエラーを返しました\n{messages}")
    return payload.get("data") or {}


def run(cmd):
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        die(f"{' '.join(cmd)} が失敗しました\n{proc.stderr.strip()}")
    return proc.stdout.strip()


def resolve_repo(explicit):
    if explicit:
        if "/" not in explicit:
            die("--repo は owner/name の形で指定してください")
        owner, name = explicit.split("/", 1)
        return owner, name
    raw = run(["gh", "repo", "view", "--json", "owner,name"])
    data = json.loads(raw)
    return data["owner"]["login"], data["name"]


def pr_node_id(pr):
    return run(["gh", "pr", "view", str(pr), "--json", "id", "--jq", ".id"])


def viewer_login():
    return graphql("{ viewer { login } }", {})["viewer"]["login"]


def check_role(role):
    prefix = role.split(":", 1)[0]
    if prefix not in STATUS_BY_ROLE:
        die(
            f"役割 '{role}' は使えません。接頭辞は "
            + " / ".join(sorted(STATUS_BY_ROLE))
            + " のいずれかにしてください（例: review:normal, impl:a, subleader:task4）"
        )
    return prefix


# ---------------------------------------------------------------- 本文の組み立て


def finding_body(role, finding):
    severity = finding.get("severity")
    if severity not in SEVERITIES:
        die(f"severity は {' / '.join(SEVERITIES)} のいずれかにしてください: {severity!r}")
    category = finding.get("category") or "general"
    lines = [f"**[{role}]** `{severity}` / {category}", "", finding.get("body", "").rstrip()]
    if finding.get("evidence"):
        lines += ["", f"**根拠**: {finding['evidence']}"]
    if finding.get("suggestion"):
        lines += ["", f"**提案**: {finding['suggestion']}"]
    if finding.get("target"):
        lines += ["", f"**本来の対象**: {finding['target']}"]
    return "\n".join(lines).rstrip() + "\n"


def summary_body(role, verdict, counts, extra):
    if verdict not in VERDICTS:
        die(f"verdict は {' / '.join(VERDICTS)} のいずれかにしてください: {verdict!r}")
    lines = [
        f"**[{role}]** verdict: `{verdict}`",
        "",
        "- must-fix: {must-fix} / should-fix: {should-fix} / nit: {nit}".format(**counts),
    ]
    if extra:
        lines += ["", extra.rstrip()]
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------- サブコマンド


def cmd_post(args):
    role = args.role
    check_role(role)

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
        print(
            json.dumps(
                {
                    "dry_run": True,
                    "counts": counts,
                    "line_threads": line_threads,
                    "file_threads": file_threads,
                    "summary": body,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    owner, name = resolve_repo(args.repo)
    if not args.keep_pending:
        deleted = delete_own_pending(owner, name, args.pr)
        if deleted:
            print(f"残っていた PENDING レビューを {deleted} 件消しました", file=sys.stderr)

    review_id = graphql(
        "mutation($pr:ID!,$threads:[DraftPullRequestReviewThread!]){"
        "addPullRequestReview(input:{pullRequestId:$pr,threads:$threads}){"
        "pullRequestReview{id}}}",
        {"pr": pr_node_id(args.pr), "threads": line_threads},
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

    print(
        json.dumps(
            {
                "review_url": result["url"],
                "state": result["state"],
                "line_threads": len(line_threads),
                "file_threads": len(file_threads),
                "counts": counts,
                "verdict": args.verdict,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


THREADS_QUERY = (
    "query($o:String!,$r:String!,$n:Int!){repository(owner:$o,name:$r){"
    "pullRequest(number:$n){reviewThreads(first:100){nodes{"
    "id isResolved isOutdated path line subjectType "
    "comments(first:30){nodes{author{login} body}}}}}}}"
)


def fetch_threads(owner, name, pr):
    data = graphql(THREADS_QUERY, {"o": owner, "r": name, "n": int(pr)})
    return data["repository"]["pullRequest"]["reviewThreads"]["nodes"]


def cmd_threads(args):
    owner, name = resolve_repo(args.repo)
    nodes = fetch_threads(owner, name, args.pr)
    if not args.all:
        nodes = [n for n in nodes if not n["isResolved"]]
    out = [
        {
            "id": n["id"],
            "isResolved": n["isResolved"],
            "isOutdated": n["isOutdated"],
            "path": n["path"],
            "line": n["line"],
            "subjectType": n["subjectType"],
            "comments": [
                {"author": (c["author"] or {}).get("login"), "body": c["body"]}
                for c in n["comments"]["nodes"]
            ],
        }
        for n in nodes
    ]
    print(json.dumps(out, ensure_ascii=False, indent=2))


def cmd_reply(args):
    role = args.role
    prefix = check_role(role)
    allowed = STATUS_BY_ROLE[prefix]
    if args.status not in allowed:
        die(f"役割 '{role}' が使える状態語は {' / '.join(allowed)} です: {args.status!r}")
    # 返信を投稿する前に判定する（投稿してから拒否すると片方だけ実行された状態になる）
    if args.resolve and prefix == "impl":
        die("実装エージェントはスレッドを畳めません（自己承認になります）")

    lines = [f"**[{role}]** `{args.status}` — {args.message}"]
    if args.commit:
        lines += ["", f"commit: {args.commit}"]
    body = "\n".join(lines).rstrip() + "\n"

    graphql(
        "mutation($t:ID!,$b:String!){addPullRequestReviewThreadReply("
        "input:{pullRequestReviewThreadId:$t,body:$b}){comment{id}}}",
        {"t": args.thread, "b": body},
    )
    result = {"replied": args.thread, "status": args.status}

    if args.resolve:
        resolved = graphql(
            "mutation($t:ID!){resolveReviewThread(input:{threadId:$t}){thread{isResolved}}}",
            {"t": args.thread},
        )["resolveReviewThread"]["thread"]["isResolved"]
        result["resolved"] = resolved

    print(json.dumps(result, ensure_ascii=False))


def cmd_resolve(args):
    check_role(args.role)
    if args.role.split(":", 1)[0] == "impl":
        die("実装エージェントはスレッドを畳めません（自己承認になります）")
    resolved = graphql(
        "mutation($t:ID!){resolveReviewThread(input:{threadId:$t}){thread{isResolved}}}",
        {"t": args.thread},
    )["resolveReviewThread"]["thread"]["isResolved"]
    print(json.dumps({"resolved": args.thread, "isResolved": resolved}, ensure_ascii=False))


PENDING_QUERY = (
    "query($o:String!,$r:String!,$n:Int!){repository(owner:$o,name:$r){"
    "pullRequest(number:$n){reviews(first:50,states:[PENDING]){nodes{id author{login}}}}}}"
)


def own_pending(owner, name, pr):
    me = viewer_login()
    nodes = graphql(PENDING_QUERY, {"o": owner, "r": name, "n": int(pr)})[
        "repository"
    ]["pullRequest"]["reviews"]["nodes"]
    return [n for n in nodes if (n["author"] or {}).get("login") == me]


def delete_own_pending(owner, name, pr):
    reviews = own_pending(owner, name, pr)
    for review in reviews:
        graphql(
            "mutation($id:ID!){deletePullRequestReview(input:{pullRequestReviewId:$id}){"
            "pullRequestReview{id}}}",
            {"id": review["id"]},
        )
    return len(reviews)


def cmd_pending(args):
    owner, name = resolve_repo(args.repo)
    if args.delete:
        count = delete_own_pending(owner, name, args.pr)
        print(json.dumps({"deleted": count}, ensure_ascii=False))
        return
    reviews = own_pending(owner, name, args.pr)
    print(json.dumps({"pending": len(reviews)}, ensure_ascii=False))
    if args.fail_if_any and reviews:
        sys.exit(1)


def cmd_gate(args):
    owner, name = resolve_repo(args.repo)
    unresolved = [t for t in fetch_threads(owner, name, args.pr) if not t["isResolved"]]
    pending = own_pending(owner, name, args.pr)
    clean = not unresolved and not pending
    print(
        json.dumps(
            {
                "clean": clean,
                "unresolved": len(unresolved),
                "pending_reviews": len(pending),
                "unresolved_threads": [
                    {"id": t["id"], "path": t["path"], "line": t["line"],
                     "isOutdated": t["isOutdated"]}
                    for t in unresolved
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    sys.exit(0 if clean else 1)


# ---------------------------------------------------------------- 引数


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

    p = sub.add_parser("pending", help="自分の PENDING レビューを数える / 消す")
    add_common(p)
    p.add_argument("--delete", action="store_true")
    p.add_argument("--fail-if-any", action="store_true", help="1 件でもあれば終了コード 1")
    p.set_defaults(func=cmd_pending)

    p = sub.add_parser("gate", help="未解決スレッドと PENDING が 0 件かを判定する")
    add_common(p)
    p.set_defaults(func=cmd_gate)

    return parser


if __name__ == "__main__":
    parsed = build_parser().parse_args()
    parsed.func(parsed)
