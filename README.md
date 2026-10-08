# dotfiles

個人のシェル、Git、開発 CLI を管理する。
環境構築の入口として [agent-toolkit](https://github.com/Luna-rab/agent-toolkit) も導入する。
Skills・フック・autodev・HUD の配置と実装は toolkit 側が担当する。

## インストール

```sh
git clone https://github.com/Luna-rab/dotfiles.git
cd dotfiles
./install.sh
exec zsh
```

clone 先は任意。設定を配置し、Claude Code・Codex CLI・GitHub Copilot CLI を含む mise のツールを
導入した後、toolkit 自身のインストーラーを呼ぶ。
既存 toolkit checkout があれば自動で pull せずに再利用する。

```sh
# 通常の開発環境だけを導入する
./install.sh --skip-agent-toolkit

# 使う toolkit checkout を指定する
AGENT_TOOLKIT_DIR=/path/to/agent-toolkit ./install.sh
```

指定がなければ ghq に登録された checkout を使う。なければ `$(ghq root)/github.com/Luna-rab/agent-toolkit`
に取得する。ghq が使えない場合は `~/.local/share/agent-toolkit` を使う。複数の checkout が見つかった場合は
`AGENT_TOOLKIT_DIR` を指定する。editable インストールなので toolkit checkout は削除しない。

設定の配置失敗は非ゼロで終了する。mise や toolkit の取得失敗は警告して終了コード 0 とし、
最後に設定・ツール・toolkit の結果を別々に表示する。警告があれば原因を直して再実行する。

Claude・Codex・Copilot は mise で管理する。別の方法で導入済みでも mise の管理分を導入し、
既存の実行ファイルは削除しない。`--skip-agent-toolkit` を指定しても 3 つの CLI は導入する。
各 CLI の認証は起動後に手動で行う。
フックの初回登録は toolkit の README を参照する。

## 構成

| モジュール | 配置・導入するもの |
| --- | --- |
| `modules/git` | `~/.config/git/ignore`（コピー） |
| `modules/mise` | mise のツール一覧（symlink）、mise 本体と依存 CLI |
| `modules/sheldon` | `~/.config/sheldon/plugins.toml`（symlink） |
| `modules/zsh` | `~/.zshrc`、`~/.config/zsh/conf.d` の管理ファイル（個別 symlink） |
| `modules/agent-toolkit` | toolkit の取得とインストーラー起動 |

各モジュールの `files/` に配布物、`install.sh` に配置処理を置く。
ルートの `install.sh` は順序と結果を管理し、`lib/install.sh` は共通の配置処理を持つ。
ファイルを置くだけでは配置されない。配置先と方式は各モジュールで明記する。

同じリンク・コピー内容は変更しない。置き換えるファイルやリンクは `~/.dotbackup/` に退避する。
旧 `dist/` 配置からも `./install.sh` の再実行で切り替えられる。

## シェルとツールの変更

- 全環境の CLI は `modules/mise/files/config.toml` に追加する。導入は `./install.sh`、
  シェルでは有効化だけを行う。言語サーバの接続設定は利用するエディタや toolkit が担当する。
- zsh の読込順序は mise・PATH → sheldon・補完 → 操作関数。
  個人端末だけの設定は `~/.config/zsh/local.zsh` に置く。このファイルは配置・上書きしない。
- Ctrl-G は ghq のリポジトリを fzf で選択する。`gs` は引数なしでブランチを選び、
  引数ありならそのまま `git switch` に渡す。キャンセル時は移動しない。
- `~/.local/bin` は PATH に重複なく載せる。mise が有効な場合は mise の管理ツールを優先する。

設定やモジュールの追加は [構成と変更手順](docs/architecture.md) を参照する。
