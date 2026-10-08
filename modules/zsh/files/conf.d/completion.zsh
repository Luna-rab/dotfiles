if (( $+commands[sheldon] )); then
  eval "$(sheldon source)"
else
  print -u2 "WARNING: sheldon not found; run ./install.sh in dotfiles"
fi
autoload -Uz compinit
compinit
