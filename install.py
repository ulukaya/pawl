#!/usr/bin/env python3
"""install.py: install pawl into Antigravity, Claude Code and Codex, or remove it.

    ./install.sh [--antigravity] [--claude] [--codex] [--uninstall]
                 [--source SOURCE] [--force] [--no-verify] [--dry-run]

With no harness flag, every harness found on this machine is a target:
Antigravity when ~/.gemini exists, Claude Code and Codex when their CLI is
on PATH.

    antigravity  symlink this checkout to <config>/plugins/pawl and add
                 {"path": "plugins/pawl"} to <config>/plugins.json
    claude       claude plugin marketplace add SOURCE
                 claude plugin install pawl@pawl
    codex        codex plugin marketplace add SOURCE
                 codex plugin add pawl@pawl

SOURCE defaults to this checkout (the plugin then loads in place, so edits
apply at the next session); pass `--source ulukaya/pawl` to track GitHub.
Every step is idempotent, and --uninstall reverses each one. After an
install, run_tests.py verifies the checkout unless --no-verify is given.

Environment:
    PAWL_PLUGIN_CONFIG_DIR   Antigravity config dir (default ~/.gemini/config)
    PAWL_CLAUDE_BIN          the claude CLI (default: claude on PATH)
    PAWL_CODEX_BIN           the codex CLI (default: codex on PATH)

Exit codes: 0 done; 1 a conflict was refused or the tests failed; 2 a
requested harness CLI, pytest, or a readable plugins.json is missing.
Standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path  # pylint: disable=g-importing-member
import shutil
import subprocess
import sys
import time
from typing import Dict, List, Optional, Sequence

REPO = Path(__file__).resolve().parent
PLUGIN, MARKETPLACE = "pawl", "pawl"
ENTRY = {"path": "plugins/pawl"}
CLI_TIMEOUT_S = 300
CLI: Dict[str, Dict[str, object]] = {
    "claude": {
        "env": "PAWL_CLAUDE_BIN",
        "install": [["plugin", "marketplace", "add", "{source}"],
                    ["plugin", "install", f"{PLUGIN}@{MARKETPLACE}"]],
        "uninstall": [["plugin", "uninstall", f"{PLUGIN}@{MARKETPLACE}"],
                      ["plugin", "marketplace", "remove", MARKETPLACE]],
    },
    "codex": {
        "env": "PAWL_CODEX_BIN",
        "install": [["plugin", "marketplace", "add", "{source}"],
                    ["plugin", "add", f"{PLUGIN}@{MARKETPLACE}"]],
        "uninstall": [["plugin", "remove", f"{PLUGIN}@{MARKETPLACE}"],
                      ["plugin", "marketplace", "remove", MARKETPLACE]],
    },
}
HARNESSES = ("antigravity", "claude", "codex")


class InstallError(Exception):
  """A step that cannot proceed; `code` is the process exit code."""

  def __init__(self, message: str, code: int) -> None:
    super().__init__(message)
    self.code = code


def config_dir() -> Path:
  raw = os.environ.get("PAWL_PLUGIN_CONFIG_DIR")
  return Path(raw) if raw else Path.home() / ".gemini" / "config"


def cli_bin(harness: str) -> Optional[str]:
  return os.environ.get(str(CLI[harness]["env"])) or shutil.which(harness)


def detect() -> List[str]:
  """The harnesses present on this machine."""
  found = []
  if config_dir().parent.is_dir():
    found.append("antigravity")
  found += [h for h in ("claude", "codex") if cli_bin(h)]
  return found


# --- antigravity: symlink and registry ----------------------------------------


def link(dest: Path, force: bool, dry: bool) -> str:
  """Points `dest` at this checkout; refuses to replace anything else."""
  if dest.is_symlink() and dest.resolve() == REPO:
    return f"link: {dest} -> {REPO} (already in place)"
  if (dest.is_symlink() or dest.exists()) and not force:
    raise InstallError(
        f"{dest} exists and is not a link to {REPO}; rerun with --force to"
        " move it aside", 1)
  if dry:
    return f"link: would point {dest} at {REPO}"
  dest.parent.mkdir(parents=True, exist_ok=True)
  if dest.is_symlink() or dest.exists():
    backup = dest.with_name(f"{dest.name}.bak.{time.strftime('%Y%m%d-%H%M%S')}")
    dest.rename(backup)
  dest.symlink_to(REPO)
  return f"link: {dest} -> {REPO}"


def unlink(dest: Path, dry: bool) -> str:
  if not (dest.is_symlink() and dest.resolve() == REPO):
    return f"link: {dest} does not point here; left alone"
  if not dry:
    dest.unlink()
  return f"link: removed {dest}"


def load_registry(path: Path) -> Dict[str, object]:
  if not path.exists() or not path.read_text(encoding="utf-8").strip():
    return {"entries": []}
  try:
    data = json.loads(path.read_text(encoding="utf-8"))
  except ValueError as exc:
    raise InstallError(f"{path} is not valid JSON ({exc}); fix it and rerun",
                       2) from exc
  if not isinstance(data, dict) or not isinstance(data.get("entries", []),
                                                  list):
    raise InstallError(f'{path} has no "entries" list; fix it and rerun', 2)
  data.setdefault("entries", [])
  return data


def save_registry(path: Path, data: Dict[str, object]) -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
  tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
  os.replace(tmp, path)


def register(path: Path, dry: bool) -> str:
  data = load_registry(path)
  entries = data["entries"]
  if ENTRY in entries:
    return f"registry: {ENTRY['path']} already in {path}"
  if not dry:
    save_registry(path, dict(data, entries=entries + [ENTRY]))
  return f"registry: added {ENTRY['path']} to {path}"


def unregister(path: Path, dry: bool) -> str:
  data = load_registry(path)
  kept = [e for e in data["entries"] if e != ENTRY]
  if len(kept) == len(data["entries"]):
    return f"registry: {ENTRY['path']} not in {path}"
  if not dry:
    save_registry(path, dict(data, entries=kept))
  return f"registry: removed {ENTRY['path']} from {path}"


def antigravity(uninstall: bool, force: bool, dry: bool) -> List[str]:
  dest, registry = config_dir() / "plugins" / PLUGIN, config_dir() / \
      "plugins.json"
  if uninstall:
    return [unregister(registry, dry), unlink(dest, dry)]
  return [link(dest, force, dry), register(registry, dry)]


# --- claude and codex: their own plugin CLIs ------------------------------------


def run_cli(binary: str, args: List[str], uninstall: bool) -> str:
  proc = subprocess.run([binary] + args, capture_output=True, text=True,
                        timeout=CLI_TIMEOUT_S, check=False)
  out = (proc.stdout + proc.stderr).strip()
  if proc.returncode == 0 or (uninstall and "not found" in out.lower()):
    return out.splitlines()[-1] if out else "ok"
  raise InstallError(f"`{' '.join([binary] + args)}` failed: {out}", 1)


def plugin_cli(harness: str, source: str, uninstall: bool,
               dry: bool) -> List[str]:
  binary = cli_bin(harness)
  steps = CLI[harness]["uninstall" if uninstall else "install"]
  argvs = [[a.format(source=source) for a in step] for step in steps]
  if not binary:
    shown = "; ".join(f"{harness} {' '.join(a)}" for a in argvs)
    raise InstallError(f"{harness}: CLI not found; run by hand: {shown}", 2)
  if dry:
    return [f"would run: {binary} {' '.join(a)}" for a in argvs]
  return [run_cli(binary, a, uninstall) for a in argvs]


# --- main -----------------------------------------------------------------------


def verify() -> int:
  if subprocess.run([sys.executable, "-c", "import pytest"],
                    capture_output=True, check=False).returncode:
    print(f"install: done, but {sys.executable} has no pytest, so the tests"
          " cannot run; install pytest or set PAWL_PYTHON, then run"
          f" {sys.executable} -B {REPO / 'run_tests.py'}", file=sys.stderr)
    return 2
  print(f"verify: {sys.executable} -B {REPO / 'run_tests.py'}")
  code = subprocess.run([sys.executable, "-B", str(REPO / "run_tests.py")],
                        check=False).returncode
  if code:
    print("install: tests failed; pawl is installed but not verified",
          file=sys.stderr)
  return 1 if code else 0


def parser() -> argparse.ArgumentParser:
  ap = argparse.ArgumentParser(
      prog="install.sh", description="Install pawl into agent harnesses.")
  for name in HARNESSES:
    ap.add_argument(f"--{name}", action="store_true",
                    help=f"target {name} (default: every harness found)")
  ap.add_argument("--all", action="store_true",
                  help="target every harness found (the default)")
  ap.add_argument("--uninstall", action="store_true")
  ap.add_argument("--source", default=str(REPO),
                  help="marketplace source for claude and codex (default:"
                  " this checkout; e.g. ulukaya/pawl)")
  ap.add_argument("--force", action="store_true",
                  help="move aside a conflicting Antigravity plugin dir")
  ap.add_argument("--no-verify", action="store_true",
                  help="skip run_tests.py after installing")
  ap.add_argument("--dry-run", action="store_true",
                  help="print what would change, change nothing")
  return ap


def main(argv: Optional[Sequence[str]] = None) -> int:
  args = parser().parse_args(argv)
  targets = [h for h in HARNESSES if getattr(args, h)] or detect()
  if not targets:
    print("install: no harness found (no ~/.gemini, no claude or codex on"
          " PATH); name one with --antigravity, --claude or --codex",
          file=sys.stderr)
    return 2
  try:
    for harness in targets:
      print(f"==> {harness}")
      if harness == "antigravity":
        lines = antigravity(args.uninstall, args.force, args.dry_run)
      else:
        lines = plugin_cli(harness, args.source, args.uninstall, args.dry_run)
      print("\n".join(lines))
  except InstallError as exc:
    print(f"install: {exc}", file=sys.stderr)
    return exc.code
  if args.uninstall or args.dry_run or args.no_verify:
    return 0
  return verify()


if __name__ == "__main__":
  sys.exit(main())
