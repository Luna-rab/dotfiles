"""git のディレクトリ配置と、team-supervisor が使う場所の求め方。

worktree・状態ファイル・ベース資料の置き場を決める式をここ 1 か所に置く。同じ式を複数の
スクリプトに書き写すと、片方だけ直したときに 2 つのスクリプトが別の場所を指す。
"""

import os

from .shell import die, out

# ベース 3 資料のファイル名。base_dir が返すディレクトリの中にこの名前で置く
BASE_FILES = ("brief.md", "map.md", "ledger.md")


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


def ensure_state_dir():
    sd = state_dir()
    os.makedirs(sd, exist_ok=True)
    return sd


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
