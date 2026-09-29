#!/usr/bin/env python3
"""Prompt budget: a token ceiling for always-loaded instruction files.

Reads a `budget.json` that lists the files an agent loads on every prompt,
estimates their token cost, and fails when any file or the total goes over
its ceiling. Standard library only. Token estimate is an approximation:
ceil(characters * tokens_per_char), default 0.25 (one token per 4 chars).

Exit codes:
  0  every file and the total are under ceiling (or `report` mode)
  1  at least one file or the total is over, or a frontmatter pin is missing
  2  a required file listed in the config does not exist
  3  bad config or usage
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
import math
from pathlib import Path
import sys
from typing import Optional, TextIO

DEFAULT_TOKENS_PER_CHAR = 0.25


@dataclass
class Row:
  path: str
  tokens: int
  ceiling: Optional[int]
  over: bool
  notes: list[str] = field(default_factory=list)

  @property
  def status(self) -> str:
    return "OVER" if self.over else "OK"

  def line(self) -> str:
    ceiling = str(self.ceiling) if self.ceiling is not None else "-"
    out = f"{self.status:<4}  {self.tokens}/{ceiling}  {self.path}"
    if self.notes:
      out += "  (" + "; ".join(self.notes) + ")"
    return out


class MissingFile(Exception):

  def __init__(self, path: str) -> None:
    super().__init__(path)
    self.path = path


def estimate_tokens(text: str, tokens_per_char: float) -> int:
  return math.ceil(len(text) * tokens_per_char)


def parse_frontmatter(text: str) -> dict[str, str]:
  """Return key/value pairs from a `---` fenced block at the top of the file.

  Plain string ops, no YAML. Only `key: value` lines are read; anything
  else inside the block is ignored. Returns {} when no block is present.
  """
  lines = text.splitlines()
  if not lines or lines[0].strip() != "---":
    return {}
  out: dict[str, str] = {}
  for line in lines[1:]:
    if line.strip() == "---":
      return out
    if ":" not in line or line.startswith((" ", "\t")):
      continue
    key, _, value = line.partition(":")
    out[key.strip()] = value.strip()
  return {}  # opening fence without a closing fence: not a frontmatter block


def frontmatter_gaps(text: str, required: dict[str, str]) -> list[str]:
  have = parse_frontmatter(text)
  gaps = []
  for key, value in required.items():
    if have.get(key) != str(value):
      gaps.append(f"frontmatter {key}: {value} missing")
  return gaps


def resolve_entry(entry: dict, root: Path) -> list[tuple[Path, Optional[int]]]:
  """Expand one config entry to (path, ceiling) pairs. Raises MissingFile."""
  optional = bool(entry.get("optional", False))
  if "path" in entry:
    p = root / entry["path"]
    if not p.is_file():
      if optional:
        return []
      raise MissingFile(entry["path"])
    return [(p, entry.get("max_tokens"))]
  if "glob" in entry:
    matches = sorted(q for q in root.glob(entry["glob"]) if q.is_file())
    if not matches and not optional:
      raise MissingFile(entry["glob"])
    ceiling = entry.get("max_tokens_each", entry.get("max_tokens"))
    return [(q, ceiling) for q in matches]
  raise ValueError(f"config entry needs 'path' or 'glob': {entry}")


def measure(
    config: dict, root: Path, tokens_per_char: float
) -> tuple[list[Row], int, Optional[int], bool]:
  """Return (rows, total_tokens, total_ceiling, total_over)."""
  rows: list[Row] = []
  for entry in config.get("files", []):
    required_fm = entry.get("require_frontmatter") or {}
    for path, ceiling in resolve_entry(entry, root):
      text = path.read_text(encoding="utf-8", errors="ignore")
      tokens = estimate_tokens(text, tokens_per_char)
      notes = frontmatter_gaps(text, required_fm) if required_fm else []
      over = (ceiling is not None and tokens > int(ceiling)) or bool(notes)
      try:
        shown = str(path.relative_to(root))
      except ValueError:
        shown = str(path)
      rows.append(
          Row(
              shown,
              tokens,
              int(ceiling) if ceiling is not None else None,
              over,
              notes,
          )
      )
  total = sum(r.tokens for r in rows)
  total_ceiling = config.get("total_tokens")
  total_ceiling = int(total_ceiling) if total_ceiling is not None else None
  total_over = total_ceiling is not None and total > total_ceiling
  return rows, total, total_ceiling, total_over


def total_line(total: int, ceiling: Optional[int], over: bool) -> str:
  shown = str(ceiling) if ceiling is not None else "-"
  return f"{'OVER' if over else 'OK':<4}  {total}/{shown}  total"


def load_config(path: Path) -> dict:
  config = json.loads(path.read_text(encoding="utf-8"))
  files_ok = isinstance(config.get("files", []), list)
  if not isinstance(config, dict) or not files_ok:
    raise ValueError("config must be an object with a 'files' list")
  return config


def run(
    mode: str,
    config_path: Path,
    root: Optional[Path],
    tokens_per_char: float,
    out: Optional[TextIO] = None,
) -> int:
  """Runs one mode against the config and writes the report to `out`."""
  out = out if out is not None else sys.stdout
  try:
    config = load_config(config_path)
  except (OSError, ValueError) as e:
    print(f"config error: {e}", file=out)
    return 3
  base = root if root is not None else config_path.parent
  try:
    rows, total, total_ceiling, total_over = measure(
        config, base, tokens_per_char
    )
  except MissingFile as e:
    if mode == "report":
      print(f"MISSING  {e.path}", file=out)
      return 0
    print(f"missing file: {e.path}", file=out)
    return 2
  except ValueError as e:
    print(f"config error: {e}", file=out)
    return 3

  if mode == "report":
    ordered = sorted(rows, key=lambda r: (-r.tokens, r.path))
  else:
    ordered = sorted(rows, key=lambda r: (not r.over, -r.tokens, r.path))
  for r in ordered:
    print(r.line(), file=out)
  print(total_line(total, total_ceiling, total_over), file=out)

  if mode == "report":
    return 0
  return 1 if (total_over or any(r.over for r in rows)) else 0


def main(argv: Optional[list[str]] = None) -> int:
  ap = argparse.ArgumentParser(
      description="Token ceiling for always-loaded instruction files."
  )
  sub = ap.add_subparsers(dest="mode", required=True)
  for name, help_text in (
      ("check", "fail when over ceiling"),
      ("report", "print the table, never fail"),
  ):
    p = sub.add_parser(name, help=help_text)
    p.add_argument(
        "--config", required=True, type=Path, help="path to budget.json"
    )
    p.add_argument(
        "--root",
        type=Path,
        default=None,
        help="directory paths resolve against (default: config dir)",
    )
    p.add_argument(
        "--tokens-per-char",
        type=float,
        default=DEFAULT_TOKENS_PER_CHAR,
        help=f"estimate multiplier (default {DEFAULT_TOKENS_PER_CHAR})",
    )
  args = ap.parse_args(argv)
  return run(args.mode, args.config, args.root, args.tokens_per_char)


if __name__ == "__main__":
  sys.exit(main())
