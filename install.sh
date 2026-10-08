#!/bin/bash
set -euo pipefail

DOTFILES_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
DOTFILES_TARGET_HOME="${DOTFILES_TARGET_HOME:-$HOME}"
[[ "$DOTFILES_TARGET_HOME" == /* ]] || { echo "ERROR: DOTFILES_TARGET_HOME must be absolute" >&2; exit 2; }
skip_toolkit=0
for arg in "$@"; do
  case "$arg" in
    --skip-agent-toolkit) skip_toolkit=1 ;;
    --help|-h) echo "usage: ./install.sh [--skip-agent-toolkit]"; exit 0 ;;
    *) echo "ERROR: unknown argument: $arg" >&2; exit 2 ;;
  esac
done

source "$DOTFILES_ROOT/lib/install.sh"
for module in git mise sheldon zsh agent-toolkit; do
  source "$DOTFILES_ROOT/modules/$module/install.sh"
done

# 配置はネットワークに依存させない。配置が失敗した場合はここで終了する。
install_git
install_mise_config
install_sheldon
install_zsh

# 外部ツールの取得失敗は、利用可能になった通常設定を巻き添えにしない。
tools_result="installed"
if ! install_mise_tools; then
  tools_result="warning (retry ./install.sh)"
fi
toolkit_result="skipped"
if [[ "$skip_toolkit" == 0 ]]; then
  toolkit_result="installed"
  if ! install_agent_toolkit; then
    toolkit_result="warning (retry ./install.sh)"
  fi
fi
printf '\nSettings: installed\nTools: %s\nAgent toolkit: %s\n' "$tools_result" "$toolkit_result"
echo "run 'exec zsh' to use the installed shell configuration"
