# dotfiles

## Install
1. download
    ```shell
    git clone https://github.com/Luna-rab/dotfiles.git $HOME/dotfiles
    cd $HOME/dotfiles
    ````

2. install

    `install.sh` が設定ファイルを symlink し、`mise/config.toml` に書いたツール
    （fzf / ghq / jq / sheldon / uv）を入れる。

    ```shell
    ./install.sh
    ```

3. zsh を起動する

    `exec zsh` すると、`install.sh` が入れた
    [sheldon](https://github.com/rossmacarthur/sheldon)（zsh のプラグインマネージャ）が
    `~/.config/sheldon/plugins.toml`（`sheldon/plugins.toml` への symlink）に従って
    プラグインを取得する。

    ```shell
    exec zsh
    ```

## ツール管理（`mise/config.toml`）

全環境に入れるツールは
[mise](https://mise.jdx.dev)（プログラミング言語やコマンドラインツールのバージョンを
管理するツール）に任せ、一覧を `mise/config.toml` に書く。
`install.sh` はこのファイルを `~/.config/mise/config.toml` に symlink したあと、
`mise install` を実行する。

- **入れる処理は `install.sh` だけが持つ。** `.zshrc` は `mise activate` で PATH に
  載せるだけで、インストールを試さない。ツールが足りないときは `./install.sh` を
  実行し直す。
- **`install.sh` は何度実行してもよい。** `mise install` は未インストールのツールだけを
  入れる。全部入っていれば「mise all tools are installed」と出して 0.011 秒で終わる。
- **mise 本体が無ければ、`https://mise.run` のインストーラで `~/.local/bin/mise` に
  入れる。** curl が無い環境では警告を出して、symlink の作成だけを続ける。
- **ツールを増やすときは `mise/config.toml` に 1 行足してコミットする。**
  `mise use -g <tool>@latest` を実行してもよい。symlink 越しにこのファイルへ
  書き込まれるので、`git diff` に出る。

devcontainer（VS Code が開発用に作るコンテナ）は毎回まっさらなコンテナから始まるので、
ホストに入れたツールをコンテナは引き継がない。VS Code はコンテナを作るときに
dotfiles の `installCommand`（下記の Dev Containers 参照）を実行するため、
上の仕組みで `mise/config.toml` に書いたツールが新しいコンテナにも入る。

## Claude Code (`~/.claude/`)

`.claude/` 配下を全環境で共通利用する。`install.sh` が次を行う:

- `skills/` `commands/` `agents/` と `CLAUDE.md` / `keybindings.json` を `~/.claude/` に symlink（編集が即反映）
- `settings.json` は symlink せず、ポータブル基盤 `.claude/settings.json` にマシン固有
  `~/.claude/settings.local.json` を `jq` でディープマージして `~/.claude/settings.json` を生成

既存の `projects/` `history.jsonl` 等ランタイムデータは温存される。

### プラグイン（marketplace から配布されるもの）

Claude Code のプラグインは marketplace（プラグインの配布カタログ）から入れる。
状態は次の 2 つに分かれていて、dotfiles で管理するのは前者だけ。

- **宣言**: `~/.claude/settings.json` の `extraKnownMarketplaces` と `enabledPlugins`。
  どのカタログを使い、どのプラグインを有効にするかを書いたもの
- **実体**: `~/.claude/plugins/` 配下のカタログの clone とプラグイン本体。
  マシンごとのランタイムデータなのでコミットしない

宣言は dotfiles の `.claude/settings.json` に書く。

```json
{
  "extraKnownMarketplaces": {
    "claude-plugins-official": {
      "source": { "source": "github", "repo": "anthropics/claude-plugins-official" }
    }
  },
  "enabledPlugins": {
    "code-simplifier@claude-plugins-official": true
  }
}
```

`extraKnownMarketplaces` のキーはカタログが名乗る名前で、リポジトリ名とは限らない
（`anthropics/claude-plugins-community` のカタログ名は `claude-community`）。
`enabledPlugins` のキーは `<プラグイン名>@<marketplace 名>` の形式。

**宣言を置くだけでは新しいマシンに実体は入らない。** そのため `install.sh` がこの宣言を読んで
`claude plugin marketplace add` と `claude plugin install --scope user` を実行し、実体を取得する。
どちらのコマンドも既に入っていれば何もしないので、`./install.sh` は何度実行してもよい。
`claude` コマンドが無い環境では警告を出して読み飛ばす。

プラグインを追加する手順:

1. 使うマシンで `/plugin` から入れる。`~/.claude/settings.json` に宣言が書き込まれる
2. その差分を dotfiles の `.claude/settings.json` にコピーしてコミットする
3. 他のマシンで `./install.sh` を実行すると同じものが入る

