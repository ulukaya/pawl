#!/usr/bin/env python3
"""Tests for hooks/pawl.py: argument parsing and how verdicts merge.

Run: python3 -m pytest -q pawl_test.py (from hooks/).
"""

from __future__ import annotations

from pathlib import Path  # pylint: disable=g-importing-member
import sys
import unittest

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
  sys.path.insert(0, str(HERE))

import pawl  # noqa: E402  # pylint: disable=g-import-not-at-top
from harness import Verdict  # noqa: E402  # pylint: disable=g-import-not-at-top


class ParseArgsTest(unittest.TestCase):

  def test_both_option_spellings(self) -> None:
    self.assertEqual(
        pawl.parse_args(["pre", "--harness", "claude", "--only=git, poll"]),
        pawl.Args("pre", "claude", ["git", "poll"]))
    self.assertEqual(pawl.parse_args(["stop"]), pawl.Args("stop"))

  def test_bad_command_lines_raise_with_a_reason(self) -> None:
    for argv, needle in (([], "expected one of"),
                         (["run"], "expected one of"),
                         (["pre", "--harness", "gemini"], "unknown harness"),
                         (["pre", "--only"], "needs a value"),
                         (["pre", "-x"], "unknown option")):
      with self.assertRaisesRegex(ValueError, needle):
        pawl.parse_args(argv)

  def test_main_exits_2_on_usage_errors(self) -> None:
    self.assertEqual(pawl.main(["pre", "--harness", "nope"]), 2)
    self.assertEqual(pawl.main(["pre", "--only", "nope"]), 2)


class MergeTest(unittest.TestCase):

  def test_deny_wins_and_keeps_its_own_reason(self) -> None:
    out = pawl.merge([Verdict("force_ask", "a"), Verdict("deny", "d"),
                      Verdict("auto_approve")])
    self.assertEqual((out.decision, out.reason), ("deny", "d"))

  def test_asks_join_their_reasons_and_gates(self) -> None:
    out = pawl.merge([Verdict("force_ask", "one", gate="git"),
                      Verdict("auto_approve", gate="readonly"),
                      Verdict("force_ask", "two", gate="loop")])
    self.assertEqual((out.decision, out.reason, out.gate),
                     ("force_ask", "one\ntwo", "git,loop"))

  def test_approval_only_without_objections(self) -> None:
    self.assertEqual(pawl.merge([Verdict(), Verdict("auto_approve")]).decision,
                     "auto_approve")
    self.assertEqual(pawl.merge([Verdict(), Verdict()]).decision, "allow")
    self.assertEqual(pawl.merge([]).decision, "allow")

  def test_overwrite_rides_along_unless_denied(self) -> None:
    clean = {"CodeContent": "ab"}
    self.assertEqual(pawl.merge([Verdict(overwrite=clean)]).overwrite, clean)
    self.assertEqual(
        pawl.merge([Verdict(overwrite=clean), Verdict("force_ask", "x")]
                   ).overwrite, clean)
    self.assertIsNone(
        pawl.merge([Verdict(overwrite=clean), Verdict("deny", "x")]).overwrite)

  def test_block_is_a_stop_answer_without_overwrite(self) -> None:
    out = pawl.merge([Verdict("block", "idle", {"a": 1}, "idle")])
    self.assertEqual((out.decision, out.overwrite), ("block", None))


if __name__ == "__main__":
  unittest.main()
