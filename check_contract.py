#!/usr/bin/env python3
"""check_contract.py: the plugin's wiring agrees with itself in every harness.

Run by check_portable.py (checks 9 to 12):

  9. Each hook config runs hooks/pawl.py the way its harness needs:
       hooks.json        python3 -B hooks/pawl.py <event> --only <gate>
                         --harness antigravity
       hooks/hooks.json  python3 -B "${CLAUDE_PLUGIN_ROOT}/hooks/pawl.py"
                         <event> --harness claude
       hooks/codex.json  python3 -B "${PLUGIN_ROOT}/hooks/pawl.py" <event>
                         --harness codex
     The event word matches its section (PreToolUse pre, Stop stop), every
     --only gate exists and runs on that event, every gate has an
     Antigravity group, and Claude Code and Codex run every gate.
 10. The Claude Code and Codex manifests and the marketplace name one plugin
     at one version, and every path they give exists. Each Claude Code
     plugin setting (userConfig) is one hooks/pawl.py PLUGIN_OPTIONS reads,
     and the other way round.
 11. No source file over 500 lines (CLAUDE.md guardrail 1).
 12. No function nests blocks more than 3 deep; an elif chain is one level
     (CLAUDE.md guardrail 2).

Standard library only.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path  # pylint: disable=g-importing-member
import shlex
import sys
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parent
# Tooling that lives beside a checkout but never ships: the virtualenv
# CLAUDE.md runs the battery from, and caches pytest and linters leave.
SKIP_DIRS = frozenset({
    "__pycache__", ".git", ".venv", "venv", ".tox", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", "node_modules",
})
EVENTS = {"PreToolUse": "pre", "Stop": "stop"}
HOOK_CONFIGS = {
    "hooks.json": ("antigravity", "hooks/pawl.py"),
    "hooks/hooks.json": ("claude", "${CLAUDE_PLUGIN_ROOT}/hooks/pawl.py"),
    "hooks/codex.json": ("codex", "${PLUGIN_ROOT}/hooks/pawl.py"),
}
MAX_LINES = 500
MAX_DEPTH = 3
LENGTH_SUFFIXES = frozenset({".py", ".sh", ".md", ".json", ".toml"})
_BLOCKS = (ast.If, ast.For, ast.While, ast.Try, ast.With, ast.AsyncFor,
           ast.AsyncWith, ast.Match)
_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)


def tree(root: Path = ROOT, pattern: str = "*") -> Iterator[Path]:
  """Paths under root matching pattern, sorted, tooling dirs skipped."""
  for p in sorted(root.rglob(pattern)):
    if not any(part in SKIP_DIRS for part in p.relative_to(root).parts):
      yield p


def is_test(p: Path) -> bool:
  return p.suffix == ".py" and (p.name.startswith("test_")
                                or p.name.endswith("_test.py"))


def hook_commands(entry: Dict[str, Any]) -> List[str]:
  """Commands of one hooks entry: nested under "hooks" or carried directly."""
  nested = [h.get("command", "") for h in entry.get("hooks", [])]
  return nested + ([entry["command"]] if "command" in entry else [])


def _sections(cfg: Dict[str, Any], harness: str) -> List[Dict[str, Any]]:
  if harness != "antigravity":
    return [cfg.get("hooks") or {}]
  return [g for g in cfg.values() if isinstance(g, dict)]


def config_commands(cfg: Dict[str, Any],
                    harness: str) -> Iterator[Tuple[str, str]]:
  """(event, command) for every hook command in one config."""
  for section in _sections(cfg, harness):
    lists = [(e, v) for e, v in section.items() if isinstance(v, list)]
    for event, entries in lists:
      for cmd in (c for entry in entries for c in hook_commands(entry)):
        yield event, cmd


# --- check 9: hook configs ------------------------------------------------------


def _gates() -> Dict[str, Set[str]]:
  """gate name -> the events it runs on, from this checkout's gates.py."""
  hooks = str(ROOT / "hooks")
  if hooks not in sys.path:
    sys.path.insert(0, hooks)
  import gates  # pylint: disable=g-import-not-at-top,import-outside-toplevel
  return {
      g.name: {e for e, r in (("pre", g.pre), ("stop", g.stop)) if r}
      for g in gates.GATES
  }


