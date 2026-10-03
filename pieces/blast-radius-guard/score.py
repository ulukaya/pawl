#!/usr/bin/env python3
"""score.py: run the blast-radius guard over corpus.json and report.

Each case runs in its own sandbox: a fake HOME, a git repo at HOME/proj as
the workspace (the case's files are written there), and a TMPDIR beside
HOME. The command runs from HOME/proj/<case cwd>.

    score.py [--corpus FILE] [--verbose] [--json]

Metrics:
    caught         expected deny or ask, and the guard denied or asked
    exact          the guard's decision equals the expected one
    false alarms   expected allow, and the guard denied or asked
    weak           expected deny, got ask (caught, but softer)

Exit 0 when the corpus ran, 2 when it could not be read. Standard library
only.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path  # pylint: disable=g-importing-member
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, NamedTuple

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import blast_radius  # noqa: E402  # pylint: disable=g-import-not-at-top

LEVEL = {"allow": 0, "ask": 1, "deny": 2}


class Result(NamedTuple):
  id: str
  expect: str
  got: str
  reason: str


class Score(NamedTuple):
  total: int
  dangerous: int
  caught: int
  exact: int
  benign: int
  false_alarms: int
  weak: int

  def line(self) -> str:
    pct = 100.0 * self.caught / self.dangerous if self.dangerous else 0.0
    return (f"caught {self.caught}/{self.dangerous} ({pct:.1f}%)  false"
            f" alarms {self.false_alarms}/{self.benign}  exact"
            f" {self.exact}/{self.total}  weak {self.weak}")


def load(path: Path) -> List[Dict[str, Any]]:
  data = json.loads(path.read_text(encoding="utf-8"))
  cases = data.get("cases") if isinstance(data, dict) else None
  if not isinstance(cases, list):
    raise ValueError(f"{path}: no 'cases' list")
  return cases


def sandbox(root: Path, case: Dict[str, Any]) -> Dict[str, str]:
  """Builds HOME, the workspace repo and TMPDIR; returns the env to use."""
  home = root / "home"
  work = home / "proj"
  tmpdir = root / "tmpdir"
  for d in (work, tmpdir):
    d.mkdir(parents=True)
  subprocess.run(["git", "init", "-q", str(work)], check=True)
  for rel, text in case.get("files", {}).items():
    target = work / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
  for rel in case.get("exec", []):
    os.chmod(work / rel, 0o755)
  return {"HOME": str(home), "TMPDIR": str(tmpdir), "PATH": os.defpath}


def run_case(case: Dict[str, Any]) -> Result:
  root = Path(tempfile.mkdtemp(prefix="pawl-blast-"))
  try:
    env = sandbox(root, case)
    cwd = str(Path(env["HOME"]) / "proj" / case.get("cwd", "."))
    verdict = blast_radius.assess(case["cmd"], cwd, env)
    return Result(case["id"], case["expect"], verdict.decision,
                  verdict.reason)
  finally:
    shutil.rmtree(root, ignore_errors=True)


def score(results: List[Result]) -> Score:
  dangerous = [r for r in results if r.expect != "allow"]
  benign = [r for r in results if r.expect == "allow"]
  return Score(
      total=len(results),
      dangerous=len(dangerous),
      caught=sum(1 for r in dangerous if r.got != "allow"),
      exact=sum(1 for r in results if r.got == r.expect),
      benign=len(benign),
      false_alarms=sum(1 for r in benign if r.got != "allow"),
      weak=sum(1 for r in results if r.expect == "deny" and r.got == "ask"),
  )


def main(argv: List[str] | None = None) -> int:
  ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
  ap.add_argument("--corpus", default=str(HERE / "corpus.json"))
  ap.add_argument("--verbose", action="store_true",
                  help="list every case whose decision differs")
  ap.add_argument("--json", action="store_true")
  args = ap.parse_args(argv)
  try:
    cases = load(Path(args.corpus))
  except (OSError, ValueError) as exc:
    print(f"score: cannot read the corpus: {exc}", file=sys.stderr)
    return 2
  results = [run_case(c) for c in cases]
  summary = score(results)
  if args.json:
    print(json.dumps({"score": summary._asdict(),
                      "results": [r._asdict() for r in results]}, indent=1))
    return 0
  for r in results:
    if r.got != r.expect and (args.verbose or LEVEL[r.got] < LEVEL[r.expect]
                              or r.expect == "allow"):
      print(f"{r.id:<32} expect {r.expect:<5} got {r.got:<5} {r.reason[:90]}")
  print(summary.line())
  return 0


if __name__ == "__main__":
  sys.exit(main())
