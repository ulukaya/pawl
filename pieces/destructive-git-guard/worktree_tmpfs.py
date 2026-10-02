"""worktree_tmpfs.py: deny `git worktree add` onto tmpfs.

A working tree under /tmp, /dev/shm or /run disappears on reboot together with
every uncommitted change in it. The rule reads the destination, not the source
repo, so it applies with zero protected roots.

The typed path is matched against the literal prefixes and its realpath against
their realpaths (covers a symlinked /tmp). `~` and `$VAR` are expanded first; a
path that still starts with an unknown variable is allowed. Option values
(`-b`, `-B`, `--reason`) are skipped when locating the destination. Standard
library only.
"""

from __future__ import annotations

import os
from typing import List, Optional

TMPFS_PREFIXES = ("/tmp", "/dev/shm", "/run")
_VALUE_OPTS = frozenset({"-b", "-B", "--reason"})
DURABLE_HINT = (
    "Put the worktree somewhere durable, e.g. `git worktree add"
    " ~/worktrees/<name>` or a sibling of the repo."
)


def destination(args: List[str]) -> Optional[str]:
  """The path operand of `git worktree add`, or None for anything else.

  Args:
    args: the tokens after `worktree`.

  Returns:
    The first operand after `add` that is not an option or option value.
  """
  if not args or args[0] != "add":
    return None
  i = 1
  while i < len(args):
    tok = args[i]
    if tok == "--":
      return args[i + 1] if i + 1 < len(args) else None
    if tok in _VALUE_OPTS:
      i += 2
      continue
    if not tok.startswith("-"):
      return tok
    i += 1
  return None


def _inside(path: str, root: str) -> bool:
  return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def tmpfs_prefix(path: str, base: str) -> str:
  """The tmpfs prefix `path` lands under, or '' when it is durable.

  Args:
    path: the destination as typed.
    base: directory a relative path is resolved against.

  Returns:
    One of TMPFS_PREFIXES, or ''.
  """
  expanded = os.path.expandvars(os.path.expanduser(path))
  if expanded.startswith("$"):
    return ""  # unknown variable: the destination is not knowable here
  literal = os.path.normpath(os.path.join(base, expanded))
  real = os.path.realpath(literal)
  for prefix in TMPFS_PREFIXES:
    if _inside(literal, prefix) or _inside(real, os.path.realpath(prefix)):
      return prefix
  return ""


def reason(args: List[str], base: str) -> str:
  """Deny reason for `git worktree <args>` run from `base`, or ''."""
  dest = destination(args)
  if dest is None:
    return ""
  prefix = tmpfs_prefix(dest, base)
  if not prefix:
    return ""
  return (
      f"`git worktree add {dest}` puts the working tree on tmpfs ({prefix});"
      " it disappears on reboot together with every uncommitted change in"
      f" it. {DURABLE_HINT}"
  )
