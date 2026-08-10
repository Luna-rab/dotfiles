#!/usr/bin/env python3
"""team-supervisor スキルの作業ツリー・報告確認・状態ファイルの操作。

判断を伴わない手順をここに閉じ込め、エージェントは引数だけを渡す。狙いは 3 つ。

1. `--path-format=absolute` の付け忘れによる作業ツリーの誤判定を構造的に消す
   （省くとサブディレクトリで --git-common-dir だけが相対パスになり、メインツリーを
   worktree と誤判定してメインツリーにコードを書き込む）。
2. サブリーダーの承認報告を、リードが手順を飛ばせない形（終了コード）で実物と突き合わせる。
3. 後戻りできない操作（worktree の削除・未検証コミットの push）に安全条件の検査を付ける。

サブコマンド:
  where         実装 subagent が戻るべき worktree の絶対パスか STAY を 1 行返す（作用なし）
  base-dir      brief.md / map.md / ledger.md を置くディレクトリの絶対パスを 1 行返す
  verify        承認報告をブランチ・コミット・PR の実物で確かめる
  wt-remove     残留 worktree を、安全条件を検査してから消す
  wt-rescue     落ちた worktree の未コミットの変更を保全 commit して push する
  state-init    状態ディレクトリ直下の状態ファイルを消す（ベース資料は残す）
  state-branch  branch-<agentId> を書く（SubagentStop フックが読む）
  state-resume  resume-count-task<番号> を加算する（上限を超えたら終了コード 1）
  state-clear   resume-count-task<番号> を消す
  state-block   blocked-<agentId> を作る（押し戻しと再開を止める目印）

`where` と `base-dir` は stdout に 1 行（`STAY` か絶対パス）を出す。EnterWorktree(path:) や
Read にそのまま渡せるようにするため。**どちらも worktree を作らない**（`where` は問い合わせ
専用である。以前は subleader / review 用に `git worktree add` していたが、`isolation:
"worktree"` で起動した subagent は Bash が起動時の worktree に固定され、新しく作った
worktree ではコマンドを 1 つも実行できないので、作った実体が使えないまま残っていた）。
他のサブコマンドは JSON を出す。

レビューコメントの操作は gh-review.py が持つ。このスクリプトはそちらを呼ばない
（失敗の出どころを分け、allowed-tools でサブコマンドを絞っている意味を保つため）。
"""

import argparse
import json
import os
import subprocess
import sys

# リードが同じサブリーダーを SendMessage で再開してよい回数（SKILL.md §7）
RESUME_LIMIT = 3

RESCUE_MESSAGE = "wip: 中断時点の保全（検証未実施）"

# where が扱う役割。`isolation` を付けずに起動する実装 subagent だけが対象である
# （isolation 付きの subagent は再開しても自分の worktree のままなので、聞く必要がない）
WHERE_ROLES = ("impl",)

# ベース 3 資料のファイル名。base-dir が返すディレクトリの中にこの名前で置く
BASE_FILES = ("brief.md", "map.md", "ledger.md")


def die(message):
    print(f"lane: {message}", file=sys.stderr)
    sys.exit(1)


def run(cmd, allow_fail=False):
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 and not allow_fail:
        die(f"{' '.join(cmd)} が失敗しました\n{proc.stderr.strip()}")
    return proc.returncode, proc.stdout.strip()


def out(cmd):
    """成功を前提に標準出力だけを返す。"""
    return run(cmd)[1]


# ---------------------------------------------------------------- 場所の解決


def git_dir():
    return out(["git", "rev-parse", "--path-format=absolute", "--git-dir"])


