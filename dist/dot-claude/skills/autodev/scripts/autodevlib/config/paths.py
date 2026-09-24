"""ランごとのディレクトリを組み立てる。**パスを呼び出し側で連結しない。**

置き場は**対象リポジトリの外**である。worktree を消しても記録が残り、対象リポジトリに
`.gitignore` を 1 行も足さずに済む。

    ~/.local/state/autodev/<ラン名>/
      state.json          進行状態（driver だけが書く。ステージには渡さない）
      config.json         リポジトリ固有の設定（検証コマンド・テストのパス・変更禁止パス）
      brief.md            ステージが読むブリーフ（検証コマンド・変更禁止パス・ブランチ規約）
      map.md              ステージが読むコードベースの入口
      overview-pr-body.md    概要 PR の本文（driver が書き出す）
      guard.json          書き込みを止めるフックの設定。`claude --settings` で渡す
      tree/               worktree。git と gh stack を叩くのはここだけ
      tasks/task<番号>/
        review.json       レビュー記録
        result-<ステージ>-<ラウンド>.json  ステージが返す構造化結果
        pr-body.md        タスク PR の本文
      logs/<ステージ>-<ラウンド>.jsonl     claude の出力そのまま
"""

from __future__ import annotations

import os
import re

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,48}$")


def state_root() -> str:
    """ランディレクトリの根。`AUTODEV_STATE_DIR` で差し替えられる（試験と検査で使う）。"""
    override = os.environ.get("AUTODEV_STATE_DIR")
    if override:
        return os.path.abspath(override)
    xdg = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local/state")
    return os.path.join(xdg, "autodev")


def config_root() -> str:
    """リポジトリ固有の設定（検証コマンド・テストのパス・変更禁止パス）の置き場。

    ランをまたいで使い回す。毎回 CI 定義から拾い直させると、計画ステージの仕事が増えるだけで
    答えは同じである。
    """
    override = os.environ.get("AUTODEV_CONFIG_DIR")
    if override:
        return os.path.abspath(override)
    xdg = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(xdg, "autodev", "repos")


def repo_config(repo: str) -> str:
    slug = repo.strip("/").replace("/", "__").replace(":", "_")
    return os.path.join(config_root(), f"{slug}.json")


def skill_root() -> str:
    """`SKILL.md` がある場所。指示書・スキーマ・テンプレート・フックはこの下にある。

    **階層を数えて上らない。** 数えると、このファイルを別の階層へ動かしたときに黙ってずれる
    ——`autodevlib/paths.py` から `autodevlib/config/paths.py` へ動かしたとき、`dirname` の
    回数が足りずに 5 つのパスが実在しない場所を指した。そのとき ruff も ty も pytest も
    CLI の起動も全部通った。
    """
    current = os.path.dirname(os.path.abspath(__file__))
    while not os.path.exists(os.path.join(current, "SKILL.md")):
        parent = os.path.dirname(current)
        if parent == current:
            raise RuntimeError("SKILL.md が見つからない（autodev の置き場が壊れている）")
        current = parent
    return current


def launcher() -> str:
    """ステージが `autodev review …` を呼ぶための絶対パス。

    PATH に頼らない。ステージは driver が起動した claude の中で走るので、PATH が
    install.sh を通していないチェックアウトでは通らないことがある。
    """
    return os.path.join(skill_root(), "scripts", "autodev.py")


def contract(name: str) -> str:
    return os.path.join(skill_root(), "contracts", f"{name}.md")


def schema(name: str) -> str:
    """ステージが返す結果の形。**形の出所はここ 1 か所で、`claude --json-schema` に渡す。**"""
    return os.path.join(skill_root(), "schemas", f"{name}.json")


def hook(name: str) -> str:
    return os.path.join(skill_root(), "hooks", f"{name}.py")


def check_name(run_name: str) -> str:
    """ラン名はブランチ名と置き場のパスに入るので、使える字を絞る。"""
    if not NAME_RE.match(run_name):
        raise ValueError(f"ラン名は英小文字・数字・ハイフンで 1〜49 字にしてください: {run_name!r}")
    return run_name


class Run:
    """1 つの run（＝1 つのラン名）のパス。"""

    def __init__(self, run_name: str) -> None:
        self.run_name = check_name(run_name)
        self.dir = os.path.join(state_root(), self.run_name)

    def path(self, *parts: str) -> str:
        return os.path.join(self.dir, *parts)

    @property
    def state(self) -> str:
        return self.path("state.json")

    @property
    def config(self) -> str:
        return self.path("config.json")

    @property
    def brief(self) -> str:
        return self.path("brief.md")

    @property
    def map(self) -> str:
        return self.path("map.md")

    @property
    def overview_pr_body(self) -> str:
        return self.path("overview-pr-body.md")

    @property
    def guard(self) -> str:
        """書き込みを止めるフックの設定。**worktree の外に置く。**

        `claude --settings` で渡すので、worktree にファイルを置かずに済む
        （置くと commit に混ざる危険があった）。
        """
        return self.path("guard.json")

    @property
    def tree(self) -> str:
        return self.path("tree")

    def question(self, key: str) -> str:
        """ステージが聞いたこと。フックが書き、driver が読む。"""
        return self.path("questions", f"{key}.json")

    def answer(self, key: str) -> str:
        """呼び出し元のエージェントが置いた回答。**このファイルの実在がステージの再開の合図である。**"""
        return self.path("answers", f"{key}.json")

    def question_keys(self) -> list[str]:
        directory = self.path("questions")
        if not os.path.isdir(directory):
            return []
        return sorted(f[:-5] for f in os.listdir(directory) if f.endswith(".json"))

    def prose(self, name: str) -> str:
        """agent が書いた自由記述の置き場。書き出すたびに読み直すのでファイルに残す。"""
        return self.path("prose", f"{name}.md")

    def task_dir(self, task_id: str) -> str:
        return self.path("tasks", task_id)

    def review(self, task_id: str) -> str:
        return os.path.join(self.task_dir(task_id), "review.json")

    def result(self, task_id: str, stage: str, round_label: str) -> str:
        return os.path.join(self.task_dir(task_id), f"result-{stage}-{round_label}.json")

    def task_pr_body(self, task_id: str) -> str:
        return os.path.join(self.task_dir(task_id), "pr-body.md")

    def log(self, task_id: str, stage: str, round_label: str) -> str:
        return self.path("logs", task_id, f"{stage}-{round_label}.jsonl")

    def exists(self) -> bool:
        return os.path.exists(self.state)

    def ensure(self) -> None:
        for d in (self.dir, self.path("tasks"), self.path("logs")):
            os.makedirs(d, exist_ok=True)


def list_runs() -> list[str]:
    root = state_root()
    if not os.path.isdir(root):
        return []
    return sorted(
        name for name in os.listdir(root) if os.path.exists(os.path.join(root, name, "state.json"))
    )
