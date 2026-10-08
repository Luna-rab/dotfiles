# uv tool の CLI を利用できるようにする。
# mise の有効化前に追加し、mise が管理する CLI を優先する。
typeset -U path PATH
path=("$HOME/.local/bin" $path)
export PATH

# インストールは install.sh の責務。シェルでは有効化だけを行う。
if [[ -x "$HOME/.local/bin/mise" ]]; then
  eval "$("$HOME/.local/bin/mise" activate zsh)"
else
  print -u2 "WARNING: mise not found; run ./install.sh in dotfiles"
fi
