#!/bin/bash
# Ref: https://qiita.com/yutkat/items/c6c7584d9795799ee164#%E3%82%B7%E3%83%B3%E3%83%97%E3%83%AB%E3%81%AAdotfiles%E3%82%A4%E3%83%B3%E3%82%B9%E3%83%88%E3%83%BC%E3%83%A9%E3%83%BC%E3%82%92%E4%BD%9C%E3%81%A3%E3%81%A6%E3%81%BF%E3%82%88%E3%81%86

set -ue

# dist/ の中身は $HOME の写しである。`dot-` で始まる名前が、$HOME では先頭にドットが付く
# 名前になる（`dist/dot-zshrc` → `~/.zshrc`）。
#
# **配るものは dist/ に置く。** dist/ の外は見ないので、このリポジトリ自身の設定や
# 検査の生成物が $HOME に混ざることはない。
link_to_homedir() {
  command echo "backup old dotfiles..."
  if [ ! -d "$HOME/.dotbackup" ];then
    command echo "$HOME/.dotbackup not found. Auto Make it"
    command mkdir "$HOME/.dotbackup"
  fi

  local distdir=$1
  local f
  for f in "$distdir"/dot-*; do
    [[ -e "$f" ]] || continue
    local name
    name=$(command basename "$f")
    # 配布先が $HOME の直下ではないもの（dot-config → ~/.config、dot-local → ~/.local）と、
    # 項目ごとに置き方が違うもの（dot-claude は link_claude_config() を見る）は、
    # それぞれ専用の関数が受け持つ。
    #
    # **dot-config の下は 1 ファイルずつ名指しで配る。** `dist/dot-config/gh/config.yml` を
    # 足しても、それを配る関数が無ければ何も起きない（警告も出ない）。~/.config へ配る
    # ファイルを増やすときは、link_mise_config() と同じ形の関数を書いて呼び出しに足す。
    [[ $name == dot-claude || $name == dot-config || $name == dot-local ]] && continue
    local dst="$HOME/.${name#dot-}"
    if [[ -L "$dst" ]]; then
      command rm -f "$dst"
    elif [[ -e "$dst" ]]; then
      command mv "$dst" "$HOME/.dotbackup"
    fi
    command ln -snf "$f" "$dst"
    command echo "create symboliclink $f"
  done
}

set_global_gitignore() {
  if [ ! -d "$HOME/.config/git" ];then
    command echo "$HOME/.config/git not found. Auto Make it"
    command mkdir -p "$HOME/.config/git"
  fi
  if [[ -e "$HOME/.config/git/ignore" ]];then
    command mv "$HOME/.config/git/ignore" "$HOME/.config/git/ignore.backup"
  fi
  local distdir=$1
  command cp "$distdir/dot-config/git/ignore" "$HOME/.config/git/ignore"
}