プラグインを削除するときは 2 か所を手で消す。`install.sh` は宣言から消えたプラグインを
自動では削除しない（ローカルで試しに入れたものを勝手に消さないため）。

```shell
claude plugin uninstall <プラグイン名>@<marketplace 名> --scope user
```

を実行して実体を消し、dotfiles の `.claude/settings.json` からも該当エントリを消す。

#### 注意: この dotfiles リポジトリの中では宣言が二重に効く

`.claude/settings.json` は dotfiles のマージ素材であると同時に、**このリポジトリ自身の
プロジェクト設定**でもある（Claude Code は作業ディレクトリの `.claude/settings.json` を
プロジェクトスコープの設定として読む）。そのため、このリポジトリの中で Claude Code を
動かすと、宣言したプラグインがユーザースコープとは別にプロジェクトスコープにも
インストールされる。実体のキャッシュ（`~/.claude/plugins/cache/`）は共有されるので
動作上の実害はない。

一方で `/plugin` の操作や `claude plugin uninstall --scope project` は、プロジェクト設定
としてこのファイルを直接書き換える。リポジトリ内でプラグインを操作したあとは
`git diff .claude/settings.json` で意図しない変更が入っていないか確認する。

### マシン固有設定（Bedrock など）

```shell
cp .claude/settings.local.json.example ~/.claude/settings.local.json
# 編集後
./install.sh
```

`~/.claude/settings.local.json` は **gitignore** 済み（コミットされない）。

### タスクリストのツール（`CLAUDE_CODE_ENABLE_TODO_TOOLS`）

`.claude/settings.json` の `env` にこのキーを入れてある。**入れないと、新しいモデルでは
`TaskCreate` / `TaskUpdate` / `TaskGet` / `TaskList` の 4 ツールが Claude に渡らず、作業中の
タスクリストに何も載らない**（画面のパネルにも出ない）。

