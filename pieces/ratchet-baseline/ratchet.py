#!/usr/bin/env python3
"""ratchet.py: a defect baseline that can only go down.

One file, standard library only. A JSON file holds named integer counters
(failing tests, swallowed exceptions, lint warnings, anything you can count).
`check` compares fresh counts to the file and fails when any counter rose.
`update` lowers the stored numbers, and refuses to raise any of them or to
lower them without a written reason. The file only ever gets better.

CLI:

    ratchet.py check  --baseline FILE --metric name=value [--metric ...]
    ratchet.py update --baseline FILE --metric name=value [...] --reason "..."
    ratchet.py show   --baseline FILE

Exit codes:

    0  check: every metric is at or below the baseline
       update: the file was written
    1  check: at least one metric rose (metric, old and new are printed)
    2  update: a rule was broken (value above baseline, or reason too short)
       any: bad arguments or an unreadable baseline file

The baseline file is written atomically: temp file in the same directory,
then os.replace. A crash mid-write leaves the old file untouched.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

OK = 0
ROSE = 1
RULE = 2

MIN_REASON_CHARS = 40
SCHEMA_VERSION = 1

RULE_TEXT = (
    "Rule: a baseline value may only go down, and lowering it needs a written "
    f"reason of at least {MIN_REASON_CHARS} characters."
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_metric(text: str) -> Tuple[str, int]:
    """Turns 'name=value' into ('name', int). Raises ValueError on bad input."""
    if "=" not in text:
        raise ValueError(f"metric must look like name=value, got {text!r}")
    name, _, raw = text.partition("=")
    name = name.strip()
    if not name:
        raise ValueError(f"metric name is empty in {text!r}")
    try:
        value = int(raw.strip())
    except ValueError:
        raise ValueError(f"metric {name!r} needs an integer value, got {raw!r}") from None
    if value < 0:
        raise ValueError(f"metric {name!r} cannot be negative, got {value}")
    return name, value


def parse_metrics(items: List[str]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for item in items:
        name, value = parse_metric(item)
        out[name] = value
    return out


def load_baseline(path: Path) -> Optional[dict]:
    """Returns the parsed baseline, or None when the file does not exist."""
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("metrics"), dict):
        raise ValueError(f"{path} is not a ratchet baseline (needs a 'metrics' object)")
    metrics = {str(k): int(v) for k, v in data["metrics"].items()}
    return {"schema": SCHEMA_VERSION, "metrics": metrics,
            "updated": data.get("updated"), "reason": data.get("reason")}


def write_baseline(path: Path, metrics: Dict[str, int], reason: str) -> None:
    """Writes the baseline via a sibling temp file and os.replace."""
    payload = {
        "schema": SCHEMA_VERSION,
        "metrics": {k: int(metrics[k]) for k in sorted(metrics)},
        "updated": _now(),
        "reason": reason,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def compare(current: Dict[str, int], baseline: Dict[str, int]) -> Tuple[List[str], List[str], List[str]]:
    """Returns (rose, dropped, unknown) as printable lines."""
    rose: List[str] = []
    dropped: List[str] = []
    unknown: List[str] = []
    for name in sorted(current):
        new = current[name]
        if name not in baseline:
            unknown.append(f"{name}: {new} (first observation, no baseline yet)")
            continue
        old = baseline[name]
        if new > old:
            rose.append(f"{name} rose: {old} -> {new}")
        elif new < old:
            dropped.append(f"{name} dropped: {old} -> {new}")
    return rose, dropped, unknown


def cmd_check(path: Path, current: Dict[str, int]) -> int:
    baseline = load_baseline(path)
    stored = baseline["metrics"] if baseline else {}
    if baseline is None:
        print(f"no baseline at {path}; every metric counts as a first observation")
    rose, dropped, unknown = compare(current, stored)
    for line in unknown:
        print(f"  new   {line}")
    for line in dropped:
        print(f"  down  {line}")
    for line in rose:
        print(f"  UP    {line}")
    if rose:
        print(f"FAIL: {len(rose)} metric(s) rose above {path}")
        return ROSE
    if dropped:
        print(f"OK: {len(dropped)} metric(s) dropped; run update to lock the new floor")
    else:
        print("OK: every metric is at its baseline")
    return OK


def cmd_update(path: Path, current: Dict[str, int], reason: str) -> int:
    reason = reason.strip()
    if len(reason) < MIN_REASON_CHARS:
        print(f"REFUSED: reason is {len(reason)} characters, need {MIN_REASON_CHARS}")
        print(RULE_TEXT)
        return RULE
    baseline = load_baseline(path)
    stored = dict(baseline["metrics"]) if baseline else {}
    rose, dropped, unknown = compare(current, stored)
    if rose:
        for line in rose:
            print(f"  UP    {line}")
        print(f"REFUSED: {len(rose)} metric(s) would raise the baseline")
        print(RULE_TEXT)
        return RULE
    merged = dict(stored)
    merged.update(current)
    write_baseline(path, merged, reason)
    for line in unknown:
        print(f"  added {line.split(' (', 1)[0]}")
    for line in dropped:
        print(f"  down  {line}")
    if not unknown and not dropped:
        print("  no change in values; timestamp and reason refreshed")
    print(f"wrote {path}")
    return OK


def cmd_show(path: Path) -> int:
    baseline = load_baseline(path)
    if baseline is None:
        print(f"no baseline at {path}")
        return RULE
    metrics = baseline["metrics"]
    width = max([len(k) for k in metrics] + [6])
    print(f"{'metric':<{width}}  value")
    print(f"{'-' * width}  -----")
    for name in sorted(metrics):
        print(f"{name:<{width}}  {metrics[name]:>5}")
    if baseline.get("updated"):
        print(f"updated: {baseline['updated']}")
    if baseline.get("reason"):
        print(f"reason:  {baseline['reason']}")
    return OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ratchet.py", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_check = sub.add_parser("check", help="fail when any metric rose above the baseline")
    p_check.add_argument("--baseline", required=True, type=Path)
    p_check.add_argument("--metric", action="append", default=[], metavar="NAME=VALUE", required=True)

    p_update = sub.add_parser("update", help="lower the baseline; needs a written reason")
    p_update.add_argument("--baseline", required=True, type=Path)
    p_update.add_argument("--metric", action="append", default=[], metavar="NAME=VALUE", required=True)
    p_update.add_argument("--reason", required=True, help=f"why the floor moves; {MIN_REASON_CHARS}+ characters")

    p_show = sub.add_parser("show", help="print the baseline table")
    p_show.add_argument("--baseline", required=True, type=Path)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.cmd == "show":
            return cmd_show(args.baseline)
        current = parse_metrics(args.metric)
        if args.cmd == "check":
            return cmd_check(args.baseline, current)
        return cmd_update(args.baseline, current, args.reason)
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return RULE


if __name__ == "__main__":
    sys.exit(main())
