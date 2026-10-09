#!/bin/sh
# One-line installer:
#   curl -fsSL https://raw.githubusercontent.com/quarksus/SwyxClaude/main/install.sh | sh
set -e
REPO=https://github.com/quarksus/SwyxClaude
if ! command -v pipx >/dev/null 2>&1; then
  echo "pipx is required. Install it first, e.g.: sudo apt install pipx && pipx ensurepath" >&2
  exit 1
fi
pipx install --force "git+$REPO"
echo
echo "Installed. Open a new terminal if needed, then run:  p280-bridge run"
echo "(the first run guides you through phone setup)"
