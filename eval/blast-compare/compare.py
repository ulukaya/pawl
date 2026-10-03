#!/usr/bin/env python3
"""compare.py: run deletion guards on the blast-radius corpora, side by side.

Each guard is a command that reads a Claude Code PreToolUse payload (Bash
tool) on stdin and answers the way Claude Code reads hooks: a JSON
`permissionDecision` of deny or ask, exit code 2 (a block), or nothing
(allow). Every case runs in score.py's sandbox (fake HOME, a git repo at
HOME/proj, TMPDIR beside it, the case's files written).

    compare.py --guard 'pawl=python3 -B hooks/pawl.py pre --only blast
                        --harness claude' --guard 'other=/path/to/hook'

Prints one markdown row per guard and corpus: caught (deny or ask on a
dangerous case), false alarms (deny or ask on an everyday case), and the
ids each guard got wrong with --verbose. Labels are pawl's policy: temp
directories are fair game, so a guard that blocks every deletion outside
the current directory pays for it in false alarms.

Standard library only; the guards are whatever you install.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path  # pylint: disable=g-importing-member
import shlex
import shutil
import subprocess
import sys
import tempfile
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[2]
PIECE = ROOT / "pieces" / "blast-radius-guard"
sys.path.insert(0, str(PIECE))

import score  # noqa: E402  pylint: disable=g-import-not-at-top

CORPORA = ("corpus.json", "holdout.json", "holdout2.json", "holdout3.json",
           "holdout4.json")


def verdict(proc: subprocess.CompletedProcess) -> str:
  if proc.returncode == 2:
    return "deny"
  text = proc.stdout.strip()
  if not text:
    return "allow"
  try:
    data = json.loads(text.splitlines()[-1])
  except ValueError:
    return "allow"
  spec = data.get("hookSpecificOutput") or {}
  decision = spec.get("permissionDecision") or data.get("decision") or ""
  return {"deny": "deny", "block": "deny", "ask": "ask"}.get(decision,
                                                             "allow")


def run_guard(command: str, case: Dict) -> str:
  root = Path(tempfile.mkdtemp(prefix="pawl-compare-"))
  try:
    env = score.sandbox(root, case)
    env["PATH"] = os.environ.get("PATH", os.defpath)
    cwd = Path(env["HOME"]) / "proj" / case.get("cwd", ".")
    payload = {"session_id": "compare", "cwd": str(cwd),
               "transcript_path": str(root / "t.jsonl"),
               "hook_event_name": "PreToolUse", "tool_name": "Bash",
               "tool_input": {"command": case["cmd"]}}
    proc = subprocess.run(shlex.split(command), input=json.dumps(payload),
                          capture_output=True, text=True, env=env,
                          cwd=str(cwd), timeout=30, check=False)
    return verdict(proc)
  except subprocess.TimeoutExpired:
    return "allow"
  finally:
    shutil.rmtree(root, ignore_errors=True)


def tally(command: str, cases: List[Dict]) -> Tuple[int, int, int, int, List]:
  caught = alarms = dangerous = benign = 0
  wrong = []
  for case in cases:
    got = run_guard(command, case)
    if case["expect"] == "allow":
      benign += 1
      alarms += got != "allow"
    else:
      dangerous += 1
      caught += got != "allow"
    if (got == "allow") != (case["expect"] == "allow"):
      wrong.append(case["id"])
  return caught, dangerous, alarms, benign, wrong


def main(argv: List[str] | None = None) -> int:
  ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
  ap.add_argument("--guard", action="append", default=[],
                  help="NAME=COMMAND reading a Claude Code hook payload")
  ap.add_argument("--verbose", action="store_true")
  args = ap.parse_args(argv)
  guards = [g.split("=", 1) for g in args.guard if "=" in g]
  if not guards:
    print("compare: give at least one --guard NAME=COMMAND", file=sys.stderr)
    return 2
  print("| Guard | Set | Caught | False alarms |")
  print("| --- | --- | --- | --- |")
  for name, command in guards:
    total = [0, 0, 0, 0]
    for corpus in CORPORA:
      caught, dangerous, alarms, benign, wrong = tally(
          command, score.load(PIECE / corpus))
      total = [a + b for a, b in zip(total, (caught, dangerous, alarms,
                                              benign), strict=True)]
      print(f"| {name} | {corpus} | {caught}/{dangerous} | {alarms}/{benign} |")
      if args.verbose and wrong:
        print(f"<!-- {name} {corpus} wrong: {' '.join(wrong)} -->")
    print(f"| {name} | **all** | **{total[0]}/{total[1]}** |"
          f" **{total[2]}/{total[3]}** |")
  return 0


if __name__ == "__main__":
  sys.exit(main())
