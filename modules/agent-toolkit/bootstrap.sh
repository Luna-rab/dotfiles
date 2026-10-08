#!/bin/bash
set -euo pipefail
repo="Luna-rab/agent-toolkit"
toolkit_dir="${AGENT_TOOLKIT_DIR:-}"

if ! command -v uv >/dev/null 2>&1; then
  echo "ERROR: uv not found; install mise tools and retry" >&2
  exit 1
fi
if [[ -z "$toolkit_dir" ]]; then
  if command -v ghq >/dev/null 2>&1; then
    candidates=$(ghq list --exact --full-path "$repo")
    if [[ "$candidates" == *$'\n'* ]]; then
      echo "ERROR: multiple toolkit checkouts; set AGENT_TOOLKIT_DIR" >&2
      exit 1
    fi
    toolkit_dir="${candidates:-$(ghq root)/github.com/$repo}"
  else
    toolkit_dir="$HOME/.local/share/agent-toolkit"
  fi
fi
if [[ ! -e "$toolkit_dir" && ! -L "$toolkit_dir" ]]; then
  mkdir -p "$(dirname "$toolkit_dir")"
  git clone "https://github.com/$repo.git" "$toolkit_dir"
fi
root=$(cd "$toolkit_dir" && pwd -P)
git_root=$(git -C "$root" rev-parse --show-toplevel)
if [[ "$git_root" != "$root" || ! -f "$root/install.sh" || ! -f "$root/pyproject.toml" ]]; then
  echo "ERROR: not a toolkit checkout: $toolkit_dir; set AGENT_TOOLKIT_DIR" >&2
  exit 1
fi
# 作業中の変更を保持する。editable の参照先なので checkout は残す。
echo "install agent-toolkit from $root (no automatic pull)"
bash "$root/install.sh"
