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

# mise 本体を入れる。既に PATH 上にあるか ~/.local/bin/mise が存在すればそれを使う。
# 見つからない場合だけ公式インストーラ (https://mise.run) を実行する。
install_mise() {
  if command -v mise >/dev/null 2>&1; then
    MISE="$(command -v mise)"
    return 0
  fi
  if [[ ! -x "$HOME/.local/bin/mise" ]]; then
    command echo "install mise ..."
    command curl -fsSL https://mise.run | command sh
  fi
  MISE="$HOME/.local/bin/mise"
}

# mise/config.toml を ~/.config/mise/config.toml へ symlink し、
# そこに書かれたツールをインストールする。
#
# 引数なしの `mise install` を使わないのは、~/.tool-versions のような
# dotfiles 管理外の設定ファイルにあるツールまで入れに行くため。
# [tools] セクションのツール名だけを取り出して明示的に渡す。
install_mise_tools() {
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

  local tools
  tools=$(command awk '/^\[tools\]/{f=1; next} /^\[/{f=0} f && /^[[:alnum:]_-]+[[:space:]]*=/{print $1}' "$src")
  if [[ -n "$tools" ]]; then
    command echo "install tools: $(command echo $tools)"
    # shellcheck disable=SC2086
    "$MISE" install $tools
  fi

  # mise が生成する shim（ツール本体へ橋渡しする実行ファイル）を PATH に載せる。
  # この install.sh の以降の処理が jq を呼べるようにするため。
  export PATH="$HOME/.local/share/mise/shims:$PATH"
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
  for d in skills commands agents rules scripts; do
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

dotdir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
link_to_homedir $dotdir
set_global_gitignore $dotdir
install_mise
# link_claude_config は jq を使うので、jq を入れる install_mise_tools を先に呼ぶ
install_mise_tools $dotdir
link_sheldon_config $dotdir
link_claude_config $dotdir
command echo "Install completed!!!!"