def _options(argv: List[str]) -> Dict[str, str]:
  pairs = zip(argv[::2], argv[1::2], strict=False)
  return {k.lstrip("-"): v for k, v in pairs if k.startswith("--")}


def command_errors(cmd: str, event: str, harness: str, script: str,
                   gates: Dict[str, Set[str]]) -> List[str]:
  """What is wrong with one hook command, or []."""
  word = EVENTS.get(event)
  argv = shlex.split(cmd)
  expected = ["python3", "-B", script, word]
  if word is None or argv[:4] != expected:
    return [f"runs {cmd!r}; expected {' '.join(expected[:3])} {word}"]
  if script.startswith("${") and f'"{script}"' not in cmd:
    return [f"{cmd!r}: quote {script.split('/')[0]} (install paths may hold"
            " spaces)"]
  opts = _options(argv[4:])
  errors = []
  if opts.get("harness") != harness:
    errors.append(f"{cmd!r}: --harness must be {harness}")
  only = [g for g in opts.get("only", "").split(",") if g]
  if harness != "antigravity" and only:
    errors.append(f"{cmd!r}: {harness} runs every gate; drop --only")
  if harness == "antigravity" and not only:
    errors.append(f"{cmd!r}: each Antigravity group names its --only gate")
  for gate in only:
    if word not in gates.get(gate, set()):
      errors.append(f"{cmd!r}: gate {gate!r} does not run on {event}")
  return errors


def check_hook_configs(errors: List[str], root: Path = ROOT) -> None:
  gates = _gates()
  covered: Set[Tuple[str, str]] = set()
  for rel, (harness, script) in HOOK_CONFIGS.items():
    try:
      cfg = json.loads((root / rel).read_text())
    except (OSError, ValueError) as exc:
      errors.append(f"{rel}: unreadable ({exc})")
      continue
    seen = []
    for event, cmd in config_commands(cfg, harness):
      seen.append(EVENTS.get(event, event))
      errors.extend(f"{rel}[{event}]: {e}"
                    for e in command_errors(cmd, event, harness, script, gates))
      if harness == "antigravity":
        only = _options(shlex.split(cmd)[4:]).get("only", "")
        covered.update((g, EVENTS.get(event, "")) for g in only.split(","))
    if harness != "antigravity" and sorted(seen) != ["pre", "stop"]:
      errors.append(f"{rel}: needs one PreToolUse and one Stop command, has"
                    f" {sorted(seen)}")
  for gate, events in sorted(gates.items()):
    for event in sorted(events - {e for g, e in covered if g == gate}):
      errors.append(f"hooks.json: no Antigravity group runs gate {gate!r} on"
                    f" {event}")


# --- check 10: manifests --------------------------------------------------------


def _load(root: Path, rel: str, errors: List[str]) -> Optional[Dict[str, Any]]:
  try:
    data = json.loads((root / rel).read_text())
  except (OSError, ValueError) as exc:
    errors.append(f"{rel}: unreadable ({exc})")
    return None
  return data if isinstance(data, dict) else None


def _path_errors(root: Path, rel: str, field: str, value: Any) -> List[str]:
  if not isinstance(value, str) or not value.startswith("./"):
    return [f"{rel}: {field} must be a ./relative path, got {value!r}"]
  if not (root / value).exists():
    return [f"{rel}: {field} {value!r} does not exist"]
  return []


