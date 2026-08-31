#!/bin/bash
# Ref: https://qiita.com/yutkat/items/c6c7584d9795799ee164#%E3%82%B7%E3%83%B3%E3%83%97%E3%83%AB%E3%81%AAdotfiles%E3%82%A4%E3%83%B3%E3%82%B9%E3%83%88%E3%83%BC%E3%83%A9%E3%83%BC%E3%82%92%E4%BD%9C%E3%81%A3%E3%81%A6%E3%81%BF%E3%82%88%E3%81%86

set -ue

link_to_homedir() {
  command echo "backup old dotfiles..."
  if [ ! -d "$HOME/.dotbackup" ];then
    command echo "$HOME/.dotbackup not found. Auto Make it"
    command mkdir "$HOME/.dotbackup"
  fi

  local dotdir=$1
  if [[ "$HOME" != "$dotdir" ]];then
    for f in $dotdir/.??*; do
      # .git で始まるファイル/ディレクトリはリンクしない
      local fname=`basename "$f"`
      [[ $fname == .git* ]] && continue
      # .claude は link_claude_config() で個別に扱う（ランタイムデータを巻き込まないため）
      [[ $fname == .claude ]] && continue
      # 開発ツールの生成物はリンクしない。このリポジトリで `uv run ruff` や `uv run ty` を
      # 実行すると .venv/（uv が作る仮想環境）と .ruff_cache/（ruff のキャッシュ）が
      # ルート直下に生まれる。どちらも .gitignore で追跡対象外なので clone した人の手元には
      # 無いが、検査を回した後に install.sh を実行すると $HOME に symlink が張られてしまう。
      [[ $fname == .venv || $fname == .ruff_cache ]] && continue
      if [[ -L "$HOME/`basename $f`" ]];then
        command rm -f "$HOME/`basename $f`"
      fi
      if [[ -e "$HOME/`basename $f`" ]];then
        command mv "$HOME/`basename $f`" "$HOME/.dotbackup"
      fi
      command ln -snf $f $HOME
      command echo "create symboliclink $f"
    done
  else
    command echo "same install src dest"
  fi
}

set_global_gitignore() {
  if [ ! -d "$HOME/.config/git" ];then
    command echo "$HOME/.config/git not found. Auto Make it"
    command mkdir -p "$HOME/.config/git"
  fi
  if [[ -e "$HOME/.config/git/ignore" ]];then
    command mv "$HOME/.config/git/ignore" "$HOME/.config/git/ignore.backup"
  fi
  local dotdir=$1
  command cp $dotdir/.gitignore_global $HOME/.config/git/ignore
}

link_sheldon_config() {
  local dotdir=$1
  local src="$dotdir/sheldon/plugins.toml"
  local dst="$HOME/.config/sheldon/plugins.toml"
  [[ -e "$src" ]] || return 0

  command echo "setup ~/.config/sheldon ..."
  command mkdir -p "$HOME/.config/sheldon"
  if [[ -L "$dst" ]]; then
    command rm -f "$dst"
  elif [[ -e "$dst" ]]; then
    command mv "$dst" "$HOME/.dotbackup"
  fi
  command ln -snf "$src" "$dst"
  command echo "create symboliclink $src"
}

# mise（プログラミング言語やコマンドラインツールのバージョンを管理するツール）の
# 設定ファイルを ~/.config/mise/config.toml に symlink する。
# ここに書いてあるツールは、このあと install_mise_tools() が入れる。
#
# symlink にできるのは、`mise use -g <tool>@latest` が symlink を消して新しい
# ファイルを作るのではなく、symlink 越しに元のファイルへ書き込むため（mise
# 2026.8.2 で確認）。つまりコマンドで追加したツールも dotfiles リポジトリの
# git diff に出る。
link_mise_config() {
  local dotdir=$1
  local src="$dotdir/mise/config.toml"
  local dst="$HOME/.config/mise/config.toml"
  [[ -e "$src" ]] || return 0

  command echo "setup ~/.config/mise ..."
  command mkdir -p "$HOME/.config/mise"
  if [[ -L "$dst" ]]; then
    command rm -f "$dst"
  elif [[ -e "$dst" ]]; then
    command mv "$dst" "$HOME/.dotbackup"
  fi
  command ln -snf "$src" "$dst"
  command echo "create symboliclink $src"
}

