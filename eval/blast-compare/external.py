#!/usr/bin/env python3
"""external.py: score pawl's blast gate on other guards' own test cases.

The corpus pawl was tuned on was written by pawl's author. This reads the
labelled cases other projects wrote for their own guards, keeps the ones
about deleting files or disks, and reports where pawl agrees with them:

    dcg            tests/corpus/**/*.toml   (command, expected deny|allow)
    shguard        tests/bypass_corpus.toml, tests/guardfall.rs,
                   tests/benign_corpus.rs
    cc-safety-net  tests/gate/behavioral-contract-cases.ts (literal commands)

    external.py --dcg DIR --shguard DIR --ccsn DIR [--verbose]

A case they block counts as caught when pawl denies or asks; a case they
allow counts as agreed when pawl allows. Disagreements are listed with
--verbose: some are pawl's bugs, some are the projects' different
policies, and the point of the list is to tell which is which by hand.

Nothing is run: commands are judged in a fake home (/u/tester) with the
workspace at /u/tester/proj, outside every temp directory.

Standard library only (tomllib needs Python 3.11).
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import sys
import tomllib
from typing import Dict, Iterator, List, NamedTuple, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "pieces", "blast-radius-guard"))

import blast_radius  # noqa: E402  pylint: disable=g-import-not-at-top

HOME = "/u/tester"
WORKSPACE = HOME + "/proj"
# Deleting files or disks, spelled plainly or hidden; everything else (git,
# databases, clouds) is another gate's business or no gate's.
IN_SCOPE = re.compile(
    r"\b(?:rm|rmdir|unlink|shred|rimraf|rmtree|rmSync|rm_rf|rm_r"
    r"|remove_tree|RemoveAll|Remove-Item|wipefs|mkfs[\w.]*|diskutil)\b"
    r"|find\b[^|;&]*-delete|\bdd\b[^|;&]*\bof=|\b(?:rd|del|rmdir)\s+/[sS]"
    r"|r''m|\\x72\\x6d|base64|cm0g")
BLOCKED = {"deny", "block", "ask", "Block", "Ask"}


class Case(NamedTuple):
  source: str
  ident: str
  cmd: str
  blocked: bool  # the project's own label: block/ask (True) or allow


def dcg_cases(root: str) -> Iterator[Case]:
  pattern = os.path.join(root, "tests", "corpus", "**", "*.toml")
  for path in sorted(glob.glob(pattern, recursive=True)):
    with open(path, "rb") as fh:
      data = tomllib.load(fh)
    rel = os.path.relpath(path, os.path.join(root, "tests", "corpus"))
    for i, case in enumerate(data.get("case", [])):
      cmd, expected = case.get("command"), case.get("expected")
      if isinstance(cmd, str) and expected in ("deny", "allow"):
        yield Case("dcg", f"{rel}#{i}", cmd, expected == "deny")


def _rust_strings(text: str) -> Iterator[str]:
  for m in re.finditer(r'"((?:\\.|[^"\\])*)"', text):
    yield bytes(m.group(1), "utf-8").decode("unicode_escape")


def shguard_cases(root: str) -> Iterator[Case]:
  tests = os.path.join(root, "tests")
  with open(os.path.join(tests, "bypass_corpus.toml"), "rb") as fh:
    for i, case in enumerate(tomllib.load(fh).get("case", [])):
      yield Case("shguard", f"bypass#{i}", case["payload"],
                 case["expected"] in BLOCKED)
  with open(os.path.join(tests, "guardfall.rs"), encoding="utf-8") as fh:
    text = fh.read()
  for i, m in enumerate(re.finditer(
      r'\(\s*"((?:\\.|[^"\\])*)"\s*,\s*Decision::(\w+)\s*\)', text)):
    cmd = bytes(m.group(1), "utf-8").decode("unicode_escape")
    yield Case("shguard", f"guardfall#{i}", cmd, m.group(2) in BLOCKED)
  with open(os.path.join(tests, "benign_corpus.rs"), encoding="utf-8") as fh:
    text = fh.read()
  body = text[text.find("let commands"):]
  body = body[:body.find("];")]
  for i, cmd in enumerate(_rust_strings(body)):
    yield Case("shguard", f"benign#{i}", cmd, False)


def ccsn_cases(root: str) -> Iterator[Case]:
  path = os.path.join(root, "tests", "gate", "behavioral-contract-cases.ts")
  with open(path, encoding="utf-8") as fh:
    text = fh.read()
  pattern = re.compile(
      r"command:\s*(['\"])((?:\\.|(?!\1).)*)\1(.*?)kind:\s*'(allow|block)'",
      re.S)
  for i, m in enumerate(pattern.finditer(text)):
    if "${" in m.group(2) or "tempRepos" in m.group(3):
      continue  # needs the project's own temp paths
    cmd = m.group(2).encode().decode("unicode_escape")
    yield Case("cc-safety-net", f"contract#{i}", cmd, m.group(4) == "block")


def judge(cmd: str) -> str:
  env = {"HOME": HOME, "PATH": os.defpath, "TMPDIR": "/tmp"}
  return blast_radius.assess(cmd, WORKSPACE, env).decision


def report(cases: List[Case], verbose: bool) -> Dict[str, List[int]]:
  totals: Dict[str, List[int]] = {}
  for case in cases:
    if not IN_SCOPE.search(case.cmd):
      continue
    got = judge(case.cmd)
    row = totals.setdefault(case.source, [0, 0, 0, 0])
    agree = (got != "allow") == case.blocked
    row[0 if case.blocked else 2] += agree
    row[1 if case.blocked else 3] += 1
    if verbose and not agree:
      label = "they block" if case.blocked else "they allow"
      print(f"  {case.source} {case.ident}: {label}, pawl {got}:"
            f" {case.cmd[:110]!r}")
  return totals


def main(argv: Optional[List[str]] = None) -> int:
  ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
  ap.add_argument("--dcg")
  ap.add_argument("--shguard")
  ap.add_argument("--ccsn")
  ap.add_argument("--verbose", action="store_true")
  args = ap.parse_args(argv)
  loaders = [(args.dcg, dcg_cases), (args.shguard, shguard_cases),
             (args.ccsn, ccsn_cases)]
  cases: List[Case] = []
  for root, load in loaders:
    if root:
      try:
        cases.extend(load(root))
      except (OSError, ValueError, KeyError) as exc:
        print(f"external: cannot read {root}: {exc}", file=sys.stderr)
        return 2
  if not cases:
    print("external: give at least one of --dcg, --shguard, --ccsn",
          file=sys.stderr)
    return 2
  totals = report(cases, args.verbose)
  print("| Their corpus | Their blocks pawl catches | Their allows pawl allows |")
  print("| --- | --- | --- |")
  for source, (caught, blocked, allowed, benign) in totals.items():
    print(f"| {source} | {caught}/{blocked} | {allowed}/{benign} |")
  return 0


if __name__ == "__main__":
  sys.exit(main())
