#!/usr/bin/env bash
# install.sh: link this checkout into the agent harness and verify it.
#
#   1. Symlink the checkout to <config>/plugins/pawl. An existing link to this
#      checkout is left alone. A link elsewhere, or a real directory, is
#      refused unless --force is given; --force relinks, and moves a real
#      directory aside to pawl.bak.<timestamp> instead of deleting it.
#   2. Add {"path": "plugins/pawl"} to <config>/plugins.json, keeping every
#      entry already there. An entry that is already present is not added
#      twice. The file is rewritten atomically.
#   3. Run run_tests.py through the link.
#
# <config> is ~/.gemini/config, or PAWL_PLUGIN_CONFIG_DIR when set. The
# interpreter is PAWL_PYTHON, else python3; the tests need pytest, the plugin
# itself only the standard library.
#
# Exit codes: 0 installed and verified; 1 refused a conflict or tests
# failed; 2 missing interpreter or pytest, or an unreadable plugins.json.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
CONFIG="${PAWL_PLUGIN_CONFIG_DIR:-$HOME/.gemini/config}"
DEST="$CONFIG/plugins/pawl"
REGISTRY="$CONFIG/plugins.json"
PYTHON="${PAWL_PYTHON:-python3}"
FORCE=0

for arg in "$@"; do
  case "$arg" in
    --force) FORCE=1 ;;
    -h|--help) sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "install: unknown option $arg (use --force or --help)" >&2
       exit 1 ;;
  esac
done

if ! command -v "$PYTHON" > /dev/null; then
  echo "install: $PYTHON not found; set PAWL_PYTHON to a Python 3 binary" >&2
  exit 2
fi

link_plugin() {
  mkdir -p "$CONFIG/plugins"
  if [[ -L "$DEST" ]]; then
    local current
    current="$(cd "$DEST" 2> /dev/null && pwd -P || readlink "$DEST")"
    if [[ "$current" == "$REPO" ]]; then
      echo "link: $DEST -> $REPO (already in place)"
      return 0
    fi
    if [[ "$FORCE" != 1 ]]; then
      echo "install: $DEST points to $current; rerun with --force" \
        "to point it at $REPO" >&2
      exit 1
    fi
    ln -sfn "$REPO" "$DEST"
    echo "link: $DEST -> $REPO (replaced link to $current)"
    return 0
  fi
  if [[ -e "$DEST" ]]; then
    if [[ "$FORCE" != 1 ]]; then
      echo "install: $DEST exists and is not a link; rerun with --force" \
        "to move it aside" >&2
      exit 1
    fi
    local backup
    backup="$DEST.bak.$(date +%Y%m%d-%H%M%S)"
    mv "$DEST" "$backup"
    echo "moved: $DEST -> $backup"
  fi
  ln -s "$REPO" "$DEST"
  echo "link: $DEST -> $REPO"
}

register_plugin() {
  "$PYTHON" - "$REGISTRY" << 'PY'
import json
import os
import sys

path = sys.argv[1]
entry = {"path": "plugins/pawl"}
data = {"entries": []}
if os.path.exists(path):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if text.strip():
        try:
            data = json.loads(text)
        except ValueError as exc:
            print(f"install: {path} is not valid JSON ({exc}); fix it and"
                  " rerun", file=sys.stderr)
            sys.exit(2)
if not isinstance(data, dict) or not isinstance(data.get("entries", []), list):
    print(f"install: {path} has no \"entries\" list; fix it and rerun",
          file=sys.stderr)
    sys.exit(2)
entries = data.setdefault("entries", [])
if any(isinstance(e, dict) and e.get("path") == entry["path"]
       for e in entries):
    print(f"registry: {entry['path']} already in {path}")
    sys.exit(0)
entries.append(entry)
tmp = f"{path}.{os.getpid()}.tmp"
with open(tmp, "w", encoding="utf-8") as fh:
    json.dump(data, fh, indent=2)
    fh.write("\n")
os.replace(tmp, path)
print(f"registry: added {entry['path']} to {path}"
      f" ({len(entries)} entries)")
PY
}

link_plugin
register_plugin

if ! "$PYTHON" -c 'import pytest' 2> /dev/null; then
  echo "install: linked and registered, but $PYTHON has no pytest, so the" \
    "tests cannot run; install pytest or set PAWL_PYTHON, then run" \
    "$PYTHON -B $DEST/run_tests.py" >&2
  exit 2
fi
echo "verify: $PYTHON -B $DEST/run_tests.py"
if ! "$PYTHON" -B "$DEST/run_tests.py"; then
  echo "install: tests failed; the plugin is linked but not verified" >&2
  exit 1
fi
echo "pawl installed; restart Antigravity to load it"
