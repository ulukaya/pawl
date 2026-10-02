#!/usr/bin/env python3
"""Portability gate for the pawl plugin.

Exit 1 when the tree carries anything that ties it to one machine or adds a
dependency the loader cannot satisfy on a fresh install.

Checked:
  1. No absolute home directory in any text file.
  2. No files or directories that would add a dependency (agents/,
     mcp_config.json, plugins.json).
  3. plugin.json has only the fields the loader keeps, and its logo path
     resolves to a file inside the plugin.
  4. Every hook command, PreToolUse and Stop alike, uses ${PLUGIN_ROOT} or a
     relative path, never an absolute one.
  5. pieces/ import only the standard library.
  6. No CR byte in any text file; a line-ending presubmit
     presubmit rejects CRLF.
  7. No markdown prose line over 80 columns. Front matter, fenced code,
     table rows, HTML lines, lines carrying a URL and lines whose only wide
     token is one backtick span are exempt; MarkdownLinter flags the rest.
  8. Every skills/pawl/references/<x>.md has an Environment section, and
     every env var it names appears in that piece's source under pieces/<x>/.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path  # pylint: disable=g-importing-member
import re
import sys

ROOT = Path(__file__).resolve().parent
TEXT_SUFFIXES = frozenset(
    {".py", ".md", ".json", ".txt", ".toml", ".yaml", ".yml", ".sh", ".svg"}
)
SKIP_DIRS = frozenset({"__pycache__", ".git"})

HOME_DIR_RE = re.compile(r"/(?:usr/local/google/)?home/[a-z]+/|/Users/[a-z]+/")
FORBIDDEN_PATHS = frozenset({"agents", "mcp_config.json", "plugins.json"})
PLUGIN_JSON_FIELDS = frozenset({
    "name",
    "displayName",
    "description",
    "logo",
    "suggestedPrompts",
    "disabled",
})


def text_files():
  for p in ROOT.rglob("*"):
    if any(part in SKIP_DIRS for part in p.parts):
      continue
    if (
        p.is_file()
        and p.suffix in TEXT_SUFFIXES
        and p.name != Path(__file__).name
    ):
      yield p


def stdlib_names() -> set[str]:
  return set(sys.stdlib_module_names)


def is_test_module(p: Path) -> bool:
  return p.suffix == ".py" and (
      p.name.startswith("test_") or p.name.endswith("_test.py")
  )


def check_home_dirs(errors: list[str]) -> None:
  for p in text_files():
    if is_test_module(p):
      continue  # fixtures carry fake home paths to trigger the egress firewall
    for i, line in enumerate(p.read_text(errors="replace").splitlines(), 1):
      m = HOME_DIR_RE.search(line)
      if m:
        errors.append(
            f"{p.relative_to(ROOT)}:{i}: absolute home dir {m.group(0)!r}"
        )


def check_paths(errors: list[str]) -> None:
  for p in ROOT.rglob("*"):
    if p.name in FORBIDDEN_PATHS:
      errors.append(f"{p.relative_to(ROOT)}: must not ship")


def check_plugin_json(errors: list[str], root: Path = ROOT) -> None:
  data = json.loads((root / "plugin.json").read_text())
  extra = set(data) - PLUGIN_JSON_FIELDS
  if extra:
    errors.append(f"plugin.json: unknown fields {sorted(extra)}")
  logo = data.get("logo")
  if logo is not None and not (root / logo).is_file():
    errors.append(f"plugin.json: logo {logo!r} is not a file in the plugin")


def _hook_commands(entry: dict) -> list[str]:
  """Commands of one hooks.json entry: nested under "hooks" (PreToolUse) or
  carried directly (Stop)."""
  nested = [h.get("command", "") for h in entry.get("hooks", [])]
  return nested + ([entry["command"]] if "command" in entry else [])


def check_hooks(errors: list[str], root: Path = ROOT) -> None:
  hooks = json.loads((root / "hooks.json").read_text())
  for group, cfg in hooks.items():
    for event, entries in cfg.items():
      if not isinstance(entries, list):
        continue
      for cmd in (c for e in entries for c in _hook_commands(e)):
        if re.search(r"\s/(?!tmp/)", " " + cmd):
          errors.append(
              f"hooks.json[{group}][{event}]: absolute path in {cmd!r}"
          )


def check_line_endings(errors: list[str], root: Path = ROOT) -> None:
  """Fails on any CR byte in any text file under root, this file included."""
  for p in sorted(root.rglob("*")):
    if any(part in SKIP_DIRS for part in p.parts):
      continue
    if not (p.is_file() and p.suffix in TEXT_SUFFIXES):
      continue
    for i, line in enumerate(p.read_bytes().split(b"\n"), 1):
      if b"\r" in line:
        errors.append(f"non-unix line ending: {p.relative_to(root)}:{i}")


def check_stdlib_only(errors: list[str]) -> None:
  """Flags any import under pieces/ that is not in the stdlib."""
  std = stdlib_names()
  for p in (ROOT / "pieces").rglob("*.py"):
    tree = ast.parse(p.read_text(), filename=str(p))
    for node in ast.walk(tree):
      names = []
      if isinstance(node, ast.Import):
        names = [a.name.split(".")[0] for a in node.names]
      elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
        names = [node.module.split(".")[0]]
      for n in names:
        if n == "pytest" and p.name.startswith("test_"):
          continue
        if n not in std and not (p.parent / f"{n}.py").exists():
          errors.append(f"{p.relative_to(ROOT)}: non-stdlib import {n!r}")


MAX_MD_WIDTH = 80
CODE_SPAN_RE = re.compile(r"`[^`\n]+`")


def _only_wide_token_is_code_span(line: str) -> bool:
  spans = CODE_SPAN_RE.findall(line)
  if len(spans) != 1:
    return False
  return len(line.replace(spans[0], "", 1)) <= MAX_MD_WIDTH


def check_markdown_width(errors: list[str], root: Path = ROOT) -> None:
  """Fails on markdown prose lines over 80 columns.

  Exempt: YAML front matter, lines inside fenced code, table rows (including
  the `:` continuation rows mdformat emits for multi-line cells), HTML lines,
  lines carrying a URL, and lines whose only over-width token is a single
  backtick span.

  Args:
    errors: list that receives one message per offending line.
    root: plugin root to scan.
  """
  for p in sorted(root.rglob("*.md")):
    if any(part in SKIP_DIRS for part in p.parts):
      continue
    lines = p.read_text(errors="replace").splitlines()
    in_front_matter = bool(lines) and lines[0] == "---"
    in_fence = False
    for i, line in enumerate(lines, 1):
      if in_front_matter:
        in_front_matter = not (i > 1 and line == "---")
        continue
      stripped = line.lstrip()
      if stripped.startswith("```") or stripped.startswith("~~~"):
        in_fence = not in_fence
        continue
      if in_fence or len(line) <= MAX_MD_WIDTH:
        continue
      if stripped.startswith(("|", ":", "<")):
        continue
      if "http://" in line or "https://" in line:
        continue
      if _only_wide_token_is_code_span(line):
        continue
      errors.append(f"line over 80: {p.relative_to(root)}:{i}")


# References whose name does not match their pieces/ directory. send-gates.md
# documents hooks/pawl_hook.py, which fronts three pieces, so it is checked
# against the hook plus those pieces.
SKILL_PIECE_DIRS = {
    "ratchet": ("pieces/ratchet-baseline",),
    "send-gates": (
        "hooks",
        "pieces/send-budget",
        "pieces/egress-firewall",
        "pieces/prose-gate",
    ),
}
ENV_VAR_RE = re.compile(r"`([A-Z][A-Z0-9_]+)(?:=[^`]*)?`")
HEADING_RE = re.compile(r"^#+\s")


def _environment_section(text: str) -> list[str] | None:
  """Lines of the `## Environment` section; None when there is no heading."""
  out: list[str] | None = None
  for line in text.splitlines():
    if HEADING_RE.match(line):
      if out is not None:
        break
      if line.strip().lower() == "## environment":
        out = []
      continue
    if out is not None:
      out.append(line)
  return out


def check_skill_env(errors: list[str], root: Path = ROOT) -> None:
  """Fails when a piece reference documents an env var its source never reads.

  Each skills/pawl/references/<x>.md Environment section may name only
  variables that appear in pieces/<x>/*.py (or the dirs in SKILL_PIECE_DIRS).
  This is what catches a generator pasting one piece's block into every
  reference.

  Args:
    errors: list that receives one message per unknown variable.
    root: plugin root to scan.
  """
  for skill in sorted((root / "skills" / "pawl" / "references").glob("*.md")):
    short = skill.stem
    dirs = SKILL_PIECE_DIRS.get(short, (f"pieces/{short}",))
    source = ""
    for d in dirs:
      for py in sorted((root / d).glob("*.py")):
        source += py.read_text(errors="replace")
    rel = skill.relative_to(root)
    if not source:
      errors.append(f"{rel}: no piece source under {dirs}")
      continue
    section = _environment_section(skill.read_text(errors="replace"))
    if section is None:
      errors.append(f"{rel}: no `## Environment` section")
      continue
    env_text = "\n".join(section)
    for var in sorted(set(ENV_VAR_RE.findall(env_text))):
      if var not in source:
        errors.append(f"{rel}: env var {var} not in {' '.join(dirs)}")


def main() -> int:
  errors: list[str] = []
  for check in (
      check_home_dirs,
      check_paths,
      check_plugin_json,
      check_hooks,
      check_stdlib_only,
      check_line_endings,
      check_markdown_width,
      check_skill_env,
  ):
    check(errors)
  for e in errors:
    print(f"PORTABLE: {e}")
  print(
      "portable: clean" if not errors else f"portable: {len(errors)} problem(s)"
  )
  return 1 if errors else 0


if __name__ == "__main__":
  sys.exit(main())
