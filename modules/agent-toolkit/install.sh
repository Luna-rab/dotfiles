install_agent_toolkit() {
  # toolkit 自身は HOME へ配置するため、配置先が実ホーム以外なら起動しない。
  if [[ "$DOTFILES_TARGET_HOME" != "$HOME" ]]; then
    warn "agent-toolkit skipped for alternate destination"
    return 1
  fi
  if ! with_mise bash "$DOTFILES_ROOT/modules/agent-toolkit/bootstrap.sh"; then
    warn "agent-toolkit installation failed; fix the error and retry ./install.sh"
    return 1
  fi
}
