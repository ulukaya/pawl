#!/usr/bin/env bash
# install.sh: link this checkout into agent harnesses and verify it.
#
# Options:
#   --antigravity  Install into Antigravity / Jetski (~/.gemini/config)
#   --claude       Install into Claude Code (~/.claude/plugins/pawl)
#   --codex        Install into Codex / Agent Skills (~/.codex, ~/.agent-skills)
#   --all          Install into all detected harnesses (default when no flags)
#   --force        Relink or move aside conflicting directories
#   -h, --help     Show this help
#
# Exit codes: 0 installed and verified; 1 refused a conflict or tests
# failed; 2 missing interpreter or pytest, or an unreadable registry.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
CONFIG="${PAWL_PLUGIN_CONFIG_DIR:-$HOME/.gemini/config}"
CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
CODEX_DIR="${CODEX_CONFIG_DIR:-$HOME/.codex}"
AGENT_SKILLS_DIR="${AGENT_SKILLS_CONFIG_DIR:-$HOME/.agent-skills}"
DEST="$CONFIG/plugins/pawl"
REGISTRY="$CONFIG/plugins.json"
PYTHON="${PAWL_PYTHON:-python3}"
FORCE=0

TARGET_ANTIGRAVITY=0
TARGET_CLAUDE=0
TARGET_CODEX=0
SPECIFIC_TARGET=0

for arg in "$@"; do
  case "$arg" in
    --force) FORCE=1 ;;
    --antigravity) TARGET_ANTIGRAVITY=1; SPECIFIC_TARGET=1 ;;
    --claude) TARGET_CLAUDE=1; SPECIFIC_TARGET=1 ;;
    --codex) TARGET_CODEX=1; SPECIFIC_TARGET=1 ;;
    --all) SPECIFIC_TARGET=0 ;;
    -h|--help) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "install: unknown option $arg (use --force or --help)" >&2
       exit 1 ;;
  esac
done

if [[ "$SPECIFIC_TARGET" -eq 0 ]]; then
  TARGET_ANTIGRAVITY=1
  if [[ -d "$CLAUDE_DIR" ]]; then
    TARGET_CLAUDE=1
  fi
  if [[ -d "$CODEX_DIR" ]] || [[ -d "$AGENT_SKILLS_DIR" ]]; then
    TARGET_CODEX=1
  fi
fi

if ! command -v "$PYTHON" > /dev/null; then
  echo "install: $PYTHON not found; set PAWL_PYTHON to a Python 3 binary" >&2
  exit 2
fi

symlink_path() {
  local target="$1"
  local dest="$2"
  local parent
  parent="$(dirname "$dest")"
  mkdir -p "$parent"
  if [[ -L "$dest" ]]; then
    local current
    current="$(cd "$dest" 2> /dev/null && pwd -P || readlink "$dest")"
    if [[ "$current" == "$target" ]]; then
      echo "link: $dest -> $target (already in place)"
      return 0
    fi
    if [[ "$FORCE" != 1 ]]; then
      echo "install: $dest points to $current; rerun with --force to point it at $target" >&2
      exit 1
    fi
    ln -sfn "$target" "$dest"
    echo "link: $dest -> $target (replaced link to $current)"
    return 0
  fi
  if [[ -e "$dest" ]]; then
    if [[ "$FORCE" != 1 ]]; then
      echo "install: $dest exists and is not a link; rerun with --force to move it aside" >&2
      exit 1
    fi
    local backup
    backup="$dest.bak.$(date +%Y%m%d-%H%M%S)"
    mv "$dest" "$backup"
    echo "moved: $dest -> $backup"
  fi
  ln -s "$target" "$dest"
  echo "link: $dest -> $target"
}

register_antigravity() {
  mkdir -p "$CONFIG"
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

if [[ "$TARGET_ANTIGRAVITY" -eq 1 ]]; then
  echo "==> Antigravity / Jetski..."
  symlink_path "$REPO" "$DEST"
  register_antigravity
fi

if [[ "$TARGET_CLAUDE" -eq 1 ]]; then
  echo "==> Claude Code..."
  symlink_path "$REPO" "$CLAUDE_DIR/plugins/pawl"
  if [[ -d "$CLAUDE_DIR/skills" ]]; then
    symlink_path "$REPO/skills/pawl" "$CLAUDE_DIR/skills/pawl"
  fi
fi

if [[ "$TARGET_CODEX" -eq 1 ]]; then
  echo "==> OpenAI Codex / Agent Skills..."
  symlink_path "$REPO/skills/pawl" "$CODEX_DIR/skills/pawl"
  symlink_path "$REPO/skills/pawl" "$AGENT_SKILLS_DIR/pawl"
fi

if ! "$PYTHON" -c 'import pytest' 2> /dev/null; then
  echo "install: linked and registered, but $PYTHON has no pytest, so the" \
    "tests cannot run; install pytest or set PAWL_PYTHON, then run" \
    "$PYTHON -B $REPO/run_tests.py" >&2
  exit 2
fi
echo "verify: $PYTHON -B $REPO/run_tests.py"
if ! "$PYTHON" -B "$REPO/run_tests.py"; then
  echo "install: tests failed; the plugin is linked but not verified" >&2
  exit 1
fi
echo "pawl installed and verified across all target harnesses."
