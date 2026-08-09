# Created by newuser for 5.8.1

# ツールのインストールは install.sh が行う。ここでは既に入っているものを
# シェルに適用するだけにする（インストール対象は mise/config.toml を参照）。

# mise が管理するツールを PATH に載せる。シェルごとに評価が必要なのでここに置く。
# 公式インストーラは ~/.local/bin に入れるが、この場所は PATH に無いことがあるため
# PATH 上に無ければフルパスで呼ぶ。
if command -v mise >/dev/null 2>&1; then
  eval "$(mise activate zsh)"
elif [[ -x "$HOME/.local/bin/mise" ]]; then
  eval "$("$HOME/.local/bin/mise" activate zsh)"
fi

# プロンプト・補完・シンタックスハイライトなどのプラグインを読み込む。
# zsh-completions は fpath に補完関数を追加するだけなので、
# sheldon source で fpath を確定させてから compinit を呼ぶ。
eval "$(sheldon source)"

autoload -Uz compinit && compinit

# ghq + fzf integration
function ghq-fzf() {
  local src=$(ghq list | fzf)
  if [ -n "$src" ]; then
    BUFFER="cd $(ghq root)/$src"
    zle accept-line
  fi
  zle -R -c
}
zle -N ghq-fzf
bindkey '^g' ghq-fzf

# git switch + fzf integration
function gs() {
  if [ -n "$1" ]; then
    git switch "$1" 2>/dev/null && return
  fi
  local branch=$(git branch -a | grep -v HEAD | sed 's/remotes\/origin\///' | sed 's/^\*\? *//' | sort -u | fzf --height 40% --reverse --border --query="$1")
  if [ -n "$branch" ]; then
    git switch "$branch"
  fi
}
