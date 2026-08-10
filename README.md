# dotfiles

## Install
1. download
    ```shell
    git clone https://github.com/Luna-rab/dotfiles.git $HOME/dotfiles
    cd $HOME/dotfiles
    ````

2. install 
    ```shell
    ./install.sh
    ```

    `install.sh` は次を行う。

    - [mise](https://mise.jdx.dev/)（言語・CLI ツールのバージョン管理ツール）が
      未導入なら公式インストーラで入れる
    - `mise/config.toml` を `~/.config/mise/config.toml` へ symlink し、
      そこに書かれたツールをインストールする

3. zsh plugin install

    `exec zsh` すると、`install.sh` が入れた
    [sheldon](https://github.com/rossmacarthur/sheldon)（zsh のプラグインマネージャ）が
    `~/.config/sheldon/plugins.toml`（`sheldon/plugins.toml` への symlink）に従って
    プラグインを取得する。

    ```shell
    exec zsh
    ```

## ツール管理 (`mise/config.toml`)

グローバルに入れる CLI ツールは `mise/config.toml` の `[tools]` で管理する。
ツールを増やすときはここに 1 行足して `./install.sh` を実行する。

`.zshrc` はインストールを行わず、`mise activate` でツールを PATH に載せることと、
プロンプトなど見た目の適用だけを担当する。

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

### Dev Containers

VS Code のユーザ設定に以下を追加すると、コンテナ作成時に自動適用される:

```json
"dotfiles.repository": "<owner>/dotfiles",
"dotfiles.targetPath": "~/dotfiles",
"dotfiles.installCommand": "install.sh"
```

### 検証（`.claude/scripts/`）

`.claude/skills/` 配下には Claude Code の skill 定義（`SKILL.md` と補助ファイル）、
`.claude/skills/team-supervisor/scripts/subagent-stop.sh` には agent の終了を制御するフックが
入っている。どちらも壊れていても、実際に Claude Code から起動するまで気づけない。それを
commit の時点で捕まえるため、次の 2 本の検査スクリプトを用意している。
`.github/workflows/skill-checks.yml` が push と pull request のたびに両方を自動で走らせる。

| コマンド | 何を検査するか |
| --- | --- |
| `python3 .claude/scripts/check-skills.py` | `.claude/skills/` 配下の全 skill について、frontmatter が YAML として読めるか、`SKILL.md` が 500 行以下か、本文中の相対リンクと `scripts/` 配下への参照先が実在するか、実行されるスクリプトに実行権限が付いているかを調べる |
| `.claude/scripts/test-subagent-stop.sh` | `subagent-stop.sh` を実際に呼び出し、ブランチ登録の有無・登録したブランチが push 済みかどうか・打ち切り済みの目印の有無といった分岐ごとに、仕様どおりの終了コードを返すかを調べる |

どちらもリポジトリのルートから引数なしで実行し、検査に通れば終了コード 0、落ちれば 0 以外を
返す。手元で同じ確認をするときは次を実行する。

```shell
python3 .claude/scripts/check-skills.py
.claude/scripts/test-subagent-stop.sh
```