これは公式に文書化された opt-in である。[Tools reference の「Task tool
availability」](https://code.claude.com/docs/en/tools-reference#task-tool-availability)（Claude
Code v2.1.233 以降）が、次の 2 点を述べている。

- 対象は **Opus 4.8 / Sonnet 5 / Fable 5 / Mythos 5 と、それぞれの系列のそれ以降**。
  この dotfiles が使うモデルはこの範囲に入る。
- 既定で外している理由は「これらのモデルは書かれたチェックリスト無しでも複数手順の作業を追え、
  ツールの定義とリマインダーがコンテキストを食う」から。**廃止ではない**（廃止されたのは
  `TodoWrite` の方で、`TaskCreate` などの 4 ツールに置き換わった）。

opt-in の方法は 4 つ挙げられている。ここでは 1 つ目を使っている。

| 方法 | 効く範囲 |
| --- | --- |
| `env` に `CLAUDE_CODE_ENABLE_TODO_TOOLS=1`（ここで採用） | 全セッション・全モデル・全プロバイダ |
| `claude --allowedTools TaskCreate` | その起動だけ |
| `claude --tools …`（並べたものだけに絞る） | その起動だけ |
| Agent SDK の `allowedTools` / `tools` / `env` | その呼び出しだけ |

受け付ける値は `1` / `true` / `yes` / `on`（大文字小文字とも）。プロジェクトの
`.claude/settings.json` に足したときは、**走っているセッションでもその場で 4 ツールが増えた**
（Claude Code は設定ファイルの変更を監視している。2.1.234 で実測）。増えなければ再起動する。
同じモデルでキーの有無だけを変えた実測:

| 実行 | `TaskCreate` があるか |
| --- | --- |
| `claude -p "…"` | ない |
| `CLAUDE_CODE_ENABLE_TODO_TOOLS=1 claude -p "…"` | ある |

**このリポジトリで opt-in する理由は、進捗を人が見るためである。** モデルの側は無くても困らない
（上の公式の記述）。**払っているのは 4 ツールの定義とリマインダーのぶんのコンテキストである。**

`.claude/skills/supervisor/` はこのキーを前提にしている。タスクの分解と依存（`blockedBy`）を
`TaskCreate` / `TaskUpdate` でタスクリストに登録して見せる段があり
（[lead-setup.md](.claude/skills/supervisor/lead-setup.md) §6）、**キーを外すとその段が実行できない**
——1 回通したときに実際に止まった。進行状態の出所は統合ツリーの `state.json` なので作業自体は
続けられるが、並列の進み具合は画面から消える。

サブエージェントには、**セッションがツールを持っているときだけ**同じものが渡る（モデルが違っても
同じ。上の公式ページ）。

### Dev Containers

VS Code のユーザ設定に以下を追加すると、コンテナ作成時に自動適用される:

```json
"dotfiles.repository": "<owner>/dotfiles",
"dotfiles.targetPath": "~/dotfiles",
"dotfiles.installCommand": "install.sh"
```

### フック（`.claude/hooks/`）

Claude Code のフック（決まったタイミングで Claude Code が起動するスクリプト）。
`~/.claude/hooks/` が `.claude/hooks/` への symlink になり、`.claude/settings.json` の
`hooks` がマージされて全プロジェクトで動く。

| スクリプト | いつ | 何をするか |
| --- | --- | --- |
| `review-added-comments.py` | Stop（Claude が応答を終える直前） | その turn で Claude が追加したコメントを tree-sitter で拾い出し、`{"decision": "block", "reason": ...}` で 1 件ずつ要否を問い直させる。コメントを書いていない turn では何もしない |

AI は処理を言い直しただけのコメントを大量に書く。書いている最中は推論の足場になるので
残してよいが、放置するとコードが読みにくくなる。`CLAUDE.md` に書いても長い会話では埋もれる。
PostToolUse ではなく Stop にしたのは、編集のたびに割り込むと会話がぶつ切りになるため。

shebang は `#!/usr/bin/env -S uv run --script` で、依存（`tree-sitter-language-pack`）は
初回起動時に uv が取り寄せる。`install.sh` が `--warm` で 1 回起動して先に取り寄せておく。
uv が無い環境では起動に失敗し、何もしないフックになる（Claude の停止は妨げない）。
テストは `.claude/hooks/tests/` にあり、`uv run pytest` で走る。

### 検証（`.claude/scripts/`）

`.claude/skills/` 配下には Claude Code の skill 定義（`SKILL.md` と補助ファイル）が入っている。
壊れていても、実際に Claude Code から起動するまで気づけない。それを commit の時点で捕まえるため、
検査スクリプトを用意している。`.github/workflows/skill-checks.yml` が push と pull request の
たびに自動で走らせる。

| コマンド | 何を検査するか |
| --- | --- |
| `uv run ruff check .` | Python の lint（未使用の import、古い書き方など）。`--fix` を付けると直せるものを直す |
| `uv run ruff format .` | Python の整形。CI は `--check` を付けて差分が無いことだけを見る |
| `uv run ty check` | Python の型 |
| `uv run pytest` | `.claude/hooks/` のフックの動作（テストは `.claude/hooks/tests/`） |
| `./.claude/scripts/check-skills.py` | `.claude/skills/` 配下の全 skill について、frontmatter が YAML として読めるか、`SKILL.md` が 500 行以下か、本文中の相対リンクと `scripts/` 配下への参照先が実在するか、実行されるスクリプトに実行権限が付いているかを調べる |

どれもリポジトリのルートから実行し、通れば終了コード 0、落ちれば 0 以外を返す。
`.github/workflows/skill-checks.yml` が push と pull request のたびに 5 つとも走らせる。

```shell
uv run ruff check . && uv run ruff format --check . && uv run ty check && uv run pytest && ./.claude/scripts/check-skills.py
```

#### 依存の置き場

**dotfiles を入れた人が、上のツールを持っている必要はない。** `.claude/skills/supervisor/scripts/`
の 6 本（`review.py` / `place.py` / `verify.py` / `worktree.py` / `stack.py` / `state.py`）は
**標準ライブラリだけで動き**、shebang も `#!/usr/bin/env python3` のままである。`python3` があるマシンなら、uv が無くても
supervisor スキルは動く。

依存は 2 か所に分けてある。

| どこ | 何のため | 誰が要るか |
| --- | --- | --- |
| `pyproject.toml` の `[dependency-groups] dev` | ruff・ty・pytest（と、ty と pytest がスクリプトの import を解決するための pyyaml・tree-sitter-language-pack） | このリポジトリの Python を触る人と CI |
| `.claude/scripts/check-skills.py` 先頭の `# /// script`（PEP 723） | 実行時の PyYAML | このスクリプトを走らせる人 |
| `.claude/hooks/review-added-comments.py` 先頭の `# /// script`（PEP 723） | 実行時の tree-sitter-language-pack | このフックが動く（= install.sh を通した）マシン |

`check-skills.py` の shebang は `#!/usr/bin/env -S uv run --script` なので、直接起動すれば
uv が PyYAML を用意する。PyYAML が既に入っている環境なら
`python3 .claude/scripts/check-skills.py` でも動く。

uv 自体は `mise/config.toml` に入れてあるので、`install.sh` を通したマシンには入っている。
