"""Tests for compare.py: reading each guard's answer, and a full run.

Run: python3 -m pytest -q eval/blast-compare/test_compare.py
"""

from __future__ import annotations

import json
from pathlib import Path  # pylint: disable=g-importing-member
import subprocess
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import compare  # noqa: E402  pylint: disable=g-import-not-at-top


def proc(stdout: str = "", code: int = 0) -> subprocess.CompletedProcess:
  return subprocess.CompletedProcess([], code, stdout, "")


def spec(decision: str) -> str:
  return json.dumps({"hookSpecificOutput": {
      "hookEventName": "PreToolUse", "permissionDecision": decision}})


def test_answers_map_onto_three_words() -> None:
  assert compare.verdict(proc(spec("deny"))) == "deny"
  assert compare.verdict(proc(spec("ask"))) == "ask"
  assert compare.verdict(proc(spec("allow"))) == "allow"
  assert compare.verdict(proc(json.dumps({"decision": "block"}))) == "deny"
  assert compare.verdict(proc("", 2)) == "deny"
  assert compare.verdict(proc("")) == "allow"
  assert compare.verdict(proc("not json")) == "allow"


def test_a_guard_that_blocks_everything_catches_all_and_alarms_all() -> None:
  cases = [{"id": "a", "expect": "deny", "cmd": "rm -rf ~"},
           {"id": "b", "expect": "allow", "cmd": "ls"}]
  block = f"{sys.executable} -c \"import sys; sys.exit(2)\""
  caught, dangerous, alarms, benign, wrong = compare.tally(block, cases)
  assert (caught, dangerous, alarms, benign) == (1, 1, 1, 1)
  assert wrong == ["b"]


def test_no_guard_is_a_usage_error() -> None:
  assert compare.main([]) == 2
