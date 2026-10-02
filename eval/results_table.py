#!/usr/bin/env python3
"""results_table.py: pooled bucket table and per-case k/n for the pawl eval.

Reads one or more results.jsonl files written by run_arms.sh (one row per
run: pass, arm, case, rc) and prints two GitHub-flavored tables: pass rate per
bucket and arm, pooled across every file given (pass several seats to pool
them), and k/n per case. rc 0 is a pass, 1 a fail; any other rc is a grader
or setup error, shown as "+N err" and left out of the denominator.

  results_table.py results.jsonl [more.jsonl ...] [--eval EVAL.json]

Exit 0, or 2 when a results file is missing. Standard library only.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path  # pylint: disable=g-importing-member
import sys
from typing import Dict, List, Tuple

HERE = Path(__file__).resolve().parent
EVAL_JSON = HERE / "EVAL.json"
BUCKETS = ("code-change", "repo-hygiene", "outbound")
ARMS = ("on", "off")


def load(paths: List[Path]) -> List[Dict]:
  rows: List[Dict] = []
  for path in paths:
    for line in path.read_text().splitlines():
      if line.strip():
        rows.append(json.loads(line))
  return rows


def buckets_of(eval_path: Path = EVAL_JSON) -> Dict[str, str]:
  cases = json.loads(eval_path.read_text())["cases"]
  return {c["id"]: c["bucket"] for c in cases}


def _cell(tally: Tuple[int, int, int], pct: bool) -> str:
  passed, graded, errors = tally
  if not graded and not errors:
    return "-"
  text = f"{passed}/{graded}"
  if pct:
    text += f" ({round(100 * passed / graded)}%)" if graded else " (-)"
  return text + (f" +{errors} err" if errors else "")


def _tally(rows: List[Dict]) -> Tuple[int, int, int]:
  passed = sum(1 for r in rows if r["rc"] == 0)
  graded = sum(1 for r in rows if r["rc"] in (0, 1))
  return passed, graded, len(rows) - graded


def render(rows: List[Dict], eval_path: Path = EVAL_JSON) -> str:
  bucket = buckets_of(eval_path)
  groups: Dict[Tuple[str, str], List[Dict]] = defaultdict(list)
  cases: Dict[Tuple[str, str], List[Dict]] = defaultdict(list)
  for row in rows:
    groups[(bucket.get(row["case"], "unknown"), row["arm"])].append(row)
    groups[("pooled", row["arm"])].append(row)
    cases[(row["case"], row["arm"])].append(row)
  out = ["| bucket | on | off |", "|---|---|---|"]
  for name in (*BUCKETS, "pooled"):
    cells = [_cell(_tally(groups[(name, arm)]), True) for arm in ARMS]
    out.append(f"| {name} | {' | '.join(cells)} |")
  out += ["", "| case | bucket | on | off |", "|---|---|---|---|"]
  for case in sorted({r["case"] for r in rows}):
    cells = [_cell(_tally(cases[(case, arm)]), False) for arm in ARMS]
    out.append(f"| {case} | {bucket.get(case, 'unknown')} | "
               f"{' | '.join(cells)} |")
  return "\n".join(out) + "\n"


def main(argv: List[str]) -> int:
  parser = argparse.ArgumentParser(prog="results_table.py")
  parser.add_argument("results", nargs="+", type=Path)
  parser.add_argument("--eval", type=Path, default=EVAL_JSON)
  ns = parser.parse_args(argv)
  missing = [str(p) for p in ns.results if not p.is_file()]
  if missing:
    print(f"results_table: missing {', '.join(missing)}", file=sys.stderr)
    return 2
  sys.stdout.write(render(load(ns.results), ns.eval))
  return 0


if __name__ == "__main__":
  sys.exit(main(sys.argv[1:]))