link_claude_config() {
  local dotdir=$1
  local src="$dotdir/.claude"
  local dst="$HOME/.claude"
  [[ -d "$src" ]] || return 0

  command echo "setup ~/.claude ..."
  # 既存のランタイムデータ（projects/ history.jsonl 等）を温存するため mkdir -p のみ
  command mkdir -p "$dst"

  # ディレクトリは symlink（リポジトリの編集が即反映される）
  local d
  for d in skills commands agents rules scripts hooks; do
    [[ -d "$src/$d" ]] || continue
    if [[ -L "$dst/$d" ]]; then
      command rm -f "$dst/$d"
    elif [[ -e "$dst/$d" ]]; then
      command mv "$dst/$d" "$HOME/.dotbackup"
    fi
    command ln -snf "$src/$d" "$dst/$d"
    command echo "create symboliclink $src/$d"
  done

  # ファイルも symlink（存在するもののみ）
  local file
  for file in CLAUDE.md keybindings.json; do
    [[ -e "$src/$file" ]] || continue
    if [[ -L "$dst/$file" ]]; then
      command rm -f "$dst/$file"
    elif [[ -e "$dst/$file" ]]; then
      command mv "$dst/$file" "$HOME/.dotbackup"
    fi
    command ln -snf "$src/$file" "$dst/$file"
    command echo "create symboliclink $src/$file"
  done

  merge_claude_settings "$src" "$dst"
  install_claude_plugins "$src"
}

# ~/.claude/settings.json への dotfiles 設定のマージ。
#
# dotfiles の .claude/settings.json (= マージ素材) を、実体の
# ~/.claude/settings.json に deep-merge する。既存の設定を土台に
# dotfiles 側のキーを重ねるため、既存キーを壊さずに合流できる。
#
# symlink にしないのは、実体が他ツールによって書き換えられる場合に
# その変更が dotfiles リポジトリへ漏れるのを避けるため。
merge_claude_settings() {
  local src=$1
  local dst=$2
  local fragment="$src/settings.json"   # dotfiles が注入するキー
  local target="$dst/settings.json"     # 実体

  [[ -e "$fragment" ]] || return 0

  if ! command -v jq >/dev/null 2>&1; then
    command echo "WARNING: jq not found. skip merging $fragment into $target"
    return 0
  fi

  if [[ -e "$target" ]]; then
    # 既存設定を壊した場合に備えてバックアップ
    command cp "$target" "$HOME/.dotbackup/settings.json.$(date +%s 2>/dev/null || echo bak)" 2>/dev/null || true
    # 既存設定を土台に、dotfiles のキーを deep-merge で重ねる。
    # '*' は再帰マージなので permissions などネストしたキーも安全に合流する。
    command jq -s '.[0] * .[1]' "$target" "$fragment" > "$target.tmp" \
      && command mv "$target.tmp" "$target"
    command echo "merge $fragment into $target (deep merge; existing keys preserved)"
  else
    # 実体が無い場合は素材をそのまま配置
    command jq '.' "$fragment" > "$target"
    command echo "generate $target from $fragment (no existing settings yet)"
  fi
}

