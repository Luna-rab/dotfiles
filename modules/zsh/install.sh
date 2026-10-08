install_zsh() {
  local name
  install_link "$DOTFILES_ROOT/modules/zsh/files/zshrc" "$DOTFILES_TARGET_HOME/.zshrc"
  for name in tools completion widgets; do
    install_link "$DOTFILES_ROOT/modules/zsh/files/conf.d/$name.zsh" "$DOTFILES_TARGET_HOME/.config/zsh/conf.d/$name.zsh"
  done
}
