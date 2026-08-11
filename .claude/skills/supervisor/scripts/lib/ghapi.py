"""GitHub の GraphQL 呼び出しと、リポジトリ・PR の同定。

`gh api graphql` を経由する。REST は使わない。クエリ文字列そのものは、それを使う側
（reviewthreads.py・gh-review.py）に置く——ここが持つのは「どう呼ぶか」だけで、
「何を聞くか」は呼ぶ側の関心だからである。
"""

import json
import subprocess

from .shell import die, out


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


def resolve_repo(explicit):
    """`owner/name` を (owner, name) に分ける。省略時はカレントのリポジトリを見る。"""
    if explicit:
        if "/" not in explicit:
            die("--repo は owner/name の形で指定してください")
        owner, name = explicit.split("/", 1)
        return owner, name
    data = json.loads(out(["gh", "repo", "view", "--json", "owner,name"]))
    return data["owner"]["login"], data["name"]


def pr_node_id(pr, owner, name):
    # --repo を付けないと gh がカレントディレクトリのリポジトリで解決してしまい、
    # --repo 指定時に他の呼び出しと別のリポジトリを見ることになる
    return out(
        ["gh", "pr", "view", str(pr), "--repo", f"{owner}/{name}",
         "--json", "id", "--jq", ".id"]
    )


def viewer_login():
    return graphql("{ viewer { login } }", {})["viewer"]["login"]
