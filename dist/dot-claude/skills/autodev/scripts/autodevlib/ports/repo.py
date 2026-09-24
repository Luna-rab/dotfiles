"""git の操作と、worktree に置くフックの出し入れ。

**git と `gh stack` を叩くのは run の worktree（`<run>/tree`）1 か所だけである。**
`gh stack` の追跡情報は worktree ごとに別なので（別の worktree で `gh stack view` を叩くと
終了コード 2 で "not part of a stack"）、場所を固定しないと stacked PR が見えなくなる。

worktree は**対象リポジトリの外**（`~/.local/state/autodev/<作業名>/tree`）に作る。
対象リポジトリに `.gitignore` を 1 行も足さずに済む。
"""

from __future__ import annotations

import json
import os

from ..config import paths
from ..core import globs
from . import files, proc


def git(tree: str, *args: str, timeout: int = 600) -> proc.Run:
    return proc.run(["git", "-C", tree, *args], timeout=timeout)


def repo_root(start: str) -> str | None:
    got = proc.run(["git", "-C", start, "rev-parse", "--show-toplevel"])
    return got.out.strip() if got.ok else None


def default_branch(repo: str) -> str:
    """origin の既定ブランチ。取れなければ main を返す（呼び出し側が `--base` で上書きできる）。"""
    got = proc.run(["git", "-C", repo, "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD"])
    if got.ok and got.out.strip():
        return got.out.strip().rsplit("/", 1)[-1]
    for name in ("main", "master"):
        if proc.run(["git", "-C", repo, "show-ref", "--verify", f"refs/remotes/origin/{name}"]).ok:
            return name
    return "main"


def remote_branch_exists(repo: str, branch: str) -> bool:
    return proc.run(["git", "-C", repo, "ls-remote", "--exit-code", "--heads", "origin", branch]).ok


def fetch(repo: str) -> proc.Run:
    return proc.run(["git", "-C", repo, "fetch", "--prune", "origin"], timeout=900)


def create_stack_base(repo: str, tree: str, branch: str, base: str) -> proc.Run:
    """土台ブランチと worktree を作る。

    土台に空コミット 1 つを載せるのは、**base と差分が 0 の状態では `gh pr create` が
    `No commits between …` で失敗する**ためである。

    先に `worktree prune` を通す。**run のディレクトリを手で消しても git 側の登録は残り**、
    そのままだとブランチが「別の場所でチェックアウト中」として扱われて作り直せない。
    """
    proc.run(["git", "-C", repo, "worktree", "prune"])
    made = proc.run(["git", "-C", repo, "branch", "--no-track", branch, f"origin/{base}"])
    if not made.ok and "already exists" not in (made.err or ""):
        return made
    os.makedirs(os.path.dirname(tree), exist_ok=True)
    added = proc.run(["git", "-C", repo, "worktree", "add", tree, branch])
    if not added.ok and "already used by worktree" not in (added.err or ""):
        return added
    if commit_count(tree, f"origin/{base}", branch) == 0:
        return git(tree, "commit", "--allow-empty", "-m", f"chore: {branch} の土台")
    return proc.Run(0, "", "")


def start_task_branch(tree: str, branch: str, parent: str) -> proc.Run:
    """タスクのブランチを parent の上に作って乗り移る。既にあればそれに乗る。"""
    if git(tree, "rev-parse", "--verify", "--quiet", branch).ok:
        return git(tree, "switch", branch)
    return git(tree, "switch", "-c", branch, parent)


def head_sha(tree: str) -> str | None:
    """いまの HEAD。**テスト作成段の後にテストが動いていないか**を見る起点に使う。"""
    got = git(tree, "rev-parse", "HEAD")
    return got.out.strip() if got.ok else None


def commit_count(tree: str, base: str, head: str) -> int:
    got = git(tree, "rev-list", "--count", f"{base}..{head}")
    if not got.ok:
        return -1
    try:
        return int(got.out.strip())
    except ValueError:
        return -1


def changed_files(tree: str, base: str, head: str) -> list[str]:
    got = git(tree, "diff", "--name-only", f"{base}..{head}")
    if not got.ok:
        return []
    return [line.strip() for line in got.out.splitlines() if line.strip()]


def dirty_files(tree: str) -> list[str]:
    got = git(tree, "status", "--porcelain")
    if not got.ok:
        return []
    return [line[3:].strip() for line in got.out.splitlines() if line.strip()]


def push(tree: str, branch: str) -> proc.Run:
    return git(tree, "push", "--set-upstream", "origin", branch, timeout=900)


def remove_worktree(repo: str, tree: str) -> proc.Run:
    """**worktree を消すと、無視されたファイルも一緒に消える**（実測）。

    記録は `<run>/` 側に置いてあるので消えないが、消す前に push が済んでいることを
    呼び出し側が確かめる。
    """
    return proc.run(["git", "-C", repo, "worktree", "remove", "--force", tree])


# --- テストを read-only にする ----------------------------------------------


def write_guard_settings(path: str) -> str:
    """書いてはいけないファイルへの書き込みを止めるフックの設定を書き出す。

    **worktree の外に置き、`claude --settings` で渡す。** worktree に置くと commit に
    混ざる危険があった。段ごとに出し入れする必要も無く、run の頭で 1 度書けばよい
    ——何を止めるかは driver が段ごとに渡す環境変数（`AUTODEV_READ_ONLY` /
    `AUTODEV_ALLOW_TESTS`）で決まる。
    """
    settings = {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Write|Edit|MultiEdit|NotebookEdit|Bash",
                    "hooks": [
                        {"type": "command", "command": f"python3 {paths.hook('deny-writes')}"}
                    ],
                },
                {
                    # 段が `autodev ask` を呼んだら、答えが置かれるまで段を止める。
                    # **`deny` より優先順位が低い**（deny > defer）ので、書き込みの拒否と
                    # 並べても順番を気にしなくてよい
                    "matcher": "Bash",
                    "hooks": [
                        {"type": "command", "command": f"python3 {paths.hook('park-on-ask')}"}
                    ],
                },
            ]
        }
    }
    return files.write_text(path, json.dumps(settings, ensure_ascii=False, indent=2) + "\n")


def lock_tests(tree: str, test_globs: list[str]) -> None:
    """実装段の前に呼ぶ。終わったら `unlock_tests()` で戻す。"""
    _chmod_tests(tree, test_globs, writable=False)


def unlock_tests(tree: str, test_globs: list[str]) -> None:
    _chmod_tests(tree, test_globs, writable=True)


def _chmod_tests(tree: str, test_globs: list[str], writable: bool) -> None:
    """フックの裏をかかれても書けないよう、ファイル側の書き込み権も落とす。

    **git は書き込み権を追跡しない**（実行ビットだけ）ので、これが差分に出ることはない。
    """
    for path in tracked_files(tree):
        if not globs.matches_any(path, test_globs):
            continue
        full = os.path.join(tree, path)
        if not os.path.isfile(full):
            continue
        mode = os.stat(full).st_mode
        os.chmod(full, (mode | 0o200) if writable else (mode & ~0o222))


def tracked_files(tree: str) -> list[str]:
    got = git(tree, "ls-files")
    if not got.ok:
        return []
    return [line.strip() for line in got.out.splitlines() if line.strip()]
