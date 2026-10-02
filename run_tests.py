#!/usr/bin/env python3
"""Runs every pawl test: 17 piece suites, hooks, root gate, grader twins.

Exit 1 on any failure.

Uses pytest when it is installed (two piece suites are plain test functions),
otherwise falls back to unittest discovery and treats "NO TESTS RAN" as a
failure.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path  # pylint: disable=g-importing-member
import subprocess
import sys
from typing import Tuple

ROOT = Path(__file__).resolve().parent
SUITES = (
    [ROOT / "hooks"]
    + sorted(p for p in (ROOT / "pieces").iterdir() if p.is_dir())
    + [
        ROOT / "test_check_portable.py",
    ]  # root gate suite and the grader twins: one file each, no recursion
)
SUITE_TIMEOUT = 45
HAVE_PYTEST = importlib.util.find_spec("pytest") is not None


def run_suite(d: Path) -> Tuple[bool, str]:
  cwd = d if d.is_dir() else d.parent
  if HAVE_PYTEST:
    cmd = [
        sys.executable,
        "-B",
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        str(d),
    ]
  elif d.is_dir():
    cmd = [
        sys.executable,
        "-B",
        "-m",
        "unittest",
        "discover",
        "-s",
        str(d),
        "-p",
        "*test*.py",
        "-t",
        str(d),
    ]
  else:
    cmd = [sys.executable, "-B", "-m", "unittest", d.stem]
  proc = subprocess.run(
      cmd,
      capture_output=True,
      text=True,
      timeout=SUITE_TIMEOUT,
      cwd=str(cwd),
      check=False,
  )
  out = (proc.stdout + proc.stderr).strip()
  tail = out.splitlines()[-1] if out else ""
  ok = (
      proc.returncode == 0
      and "NO TESTS RAN" not in out
      and "no tests ran" not in out
  )
  return ok, tail


def main() -> int:
  failed = []
  for d in SUITES:
    ok, tail = run_suite(d)
    print(f"{'OK  ' if ok else 'FAIL'} {d.stem:18} {tail}")
    if not ok:
      failed.append(d.name)
  if failed:
    print(f"failed: {', '.join(failed)}")
    return 1
  return 0


if __name__ == "__main__":
  sys.exit(main())
