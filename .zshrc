# Created by newuser for 5.8.1

# Install mise automatically if not installed
if ! command -v mise >/dev/null 2>&1; then
    curl https://mise.run | sh
fi

eval "$(~/.local/bin/mise activate zsh)"

# sheldon (zsh plugin manager) — install via mise if missing
if ! command -v sheldon >/dev/null 2>&1; then
  mise install sheldon
fi
mise use -g sheldon@latest

# zsh-completions を fpath に載せてから compinit を呼ぶ必要があるため、
# sheldon source の前に fpath を確定させ、あとで compinit を実行する。
eval "$(sheldon source)"

autoload -Uz compinit && compinit

# Configure ghq via mise (install only if missing)
if ! command -v ghq >/dev/null 2>&1; then
  mise install ghq
fi
mise use -g ghq

# Configure fzf via mise (install only if missing)
if ! command -v fzf >/dev/null 2>&1; then
  mise install fzf
fi
mise use -g fzf@latest

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