link_sheldon_config() {
  local distdir=$1
  local src="$distdir/dot-config/sheldon/plugins.toml"
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

# ここに書いてあるツールは、このあと install_mise_tools() が入れる。
#
# symlink にできるのは、`mise use -g <tool>@latest` が symlink を消して新しい
# ファイルを作るのではなく、symlink 越しに元のファイルへ書き込むため（mise
# 2026.8.2 で確認）。つまりコマンドで追加したツールも dotfiles リポジトリの
# git diff に出る。
link_mise_config() {
  local distdir=$1
  local src="$distdir/dot-config/mise/config.toml"
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

# dist/dot-local/bin/ 配下のファイルを ~/.local/bin に 1 つずつ symlink する。
# ~/.local/bin は PATH の順序で /usr/local/bin より先に来る（.profile が PATH の先頭に足す）。
link_local_bin() {
  local distdir=$1
  local srcdir="$distdir/dot-local/bin"
  local dstdir="$HOME/.local/bin"

  # dist/dot-local/bin/ ごと消したときも、残った symlink を片付ける
  prune_dead_links "$dstdir"
  prune_dead_links "$HOME/.local/share"
  [[ -d "$srcdir" ]] || return 0

  command echo "setup ~/.local/bin ..."
  command mkdir -p "$dstdir"

  local src
  for src in "$srcdir"/*; do
    [[ -f "$src" ]] || continue
    local name
    name=$(command basename "$src")
    local dst="$dstdir/$name"
    if [[ -L "$dst" ]]; then
      command rm -f "$dst"
    elif [[ -e "$dst" ]]; then
      command mv "$dst" "$HOME/.dotbackup"
    fi
    command ln -snf "$src" "$dst"
    command echo "create symboliclink $src"
  done
}

# **PATH の上に行き先の無い symlink が残ると `command not found` になり、消えたのか
# 壊れたのか区別が付かない。**
prune_dead_links() {
  local dstdir=$1
  [[ -d "$dstdir" ]] || return 0

  local link
  for link in "$dstdir"/*; do
    if [[ -L "$link" && ! -e "$link" ]]; then
      command rm -f "$link"
      command echo "remove dangling symboliclink $link"
    fi
  done
}

link_claude_config() {
  local distdir=$1
  local src="$distdir/dot-claude"
  local dst="$HOME/.claude"
  [[ -d "$src" ]] || return 0

  command echo "setup ~/.claude ..."
  # 既存のランタイムデータ（projects/ history.jsonl 等）を温存するため mkdir -p のみ
  command mkdir -p "$dst"

  # ディレクトリは symlink（リポジトリの編集が即反映される）
  local d
  for d in commands agents rules scripts hooks; do
    [[ -d "$src/$d" ]] || continue
    if [[ -L "$dst/$d" ]]; then
      command rm -f "$dst/$d"
    elif [[ -e "$dst/$d" ]]; then
      command mv "$dst/$d" "$HOME/.dotbackup"
    fi
    command ln -snf "$src/$d" "$dst/$d"
    command echo "create symboliclink $src/$d"
  done

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

  link_claude_skills "$src" "$dst"
  merge_claude_settings "$src" "$dst"
  warm_claude_hooks "$src"
}

# dist/dot-claude/hooks/ のスクリプトが宣言する依存（PEP 723 の `# /// script`）を先に取り寄せる。
# フックは Claude Code が応答を終えるたびに起動されるので、初回の起動で uv が
# tree-sitter-language-pack をダウンロードし始めると、その間ユーザーは待たされる
# （settings.json の timeout 15 秒も超える）。取り寄せてあれば 1 回 0.06 秒で終わる。
# uv が無ければ（mise の導入に失敗しているなど）警告だけ出して先へ進む。
#
# **install_mise_tools() が入れた uv は、この時点ではまだ PATH に無い。** PATH に載せるのは
# .zshrc の `mise activate` で、install.sh はそれを通らないため。
warm_claude_hooks() {
  local src=$1
  [[ -d "$src/hooks" ]] || return 0
  local mise_bin="$HOME/.local/bin/mise"
  local uv_dir="" uv_path=""
  if ! command -v uv >/dev/null 2>&1; then
    if [[ -x "$mise_bin" ]] && uv_path=$("$mise_bin" which uv 2>/dev/null); then
      uv_dir=$(command dirname "$uv_path")
    else
      command echo "WARNING: uv not found. skip warming up $src/hooks (first Stop hook run will download deps)"
      return 0
    fi
  fi
  local hook
  for hook in "$src"/hooks/*.py; do
    [[ -x "$hook" ]] || continue
    if PATH="${uv_dir:+$uv_dir:}$PATH" "$hook" --warm; then
      command echo "warm up $hook"
    else
      command echo "WARNING: failed to warm up $hook"
    fi
  done
}

# ~/.claude/skills をディレクトリごと symlink にしてはいけない。ここは第三者スキルの
# インストーラ（`npx skills add <owner>/<repo> -g`）が実体をコピーする先でもあり、
# symlink にしているとそのコピーが dotfiles リポジトリの中に落ちる（archify を入れた実測で
# 214 ファイル / 8.5MB が git status に並ぶ）。
# Claude Code はスキル 1 つ単位の symlink も辿る（v2.1.273 で確認）。
link_claude_skills() {
  local src=$1
  local dst=$2
  local srcdir="$src/skills"
  local dstdir="$dst/skills"
  [[ -d "$srcdir" ]] || return 0

  if [[ -L "$dstdir" ]]; then
    command rm -f "$dstdir"
  elif [[ -e "$dstdir" && ! -d "$dstdir" ]]; then
    command mv "$dstdir" "$HOME/.dotbackup"
  fi
  command mkdir -p "$dstdir"

  local s
  for s in "$srcdir"/*/; do
    [[ -d "$s" ]] || continue
    local name
    name=$(command basename "$s")
    local link="$dstdir/$name"
    if [[ -L "$link" ]]; then
      command rm -f "$link"
    elif [[ -e "$link" ]]; then
      command mv "$link" "$HOME/.dotbackup"
    fi
    command ln -snf "$srcdir/$name" "$link"
    command echo "create symboliclink $srcdir/$name"
  done

  # リポジトリから消したスキルの symlink が残ると、Claude Code が行き先の無い symlink を読む
  local link
  for link in "$dstdir"/*; do
    if [[ -L "$link" && ! -e "$link" ]]; then
      command rm -f "$link"
      command echo "remove dangling symboliclink $link"
    fi
  done
}

# ~/.claude/settings.json は symlink にしない。Claude Code 自身がこのファイルを
# 書き換えるため（`/plugin` が extraKnownMarketplaces と enabledPlugins を足す、
# `/config` が effortLevel などを書く）で、symlink だとその書き込みが dotfiles
# リポジトリへ漏れる。
merge_claude_settings() {
  local src=$1
  local dst=$2
  local fragment="$src/settings.json"
  local target="$dst/settings.json"

  [[ -e "$fragment" ]] || return 0

  if ! command -v jq >/dev/null 2>&1; then
    command echo "WARNING: jq not found. skip merging $fragment into $target"
    return 0
  fi

  if [[ -e "$target" ]]; then
    command cp "$target" "$HOME/.dotbackup/settings.json.$(date +%s 2>/dev/null || echo bak)" 2>/dev/null || true
    # '*' は再帰マージなので permissions などネストしたキーも安全に合流する
    command jq -s '.[0] * .[1]' "$target" "$fragment" > "$target.tmp" \
      && command mv "$target.tmp" "$target"
    command echo "merge $fragment into $target (deep merge; local-only keys preserved)"
  else
    command jq '.' "$fragment" > "$target"
    command echo "generate $target from $fragment"
  fi
}

# link_mise_config() より後に呼ぶ。~/.config/mise/config.toml の symlink ができていないと、
# mise は入れる対象を読み取れない。
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
}

# gh-stack は stacked PR（1 つの大きな変更を、互いに積み重なる小さな PR に分けて
# レビューに出す進め方）を操作する GitHub 公式の拡張で、`gh stack init` /
# `gh stack add` / `gh stack rebase` / `gh stack push` / `gh stack link` /
# `gh stack merge` を提供する。`dist/dot-claude/skills/autodev` がこの拡張に乗っていて、
# 入っていないとタスクのブランチを stacked PR へつなげない。
#
# gh 本体は dist/dot-config/mise/config.toml では管理していない（システム側に入っている前提）ので、
# 無いときは WARNING を出して飛ばす——install.sh 全体を止めると、symlink の作成まで
# 巻き添えにしてしまう。
#
# 既に入っているときは何もしない。`gh extension upgrade` を毎回走らせないのは、
# gh-stack が v0.1.0 で、非互換な変更が autodev の手順を壊しうるためである。
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

# archify は図を作る第三者のスキル（https://github.com/tt-a1i/archify）。
# link_claude_skills() が ~/.claude/skills を実ディレクトリにした後に呼ぶ必要がある。
#
# 作者が案内している `npx skills add tt-a1i/archify -g` は使わない。あれは default ブランチの
# HEAD を丸ごとコピーするので、(1) 入るものが実行日で変わり devcontainer を作り直すたびに
# 別のバージョンになる、(2) 開発版（実測では 2.17.0-dev.1）が入る、(3) テスト一式まで付いて
# 214 ファイル / 8.5MB になる。release 添付の配布用 zip は 76 ファイル / 5.9MB。
#
# 上げ方: https://tt-a1i.github.io/archify/skill-updates/archify/stable.json が公開している
# version と artifact.sha256 を下の 2 行に写して ./install.sh を実行する。
install_archify_skill() {
  local version="2.16.0"
  local sha256="4c59fa6557a2385beaaef8c7219cc414573acc9f0c30a932d5053b0b20689a46"

  local skillsdir="$HOME/.claude/skills"
  local dstdir="$skillsdir/archify"

  # ここが symlink のときに展開すると、行き先（dotfiles リポジトリ）を書き換えてしまう
  if [[ -L "$skillsdir" ]]; then
    command echo "WARNING: $skillsdir is a symlink. skip installing the archify skill"
    return 0
  fi

  # devcontainer を作り直すたびに落とし直さない
  if [[ -f "$dstdir/skill-release.json" ]] \
    && command grep -q "\"version\": \"$version\"" "$dstdir/skill-release.json"; then
    command echo "archify skill $version already installed"
    return 0
  fi

  local unpack
  if command -v unzip >/dev/null 2>&1; then
    unpack=unzip
  elif command -v python3 >/dev/null 2>&1; then
    unpack=python3
  else
    command echo "WARNING: neither unzip nor python3 found. skip installing the archify skill"
    return 0
  fi
  if ! command -v curl >/dev/null 2>&1 || ! command -v sha256sum >/dev/null 2>&1; then
    command echo "WARNING: curl or sha256sum not found. skip installing the archify skill"
    return 0
  fi

  local tmpdir
  tmpdir=$(command mktemp -d)
  local zip="$tmpdir/archify.zip"
  local url="https://github.com/tt-a1i/archify/releases/download/v$version/archify.zip"

  command echo "install the archify skill $version ..."
  if ! command curl -fsSL -o "$zip" "$url"; then
    command echo "WARNING: failed to download $url. skip installing the archify skill"
    command rm -rf "$tmpdir"
    return 0
  fi

  # スキルは Claude が読んで従う指示と、`node bin/archify.mjs` で実行するコードを含む。
  # 落ちてきたものが release 時点のものと同じだと確かめてから展開する。
  local actual
  actual=$(command sha256sum "$zip" | command cut -d' ' -f1)
  if [[ "$actual" != "$sha256" ]]; then
    command echo "WARNING: sha256 mismatch for archify $version. skip installing it"
    command echo "         expected $sha256"
    command echo "         actual   $actual"
    command rm -rf "$tmpdir"
    return 0
  fi

  # zip の中身は archify/ から始まる
  if [[ "$unpack" == unzip ]]; then
    command unzip -q "$zip" -d "$tmpdir/out"
  else
    command python3 -m zipfile -e "$zip" "$tmpdir/out"
  fi

  command rm -rf "$dstdir"
  command mv "$tmpdir/out/archify" "$dstdir"
  command rm -rf "$tmpdir"
  command echo "archify skill $version installed to $dstdir"
}

dotdir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
distdir="$dotdir/dist"
link_to_homedir "$distdir"
set_global_gitignore "$distdir"
link_sheldon_config "$distdir"
link_mise_config "$distdir"
install_mise_tools
install_gh_extensions
link_local_bin "$distdir"
link_claude_config "$distdir"
install_archify_skill
command echo "Install completed!!!!"
command echo "run 'exec zsh' to start a shell with the installed tools on PATH"
