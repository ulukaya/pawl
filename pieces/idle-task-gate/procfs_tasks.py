"""procfs_tasks.py: background task roots of one conversation, from /proc.

A task root is a pid whose own environ carries the conversation id and whose
parent's does not. Age comes from /proc/<pid>/stat field 22 (starttime)
against /proc/uptime. Linux only; anywhere else the root dir is missing and
the scan finds nothing. Standard library only.
"""

from __future__ import annotations

import os
from pathlib import Path  # pylint: disable=g-importing-member
import re
from typing import Any, Dict, List, Optional

CONV_ENV = "ANTIGRAVITY_CONVERSATION_ID"


def _read(path: Path) -> bytes:
  try:
    return path.read_bytes()
  except OSError:
    return b""


def _conv_of(proc: Path, pid: str) -> str:
  m = re.search(
      rb"(?:^|\0)" + CONV_ENV.encode() + rb"=([^\0]*)",
      _read(proc / pid / "environ"),
  )
  return m.group(1).decode("utf-8", "replace") if m else ""


def _stat_fields(proc: Path, pid: str) -> List[str]:
  """Fields after `(comm)` in /proc/<pid>/stat: 1 ppid, 2 pgid, 19 start."""
  return (
      _read(proc / pid / "stat")
      .decode("utf-8", "replace")
      .rsplit(")", 1)[-1]
      .split()
  )


def _age_s(proc: Path, pid: str, uptime_s: float, hz: float) -> float:
  fields = _stat_fields(proc, pid)
  try:
    return uptime_s - int(fields[19]) / hz
  except (IndexError, ValueError):
    return 0.0


def _ppid(proc: Path, pid: str) -> str:
  fields = _stat_fields(proc, pid)
  return fields[1] if len(fields) > 1 else ""


def _pgid(proc: Path, pid: str) -> int:
  fields = _stat_fields(proc, pid)
  try:
    return int(fields[2])
  except (IndexError, ValueError):
    return 0


def _uptime_s(proc: Path) -> float:
  try:
    return float(_read(proc / "uptime").split()[0])
  except (IndexError, ValueError):
    return 0.0


def _clock_ticks() -> float:
  try:
    return float(os.sysconf("SC_CLK_TCK"))
  except (ValueError, OSError, AttributeError):
    return 100.0


def task_roots(
    conv: str,
    proc: Path,
    limit_s: float,
    allow_re: Optional["re.Pattern[str]"] = None,
    skip_mark: str = "",
) -> List[Dict[str, Any]]:
  """Task roots owned by `conv` older than `limit_s`.

  Args:
    conv: the conversation id their environ must carry.
    proc: the procfs root.
    limit_s: roots at or under this age are skipped.
    allow_re: command lines it matches are skipped.
    skip_mark: command lines containing it are skipped (the gate itself).

  Returns:
    [{pid, pgid, age, cmd}] sorted by pid; [] when `proc` is not a dir.
  """
  if not proc.is_dir():
    return []
  uptime, hz = _uptime_s(proc), _clock_ticks()
  out: List[Dict[str, Any]] = []
  for pid in (p.name for p in proc.iterdir() if p.name.isdigit()):
    if pid == str(os.getpid()) or _conv_of(proc, pid) != conv:
      continue
    cmd = (
        _read(proc / pid / "cmdline")
        .replace(b"\0", b" ")
        .decode("utf-8", "replace")
        .strip()
    )
    if (skip_mark and skip_mark in cmd) or _conv_of(
        proc, _ppid(proc, pid)) == conv:
      continue
    age = _age_s(proc, pid, uptime, hz)
    if age <= limit_s or (allow_re and allow_re.search(cmd)):
      continue
    out.append(
        {"pid": int(pid), "pgid": _pgid(proc, pid), "age": int(age), "cmd": cmd}
    )
  return sorted(out, key=lambda r: r["pid"])