def git_common_dir():
    """共有の .git の絶対パス。worktree の中からでもメインツリーの .git を指す。

    `--path-format=absolute` を省いてはならない。省くとサブディレクトリにいるとき
    --git-common-dir だけが相対パス（`../../../.git`）になり、下の in_main_worktree が
    メインツリーを worktree と誤判定する。実際に観測された挙動である。
    """
    return out(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"])


def in_main_worktree():
    return git_dir() == git_common_dir()


def repo_root():
    return os.path.dirname(git_common_dir())


def state_dir():
    """フックと共有する状態ディレクトリ。scripts/subagent-stop.sh と同じ式で求める。"""
    return os.path.join(git_common_dir(), "team-supervisor")


def base_dir(work):
    """ベース 3 資料（brief.md / map.md / ledger.md）を置くディレクトリ。

    作業ツリーではなく `.git` の下に置くので、git の追跡対象に入らず PR の差分も汚さない。
    それでいて全 worktree から同じ絶対パスに解決でき、リポジトリのクローンが残っている
    限りセッションをまたいで残る（/tmp は WSL の再起動で消えるので使えない）。
    """
    if not work or work != os.path.basename(work) or work in (".", ".."):
        die(f"--work にディレクトリ区切りを含められません: {work!r}")
    return os.path.join(state_dir(), "base", work)


def worktrees_dir():
    return os.path.join(repo_root(), ".claude", "worktrees")


def agent_worktree(agent):
    """`isolation: "worktree"` で起動した subagent に割り当てられるパス。"""
    return os.path.join(worktrees_dir(), f"agent-{agent}")


def listed_worktrees():
    """`git worktree list` が挙げるパスの集合。

    「もう無い」の判定にディレクトリの実在ではなくこれを使う。組み立てたパスに何も
    無いことを `already-gone` と読むと、対象を取り違えていても成功したように見える。
    """
    paths = set()
    for line in out(["git", "worktree", "list", "--porcelain"]).splitlines():
        if line.startswith("worktree "):
            paths.add(os.path.realpath(line[len("worktree "):]))
    return paths


def ensure_state_dir():
    sd = state_dir()
    os.makedirs(sd, exist_ok=True)
    return sd


def emit(payload, pretty=False):
    print(json.dumps(payload, ensure_ascii=False, indent=2 if pretty else None))
    # このあと stderr に理由を書くサブコマンドがある。先に流さないと端末上で順序が入れ替わり、
    # 「何が起きたか」より「どうすべきか」が先に見える
    sys.stdout.flush()


# ---------------------------------------------------------------- where


def cmd_where(args):
    """実装 subagent がどこで作業すべきかを 1 行返す。worktree は作らない。

    対象が実装 subagent だけなのは、`isolation` を付けずに起動する唯一の subagent だから
    である。`isolation: "worktree"` 付きの subagent（サブリーダー・レビュアー）は再開しても
    自分の worktree のままで、Bash がそこに固定されるので、聞く必要がない。
    """
    # 実装 subagent はサブリーダーの worktree を共有する。新しくは作らない
    if not in_main_worktree():
        print("STAY")
        return
    if not args.parent_worktree:
        die(
            "メインの作業ツリーに再開されましたが --parent-worktree が渡されて"
            "いません。コードを 1 行も書かずにサブリーダーへ報告して終えてください"
        )
    target = os.path.abspath(args.parent_worktree)
    if not os.path.isdir(target):
        die(f"--parent-worktree のパスがありません: {target}")
    print(target)


# ---------------------------------------------------------------- base-dir


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
                f"{target} に {', '.join(missing)} がありません。ベース資料は git の"
                "追跡対象外なので checkout では揃いません。実装・検証に入らず、"
                "リード（サブリーダーなら親）へ報告して止まってください"
            )
    else:
        os.makedirs(target, exist_ok=True)
    print(target)


# ---------------------------------------------------------------- verify


def cmd_verify(args):
    """報告ではなく実物を見る。

    実行中の run に完了通知が誤って発火し、PR 番号もマージも含む捏造レポートが届いた
    実績があるため、リードは報告に基づいて動く前にこれを通す。
    """
    run(["git", "fetch", "origin"])
    checks = []

    code, _ = run(
        ["git", "ls-remote", "--exit-code", "--heads", "origin",
         f"refs/heads/{args.branch}"],
        allow_fail=True,
    )
    branch_exists = code == 0
    checks.append({"check": "branch-exists", "ok": branch_exists,
                   "detail": args.branch})

    base_code, _ = run(
        ["git", "rev-parse", "--verify", "--quiet", f"origin/{args.base}"],
        allow_fail=True,
    )
    if base_code != 0:
        die(f"base ブランチ origin/{args.base} がありません（fetch 後に確認）")

    commits = None
    if branch_exists:
        commits = int(out(["git", "rev-list", "--count",
                           f"origin/{args.base}..origin/{args.branch}"]))
        checks.append({"check": "commits-on-branch", "ok": commits > 0,
                       "detail": commits})

    pr = json.loads(out(["gh", "pr", "view", str(args.pr),
                         "--json", "state,headRefName,baseRefName"]))
    checks.append({"check": "pr-head", "ok": pr["headRefName"] == args.branch,
                   "detail": pr["headRefName"]})
    checks.append({"check": "pr-base", "ok": pr["baseRefName"] == args.base,
                   "detail": pr["baseRefName"]})
    # 取り込み前なので OPEN であるべき。MERGED なら既に入っている可能性があるので、
    # git merge-base --is-ancestor で二重取り込みかどうかを確かめる（integration.md §3）
    checks.append({"check": "pr-open", "ok": pr["state"] == "OPEN",
                   "detail": pr["state"]})

    clean = all(c["ok"] for c in checks)
    emit({"clean": clean, "branch": args.branch, "base": args.base,
          "pr": args.pr, "commits": commits, "pr_state": pr["state"],
          "checks": checks}, pretty=True)
    if not clean:
        print(
            "lane: 実物と報告が合いません。取り込まずにサブリーダーへ差し戻してください",
            file=sys.stderr,
        )
    sys.exit(0 if clean else 1)


