"""PR のレビュースレッドと、提出済み・未提出レビューの取得。

GraphQL のクエリと、ページ送りの打ち切りで取りこぼす罠をここに閉じ込める。本文の書式を
知らない（読み取りは reviewbody.parse_meta に任せる）。
"""

from .ghapi import graphql, viewer_login
from .reviewbody import parse_meta

THREADS_QUERY = (
    "query($o:String!,$r:String!,$n:Int!,$cursor:String){repository(owner:$o,name:$r){"
    "pullRequest(number:$n){reviewThreads(first:100,after:$cursor){"
    "pageInfo{hasNextPage endCursor} nodes{"
    "id isResolved isOutdated path line subjectType "
    "comments(first:30){nodes{author{login} body}}}}}}}"
)

PENDING_QUERY = (
    "query($o:String!,$r:String!,$n:Int!){repository(owner:$o,name:$r){"
    "pullRequest(number:$n){reviews(first:50,states:[PENDING]){nodes{id author{login}}}}}}"
)

SUBMITTED_REVIEWS_QUERY = (
    "query($o:String!,$r:String!,$n:Int!,$cursor:String){repository(owner:$o,name:$r){"
    "pullRequest(number:$n){reviews(first:100,after:$cursor){"
    "pageInfo{hasNextPage endCursor} nodes{state body}}}}}"
)


def fetch_threads(owner, name, pr):
    """全ページを辿ってスレッドを集める。

    スレッドは作成順に返るため、1 ページ（100 件）で打ち切ると解決済みの古いスレッドが
    枠を埋め、未解決の新しいスレッドが gate から見えなくなる。
    """
    nodes, cursor = [], None
    while True:
        data = graphql(
            THREADS_QUERY, {"o": owner, "r": name, "n": int(pr), "cursor": cursor}
        )
        conn = data["repository"]["pullRequest"]["reviewThreads"]
        nodes.extend(conn["nodes"])
        if not conn["pageInfo"]["hasNextPage"]:
            return nodes
        cursor = conn["pageInfo"]["endCursor"]


def resolve_thread(thread_id):
    """スレッドを畳み、畳めたかどうかを返す。"""
    return graphql(
        "mutation($t:ID!){resolveReviewThread(input:{threadId:$t}){thread{isResolved}}}",
        {"t": thread_id},
    )["resolveReviewThread"]["thread"]["isResolved"]


def own_pending(owner, name, pr):
    """自分が作ったまま submit していない PENDING レビュー。"""
    me = viewer_login()
    nodes = graphql(PENDING_QUERY, {"o": owner, "r": name, "n": int(pr)})[
        "repository"
    ]["pullRequest"]["reviews"]["nodes"]
    return [n for n in nodes if (n["author"] or {}).get("login") == me]


def delete_own_pending(owner, name, pr):
    """自分の PENDING レビューを消し、消した件数を返す。"""
    reviews = own_pending(owner, name, pr)
    for review in reviews:
        graphql(
            "mutation($id:ID!){deletePullRequestReview(input:{pullRequestReviewId:$id}){"
            "pullRequestReview{id}}}",
            {"id": review["id"]},
        )
    return len(reviews)


def submitted_roles(owner, name, pr):
    """提出済みレビューを役割ごとに数える。

    未解決スレッドが 0 件であることは「レビューが行われた」ことを意味しない——レビューを
    1 度も走らせていない PR ではスレッドがそもそも 0 件になる（この穴で承認の門が
    素通しになった実績がある）。post が submit するサマリ本文の隠しメタデータを数えて、
    要求した役割のレビュアーが実際に走ったことを確かめる。
    """
    counts, cursor = {}, None
    while True:
        conn = graphql(
            SUBMITTED_REVIEWS_QUERY,
            {"o": owner, "r": name, "n": int(pr), "cursor": cursor},
        )["repository"]["pullRequest"]["reviews"]
        for node in conn["nodes"]:
            # PENDING は未提出なので数えない（別に own_pending で見る）
            if node["state"] == "PENDING":
                continue
            meta = parse_meta(node.get("body"))
            if meta.get("kind") == "review" and meta.get("role"):
                counts[meta["role"]] = counts.get(meta["role"], 0) + 1
        if not conn["pageInfo"]["hasNextPage"]:
            return counts
        cursor = conn["pageInfo"]["endCursor"]
