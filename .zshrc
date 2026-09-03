# Created by newuser for 5.8.1

# mise（プログラミング言語やコマンドラインツールのバージョンを管理するツール）が
# 管理するツールを PATH に載せる。
#
# ここではツールを入れない。mise 自体と、mise/config.toml に書いたツールは
# install.sh が入れる。zsh を起動するたびにインストールを試すのをやめ、
# 入れる処理を install.sh の 1 か所に集めるため。
# ツールが足りないときは ./install.sh を実行し直す。
if [[ -x ~/.local/bin/mise ]]; then
  eval "$(~/.local/bin/mise activate zsh)"
else
  echo "WARNING: ~/.local/bin/mise not found. run install.sh in your dotfiles"
fi

# zsh-completions を fpath に載せてから compinit を呼ぶ必要があるため、
# sheldon source の前に fpath を確定させ、あとで compinit を実行する。
#
# sheldon（zsh のプラグインマネージャ）は mise/config.toml に書いてあるので
# install.sh が入れる。それでも存在を確かめるのは、install.sh を実行する前に
# zsh を開いたときに「command not found」を並べて出さないため。
if command -v sheldon >/dev/null 2>&1; then
  eval "$(sheldon source)"
else
  echo "WARNING: sheldon not found. run install.sh in your dotfiles"
fi

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
