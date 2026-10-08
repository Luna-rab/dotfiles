# 共通の配置処理。読み込みだけではホームを書き換えない。
warn() { printf 'WARNING: %s\n' "$*" >&2; }

backup_target() {
  local target=$1 relative stamp dest n=0
  relative="${target#"$DOTFILES_TARGET_HOME/"}"
  stamp=$(date +%Y%m%d%H%M%S)
  dest="$DOTFILES_TARGET_HOME/.dotbackup/$relative.$stamp"
  while [[ -e "$dest" || -L "$dest" ]]; do
    n=$((n + 1))
    dest="$DOTFILES_TARGET_HOME/.dotbackup/$relative.$stamp.$n"
  done
  mkdir -p "$(dirname "$dest")"
  mv "$target" "$dest"
  printf 'backup %s -> %s\n' "$target" "$dest"
}

install_link() {
  local source=$1 target=$2
  [[ -e "$source" ]] || { echo "ERROR: missing source: $source" >&2; return 1; }
  if [[ -L "$target" && "$(readlink "$target")" == "$source" ]]; then
    return 0
  fi
  if [[ -e "$target" || -L "$target" ]]; then backup_target "$target"; fi
  mkdir -p "$(dirname "$target")"
  ln -s "$source" "$target"
  printf 'link %s -> %s\n' "$target" "$source"
}

install_copy() {
  local source=$1 target=$2
  [[ -f "$source" ]] || { echo "ERROR: missing source: $source" >&2; return 1; }
  if [[ -f "$target" && ! -L "$target" ]] && cmp -s "$source" "$target"; then
    return 0
  fi
  if [[ -e "$target" || -L "$target" ]]; then backup_target "$target"; fi
  mkdir -p "$(dirname "$target")"
  cp "$source" "$target"
  printf 'copy %s -> %s\n' "$source" "$target"
}

# zsh の起動前でも mise の CLI と uv tool の入口を解決する。
with_mise() {
  local mise_bin="$DOTFILES_TARGET_HOME/.local/bin/mise"
  if [[ -x "$mise_bin" ]]; then
    PATH="$DOTFILES_TARGET_HOME/.local/bin:$PATH" "$mise_bin" -C "$DOTFILES_TARGET_HOME" exec -- "$@"
  else
    PATH="$DOTFILES_TARGET_HOME/.local/bin:$PATH" "$@"
  fi
}