# mise 本体と、mise/config.toml に書いたツールを入れる。link_mise_config() より後に呼ぶ。
# ~/.config/mise/config.toml の symlink ができていないと、mise は入れる対象を
# 読み取れない。
#
# devcontainer（VS Code が開発用に作るコンテナ）は毎回まっさらなコンテナから
# 始まるので、ホストに入れたツールをコンテナは引き継がない。VS Code はコンテナを
# 作るときに dotfiles の installCommand（ここでは install.sh）を実行する。
# そこでこの関数がツールを入れておくと、新しいコンテナでもすぐ使える。
install_mise_tools() {
  local mise_bin="$HOME/.local/bin/mise"

  if [[ ! -x "$mise_bin" ]]; then
    if ! command -v curl >/dev/null 2>&1; then
      command echo "WARNING: curl not found. skip installing mise and its tools"
      return 0
    fi
    command echo "mise not found. install mise ..."
    # mise.run のインストーラは $HOME/.local/bin/mise に置く。
    # パイプの途中で失敗しても set -e で止めたくないので、成否は次の -x で判定する。
    command curl -fsSL https://mise.run | sh || true
    if [[ ! -x "$mise_bin" ]]; then
      command echo "WARNING: failed to install mise. skip installing its tools"
      return 0
    fi
  fi

  command echo "install tools listed in ~/.config/mise/config.toml ..."
  # `mise install` は引数なしで呼ぶと、config.toml に書いてあって未インストールの
  # ツールだけを入れる。すでに全部入っている場合は「mise all tools are installed」と
  # 出して 0.011 秒で終わる（mise 2026.8.2 で計測）。
  # だから install.sh を何度実行してもよい。
  #
  # 失敗しても install.sh 全体を止めない。ネットワークが使えない環境で
  # symlink の作成まで巻き添えにしたくないため。
  if "$mise_bin" install; then
    command echo "mise tools installed"
  else
    command echo "WARNING: failed to install some mise tools. run './install.sh' again"
  fi

  # mise が生成する shim（ツール本体へ橋渡しする実行ファイル）を PATH に載せる。
  # この install.sh の以降の処理が jq を呼べるようにするため
  # （merge_claude_settings() が jq を使う）。zsh は .zshrc の `mise activate` で
  # 載せるが、この bash スクリプトはそれを通らない。
  export PATH="$HOME/.local/share/mise/shims:$PATH"
}

# gh（GitHub 公式のコマンドラインツール）の拡張 gh-stack を入れる。
#
# gh-stack は stacked PR（1 つの大きな変更を、互いに積み重なる小さな PR に分けて
# レビューに出す進め方）を操作する GitHub 公式の拡張で、`gh stack init` /
# `gh stack add` / `gh stack rebase` / `gh stack push` / `gh stack link` /
# `gh stack merge` を提供する。`.claude/skills/supervisor` がこの拡張に乗っていて、
# 入っていないとタスクのブランチを stacked PR へ積めない（起動前の確認で止まる）。
#
# gh 本体は mise/config.toml では管理していない（システム側に入っている前提）ので、
# 無いときは WARNING を出して飛ばす——install.sh 全体を止めると、symlink の作成まで
# 巻き添えにしてしまう。
#
# 既に入っているときは何もしない。`gh extension upgrade` を毎回走らせないのは、
# gh-stack が v0.1.0 で、非互換な変更が supervisor の手順を壊しうるためである
# （実測でも README の記述と挙動が 1 つ食い違っている。詳細は
# .claude/skills/supervisor/design-notes.md の「gh stack v0.1.0 で確かめたこと」）。
# 上げたいときはユーザーが `gh extension upgrade gh-stack` を叩く。
install_gh_extensions() {
  if ! command -v gh >/dev/null 2>&1; then
    command echo "WARNING: gh not found. skip installing the gh-stack extension"
    return 0
  fi

  # `gh extension list` は 1 行 1 拡張で `gh stack<TAB>github/gh-stack<TAB>v0.1.0` の形を出す
  if command gh extension list 2>/dev/null | command grep -q "github/gh-stack"; then
    command echo "gh-stack extension already installed"
    return 0
  fi

  command echo "install gh extension github/gh-stack ..."
  if command gh extension install github/gh-stack; then
    command echo "gh-stack extension installed"
  else
    command echo "WARNING: failed to install the gh-stack extension."
    command echo "         run 'gh extension install github/gh-stack' after fixing the cause"
  fi
}

