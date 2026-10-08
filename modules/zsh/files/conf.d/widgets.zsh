# Ctrl-G: リポジトリを選んで移動する。空白やシェル記号を含むパスも引用する。
function ghq-fzf() {
  local repo
  if ! (( $+commands[ghq] && $+commands[fzf] )); then
    print -u2 "ghq-fzf: ghq and fzf are required"
    return 1
  fi
  repo=$(ghq list --full-path | fzf) || return 0
  [[ -n "$repo" ]] || return 0
  BUFFER="cd -- ${(q)repo}"
  zle accept-line
}
zle -N ghq-fzf
bindkey '^g' ghq-fzf

# 引数あり: Git の引数とエラーをそのまま扱う。引数なし: ブランチを選ぶ。
function gs() {
  local branch
  if ! (( $+commands[git] )); then
    print -u2 "gs: git is required"
    return 1
  fi
  if (( $# )); then
    git switch "$@"
    return $?
  fi
  if ! (( $+commands[fzf] )); then
    print -u2 "gs: fzf is required"
    return 1
  fi
  git rev-parse --git-dir >/dev/null || return $?
  branch=$(git for-each-ref --format='%(refname:short)' refs/heads refs/remotes/origin |
    sed -e '/^origin\/HEAD$/d' -e 's#^origin/##' |
    sort -u | fzf --height 40% --reverse --border) || return 0
  [[ -n "$branch" ]] || return 0
  git switch "$branch"
}
