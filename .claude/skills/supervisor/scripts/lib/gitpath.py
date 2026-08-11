"""git のディレクトリ配置と、supervisor が使う場所の求め方。

worktree・状態ファイル・ベース資料の置き場を決める式をここ 1 か所に置く。同じ式を複数の
スクリプトに書き写すと、片方だけ直したときに 2 つのスクリプトが別の場所を指す。
"""

import os

from .shell import die, out

# ベース 3 資料のファイル名。base_dir が返すディレクトリの中にこの名前で置く
BASE_FILES = ("brief.md", "map.md", "ledger.md")


def git_common_dir():
    """共有の .git の絶対パス。worktree の中からでもメインツリーの .git を指す。

    `--path-format=absolute` を省いてはならない。省くとサブディレクトリにいるとき
    --git-common-dir だけが相対パス（`../../../.git`）になり、worktree の中から呼んだときに
    別の場所を指す。実際に観測された挙動である。
    """
    return out(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"])


def state_dir():
    """ベース資料と引き継ぎノートを置くディレクトリ。作業ツリーに出ないので差分を汚さない。"""
    return os.path.join(git_common_dir(), "supervisor")


def base_dir(work):
    """ベース 3 資料（brief.md / map.md / ledger.md）を置くディレクトリ。

    作業ツリーではなく `.git` の下に置くので、git の追跡対象に入らず PR の差分も汚さない。
    それでいて全 worktree から同じ絶対パスに解決でき、リポジトリのクローンが残っている
    限りセッションをまたいで残る（/tmp は WSL の再起動で消えるので使えない）。
    """
    if not work or work != os.path.basename(work) or work in (".", ".."):
        die(f"--work にディレクトリ区切りを含められません: {work!r}")
    return os.path.join(state_dir(), "base", work)


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


def run_worktrees(run_id):
    """ある dynamic workflow の run が作った worktree のパス（realpath）を返す。

    ワークフローの worktree は `wf_<runId>-<連番>` の名前になる。**`git worktree list` に
    載っているものだけ**を、名前が runId で始まるものに限って返す。`.claude/worktrees/`
    配下を名前で一括に舐めない——他プロセスの worktree まで削除した事故がある。
    """
    prefix = run_id if run_id.startswith("wf_") else f"wf_{run_id}"
    return sorted(p for p in listed_worktrees()
                  if os.path.basename(p).startswith(prefix))
