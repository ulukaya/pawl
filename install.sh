#!/usr/bin/env bash
# install.sh: install pawl into Antigravity, Claude Code and Codex.
#
#   ./install.sh                 every harness found on this machine
#   ./install.sh --claude        one harness (--antigravity, --claude, --codex)
#   ./install.sh --uninstall     reverse every step
#   ./install.sh --help          all options (install.py)
#
# PAWL_PYTHON picks the interpreter (default python3). Exit codes: 0 done;
# 1 a conflict was refused or the tests failed; 2 a missing interpreter,
# harness CLI, pytest, or readable registry.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
PYTHON="${PAWL_PYTHON:-python3}"

if ! command -v "$PYTHON" > /dev/null; then
  echo "install: $PYTHON not found; set PAWL_PYTHON to a Python 3 binary" >&2
  exit 2
fi
exec "$PYTHON" -B "$REPO/install.py" "$@"
