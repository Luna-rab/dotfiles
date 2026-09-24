"""GitHub 側の操作。**呼ぶのは driver の後段だけである。**

実装ブロックは `gh` を 1 度も叩かない（決定: インフラと実装を分ける）。コンテナ化したときに
GitHub の資格情報をコンテナへ渡さなくて済む。

stacked PR の組み立てには `gh stack link` を使う。

    gh stack link <stack-number | branch-or-pr> <branch-or-pr> [...]

`gh stack link` を選んだ理由は 2 つある。

1. **local tracking state に依らない**（help に "designed for users who manage branches with
   external tools" と書いてある）。だから「`gh stack` の追跡情報は worktree ごとに別」という
   制約を踏まない。
2. **PR のタイトルと本文を自分で決められる。** `gh stack submit --auto` は自動生成の
   タイトルになり、非対話では draft で作られる（`--open` を付けない限り）。autodev は
   PR 本文段が書いた本文を載せたいので、`gh pr create` で作ってから link で連ねる。
"""

from __future__ import annotations

import json
import re
from typing import Any

from . import proc

PR_URL = re.compile(r"/pull/(\d+)\s*$")


def gh(tree: str, *args: str, timeout: int = 600) -> proc.Run:
    return proc.run(["gh", *args], cwd=tree, timeout=timeout)


def ready() -> str | None:
    """`gh` が使えるか、`gh stack` が入っているかを先に見る。駄目なら理由を返す。"""
    if not proc.run(["gh", "auth", "status"]).ok:
        return "gh が認証されていない（`gh auth login` を実行してください）"
    listed = proc.run(["gh", "extension", "list"])
    if not listed.ok or "gh-stack" not in listed.out:
        return "gh stack 拡張が入っていない（`gh extension install github/gh-stack`）"
    return None


def pr_create(
    tree: str,
    *,
    base: str,
    head: str,
    title: str,
    body_file: str,
    draft: bool = False,
) -> tuple[int | None, proc.Run]:
    args = [
        "pr",
        "create",
        "--base",
        base,
        "--head",
        head,
        "--title",
        title,
        "--body-file",
        body_file,
    ]
    if draft:
        args.append("--draft")
    got = gh(tree, *args)
    if not got.ok:
        return None, got
    return _pr_number(got.out), got


def pr_edit(
    tree: str,
    pr: int,
    title: str | None = None,
    body_file: str | None = None,
) -> proc.Run:
    args = ["pr", "edit", str(pr)]
    if title:
        args += ["--title", title]
    if body_file:
        args += ["--body-file", body_file]
    return gh(tree, *args)


def pr_ready(tree: str, pr: int) -> proc.Run:
    return gh(tree, "pr", "ready", str(pr))


def pr_view(tree: str, pr: int) -> dict[str, Any] | None:
    """検査に使う PR の外形。head / base / state / draft を見る。"""
    got = gh(
        tree,
        "pr",
        "view",
        str(pr),
        "--json",
        "number,headRefName,baseRefName,state,isDraft,url",
    )
    if not got.ok:
        return None
    try:
        loaded = json.loads(got.out)
    except json.JSONDecodeError:
        return None
    return loaded if isinstance(loaded, dict) else None


def stack_link(tree: str, members: list[str]) -> proc.Run:
    """下から上の順に並べて stacked PR を組む・継ぎ足す。

    **2 つ以上渡す必要がある**（`gh stack link <a> <b> [...]`）。土台 PR だけの時点では
    呼ばない。
    """
    if len(members) < 2:
        return proc.Run(0, "", "")
    return gh(tree, "stack", "link", *members, timeout=900)


def _pr_number(text: str) -> int | None:
    for line in reversed(text.strip().splitlines()):
        found = PR_URL.search(line.strip())
        if found:
            return int(found.group(1))
    return None
