#!/usr/bin/env python3
"""report.py: count denial rows per gate from the pawl denial log.

Reads PAWL_DATA/denials.jsonl (default ~/.pawl) and prints one line per gate,
`<gate> <count>`, sorted by count descending, then a total line, the date range
of the rows counted, and a `skipped <n>` line for malformed rows. Nothing else:
no command text, no hashes, no conversation ids.

CLI:
    report.py [--days N] [--data DIR]

    --days N    only rows from the last N days (default 7)
    --data DIR  denial log dir (default $PAWL_DATA, then ~/.pawl)

Exit 0 after a report, 2 when the denial log does not exist. Standard library
only.
"""

from __future__ import annotations

import argparse
import collections
import datetime
import json
import os
from pathlib import Path  # pylint: disable=g-importing-member
import sys
from typing import List, Optional, Tuple

TS_FORMAT = "%Y-%m-%dT%H:%M:%S%z"
DEFAULT_DAYS = 7


def data_dir(override: Optional[str] = None) -> Path:
  """The denial log directory: --data, then PAWL_DATA, then ~/.pawl."""
  return Path(
      override or os.environ.get("PAWL_DATA") or os.path.expanduser("~/.pawl")
  )


def parse_ts(value: object) -> Optional[datetime.datetime]:
  """A timezone-aware datetime for a row's ts field, or None when malformed."""
  if not isinstance(value, str):
    return None
  try:
    return datetime.datetime.strptime(value, TS_FORMAT)
  except ValueError:
    return None


def summarize(lines: List[str], since: Optional[datetime.datetime]) -> Tuple[
    collections.Counter[str],
    int,
    Optional[datetime.datetime],
    Optional[datetime.datetime],
]:
  """Count rows per gate.

  Args:
    lines: raw lines of denials.jsonl.
    since: drop rows with a ts earlier than this; None keeps every row.

  Returns:
    (per-gate counter, skipped count, earliest ts, latest ts). A row is
    skipped when it is not a JSON object, has no string gate, or has a ts
    that does not parse.
  """
  counts: collections.Counter[str] = collections.Counter()
  skipped = 0
  first: Optional[datetime.datetime] = None
  last: Optional[datetime.datetime] = None
  for line in lines:
    line = line.strip()
    if not line:
      continue
    try:
      row = json.loads(line)
    except ValueError:
      skipped += 1
      continue
    if not isinstance(row, dict):
      skipped += 1
      continue
    gate = row.get("gate")
    ts = parse_ts(row.get("ts"))
    if not isinstance(gate, str) or not gate or ts is None:
      skipped += 1
      continue
    if since is not None and ts < since:
      continue
    counts[gate] += 1
    first = ts if first is None or ts < first else first
    last = ts if last is None or ts > last else last
  return counts, skipped, first, last


def render(
    counts: collections.Counter[str],
    skipped: int,
    first: Optional[datetime.datetime],
    last: Optional[datetime.datetime],
) -> str:
  """The report text: gate lines, total, range, skipped."""
  ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
  out = [f"{gate} {n}" for gate, n in ordered]
  out.append(f"total {sum(counts.values())}")
  if first is not None and last is not None:
    out.append(f"range {first.date().isoformat()} {last.date().isoformat()}")
  else:
    out.append("range none")
  out.append(f"skipped {skipped}")
  return "\n".join(out) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
  parser = argparse.ArgumentParser(
      prog="report.py", description="count pawl denial rows per gate"
  )
  parser.add_argument("--days", type=int, default=DEFAULT_DAYS)
  parser.add_argument("--data", default=None)
  args = parser.parse_args(argv)
  path = data_dir(args.data) / "denials.jsonl"
  if not path.is_file():
    sys.stdout.write(f"no denials file at {path}\n")
    return 2
  since: Optional[datetime.datetime] = None
  if args.days > 0:
    since = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
        days=args.days
    )
  lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
  counts, skipped, first, last = summarize(lines, since)
  sys.stdout.write(render(counts, skipped, first, last))
  return 0


if __name__ == "__main__":
  sys.exit(main())
