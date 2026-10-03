"""winpath.py: judge a Windows path a delete command would remove.

Agents on Windows pass `C:\\Users\\<you>\\...`, and on Git Bash the MSYS form
`/c/Users/<you>/...`. Both name real places a deletion should be judged by,
the same way targets.py judges POSIX paths:

  deny   a drive root, a Windows system folder (`C:\\Windows`, `Program Files`,
         `ProgramData`), the `Users` root, a user profile, or a life's-work
         folder in it (`Documents`, `.ssh`, `AppData`, ...)
  ask    the user temp directory itself, or anything else outside the workspace
  allow  inside the user temp directory (`AppData\\Local\\Temp\\...`)

`..` is resolved first, so escaping temp back into the profile is judged where
it lands. Standard library only.
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

ALLOW, ASK, DENY = 0, 1, 2

_DRIVE_RE = re.compile(r"^([A-Za-z]):[\\/](.*)$", re.S)
_MSYS_RE = re.compile(r"^/([A-Za-z])/(.*)$", re.S)
# A raw command keeps the backslashes a shell would otherwise eat.
PATH_RE = re.compile(
    r"(?i)(?:[a-z]:[\\/][^\s\"';|&]*"
    r"|/[a-z]/(?:users|windows|program ?(?:files|data)?|progra~1)"
    r"[^\s\"';|&]*)")
_SYSTEM_TOPS = frozenset({"windows", "winnt", "program files",
                          "program files (x86)", "programdata", "progra~1",
                          "$recycle.bin", "system volume information"})
_PROFILE_CRITICAL = frozenset({"documents", "desktop", "downloads", "pictures",
                               "videos", "music", "onedrive", "dropbox",
                               ".ssh", ".aws", ".config", "source", "repos",
                               "appdata"})
_TEMP_TAIL = ("appdata", "local", "temp")


def _norm(path: str) -> Optional[Tuple[str, List[str]]]:
  """(drive, segments) lowercased with `..` resolved, or None if not Windows."""
  m = _DRIVE_RE.match(path)
  if m:
    drive, rest = m.group(1).lower(), m.group(2)
  else:
    m = _MSYS_RE.match(path)
    if not m:
      return None
    drive, rest = m.group(1).lower(), m.group(2)
  parts: List[str] = []
  for seg in rest.replace("\\", "/").split("/"):
    if seg in ("", "."):
      continue
    if seg == "..":
      if parts:
        parts.pop()
    else:
      parts.append(seg.lower())
  return drive, parts


def _show(drive: str, parts: List[str]) -> str:
  sep = "\\"
  return drive.upper() + ":" + sep + sep.join(parts)


def classify(path: str) -> Optional[Tuple[int, str]]:
  """(level, what) for a Windows path, or None when `path` is not one."""
  norm = _norm(path)
  if norm is None:
    return None
  drive, parts = norm
  if not parts or all(re.fullmatch(r"\*+(?:\.\*)?", p) for p in parts):
    return DENY, f"every file on the drive {drive.upper()}:"
  shown = _show(drive, parts)
  top = parts[0]
  if top in _SYSTEM_TOPS:
    return DENY, f"{_show(drive, parts[:1])}, a Windows system folder"
  if top != "users":
    return ASK, f"{shown}, outside this workspace"
  if len(parts) <= 2:  # the Users root, or one user's whole profile
    return DENY, f"{shown}, a Windows user profile"
  tail = parts[2:]
  if tuple(tail[:3]) == _TEMP_TAIL:
    if len(tail) == 3:
      return ASK, "the whole Windows temp directory"
    return ALLOW, shown
  if tail[0] in _PROFILE_CRITICAL:
    return DENY, f"{shown}, which holds your work"
  return ASK, f"{shown}, outside this workspace"
