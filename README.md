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
