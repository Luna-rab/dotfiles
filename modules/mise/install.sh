install_mise_config() {
  install_link "$DOTFILES_ROOT/modules/mise/files/config.toml" "$DOTFILES_TARGET_HOME/.config/mise/config.toml"
}

install_mise_tools() {
  local mise_bin="$DOTFILES_TARGET_HOME/.local/bin/mise" installer
  if [[ ! -x "$mise_bin" ]]; then
    # 配置先が実ホーム以外なら、外部インストーラーで実ホームを変更しない。
    if [[ "$DOTFILES_TARGET_HOME" != "$HOME" ]]; then
      warn "mise not installed in alternate destination; skip downloading tools"
      return 1
    fi
    if ! command -v curl >/dev/null 2>&1; then
      warn "curl not found; skip installing mise"
      return 1
    fi
    installer=$(mktemp) || return 1
    if ! curl -fsSL https://mise.run -o "$installer"; then
      rm -f "$installer"
      warn "failed to download mise; retry ./install.sh"
      return 1
    fi
    if ! sh "$installer"; then
      rm -f "$installer"
      warn "failed to install mise; retry ./install.sh"
      return 1
    fi
    rm -f "$installer"
  fi
  if [[ ! -x "$mise_bin" ]] || ! "$mise_bin" -C "$DOTFILES_TARGET_HOME" install; then
    warn "failed to install mise tools; retry ./install.sh"
    return 1
  fi
}
