#!/usr/bin/env python3
"""git_parse.py: parsing and target resolution for git invocations."""

from __future__ import annotations

import os
from pathlib import Path  # pylint: disable=g-importing-member
import shlex
import subprocess
from typing import List, Optional, Tuple

ROOTS_ENV = "PAWL_GIT_PROTECTED_ROOTS"
TOPLEVEL_TIMEOUT_S = 3.0

_GLOBAL_VALUE_OPTS = frozenset({
    "-C",
    "-c",
    "--git-dir",
    "--work-tree",
    "--namespace",
    "--exec-path",
    "--super-prefix",
    "--config-env",
})
_SEPARATORS = (";", "&&", "||", "|", "\n")
_PREFIX_CMDS = frozenset(
    {"env", "sudo", "nohup", "command", "timeout", "nice", "time"}
)


def _resolve(path: str, cwd: str) -> str:
  """Absolute realpath of `path` relative to `cwd`, with ~ expanded."""
  expanded = os.path.expanduser(path)
  if not os.path.isabs(expanded):
    expanded = os.path.join(cwd, expanded)
  return os.path.realpath(expanded)


def _inside(resolved: str, root: str) -> bool:
  return resolved == root or resolved.startswith(root + os.sep)


def git_toplevel(cwd: str) -> str:
  """Realpath of the repo containing `cwd`, or '' outside a git work tree."""
  if not os.path.isdir(cwd):
    return ""
  try:
    proc = subprocess.run(
        ["git", "-C", cwd, "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        timeout=TOPLEVEL_TIMEOUT_S,
        check=False,
    )
  except (OSError, subprocess.SubprocessError):
    return ""
  top = proc.stdout.strip()
  return os.path.realpath(top) if proc.returncode == 0 and top else ""


def protected_roots(cwd: str) -> List[str]:
  """Realpaths this guard protects.

  Args:
    cwd: directory the command runs in.

  Returns:
    PAWL_GIT_PROTECTED_ROOTS when set, else the toplevel of `cwd`.
  """
  raw = os.environ.get(ROOTS_ENV)
  if raw is not None:
    return [_resolve(p, cwd) for p in raw.split(":") if p.strip()]
  top = git_toplevel(cwd)
  return [top] if top else []


def _split_segments(command: str) -> List[str]:
  segments = [command]
  for sep in _SEPARATORS:
    nxt: List[str] = []
    for seg in segments:
      nxt.extend(seg.split(sep))
    segments = nxt
  return segments


def _tokenize(segment: str) -> List[str]:
  try:
    return shlex.split(segment)
  except ValueError:
    return segment.split()


def _git_index(tokens: List[str]) -> int:
  """Index of the `git` token, skipping common prefixes (env, sudo, timeout)."""
  for i, tok in enumerate(tokens):
    if os.path.basename(tok) == "git":
      return i
    if "=" in tok and not tok.startswith("-"):
      continue
    if tok in _PREFIX_CMDS:
      continue
    if tok.startswith("-") or tok.rstrip("smh").isdigit():
      continue
    return -1
  return -1


def _git_dir_to_root(path: str) -> str:
  return os.path.dirname(path) if os.path.basename(path) == ".git" else path


def _parse_git(
    tokens: List[str], start: int, cwd: str
) -> Tuple[Optional[str], List[str], Optional[str]]:
  """Return (subcommand, remaining args, explicit target dir) for a git call."""
  target: Optional[str] = None
  i = start + 1
  n = len(tokens)
  while i < n:
    tok = tokens[i]
    if tok == "-C" and i + 1 < n:
      target = _resolve(tokens[i + 1], target or cwd)
      i += 2
    elif tok.startswith("--git-dir=") or tok.startswith("--work-tree="):
      target = _git_dir_to_root(_resolve(tok.split("=", 1)[1], cwd))
      i += 1
    elif tok in {"--git-dir", "--work-tree"} and i + 1 < n:
      target = _git_dir_to_root(_resolve(tokens[i + 1], cwd))
      i += 2
    elif tok in _GLOBAL_VALUE_OPTS:
      i += 2
    elif tok.startswith("-"):
      i += 1
    else:
      return tok, tokens[i + 1 :], target
  return None, [], target


def _short_flags(flags: List[str]) -> str:
  """Letters from clustered short options: ['-fdx', '--force'] -> 'fdx'."""
  return "".join(
      f[1:] for f in flags if f.startswith("-") and not f.startswith("--")
  )


def _targets_root(
    args: List[str], cwd: str, explicit: Optional[str], root: str
) -> bool:
  if explicit is not None:
    return _inside(explicit, root)
  if _inside(os.path.realpath(cwd), root):
    return True
  return any(
      _inside(_resolve(a, cwd), root) for a in args if not a.startswith("-")
  )


def _effective_cwd(segments: List[str], up_to: int, cwd: str) -> str:
  current = cwd
  for seg in segments[:up_to]:
    tokens = _tokenize(seg)
    if len(tokens) == 2 and tokens[0] == "cd":
      current = _resolve(tokens[1], current)
  return current