# dotfiles の .claude/settings.json に宣言したマーケットプレイスとプラグインを
# 実際に取得する。
#
# marketplace（プラグインの配布カタログ）と plugin の状態は 2 つに分かれている。
#
#   宣言: ~/.claude/settings.json の extraKnownMarketplaces と enabledPlugins。
#         「どのカタログを使い、どのプラグインを有効にするか」を書いたもの。
#   実体: ~/.claude/plugins/ 配下のカタログの clone とプラグイン本体。
#         マシンごとのランタイムデータなので dotfiles では管理しない。
#
# 宣言を settings.json に置いただけでは、新しいマシンに実体は入らない
# （空の設定ディレクトリで検証済み。`claude plugin marketplace list` は
# "No marketplaces configured" を返す）。実体を取得するのは `claude plugin`
# コマンドなので、ここで宣言を読んで実行する。
#
# どちらのコマンドも、既に入っていれば "already installed" と表示して正常終了する
# （冪等）ため、install.sh を何度実行しても問題ない。
#
# dotfiles の宣言から消したプラグインは自動では削除しない。ローカルで /plugin から
# 試しに入れたものを install.sh が勝手に消さないため。実際に消すときは
# `claude plugin uninstall <plugin>@<marketplace> --scope user` を手で実行する。
install_claude_plugins() {
  local src=$1
  local fragment="$src/settings.json"
  [[ -e "$fragment" ]] || return 0

  if ! command -v claude >/dev/null 2>&1; then
    command echo "WARNING: claude not found. skip installing plugins declared in $fragment"
    return 0
  fi
  if ! command -v jq >/dev/null 2>&1; then
    command echo "WARNING: jq not found. skip installing plugins declared in $fragment"
    return 0
  fi

  # extraKnownMarketplaces の各エントリを `claude plugin marketplace add` の引数に変換する。
  # 引数の形は source の種類ごとに違う:
  #   github           -> owner/repo（ref があれば owner/repo#ref）
  #   git              -> リポジトリ URL（ref があれば URL#ref）
  #   url              -> marketplace.json の URL
  #   directory / file -> ローカルパス
  # npm など引数に変換できない種類は空文字にして、下のループで警告して読み飛ばす。
  local marketplaces
  marketplaces=$(command jq -r '
    (.extraKnownMarketplaces // {}) | to_entries[]
    | .key as $name
    | (.value.source // {}) as $s
    | (
        if   $s.source == "github"    then $s.repo + (if $s.ref then "#" + $s.ref else "" end)
        elif $s.source == "git"       then $s.url  + (if $s.ref then "#" + $s.ref else "" end)
        elif $s.source == "url"       then $s.url
        elif $s.source == "directory" then $s.path
        elif $s.source == "file"      then $s.path
        else "" end
      ) as $arg
    | $name + "\t" + $arg
  ' "$fragment")

  local name arg
  while IFS=$'\t' read -r name arg; do
    [[ -n "$name" ]] || continue
    if [[ -z "$arg" ]]; then
      command echo "WARNING: skip marketplace '$name': unsupported source type in $fragment"
      continue
    fi
    command echo "add marketplace $name ($arg) ..."
    command claude plugin marketplace add "$arg" --scope user \
      || command echo "WARNING: failed to add marketplace '$name' ($arg)"
  done <<< "$marketplaces"

  # enabledPlugins は "<plugin>@<marketplace>" をキーに持つ。値が false のものは
  # 明示的に無効化されているので取得しない。
  local plugins
  plugins=$(command jq -r '
    (.enabledPlugins // {}) | to_entries[] | select(.value != false) | .key
  ' "$fragment")

  local plugin
  while read -r plugin; do
    [[ -n "$plugin" ]] || continue
    command echo "install plugin $plugin ..."
    command claude plugin install "$plugin" --scope user \
      || command echo "WARNING: failed to install plugin '$plugin'"
  done <<< "$plugins"
}

dotdir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
link_to_homedir $dotdir
set_global_gitignore $dotdir
link_sheldon_config $dotdir
link_mise_config $dotdir
install_mise_tools
install_gh_extensions
link_claude_config $dotdir
command echo "Install completed!!!!"
command echo "run 'exec zsh' to start a shell with the installed tools on PATH"
