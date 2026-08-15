"""git のディレクトリ配置と、supervisor が使う場所の求め方。

worktree・状態ファイル・ベース資料の置き場を決める式をここ 1 か所に置く。同じ式を複数の
スクリプトに書き写すと、片方だけ直したときに 2 つのスクリプトが別の場所を指す。
"""

import os

from .shell import die, out

# ベース 3 資料のファイル名。base_dir が返すディレクトリの中にこの名前で置く
BASE_FILES = ("brief.md", "map.md", "ledger.md")

# ベースディレクトリに置く .gitignore の中身。`*` は自分自身にも当たるので、ディレクトリごと
# `git status` に出なくなる（実測）。統合ツリーは作業ツリーなので、これが無いと差分に出る
GITIGNORE = "# supervisor の作業ファイル。git で追跡しない\n*\n"


def main_checkout():
    """メインチェックアウト（最初にクローンした作業ツリー）の絶対パス。

    `git worktree list` の 1 行目がメインの作業ツリーである、という git の仕様を使う。
    `git rev-parse --git-common-dir` の親を取る形にしない——`--separate-git-dir` や
    `.git` ファイル方式のリポジトリでは共有 .git が作業ツリーの外にあり、親が別の場所を指す。
    """
    for line in out(["git", "worktree", "list", "--porcelain"]).splitlines():
        if line.startswith("worktree "):
            return line[len("worktree "):]
    die("git worktree list が作業ツリーを 1 つも返しません。git リポジトリの中で実行してください。")


def check_work(work):
    """作業名がパスの 1 要素として使えることを確かめる。"""
    if not work or work != os.path.basename(work) or work in (".", ".."):
        die(f"--work にディレクトリ区切りを含められません: {work!r}")
    return work


def integration_tree(work):
    """リード専用の worktree（統合ツリー）の絶対パス。"""
    return os.path.join(main_checkout(), ".claude", "worktrees",
                        f"supervisor-{check_work(work)}")


def base_dir(work):
    """ベース 3 資料（brief.md / map.md / ledger.md）と引き継ぎノートを置くディレクトリ。

    統合ツリーの中に置く。worktree に隔離されたセッションは `Edit` / `Write` が
    **メインチェックアウト内のパス**を対象にすると遮断され、`.git` 配下もその中に入るためである
    （公式ドキュメント https://code.claude.com/docs/en/worktrees の "How Claude Code enforces
    isolation"）。統合ツリーはメインチェックアウトではないので、リードも、自分の worktree で走る
    サブエージェントも書ける。`GITIGNORE` を置いて git の追跡対象から外す。
    """
    return os.path.join(integration_tree(work), ".claude", "supervisor")


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


def locked_worktrees():
    """ロックされている worktree のパスと、その理由を返す。

    Claude Code は走行中のエージェントの worktree に
    `git worktree lock --reason "claude <名前> <id> (pid <番号> start <時刻>)"` を掛け、
    エージェントが終わると外す（公式ドキュメント
    https://code.claude.com/docs/en/worktrees の "Clean up subagent and background-session
    worktrees"）。したがってロックが残っているものは、まだ生きているエージェントが掴んで
    いるか、ロックを外さずに死んだ run である。**run が終わったかどうかの手がかりになる。**

    `--porcelain` は worktree ごとに `worktree <パス>` を出し、ロックされているものには
    続けて `locked` か `locked <理由>` の行を出す。
    """
    locks, current = {}, None
    for line in out(["git", "worktree", "list", "--porcelain"]).splitlines():
        if line.startswith("worktree "):
            current = os.path.realpath(line[len("worktree "):])
        elif current and (line == "locked" or line.startswith("locked ")):
            locks[current] = line[len("locked"):].strip()
    return locks


def run_worktrees(run_id):
    """ある dynamic workflow の run が作った worktree のパス（realpath）を返す。

    ワークフローの worktree は `wf_<runId>-<連番>` の名前になる。**`git worktree list` に
    載っているものだけ**を、名前が runId で始まるものに限って返す。`.claude/worktrees/`
    配下を名前で一括に舐めない——他プロセスの worktree まで削除した事故がある。
    """
    prefix = run_id if run_id.startswith("wf_") else f"wf_{run_id}"
    return sorted(p for p in listed_worktrees()
                  if os.path.basename(p).startswith(prefix))
