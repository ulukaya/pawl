#!/usr/bin/env python3
"""Runs every pawl test: hooks, each piece, the root tools, eval grader twins.

Requires pytest; exits 2 when pytest is missing, 1 on any test failure,
0 when all suites pass.
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
        ROOT / "test_install.py",
        ROOT / "eval" / "fixtures" / "test_validate.py",
    ]  # root tools and the grader twins: one file each, no recursion
)
SUITE_TIMEOUT = 45
HAVE_PYTEST = importlib.util.find_spec("pytest") is not None


def run_suite(d: Path) -> Tuple[bool, str]:
  cwd = d if d.is_dir() else d.parent
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
  if not HAVE_PYTEST:
    print(
        "run_tests: pytest is required to run the test suite; install pytest"
        " or run with a virtualenv (e.g. .venv/bin/python3 run_tests.py)",
        file=sys.stderr,
    )
    return 2
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

