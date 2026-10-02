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


class PluginOptionsTest(unittest.TestCase):
  """Claude Code exports each plugin option as CLAUDE_PLUGIN_OPTION_<KEY>."""

  def apply(self, env):
    pawl.apply_plugin_options(env)
    return env

  def test_options_become_pawl_settings(self) -> None:
    env = self.apply({
        "CLAUDE_PLUGIN_OPTION_DISABLE": "git, poll",
        "CLAUDE_PLUGIN_OPTION_PROTECTED_ROOTS": "/a:/b",
        "CLAUDE_PLUGIN_OPTION_STRICT_FENCE": "true",
        "CLAUDE_PLUGIN_OPTION_READONLY_AUTO_APPROVE": "false",
    })
    self.assertEqual(env["PAWL_DISABLE"], "git, poll")
    self.assertEqual(env["PAWL_GIT_PROTECTED_ROOTS"], "/a:/b")
    self.assertEqual(env["PAWL_CONVERSATION_FENCE_STRICT"], "1")
    self.assertEqual(env["PAWL_READONLY_PASS_OFF"], "1")

  def test_defaults_and_empty_values_change_nothing(self) -> None:
    env = self.apply({
        "CLAUDE_PLUGIN_OPTION_DISABLE": "",
        "CLAUDE_PLUGIN_OPTION_PROTECTED_ROOTS": "",
        "CLAUDE_PLUGIN_OPTION_STRICT_FENCE": "false",
        "CLAUDE_PLUGIN_OPTION_READONLY_AUTO_APPROVE": "true",
    })
    self.assertEqual(sorted(env), sorted(
        k for k in env if k.startswith("CLAUDE_PLUGIN_OPTION_")))

  def test_an_env_the_user_set_wins(self) -> None:
    env = self.apply({"CLAUDE_PLUGIN_OPTION_DISABLE": "git",
                      "PAWL_DISABLE": "loop"})
    self.assertEqual(env["PAWL_DISABLE"], "loop")
