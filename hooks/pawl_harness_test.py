#!/usr/bin/env python3
"""Tests for hooks/pawl_harness.py multi-harness adapter.

Run: python3 -m unittest pawl_harness_test -v (from hooks/).
"""

from __future__ import annotations

import io
import json
from pathlib import Path  # pylint: disable=g-importing-member
import sys
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
  sys.path.insert(0, str(HERE))

import pawl_harness as harness  # noqa: E402


class PawlHarnessTest(unittest.TestCase):

  def test_is_claude_harness_by_tool_name(self) -> None:
    self.assertTrue(harness.is_claude_harness({"tool_name": "Bash"}))
    self.assertTrue(harness.is_claude_harness({"tool_name": "Edit"}))
    self.assertTrue(harness.is_claude_harness({"tool_name": "Write"}))
    self.assertTrue(harness.is_claude_harness({"tool_name": "Read"}))
    self.assertFalse(harness.is_claude_harness({"tool_name": "run_command"}))

  def test_is_claude_harness_by_event(self) -> None:
    self.assertTrue(harness.is_claude_harness({"hook_event_name": "PreToolUse"}))
    self.assertTrue(harness.is_claude_harness({"hook_event_name": "Stop"}))

  def test_is_claude_harness_antigravity_precedence(self) -> None:
    # If toolCall is present, it is Antigravity
    self.assertFalse(
        harness.is_claude_harness({
            "toolCall": {"name": "Bash"},
            "tool_name": "Bash",
        })
    )

  def test_is_claude_harness_env_override(self) -> None:
    with mock.patch.dict("os.environ", {"PAWL_HARNESS": "claude"}):
      self.assertTrue(harness.is_claude_harness({}))

  def test_emit_decision_antigravity_allow(self) -> None:
    stdout = io.StringIO()
    with mock.patch("sys.stdout", stdout), self.assertRaises(SystemExit) as ctx:
      harness.emit_decision("allow", payload={"toolCall": {"name": "run_command"}})
    self.assertEqual(ctx.exception.code, 0)
    self.assertEqual(json.loads(stdout.getvalue()), {"decision": "allow"})

  def test_emit_decision_antigravity_deny(self) -> None:
    stdout = io.StringIO()
    with mock.patch("sys.stdout", stdout), self.assertRaises(SystemExit) as ctx:
      harness.emit_decision(
          "deny",
          reason="blocked",
          payload={"toolCall": {"name": "run_command"}},
      )
    self.assertEqual(ctx.exception.code, 0)
    self.assertEqual(
        json.loads(stdout.getvalue()),
        {"decision": "deny", "reason": "blocked"},
    )

  def test_emit_decision_claude_deny_exits_code_2(self) -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()
    payload = {"tool_name": "Bash", "tool_input": {"command": "git reset --hard"}}
    with mock.patch("sys.stdout", stdout), mock.patch(
        "sys.stderr", stderr
    ), self.assertRaises(SystemExit) as ctx:
      harness.emit_decision("force_ask", reason="git reset blocked", payload=payload)
    self.assertEqual(ctx.exception.code, 2)
    self.assertIn("git reset blocked", stderr.getvalue())
    data = json.loads(stdout.getvalue())
    self.assertEqual(
        data["hookSpecificOutput"]["permissionDecision"], "deny"
    )
    self.assertEqual(
        data["hookSpecificOutput"]["permissionDecisionReason"],
        "git reset blocked",
    )

  def test_emit_decision_claude_allow_with_overwrite(self) -> None:
    stdout = io.StringIO()
    payload = {"tool_name": "Write", "tool_input": {"content": "foo\u200bbar"}}
    with mock.patch("sys.stdout", stdout), self.assertRaises(SystemExit) as ctx:
      harness.emit_decision(
          "allow",
          reason="sanitized",
          payload=payload,
          overwrite={"content": "foobar"},
      )
    self.assertEqual(ctx.exception.code, 0)
    data = json.loads(stdout.getvalue())
    self.assertEqual(data["hookSpecificOutput"]["permissionDecision"], "allow")
    self.assertEqual(data["hookSpecificOutput"]["updatedInput"], {"content": "foobar"})


if __name__ == "__main__":
  unittest.main()