# ---------------------------------------------------------------- worktree の後始末


def cmd_wt_remove(args):
    path = agent_worktree(args.agent)
    # 判定は `git worktree list` で行う。ディレクトリの実在だけを見ると、パスを取り違えて
    # いても「もう無い」と読めてしまい、消えていないのに後始末が済んだように見える
    if os.path.realpath(path) not in listed_worktrees():
        run(["git", "worktree", "prune"])
        emit({"removed": False, "reason": "already-gone", "path": path})
        return

    # 消してよいのは、統合を終えたタスクと、再開を打ち切ったタスクだけ。前者はスクリプト
    # からは分からない（台帳とタスクリストにしかない）のでリードが --merged で明示する
    blocked = os.path.exists(os.path.join(state_dir(), f"blocked-{args.agent}"))
    if not blocked and not args.merged:
        die(
            f"agent-{args.agent} は打ち切り済み（blocked-{args.agent} が無い）でも"
            "統合済み（--merged が無い）でもありません。まだ再開しうる worktree は"
            "消しません"
        )

    if not os.path.isdir(path):
        # 登録は残っているが実体が消えている（エージェントが自分のツリーを消した、
        # 外から rm された）。未コミットの変更を確かめる相手がいないので prune で片づける
        run(["git", "worktree", "prune"])
        emit({"removed": True, "reason": "pruned-stale-registration", "path": path,
              "authorized_by": "blocked-marker" if blocked else "--merged"})
        return

    dirty = out(["git", "-C", path, "status", "--porcelain"])
    if dirty:
        die(
            f"{path} に未コミットの変更が {len(dirty.splitlines())} 件あります。先に "
            f"wt-rescue --agent {args.agent} --branch <タスクブランチ> で保全してください"
        )

    # --force を付けない。上の検査を抜けた場合も git 自身に最後の砦を担わせる
    proc = subprocess.run(["git", "worktree", "remove", path],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        # 生きている subagent の worktree は git がロックしている。統合を終えたタスクでも
        # サブリーダーが報告を終えてまだ終了していないことがあるので、他の失敗と区別して
        # 返す（呼び出し側は終了通知を待って再実行すればよい）
        stderr = proc.stderr.strip()
        locked = "locked working tree" in stderr
        emit({
            "removed": False,
            "reason": "locked" if locked else "remove-failed",
            "path": path,
            "detail": stderr,
            "hint": "この worktree を使っている subagent がまだ生きています。終了通知を"
                    "待ってから同じコマンドを再実行してください"
                    if locked else "git worktree remove が失敗しました",
        })
        sys.exit(1)
    run(["git", "worktree", "prune"])
    emit({"removed": True, "path": path,
          "authorized_by": "blocked-marker" if blocked else "--merged"})


def cmd_wt_rescue(args):
    path = agent_worktree(args.agent)
    if not os.path.isdir(path):
        die(f"worktree がありません: {path}")

    dirty = out(["git", "-C", path, "status", "--porcelain"])
    if not dirty:
        emit({"rescued": False, "reason": "clean", "path": path})
        return

    run(["git", "-C", path, "add", "-A"])
    run(["git", "-C", path, "commit", "-m", RESCUE_MESSAGE])
    sha = out(["git", "-C", path, "rev-parse", "HEAD"])
    run(["git", "-C", path, "push", "origin", f"HEAD:refs/heads/{args.branch}"])
    emit({
        "rescued": True,
        "path": path,
        "branch": args.branch,
        "commit": sha,
        "files": len(dirty.splitlines()),
        "note": f"検証していない保全コミットです。台帳に 1 行残し、再開するサブリーダーへ"
                f"「前コミット {sha[:12]} は未検証の保全である」と伝えてください",
    }, pretty=True)


# ---------------------------------------------------------------- 状態ファイル


def cmd_state_init(args):
    sd = state_dir()
    os.makedirs(sd, exist_ok=True)
    # 前回の実行が残したカウンタ・目印・ブランチ登録が新しい実行の判定を狂わせないよう、
    # タスクを登録する前に消す。**ディレクトリごと消してはならない**——同じ場所の
    # base/<作業名>/ にベース 3 資料があり、state-init はそれを書いた後に走る（SKILL.md §6）
    removed = []
    for name in sorted(os.listdir(sd)):
        path = os.path.join(sd, name)
        if os.path.isfile(path):
            os.remove(path)
            removed.append(name)
    emit({"state_dir": sd, "removed": removed,
          "kept": [n for n in sorted(os.listdir(sd)) if os.path.isdir(os.path.join(sd, n))]})


def cmd_state_branch(args):
    target = os.path.join(ensure_state_dir(), f"branch-{args.agent}")
    with open(target, "w", encoding="utf-8") as fh:
        fh.write(args.branch)
    emit({"wrote": target, "branch": args.branch})


def cmd_state_resume(args):
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
        print(
            f"lane: task{args.task} の再開は {count} 回目で上限 {RESUME_LIMIT} を"
            "超えました。再開せず、台帳でそのタスクを blocked にし、state-block を"
            "実行してユーザーへ上げてください。他のタスクは止めません",
            file=sys.stderr,
        )
        sys.exit(1)


def cmd_state_clear(args):
    target = os.path.join(state_dir(), f"resume-count-task{args.task}")
    existed = os.path.exists(target)
    if existed:
        os.remove(target)
    emit({"cleared": target, "existed": existed})


def cmd_state_block(args):
    target = os.path.join(ensure_state_dir(), f"blocked-{args.agent}")
    with open(target, "a", encoding="utf-8"):
        pass
    emit({"blocked": target})


# ---------------------------------------------------------------- 引数


def build_parser():
    parser = argparse.ArgumentParser(prog="lane.py", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser(
        "where",
        help="実装 subagent が戻るべき worktree の絶対パスか STAY を 1 行返す（作用なし）",
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

    p = sub.add_parser("verify", help="承認報告を実物で確かめる")
    p.add_argument("--branch", required=True, help="タスクブランチ名")
    p.add_argument("--base", required=True, help="base ブランチ名（topic/<作業名>）")
    p.add_argument("--pr", required=True, help="PR 番号")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("wt-remove", help="残留 worktree を検査してから消す")
    p.add_argument("--agent", required=True, help="サブリーダーの agentId")
    p.add_argument("--merged", action="store_true",
                   help="このタスクを topic へ取り込み終えたことを明示する")
    p.set_defaults(func=cmd_wt_remove)

    p = sub.add_parser("wt-rescue", help="未コミットの変更を保全 commit して push する")
    p.add_argument("--agent", required=True)
    p.add_argument("--branch", required=True, help="push 先のタスクブランチ名")
    p.set_defaults(func=cmd_wt_rescue)

    p = sub.add_parser("state-init",
                       help="状態ディレクトリ直下のファイルを消す（base/ は残す）")
    p.set_defaults(func=cmd_state_init)

    p = sub.add_parser("state-branch", help="branch-<agentId> を書く")
    p.add_argument("--agent", required=True)
    p.add_argument("--branch", required=True)
    p.set_defaults(func=cmd_state_branch)

    p = sub.add_parser("state-resume", help="再開回数を加算し、上限超過なら終了コード 1")
    p.add_argument("--task", required=True)
    p.set_defaults(func=cmd_state_resume)

    p = sub.add_parser("state-clear", help="再開回数を消す")
    p.add_argument("--task", required=True)
    p.set_defaults(func=cmd_state_clear)

    p = sub.add_parser("state-block", help="blocked-<agentId> を作る")
    p.add_argument("--agent", required=True)
    p.set_defaults(func=cmd_state_block)

    return parser


if __name__ == "__main__":
    parsed = build_parser().parse_args()
    parsed.func(parsed)
