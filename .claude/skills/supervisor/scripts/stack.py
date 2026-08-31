#!/usr/bin/env python3
"""決着したブランチを stacked PR へ積む手順を、リードの代わりに順番どおり実行する。

**このスクリプトは上流の supervisor には無い**（リードのコンテキストと往復を減らすために足した）。
判断が要るのはコンフリクトの解消だけで、残りは決まった順番である。それをここに閉じ込める。

stacked PR の形は次のとおりで、`gh stack`（GitHub 公式の拡張 github/gh-stack）が組み立てる。

    <trunk: リードがユーザーと決めた分岐元のブランチ名>
     ← stack/<作業名>--task-0   空コミット 1 つ / PR = 計画と進行状況
       ← stack/<作業名>--task-1
         ← stack/<作業名>--task-2   …決着した順に積む

サブコマンド:
  init      `gh stack init` で task-0 を stacked PR の土台として据える
  precheck  積む前の検査を全部通す（ブランチ・レビューの決着・レビュアーの体数）
  append    決着したブランチを stacked PR の先頭へ積む（add → rebase → push → link → 確認）
  sync      trunk が進んだ stacked PR を origin と合わせる（`gh stack sync` の前後を見る）
  show      いまの stacked PR の並びと、積み替えが要るブランチを出す

順番は **precheck →（エージェントの worktree を外す）→ append →（検証コマンドを流す）** である。

**マージはしない。** タスク PR を base へ入れるのはユーザーで、`gh stack merge` を叩く
（../design-notes.md「なぜリードがマージしないか」）。

**検証コマンドはこのスクリプトが実行しない。** リードが `Bash` で直接流す。ここで
`--build "<コマンド>"` を受け取ると、許可リストの `Bash(stack.py *)` 1 行で任意のコマンドが
通ることになり、権限の線引きが崩れる。

`gh stack` は常に `--tree` で渡したスタックツリーを cwd にして叩く。**追跡情報は worktree ごとに
別で、連結 worktree からは見えない**（`.git/gh-stack` に入り、別の worktree で `gh stack view` を
叩くと終了コード 2 で「not part of a stack」になる。実測は
../design-notes.md「gh stack v0.1.0 で確かめたこと」）。だから stacked PR を触る場所を 1 か所に固定する。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Callable
from typing import Any

from lib.shell import die, emit, warn

SCRIPTS = os.path.dirname(os.path.abspath(__file__))

# gh stack v0.1.0 の終了コード。0 は成功、2 は「いまのブランチが stacked PR に入っていない」、
# 5 は「対象ブランチが別の worktree で checkout されている」（実測）。
GH_STACK_NOT_IN_STACK = 2
GH_STACK_CHECKOUT_BUSY = 5


def require_tree(tree: str) -> str:
    """スタックツリーが git の作業ツリーとして実在することを確かめて絶対パスを返す。"""
    path = os.path.abspath(tree)
    if not os.path.isdir(os.path.join(path, ".git")) and not os.path.isfile(
        os.path.join(path, ".git")
    ):
        die(
            f"{path} は git の作業ツリーではありません。スタックツリーのパスを渡してください"
            "（.claude/worktrees/supervisor-<作業名>）"
        )
    return path


def git(tree: str, argv: list[str], allow_fail: bool = False) -> tuple[int, str]:
    """スタックツリーの中で git を叩く。`-C` を必ず付ける。"""
    proc = subprocess.run(["git", "-C", tree, *argv], capture_output=True, text=True, check=False)
    if proc.returncode != 0 and not allow_fail:
        die(f"git -C {tree} {' '.join(argv)} が失敗しました\n{proc.stderr.strip()}")
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def gh_stack(tree: str, argv: list[str]) -> tuple[int, str]:
    """スタックツリーを cwd にして `gh stack` を叩き、(終了コード, 出力) を返す。

    `gh -C` は使えない（拡張は cwd のリポジトリを見る）。追跡情報が worktree ごとに別なので、
    cwd を固定することが正しさの条件になっている。
    """
    proc = subprocess.run(
        ["gh", "stack", *argv], capture_output=True, text=True, check=False, cwd=tree
    )
    return proc.returncode, "\n".join(p for p in (proc.stdout.strip(), proc.stderr.strip()) if p)


def stack_state(tree: str, allow_missing: bool = False) -> dict[str, Any] | None:
    """`gh stack view --json` を読む。stacked PR が無ければ止める（`allow_missing` なら None）。

    **返るのは追跡情報に記録された位置であって、実物の ref ではない**（`stale_record()` を見る）。
    """
    code, output = gh_stack(tree, ["view", "--json"])
    if code == GH_STACK_NOT_IN_STACK:
        # **detached HEAD を「追跡情報が無い」と読ませない。** どちらも終了コード 2 だが、
        # detached のときは記録は無傷なので、init のやり直し（force push と link の張り直し）を
        # 勧めてはならない。ブランチに戻すだけで直る（実測）
        if "not on any branch" in output:
            die(
                f"{tree} が detached HEAD なので `gh stack` が今のブランチを読めません"
                f"（gh stack view: {output}）。**追跡情報は残っているので init をやり直さないこと。**"
                f"`git -C {tree} switch <stacked PR の先頭のブランチ>` で戻してから叩き直してください"
            )
        if allow_missing:
            return None
        die(
            f"{tree} に stacked PR の追跡情報がありません（gh stack view: {output}）。"
            "stack.py init を先に通してください。"
            "別のセッションで作った stacked PR はこの worktree からは見えないので、"
            "その場合も init から作り直します"
        )
    if code != 0:
        die(f"gh stack view --json が終了コード {code} で失敗しました\n{output}")
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        die(f"gh stack view --json の出力を JSON として読めませんでした\n{output}")


def stack_branches(state: dict[str, Any]) -> list[str]:
    """stacked PR の並びを下（trunk 側）から上へ並べて返す。"""
    return [b["name"] for b in state.get("branches", [])]


def needs_rebase(state: dict[str, Any]) -> list[str]:
    """積み替えが要るブランチ名を返す。"""
    return [b["name"] for b in state.get("branches", []) if b.get("needsRebase")]


def rev(tree: str, ref: str) -> str | None:
    """ref の SHA を返す（無ければ None）。"""
    code, output = git(tree, ["rev-parse", ref], allow_fail=True)
    return output.split()[0] if code == 0 and output else None


def stale_record(tree: str, state: dict[str, Any]) -> list[dict[str, Any]]:
    """追跡情報に記録された位置と、実物のローカル ref・`origin/<ブランチ>` の食い違いを返す。

    **`gh stack view --json` は記録を返すので、別の場所で `gh stack sync` を通すとここがずれる。**
    ずれても `needsRebase` は false のままになる（記録の中では前後が揃っているため）ので、
    `gh stack` の出力だけを見ていると気づけない。実測では、別のクローンで sync を 2 回通した後も
    `show` は 2 回前の SHA を返し、needsRebase も全件 false だった（issue #39）。

    記録が古いまま `append` を続けると、`gh stack rebase` が古い位置を起点に積み直す。
    直し方は ../ledger.md「stacked PR の追跡情報が壊れたとき」。
    """
    rows: list[dict[str, Any]] = []
    for branch in state.get("branches", []):
        name = branch["name"]
        recorded = branch.get("head")
        local = rev(tree, f"refs/heads/{name}")
        remote = rev(tree, f"refs/remotes/origin/{name}")
        if recorded == local and (remote is None or local == remote):
            continue
        rows.append(
            {
                "branch": name,
                "recorded": (recorded or "")[:9],
                "local": (local or "")[:9],
                "origin": (remote or "")[:9],
                "why": (
                    "追跡情報の位置とローカル ref が違う"
                    if recorded != local
                    else "ローカル ref が origin と違う"
                ),
            }
        )
    return rows


def trunk_ahead(tree: str, trunk: str, bottom: str | None) -> dict[str, Any]:
    """`origin/<trunk>` の先端が土台に入っているかを見る。入っていなければ sync が要る。

    **`gh stack view --json` の `needsRebase` では代わりにならない。** あれはローカルの trunk ref と
    比べるので、**origin だけが進んだ状態では立たない**——ユーザーが自分のチェックアウトで
    `git pull` してローカルの ref が動いた瞬間に初めて true になる（実測。`precheck` の
    `git fetch origin` はリモート追跡 ref しか動かさないので、fetch では立たない）。
    それでは「trunk が進んだから sync する」の合図にならないので、origin と比べて決める。
    """
    git(tree, ["fetch", "origin", trunk], allow_fail=True)
    local = rev(tree, f"refs/heads/{trunk}")
    remote = rev(tree, f"refs/remotes/origin/{trunk}")
    if bottom is None or remote is None:
        return {"moved": False, "local": (local or "")[:9], "origin": (remote or "")[:9]}
    contained, _ = git(
        tree, ["merge-base", "--is-ancestor", remote, f"refs/heads/{bottom}"], allow_fail=True
    )
    return {
        "moved": contained != 0,
        "local": (local or "")[:9],
        "origin": remote[:9],
    }


def branch_holder(tree: str, branch: str) -> str | None:
    """そのブランチを checkout している別の worktree のパスを返す（無ければ None）。

    `gh stack add` と `gh stack rebase` は対象ブランチへ HEAD を移すので、他の worktree が
    握っていると終了コード 5 で落ちる。落ちてから読むより先に名指しで知らせる。
    """
    _, output = git(tree, ["worktree", "list", "--porcelain"], allow_fail=True)
    path: str | None = None
    for line in output.splitlines():
        if line.startswith("worktree "):
            path = line[len("worktree ") :].strip()
        elif (
            line.startswith("branch ")
            and path
            and line[len("branch ") :].strip() == f"refs/heads/{branch}"
            and os.path.abspath(path) != os.path.abspath(tree)
        ):
            return path
    return None


def ref_exists(tree: str, ref: str) -> bool:
    code, _ = git(tree, ["rev-parse", "--verify", "--quiet", ref], allow_fail=True)
    return code == 0


def set_upstream(tree: str, branch: str) -> bool:
    """`branch.<名前>.remote` / `.merge` を `origin/<branch>` に向ける。

    **これが無いと、そのブランチを checkout した人の `git pull` が必ず落ちる**
    （`There is no tracking information for the current branch.`）。実装エージェントは
    `git push origin HEAD:refs/heads/<ブランチ>` で push するので upstream は付かず、
    リードがここで作るローカルブランチが唯一の設定機会である。設定は `.git/config` に入るので、
    **ユーザーの作業ツリーでも効く**（worktree ごとではない）。

    `git branch` の既定（`branch.autoSetupMerge`）に任せず明示するのは 2 つの理由からである。
    既定を切っているユーザーでも付けるため、そして**既に upstream 無しで作られたブランチを
    `append` の叩き直しで直すため**（issue #39 で 4 本が upstream 無しで残った）。
    """
    code, _ = git(tree, ["branch", f"--set-upstream-to=origin/{branch}", branch], allow_fail=True)
    return code == 0


def ensure_local_branch(tree: str, branch: str) -> dict[str, Any]:
    """`origin/<branch>` を指すローカルブランチを用意し、upstream を付ける。

    **これが無いと `gh stack add` が別のものを積む。** 実装エージェントは
    `git push origin HEAD:refs/heads/<ブランチ>` で push するので（../implementation-prompt.md §1）、
    リードのリポジトリにはリモート追跡 ref しか無い。その状態で `gh stack add <ブランチ>` を叩くと、
    **存在しないブランチ名として扱われて HEAD の位置に空のブランチが作られ、成果の代わりに
    それが stacked PR に載る**（v0.1.0 で実測）。だから先にローカルブランチを作り、origin と揃える。

    **`--no-track` を付けない。** 付けると upstream が設定されず、`git pull` が落ちるブランチを
    ユーザーに渡すことになる（`set_upstream()`）。`gh stack` の操作は upstream を見ないので、
    付けても積み替えの挙動は変わらない。
    """
    git(tree, ["fetch", "origin", branch], allow_fail=True)
    remote = f"refs/remotes/origin/{branch}"
    local = f"refs/heads/{branch}"
    has_remote = ref_exists(tree, remote)
    has_local = ref_exists(tree, local)

    if not has_remote and not has_local:
        die(
            f"{branch} は origin にもローカルにもありません。ブランチ名が違うか、"
            "実装が push できていません（`gh stack add` に渡すと空のブランチを作ってしまうので"
            "ここで止めます）"
        )
    if not has_local:
        git(tree, ["branch", branch, remote])
        return {
            "step": "create-local-branch",
            "ok": True,
            "detail": f"{branch} ← origin/{branch}",
            "upstream": set_upstream(tree, branch),
        }
    if not has_remote:
        return {
            "step": "local-branch",
            "ok": True,
            "detail": "origin にはまだ無い（ローカルを使う）",
        }

    behind, _ = git(tree, ["merge-base", "--is-ancestor", local, remote], allow_fail=True)
    ahead, _ = git(tree, ["merge-base", "--is-ancestor", remote, local], allow_fail=True)
    if behind == 0 and ahead != 0:
        # ローカルが origin より古い（前回の積み替えより後に実装が push した）
        git(tree, ["branch", "-f", branch, remote])
        return {
            "step": "fast-forward-local",
            "ok": True,
            "detail": f"{branch} → origin/{branch}",
            "upstream": set_upstream(tree, branch),
        }
    if behind != 0 and ahead != 0:
        die(
            f"ローカルの {branch} と origin/{branch} が分岐しています。"
            f"`git -C {tree} log --oneline --graph {branch} origin/{branch}` で中身を見てから"
            "どちらを残すか決めてください（勝手に上書きしません）"
        )
    # 既にあるブランチにも付け直す（upstream 無しで作られた分がここで直る）
    return {
        "step": "local-branch",
        "ok": True,
        "detail": "origin と一致している",
        "upstream": set_upstream(tree, branch),
    }


def require_clean(tree: str, action: str) -> None:
    """未コミットの変更が残っていないことを確かめる。"""
    _, dirty = git(tree, ["status", "--porcelain"], allow_fail=True)
    if dirty:
        die(
            f"スタックツリーに未コミットの変更があります。{action}しません:\n{dirty}\n"
            "（前回のコンフリクト解消が途中で終わっている可能性があります。"
            "解消して commit するか、stack.py append --abort で stacked PR を積み替え前に戻します）"
        )


def conflicted_files(tree: str) -> list[str]:
    _, output = git(tree, ["diff", "--name-only", "--diff-filter=U"], allow_fail=True)
    return [f for f in output.splitlines() if f]


def child(name: str, argv: list[str], tree: str) -> tuple[bool, Any, str]:
    """付属スクリプトを 1 本呼び、(通ったか, JSON, 生の出力) を返す。

    同じ検査をここに書き写さないための入口である。書き写すと、片方だけ直したときに
    リードが叩く経路と検査の中身がずれる。`cwd` をスタックツリーにするのは、子側が `git` と
    `gh` を `-C` 無しで叩くためである（`--tree` を渡した意味をここで担保する）。
    """
    proc = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS, name), *argv],
        capture_output=True,
        text=True,
        check=False,
        cwd=tree,
    )
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        payload = None
    raw = "\n".join(p for p in (proc.stdout.strip(), proc.stderr.strip()) if p)
    return proc.returncode == 0, payload, raw


def entry(check: str, ok: bool | None, detail: Any, skipped: str | None = None) -> dict[str, Any]:
    row: dict[str, Any] = {"check": check, "ok": ok, "detail": detail}
    if skipped:
        row["skipped"] = skipped
    return row


def child_check(
    label: str,
    name: str,
    argv: list[str],
    *,
    tree: str,
    pick: Callable[[Any], Any],
    details: dict[str, str],
) -> dict[str, Any]:
    """付属スクリプトを 1 本呼んで 1 行の検査結果にする。落ちたら生の出力を details に残す。"""
    ok, payload, raw = child(name, argv, tree)
    if not ok:
        details[label] = raw
    return entry(label, ok, pick(payload) if payload else raw)


def run_finished_check(transcript_dir: str | None) -> dict[str, Any]:
    """run が本当に終わっているかを、完了通知の transcriptDir から確かめる。

    実行中の run に完了通知が誤って発火し、PR 番号もマージも含む捏造レポートが届いた実績がある
    （`integration.md` §1）。journal.jsonl に `"type":"result"` の行があれば終わっている。
    """
    if not transcript_dir:
        return entry(
            "run-finished",
            None,
            "--transcript-dir を渡すと journal.jsonl の result 行を確かめます",
            skipped="transcriptDir が渡されていない",
        )
    journal = os.path.join(transcript_dir, "journal.jsonl")
    if not os.path.isfile(journal):
        return entry("run-finished", False, f"{journal} がありません")
    with open(journal, encoding="utf-8") as fh:
        finished = any('"type":"result"' in line.replace(" ", "") for line in fh)
    return entry("run-finished", finished, journal)


def adversarial_check(tier: str, ran: str) -> dict[str, Any]:
    """敵対的レビューがこのタスクで 1 度でも結果を返したか。

    **ラウンド単位の reviewer-count では表せない**——敵対的レビューは各ループの 1 巡目だけ走るので、
    2 巡目で決着したタスクは reviewers も expectedReviewers も 1 になる。
    """
    if tier != "standard":
        return entry("adversarial-ran", None, f"tier {tier} では走らない", skipped="light タスク")
    return entry("adversarial-ran", ran == "true", f"adversarialRan {ran}")


def cmd_init(args: argparse.Namespace) -> None:
    """task-0 を stacked PR の土台として据える。`gh stack init` を 1 回だけ通す。

    追跡情報（`.git/gh-stack`）はこの worktree のものになる。**セッションを立て直したときは
    ここからやり直す**——別の worktree で作った stacked PR は見えない。
    """
    tree = require_tree(args.tree)
    existing = stack_state(tree, allow_missing=True)
    if existing and args.bottom in stack_branches(existing):
        emit(
            {
                "ok": True,
                "skipped": "already-initialized",
                "trunk": existing.get("trunk"),
                "branches": stack_branches(existing),
            },
            pretty=True,
        )
        warn(f"{args.bottom} は既に stacked PR に入っています。init は飛ばしました")
        raise SystemExit(0)

    holder = branch_holder(tree, args.bottom)
    if holder:
        die(
            f"{args.bottom} は {holder} で checkout されています。"
            "gh stack init はこのブランチへ HEAD を移すので、先にその worktree を外してください"
        )

    code, output = gh_stack(tree, ["init", "--base", args.trunk, args.bottom])
    if code != 0:
        die(f"gh stack init が終了コード {code} で失敗しました\n{output}")

    state = stack_state(tree)
    assert state is not None
    emit(
        {
            "ok": True,
            "trunk": state.get("trunk"),
            "branches": stack_branches(state),
            "output": output,
        },
        pretty=True,
    )


def cmd_precheck(args: argparse.Namespace) -> None:
    """積む前の検査を全部通す。**1 件でも落ちたら積まない。**

    `SKILL.md` §7「完了の根拠」と `integration.md` §1 の検査をここにまとめてある。通った検査は
    1 行で報告し、落ちた検査だけ子スクリプトの出力をそのまま出す（リードのコンテキストを
    使うのは、落ちた 1 件の中身だけで足りる）。
    """
    tree = require_tree(args.tree)
    details: dict[str, str] = {}

    # 既に stacked PR に入っていないか（再開・再実行で二重に積まないため）。**先に見る**
    # ——入っていれば起点からのコミットの数え方が変わるので、branch-and-commits を
    # 「成果が無い」と読んではいけない
    git(tree, ["fetch", "origin"])
    state = stack_state(tree)
    assert state is not None
    already = args.branch in stack_branches(state)

    branch_check = child_check(
        "branch-and-commits",
        "verify.py",
        ["--branch", args.branch, "--base", args.parent],
        tree=tree,
        pick=lambda p: p.get("commits"),
        details={} if already else details,
    )
    if already:
        branch_check["ok"] = None
        branch_check["skipped"] = "既に stacked PR へ積んであるので、起点が動いていて数が合わない"

    checks: list[dict[str, Any]] = [
        entry(
            "already-stacked",
            None,
            already,
            skipped="判定ではなく次の指示（true なら append の add を飛ばす）",
        ),
        run_finished_check(args.transcript_dir),
        branch_check,
        # レビューが全件決着したか
        child_check(
            "reviews-settled",
            "review.py",
            ["list", "--dir", args.dir, "--require-empty"],
            tree=tree,
            pick=lambda p: p.get("counts"),
            details=details,
        ),
        # レビュアーがそのラウンドで走るはずの体数だけ結果を返したか（返り値の突き合わせ）
        entry(
            "reviewer-count",
            args.reviewers >= args.expected_reviewers,
            f"reviewers {args.reviewers} / expectedReviewers {args.expected_reviewers}",
        ),
        adversarial_check(args.tier, args.adversarial_ran),
    ]

    clean = all(c["ok"] for c in checks if c["ok"] is not None)
    emit(
        {
            "clean": clean,
            "already_stacked": already,
            "task_dir": args.dir,
            "branch": args.branch,
            "parent": args.parent,
            "stack": stack_branches(state),
            "checks": checks,
            "failed_detail": details,
        },
        pretty=True,
    )
    if not clean:
        warn(
            "積まないでください。落ちた検査の出力は failed_detail にあります。"
            "resumeFrom を組み立ててワークフローを立て直します（SKILL.md §7）"
        )
    elif already:
        warn(
            f"{args.branch} は既に stacked PR に入っています。append は add を飛ばして"
            "積み替えと確認だけを行います"
        )
    raise SystemExit(0 if clean else 1)


def _finish_append(tree: str, trunk: str, branch: str, steps: list[dict[str, Any]]) -> None:
    """rebase が通った後の push → link → 確認。`append` と `--continue` の共通部分。"""
    code, output = gh_stack(tree, ["push"])
    if code != 0:
        die(f"gh stack push が終了コード {code} で失敗しました\n{output}")
    steps.append({"step": "push", "ok": True, "detail": output})

    state = stack_state(tree)
    assert state is not None
    order = stack_branches(state)
    if len(order) >= 2:
        # link は追加専用で、stacked PR の並びに合わない PR の base を直す。--open は付けない
        # （draft の PR を勝手にレビュー可能へ上げないため）
        code, output = gh_stack(tree, ["link", "--base", trunk, *order])
        if code != 0:
            die(
                f"gh stack link が終了コード {code} で失敗しました\n{output}\n"
                "ローカルの stacked PR と push は済んでいるので、原因を直してから "
                "stack.py append --continue で link からやり直せます"
            )
        steps.append({"step": "link", "ok": True, "detail": output})
    else:
        steps.append(
            {
                "step": "link",
                "ok": None,
                "skipped": "stacked PR が 1 本だけなので GitHub 上の Stack を作らない",
            }
        )

    state = stack_state(tree)
    assert state is not None
    order = stack_branches(state)
    pending = needs_rebase(state)

    # **土台（task-0）の needsRebase は落とす理由にしない。** `--no-trunk` を付けている
    # ので土台を trunk へ rebase することは無く、trunk が走行中に 1 コミット
    # でも進めば土台には必ずこの印が立つ。stacked PR そのものは正しく組めているので、これを失敗として
    # 返すと「trunk が動いた作業は毎回失敗する」ことになる（実測は
    # ../design-notes.md「gh stack v0.1.0 で確かめたこと」）。**仕上げで stack.py sync を
    # 通す必要がある**という情報として `trunk_moved` で返す。
    bottom = order[0] if order else None
    stale = [b for b in pending if b != bottom]
    ahead = trunk_ahead(tree, trunk, bottom)

    _, head = git(tree, ["symbolic-ref", "--short", "HEAD"], allow_fail=True)
    emit(
        {
            "ok": not stale,
            "branch": branch,
            "stack": order,
            "head": head,
            "needs_rebase": stale,
            "trunk_moved": ahead["moved"],
            "trunk": {"branch": trunk, **ahead},
            "stale_record": stale_record(tree, state),
            "steps": steps,
            "next": "stacked PR の先頭で検証コマンド一式を流す（integration.md §4）",
        },
        pretty=True,
    )
    if ahead["moved"]:
        warn(
            f"origin/{trunk} が進んでいるので土台（{bottom}）は trunk より古いままです。"
            "stacked PR の組み立ては済んでいるので積み替えは要りません。"
            "**仕上げで stack.py sync を通し、そのあとで検証を流し直します**（finish.md §8 の 2）"
        )
    if stale:
        warn(
            f"積み替えが残っています（{', '.join(stale)}）。stack.py append --continue を"
            "叩くか、原因を確かめてください"
        )
        raise SystemExit(1)


def cmd_append(args: argparse.Namespace) -> None:
    """決着したブランチを stacked PR の先頭へ積む。

    決まった順番は `gh stack add` → `gh stack rebase --no-trunk` → `gh stack push` →
    `gh stack link` → `gh stack view --json` での確認である。**`--no-trunk` を外さない**
    ——trunk を絡めると、ユーザーの作業ツリーが checkout している base ブランチを動かす
    経路に入る。
    """
    tree = require_tree(args.tree)
    steps: list[dict[str, Any]] = []

    if args.abort:
        code, output = gh_stack(tree, ["rebase", "--abort"])
        emit({"ok": code == 0, "aborted": True, "output": output}, pretty=True)
        raise SystemExit(0 if code == 0 else 1)

    if args.cont:
        # コンフリクトを解消した後の再開。解消済みの変更は `git add` 済みである前提で、
        # rebase が終わっていれば continue は「進行中の rebase が無い」と言って落ちるので、
        # そのときは push からやり直す
        code, output = gh_stack(tree, ["rebase", "--continue"])
        steps.append({"step": "rebase-continue", "ok": code == 0, "detail": output})
        if code != 0:
            files = conflicted_files(tree)
            if files:
                emit(
                    {
                        "ok": False,
                        "stage": "rebase",
                        "conflict": True,
                        "files": files,
                        "output": output,
                        "steps": steps,
                    },
                    pretty=True,
                )
                warn(
                    "まだコンフリクトが残っています。解消して `git add` してから"
                    " stack.py append --continue を叩いてください"
                )
                raise SystemExit(1)
            warn(f"進行中の rebase はありませんでした（{output}）。push から続けます")
        _finish_append(tree, args.trunk, args.branch or "", steps)
        return

    if not args.branch:
        die("--branch が要ります（--continue と --abort のときだけ省けます）")

    state = stack_state(tree)
    assert state is not None
    order = stack_branches(state)

    if args.branch in order:
        steps.append({"step": "add", "ok": None, "skipped": "既に stacked PR に入っている"})
    else:
        holder = branch_holder(tree, args.branch)
        if holder:
            die(
                f"{args.branch} は {holder} で checkout されています。"
                "gh stack add はこのブランチへ HEAD を移すので積めません。"
                "先に worktree.py remove でエージェントの worktree を外してください"
                "（integration.md §2）"
            )
        require_clean(tree, "積み")
        # `gh stack add` に渡す前にローカルブランチを origin と揃える（無いと空のブランチを積む）
        steps.append(ensure_local_branch(tree, args.branch))

        top = order[-1]
        if state.get("currentBranch") != top:
            # `gh stack add` は stacked PR の先頭に載っているときだけ通る
            git(tree, ["switch", top])
            steps.append({"step": "switch-to-top", "ok": True, "detail": top})

        code, output = gh_stack(tree, ["add", args.branch])
        if code == GH_STACK_CHECKOUT_BUSY:
            die(
                f"gh stack add が checkout の衝突で失敗しました\n{output}\n"
                "そのブランチを握っている worktree を外してからやり直してください"
            )
        if code != 0:
            die(f"gh stack add が終了コード {code} で失敗しました\n{output}")
        steps.append({"step": "add", "ok": True, "detail": output})

    code, output = gh_stack(tree, ["rebase", "--no-trunk"])
    if code != 0:
        files = conflicted_files(tree)
        emit(
            {
                "ok": False,
                "stage": "rebase",
                "conflict": bool(files),
                "branch": args.branch,
                "files": files,
                "output": output,
                "steps": steps,
            },
            pretty=True,
        )
        warn(
            "積み替えで止まりました。コンフリクトなら解消して `git add` してから "
            "stack.py append --continue、諦めるなら stack.py append --abort で"
            "積み替え前に戻せます（integration.md §3）"
        )
        raise SystemExit(1)
    steps.append({"step": "rebase", "ok": True, "detail": output})

    _finish_append(tree, args.trunk, args.branch, steps)


def bottom_shape(tree: str, trunk: str, bottom: str) -> dict[str, Any]:
    """土台が「空コミット 1 つ」のままかを見る。

    **`gh stack sync` の連鎖 rebase で土台の空コミットが落ち、trunk のマージコミットを平坦化した
    複製に置き換わった実績がある**（issue #39）。そうなると stack PR は「1 コミット / 0 ファイル」の
    見た目のまま中身だけ別物になり、**マージすると同じ subject のコミットが base ブランチに 1 つ増える。**
    土台は進捗の置き場なので（../design-notes.md「なぜ最初に draft の stack PR を作るか」）、
    黙って入れ替わると気づけない。

    **機序は分かっていない**（v0.1.0 で 3 回追試して毎回残った）ので、原因ではなく結果で見る。
    コミットの数・変更ファイル数・subject を返し、trunk の先端と同じ subject が混ざっていたら
    平坦化の疑いとして立てる。空コミットの文面はリポジトリの慣習に合わせるので固定値で照合しない。
    """
    _, count = git(tree, ["rev-list", "--count", f"{trunk}..{bottom}"], allow_fail=True)
    _, subjects = git(tree, ["log", "--format=%s", f"{trunk}..{bottom}"], allow_fail=True)
    _, changed = git(tree, ["diff", "--name-only", trunk, bottom], allow_fail=True)
    _, trunk_subject = git(tree, ["log", "-1", "--format=%s", trunk], allow_fail=True)
    lines = [s for s in subjects.splitlines() if s]
    files = [f for f in changed.splitlines() if f]
    duplicated = trunk_subject in lines
    return {
        "branch": bottom,
        "commits": int(count) if count.isdigit() else count,
        "files": len(files),
        "subjects": lines,
        "looks_flattened": duplicated,
        "ok": len(lines) == 1 and not files and not duplicated,
    }


def cmd_sync(args: argparse.Namespace) -> None:
    """trunk が進んだ stacked PR を origin と合わせる。`gh stack sync` の前後を見る。

    **ユーザーに `gh stack sync` を任せない。** 別の場所で通されると 4 つ起きる（issue #39）。

    1. 5 本すべてが force push で入れ替わり、**別のクローンで通すと**そちらのローカル ref が
       取り残される
    2. 土台の空コミットが落ちた（`bottom_shape()`）
    3. **スタックツリーの追跡情報が更新されないので `stack.py show` が古い位置と誤検知を返す**
       （`stale_record()`）
    4. sync で trunk 側の変更が入るので、積んだ後に流した検証はその内容を見ていない

    スタックツリーの中で通せば 1 と 3 は起きない（ref は共有で、追跡情報は `gh stack` 自身が
    更新する）。残る 2 と 4 をここで受け持つ——2 は検査して報告し、4 は `next` で検証の
    流し直しを指す。**直しはしない**（土台を作り直すと force push でユーザーが読んでいる PR の
    コミットが入れ替わるので、判断をユーザーに渡す）。

    **動く前の検査で 1 件でも当たったら 1 つも動かさない。** `gh stack sync` は途中で落ちても
    そこまでの操作が残ることがある（実測: 作業ツリーが汚れていると trunk の fast-forward だけ
    済んで rebase の前に落ち、「All branches restored」も出なかった）。
    """
    tree = require_tree(args.tree)
    state = stack_state(tree)
    assert state is not None
    order = stack_branches(state)
    trunk = args.trunk
    blockers: list[dict[str, Any]] = []

    _, dirty = git(tree, ["status", "--porcelain"], allow_fail=True)
    if dirty:
        blockers.append({"check": "clean-tree", "detail": dirty})

    # スタック内のブランチを別 worktree が握っていると、sync は rebase の手前で落ちる
    # （実測: `✗ could not start rebase … already checked out … All branches restored`）
    held = [{"branch": b, "worktree": h} for b in order if (h := branch_holder(tree, b))]
    if held:
        blockers.append({"check": "branch-checked-out-elsewhere", "detail": held})

    # **trunk を握っている worktree がいて、その trunk が origin より古いなら止める。**
    # `gh stack sync` は trunk のローカル ref を fast-forward するが、**別の worktree が
    # checkout 済みでも動かす**（git 2.34.1 で実測。その worktree は HEAD だけ進んで作業ツリーが
    # 取り残され、`git status` が大量の変更に見える）。git が拒むかはバージョン任せなので
    # （2.51.1 は `git branch -f` を拒み、`git update-ref` は両方で通る）、ここで名指しして
    # ユーザーに渡す
    ahead = trunk_ahead(tree, trunk, order[0] if order else None)
    holder = branch_holder(tree, trunk)
    if holder and ahead["local"] != ahead["origin"]:
        blockers.append(
            {
                "check": "trunk-checked-out-elsewhere",
                "detail": {
                    "worktree": holder,
                    "branch": trunk,
                    "local": ahead["local"],
                    "origin": ahead["origin"],
                },
            }
        )

    before = {b: (rev(tree, f"refs/heads/{b}") or "")[:9] for b in order}
    if blockers:
        emit(
            {"ok": False, "stage": "precheck", "blockers": blockers, "before": before}, pretty=True
        )
        warn(
            "sync しませんでした。clean-tree なら未コミットの変更を片づけ、"
            "branch-checked-out-elsewhere なら worktree.py remove でその worktree を外し、"
            "trunk-checked-out-elsewhere は**自分で直さず**、その worktree のパスと両方の SHA を"
            "ユーザーに報告して更新してもらってください（sync はその HEAD を動かします）"
        )
        raise SystemExit(1)

    code, output = gh_stack(tree, ["sync"])
    git(tree, ["fetch", "origin"], allow_fail=True)
    after = {b: (rev(tree, f"refs/heads/{b}") or "")[:9] for b in order}
    moved = [
        {
            "branch": b,
            "before": before[b],
            "after": after[b],
            "origin": (rev(tree, f"refs/remotes/origin/{b}") or "")[:9],
        }
        for b in order
    ]
    if code != 0:
        emit(
            {"ok": False, "stage": "sync", "output": output, "branches": moved},
            pretty=True,
        )
        warn(
            "gh stack sync が落ちました。**どこまで進んだかは branches の before / after で"
            "確かめてください**（「All branches restored」が出ていても trunk の fast-forward は"
            "残ります）。コンフリクトなら stack.py append --continue で解消の続きに入れます"
        )
        raise SystemExit(1)

    state = stack_state(tree)
    assert state is not None
    order = stack_branches(state)
    bottom = order[0] if order else None
    pending = needs_rebase(state)
    shape = bottom_shape(tree, trunk, bottom) if bottom else None
    ahead = trunk_ahead(tree, trunk, bottom)
    records = stale_record(tree, state)

    ok = not [b for b in pending if b != bottom] and not records and not ahead["moved"]
    emit(
        {
            "ok": ok,
            "stack": order,
            "trunk": {"branch": trunk, **ahead},
            "branches": moved,
            "bottom": shape,
            "needs_rebase": [b for b in pending if b != bottom],
            "stale_record": records,
            "output": output,
            "next": (
                "スタックツリーの先頭で brief.md の検証コマンド一式を流し直す"
                "（integration.md §4）。sync で trunk 側の変更が入ったので、"
                "sync 前の検証結果はいまの内容を見ていない"
            ),
        },
        pretty=True,
    )
    if shape and not shape["ok"]:
        warn(
            f"土台（{bottom}）が空コミット 1 つではありません（bottom を見てください）。"
            "looks_flattened が true なら、trunk のコミットを平坦化した複製に置き換わっています。"
            "**このままマージすると同じ subject のコミットが "
            f"{trunk} に 1 つ増えます。** 直すには force push が要るので、"
            "bottom の中身をユーザーに示して判断を渡してください"
        )
    if records:
        warn(
            "追跡情報が実物と食い違っています（stale_record）。"
            "ledger.md「stacked PR の追跡情報が壊れたとき」で直してから積み足してください"
        )
    raise SystemExit(0 if ok else 1)


def cmd_show(args: argparse.Namespace) -> None:
    """いまの stacked PR の並びと、積み替えが要るブランチを出す。

    `needs_rebase` から土台を除くのは `_finish_append` と同じ理由である——`--no-trunk` では
    土台を trunk へ rebase しないので、trunk が動けば土台には必ず印が立つ。そちらは
    `trunk_moved` で別に返す。

    **`branches` と `detail` の SHA は追跡情報に記録された位置である。** 実物とずれていれば
    `stale_record` に出るので、そこが空であることを確かめてから読む。
    """
    tree = require_tree(args.tree)
    state = stack_state(tree)
    assert state is not None
    order = stack_branches(state)
    pending = needs_rebase(state)
    bottom = order[0] if order else None
    trunk = state.get("trunk") or ""
    ahead = trunk_ahead(tree, trunk, bottom)
    emit(
        {
            "trunk": trunk,
            "current": state.get("currentBranch"),
            "branches": order,
            "needs_rebase": [b for b in pending if b != bottom],
            "trunk_moved": ahead["moved"],
            "trunk_position": ahead,
            "stale_record": stale_record(tree, state),
            "detail": state.get("branches"),
        },
        pretty=True,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="stack.py", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def add_tree(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--tree",
            required=True,
            help="スタックツリーのパス（.claude/worktrees/supervisor-<作業名>）",
        )

    p = sub.add_parser("init", help="task-0 を stacked PR の土台として据える")
    add_tree(p)
    p.add_argument("--trunk", required=True, help="stacked PR の土台が向く base ブランチ")
    p.add_argument("--bottom", required=True, help="土台のブランチ名（stack/<作業名>--task-0）")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("precheck", help="積む前の検査を全部通す")
    add_tree(p)
    p.add_argument("--branch", required=True, help="タスクブランチ名")
    p.add_argument(
        "--parent",
        required=True,
        help="そのブランチを切った起点のブランチ名（state.json の parent。PR の base でもある）",
    )
    p.add_argument("--dir", required=True, help="review.json の置き場（<ベース>/notes/task<番号>）")
    p.add_argument("--reviewers", required=True, type=int, help="返り値の reviewers")
    p.add_argument(
        "--expected-reviewers", required=True, type=int, help="返り値の expectedReviewers"
    )
    p.add_argument("--tier", required=True, choices=("standard", "light"), help="返り値の tier")
    p.add_argument(
        "--adversarial-ran",
        required=True,
        choices=("true", "false"),
        help="返り値の adversarialRan（standard で false なら落とす）",
    )
    p.add_argument(
        "--transcript-dir",
        help="完了通知の transcriptDir。渡すと journal.jsonl の result 行で run の終了を確かめる",
    )
    p.set_defaults(func=cmd_precheck)

    p = sub.add_parser("append", help="決着したブランチを stacked PR の先頭へ積む")
    add_tree(p)
    p.add_argument("--trunk", required=True, help="stacked PR の土台が向く base ブランチ")
    p.add_argument("--branch", help="積むタスクブランチ名（--continue / --abort では省ける）")
    p.add_argument(
        "--continue",
        dest="cont",
        action="store_true",
        help="コンフリクトを解消した後に続ける（rebase --continue → push → link）",
    )
    p.add_argument(
        "--abort",
        action="store_true",
        help="積み替えを中止して、全ブランチを積み替え前の位置に戻す",
    )
    p.set_defaults(func=cmd_append)

    p = sub.add_parser("sync", help="trunk が進んだ stacked PR を origin と合わせる")
    add_tree(p)
    p.add_argument("--trunk", required=True, help="stacked PR の土台が向く base ブランチ")
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("show", help="いまの stacked PR の並びを出す")
    add_tree(p)
    p.set_defaults(func=cmd_show)

    return parser


if __name__ == "__main__":
    parsed = build_parser().parse_args()
    parsed.func(parsed)