def check_manifests(errors: List[str], root: Path = ROOT) -> None:
  claude = _load(root, ".claude-plugin/plugin.json", errors)
  codex = _load(root, ".codex-plugin/plugin.json", errors)
  market = _load(root, ".claude-plugin/marketplace.json", errors)
  if not (claude and codex and market):
    return
  entries = [p for p in market.get("plugins", []) if isinstance(p, dict)]
  entry = next((p for p in entries if p.get("name") == claude.get("name")), {})
  if entry.get("source") not in ("./", "."):
    errors.append("marketplace.json: the plugin's source must be ./ (this"
                  " repo)")
  names = {claude.get("name"), codex.get("name"), entry.get("name")}
  versions = {claude.get("version"), codex.get("version"),
              entry.get("version")}
  if len(names) != 1:
    errors.append(f"manifests disagree on the name: {sorted(map(str, names))}")
  if len(versions) != 1:
    errors.append(
        f"manifests disagree on the version: {sorted(map(str, versions))}")
  rel = ".codex-plugin/plugin.json"
  for field in ("hooks", "skills"):
    errors.extend(_path_errors(root, rel, field, codex.get(field)))
  logo = (codex.get("interface") or {}).get("logo")
  if logo is not None:
    errors.extend(_path_errors(root, rel, "interface.logo", logo))
  if claude.get("icon") is not None:
    errors.extend(_path_errors(root, ".claude-plugin/plugin.json", "icon",
                               claude["icon"]))


def plugin_option_keys(root: Path = ROOT) -> Set[str]:
  """The CLAUDE_PLUGIN_OPTION_<KEY> names hooks/pawl.py maps, read by AST.

  Importing the dispatcher would apply the options to this process, so
  the keys come from the first string of each PLUGIN_OPTIONS tuple.
  """
  module = ast.parse((root / "hooks/pawl.py").read_text())
  for node in module.body:
    targets = getattr(node, "targets", [])
    if not any(getattr(t, "id", "") == "PLUGIN_OPTIONS" for t in targets):
      continue
    rows = getattr(node.value, "elts", [])
    return {row.elts[0].value for row in rows
            if isinstance(row, ast.Tuple) and row.elts
            and isinstance(row.elts[0], ast.Constant)}
  return set()


def check_plugin_settings(errors: List[str], root: Path = ROOT) -> None:
  rel = ".claude-plugin/plugin.json"
  claude = _load(root, rel, errors)
  if claude is None:
    return
  declared = {str(k) for k in claude.get("userConfig") or {}}
  try:
    mapped = plugin_option_keys(root)
  except (OSError, SyntaxError) as exc:
    errors.append(f"hooks/pawl.py: unreadable ({exc})")
    return
  for key in sorted(declared, key=str.upper):
    if key.upper() not in mapped:
      errors.append(f"{rel}: userConfig {key} is not in hooks/pawl.py"
                    " PLUGIN_OPTIONS, so it changes nothing")
  for key in sorted(mapped - {k.upper() for k in declared}):
    errors.append(f"hooks/pawl.py: PLUGIN_OPTIONS reads {key}, which {rel}"
                  " userConfig does not declare")


# --- checks 11 and 12: CLAUDE.md guardrails -------------------------------------


def check_file_length(errors: List[str], root: Path = ROOT) -> None:
  for p in tree(root):
    rel = p.relative_to(root).as_posix()
    if not p.is_file() or p.suffix not in LENGTH_SUFFIXES:
      continue
    n = len(p.read_text(errors="replace").splitlines())
    if n > MAX_LINES:
      errors.append(f"{rel}: {n} lines, over {MAX_LINES}; split it")


def nesting(node: ast.AST, depth: int = 0) -> int:
  """Deepest block nesting under `node`; an elif chain counts once."""
  best = depth
  for field, value in ast.iter_fields(node):
    children = value if isinstance(value, list) else [value]
    elif_chain = (isinstance(node, ast.If) and field == "orelse"
                  and len(children) == 1 and isinstance(children[0], ast.If))
    for child in children:
      if not isinstance(child, ast.AST) or isinstance(child, _SCOPES):
        continue
      step = isinstance(child, _BLOCKS) and not elif_chain
      best = max(best, nesting(child, depth + step))
  return best


def check_nesting(errors: List[str], root: Path = ROOT) -> None:
  for p in tree(root, "*.py"):
    rel = p.relative_to(root).as_posix()
    try:
      module = ast.parse(p.read_text(), filename=rel)
    except SyntaxError as exc:
      errors.append(f"{rel}: does not parse ({exc.msg})")
      continue
    for fn in ast.walk(module):
      if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
        continue
      depth = nesting(fn)
      if depth > MAX_DEPTH:
        errors.append(f"{rel}:{fn.lineno}: {fn.name} nests {depth} blocks"
                      f" deep, over {MAX_DEPTH}; use guard clauses")
