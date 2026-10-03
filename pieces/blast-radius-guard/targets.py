"""targets.py: where a deletion lands, and how bad that is.

A target is judged by place, never by spelling:

  deny   `/`, a top-level system directory, the home directory or anything
         above it, a home folder that holds a life's work (.ssh, Documents,
         code, ...), a drive root, or every child of any of these.
  ask    the workspace root itself or all of it; anything else outside the
         workspace and the temp directories; a path that starts with a value
         pawl cannot resolve. An unresolved part that could be empty makes a
         deny an ask: the path might stop short of the danger.
  allow  inside the workspace (the git toplevel of the command's directory),
         or inside a temp directory.

Paths may carry UNKNOWN (an expansion pawl could not resolve) and globs.
Standard library only.
"""

from __future__ import annotations

import os
import posixpath
import re
import subprocess
from typing import Mapping, Optional, Tuple

ALLOW, ASK, DENY = 0, 1, 2
WORDS = {ALLOW: "allow", ASK: "ask", DENY: "deny"}
UNKNOWN = "\x01"
GLOB_RE = re.compile(r"[*?\[]")
DRIVE_ROOT_RE = re.compile(r"^[A-Za-z]:[\\/]*$")
SYSTEM_DIRS = frozenset({
    "/bin", "/boot", "/dev", "/etc", "/lib", "/lib32", "/lib64", "/libx32",
    "/opt", "/proc", "/root", "/run", "/sbin", "/srv", "/sys", "/usr",
    "/usr/bin", "/usr/lib", "/usr/local", "/usr/sbin", "/usr/share", "/var",
    "/var/lib", "/var/log", "/home", "/Users", "/System", "/Library",
    "/Applications", "/private", "/private/var", "/private/etc", "/Volumes",
    "/mnt", "/media", "/snap", "/nix", "/cores",
})
HOME_CRITICAL = frozenset({
    ".ssh", ".gnupg", ".aws", ".azure", ".config", ".kube", ".docker",
    ".local", ".claude", ".codex", ".gemini", "Documents", "Desktop",
    "Downloads", "Pictures", "Movies", "Music", "Library", "Photos",
    "OneDrive", "Dropbox", "src", "code", "projects", "work", "dev", "git",
    "repos", "workspace", "Developer",
})
EVERYTHING = frozenset({"*", ".*", "**", "*.*", ".[!.]*", "{*,.*}"})
TEMP_ROOTS = ("/tmp", "/var/tmp", "/private/tmp", "/private/var/folders",
              "/dev/shm")


def inside(path: str, root: str) -> bool:
  """True when `path` is strictly below `root`."""
  return root != path and (root == "/" or path.startswith(root + "/"))


class Policy:
  """The places a command's deletions are judged against."""

  def __init__(self, cwd: Optional[str], env: Mapping[str, str]) -> None:
    home = env.get("HOME") or os.path.expanduser("~")
    self.home = posixpath.normpath(home)
    self.cwd = cwd
    self._workspace: Optional[str] = None
    self._looked = False
    temps = list(TEMP_ROOTS)
    tmpdir = env.get("TMPDIR")
    if tmpdir and not self.dangerous(posixpath.normpath(tmpdir)):
      temps.append(posixpath.normpath(tmpdir))
    self.temps = tuple(temps)

  def dangerous(self, path: str) -> bool:
    if path == "/" or path in SYSTEM_DIRS or DRIVE_ROOT_RE.match(path):
      return True
    if path == self.home or inside(self.home, path):
      return True
    return (posixpath.dirname(path) == self.home
            and posixpath.basename(path) in HOME_CRITICAL)

  @property
  def workspace(self) -> Optional[str]:
    """The git toplevel of cwd, else cwd; never home, root or above."""
    if self._looked:
      return self._workspace
    self._looked = True
    if not self.cwd:
      return None
    root = self.cwd
    try:
      proc = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                            cwd=self.cwd, capture_output=True, text=True,
                            timeout=3, check=False)
      if proc.returncode == 0 and proc.stdout.strip():
        root = proc.stdout.strip()
    except (OSError, subprocess.SubprocessError):
      pass
    root = posixpath.normpath(root)
    self._workspace = None if self.dangerous(root) else root
    return self._workspace


def _split_unknown(path: str) -> Tuple[str, bool, bool]:
  """(base, children, uncertain): the part pawl can judge, and how."""
  uncertain = False
  if UNKNOWN in path:
    uncertain = True
    at = path.index(UNKNOWN)
    prefix, after = path[:at], path[at + 1:]
    if prefix.endswith("/") and not after.strip("/"):
      # "$HOME/$X": empty X deletes $HOME itself
      path = prefix.rstrip("/") or "/"
    else:  # "build-$X", "/tmp/$(whoami)-cache": one entry in that dir
      path = posixpath.join(posixpath.dirname(prefix) or ".", "<unresolved>")
  parts = (path.rstrip("/") or "/").split("/")
  glob_at = next((i for i, p in enumerate(parts) if GLOB_RE.search(p)), None)
  if glob_at is None:
    return path, False, uncertain
  base = "/".join(parts[:glob_at]) or ("/" if path.startswith("/") else ".")
  if all(p in EVERYTHING for p in parts[glob_at:]):
    return base, True, uncertain
  # `*.pyc`, `build-*`: some entries of base, judged as one of them
  return posixpath.join(base, "<pattern>"), False, uncertain


def _place(base: str, children: bool, policy: Policy) -> Tuple[int, str]:
  every = "every file in " if children else ""
  if policy.dangerous(base):
    if base == policy.home or inside(policy.home, base):
      return DENY, f"{every}your home directory ({base})"
    return DENY, f"{every}{base}, which holds your system or your work"
  ws = policy.workspace
  if ws and base == ws:
    return ASK, f"{every}the whole workspace ({ws})"
  if ws and inside(base, ws):
    return ALLOW, base
  if inside(base, policy.home):
    return ASK, f"{base}, outside this workspace, in your home directory"
  if base in policy.temps:
    return ASK, f"{every}the temp directory {base}"
  if any(inside(base, temp) for temp in policy.temps):
    return ALLOW, base
  return ASK, f"{base}, outside this workspace"


def classify(path: str, cwd: Optional[str], policy: Policy) -> Tuple[int, str]:
  """(level, what would be deleted) for one deletion target."""
  if path.startswith(UNKNOWN) or not path:
    return ASK, ("a path that starts with a value pawl cannot resolve;"
                 " empty, it would delete from /")
  if DRIVE_ROOT_RE.match(path.rstrip("*")):
    return DENY, f"the drive {path}"
  base, children, uncertain = _split_unknown(path)
  if not base.startswith("/"):
    if cwd is None:
      return (ASK, "a relative path from a directory pawl cannot resolve") \
          if children or base in (".", "..") else (ALLOW, base)
    base = posixpath.join(cwd, base)
  base = posixpath.normpath(base)
  if base.startswith("//"):  # POSIX leaves `//` to the system; Linux is `/`
    base = "/" + base.lstrip("/")
  level, what = _place(base, children, policy)
  if uncertain and level == DENY:
    return ASK, f"possibly {what} (part of the path is unresolved)"
  return level, what
