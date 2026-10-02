#!/usr/bin/env python3
"""Tests for hooks/harness.py: payload translation and per-harness output.

Run: python3 -m pytest -q harness_test.py (from hooks/).
"""

from __future__ import annotations

import json
from pathlib import Path  # pylint: disable=g-importing-member
import sys
from typing import Any, Dict
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
  sys.path.insert(0, str(HERE))

import harness  # noqa: E402  # pylint: disable=g-import-not-at-top
from harness import Verdict  # noqa: E402  # pylint: disable=g-import-not-at-top


def claude(tool: str, tool_input: Dict[str, Any], **extra: Any) -> str:
  base = {
      "session_id": "s-1",
      "transcript_path": "/t/s-1.jsonl",
      "cwd": "/repo",
      "permission_mode": "default",
      "hook_event_name": "PreToolUse",
      "tool_name": tool,
      "tool_input": tool_input,
      "tool_use_id": "toolu_1",
  }
  return json.dumps(dict(base, **extra))


def codex(tool: str, tool_input: Dict[str, Any], **extra: Any) -> str:
  base = {
      "session_id": "th-1",
      "turn_id": "turn-7",
      "transcript_path": "/t/rollout-th-1.jsonl",
      "cwd": "/repo",
      "hook_event_name": "PreToolUse",
      "model": "m",
      "permission_mode": "default",
      "tool_name": tool,
      "tool_input": tool_input,
      "tool_use_id": "call_1",
  }
  return json.dumps(dict(base, **extra))


def decoded(rendered: harness.Rendered) -> Any:
  return json.loads(rendered.stdout) if rendered.stdout else None


class ResolveTest(unittest.TestCase):

  def test_explicit_hint_wins(self) -> None:
    self.assertEqual(harness.resolve("codex", {}), "codex")
    self.assertEqual(harness.resolve("antigravity", {"turn_id": "t"}),
                     "antigravity")

  def test_claude_hint_with_a_codex_payload_is_codex(self) -> None:
    self.assertEqual(harness.resolve("claude", {"turn_id": "t"}), "codex")

  def test_auto_reads_the_payload_shape(self) -> None:
    with mock.patch.dict("os.environ", {"PAWL_HARNESS": ""}):
      self.assertEqual(harness.resolve("auto", {"toolCall": {}}),
                       "antigravity")
      self.assertEqual(harness.resolve("", {"turn_id": "t"}), "codex")
      self.assertEqual(harness.resolve("", {"tool_name": "apply_patch"}),
                       "codex")
      self.assertEqual(harness.resolve("", {"hook_event_name": "Stop"}),
                       "claude")
      self.assertEqual(harness.resolve("", {}), "antigravity")

  def test_env_fills_an_auto_hint(self) -> None:
    with mock.patch.dict("os.environ", {"PAWL_HARNESS": "claude"}):
      self.assertEqual(harness.resolve("auto", {}), "claude")


class ParseTest(unittest.TestCase):

  def test_claude_bash_becomes_run_command_with_cwd(self) -> None:
    call = harness.parse(claude("Bash", {"command": "ls", "description": "x"}),
                         "claude")
    self.assertEqual(call.tool, "run_command")
    self.assertEqual(call.args, {"CommandLine": "ls", "Cwd": "/repo"})
    self.assertEqual(call.payload["conversationId"], "s-1")
    self.assertEqual(call.payload["transcriptPath"], "/t/s-1.jsonl")
    self.assertNotIn("turnId", call.payload)

  def test_claude_description_never_reaches_the_pieces(self) -> None:
    a = harness.parse(claude("Bash", {"command": "ls", "description": "a"}))
    b = harness.parse(claude("Bash", {"command": "ls", "description": "b"}))
    self.assertEqual(a.args, b.args)

  def test_claude_edit_and_write_map_to_antigravity_fields(self) -> None:
    edit = harness.parse(claude("Edit", {
        "file_path": "/a", "old_string": "x", "new_string": "y",
        "replace_all": True}))
    self.assertEqual(edit.tool, "replace_file_content")
    self.assertEqual(edit.args, {
        "TargetFile": "/a", "TargetContent": "x", "ReplacementContent": "y",
        "AllowMultiple": True})
    write = harness.parse(claude("Write", {"file_path": "/a", "content": "c"}))
    self.assertEqual((write.tool, write.args),
                     ("write_to_file", {"TargetFile": "/a",
                                        "CodeContent": "c"}))

  def test_claude_read_offset_and_limit_become_line_span(self) -> None:
    def args(**kw: Any) -> Dict[str, Any]:
      return harness.parse(claude("Read", dict(file_path="/f", **kw))).args

    self.assertEqual(args(), {"AbsolutePath": "/f"})
    self.assertEqual(args(offset=20, limit=5),
                     {"AbsolutePath": "/f", "StartLine": 20, "EndLine": 24})
    self.assertEqual(args(limit=3),
                     {"AbsolutePath": "/f", "StartLine": 1, "EndLine": 3})
    self.assertEqual(args(offset=40),
                     {"AbsolutePath": "/f", "StartLine": 40})

  def test_claude_task_tools_become_manage_task(self) -> None:
    out = harness.parse(claude("TaskOutput", {"task_id": "b1", "block": True}))
    self.assertEqual((out.tool, out.args),
                     ("manage_task", {"Action": "status", "TaskId": "b1"}))
    stop = harness.parse(claude("TaskStop", {"task_id": "b1"}))
    self.assertEqual(stop.args["Action"], "kill")
    self.assertEqual(harness.parse(claude("ScheduleWakeup", {})).tool,
                     "schedule")

  def test_unknown_tools_pass_through(self) -> None:
    call = harness.parse(claude("Grep", {"pattern": "x", "path": "/p"}))
    self.assertEqual((call.tool, call.args),
                     ("Grep", {"pattern": "x", "path": "/p"}))

  def test_codex_bash_and_turn_id(self) -> None:
    call = harness.parse(codex("Bash", {"command": "git status"}), "codex")
    self.assertEqual(call.harness, "codex")
    self.assertEqual(call.args, {"CommandLine": "git status", "Cwd": "/repo"})
    self.assertEqual(call.payload["turnId"], "turn-7")

  def test_codex_argv_command_is_joined(self) -> None:
    call = harness.parse(codex("Bash", {"command": ["git", "log", "a b"]}))
    self.assertEqual(call.args["CommandLine"], "git log 'a b'")

  def test_codex_apply_patch_passes_through(self) -> None:
    patch = "*** Begin Patch\n*** End Patch\n"
    call = harness.parse(codex("apply_patch", {"command": patch}))
    self.assertEqual((call.tool, call.args),
                     ("apply_patch", {"command": patch}))

  def test_stop_payload_keeps_background_tasks(self) -> None:
    raw = json.dumps({
        "session_id": "s-1", "hook_event_name": "Stop",
        "stop_hook_active": True,
        "background_tasks": [{"id": "b1", "type": "shell"}],
    })
    call = harness.parse(raw, "claude", harness.STOP)
    self.assertTrue(call.payload["stopHookActive"])
    self.assertEqual(call.payload["backgroundTasks"][0]["id"], "b1")

  def test_antigravity_payload_is_untouched(self) -> None:
    raw = {"conversationId": "c", "toolCall": {
        "name": "run_command", "args": {"CommandLine": "ls"}}}
    call = harness.parse(json.dumps(raw), "antigravity")
    self.assertEqual(call.payload, raw)
    self.assertEqual((call.tool, call.args),
                     ("run_command", {"CommandLine": "ls"}))

  def test_bad_stdin_sets_error_and_never_raises(self) -> None:
    self.assertIn("not JSON", harness.parse("{nope", "claude").error)
    self.assertIn("list", harness.parse("[1]", "claude").error)


class RenderClaudeTest(unittest.TestCase):

  def call(self, tool: str = "Bash", tool_input: Any = None,
           event: str = harness.PRE) -> harness.Call:
    return harness.parse(claude(tool, tool_input or {"command": "ls"}),
                         "claude", event)

  def test_allow_is_silent_so_the_permission_prompt_stays(self) -> None:
    out = harness.render(self.call(), harness.ALLOW)
    self.assertEqual((out.stdout, out.exit_code), ("", 0))

  def test_auto_approve_is_permission_allow(self) -> None:
    spec = decoded(harness.render(self.call(), Verdict("auto_approve")))
    self.assertEqual(spec["hookSpecificOutput"]["permissionDecision"],
                     "allow")
    self.assertEqual(spec["hookSpecificOutput"]["hookEventName"],
                     "PreToolUse")

  def test_force_ask_is_permission_ask(self) -> None:
    out = harness.render(self.call(), Verdict("force_ask", "[PAWL git] x."))
    spec = decoded(out)["hookSpecificOutput"]
    self.assertEqual(spec["permissionDecision"], "ask")
    self.assertEqual(spec["permissionDecisionReason"], "[PAWL git] x.")
    self.assertEqual(out.exit_code, 0)

  def test_deny_is_permission_deny_with_exit_0(self) -> None:
    out = harness.render(self.call(), Verdict("deny", "[PAWL no-op] y"))
    spec = decoded(out)["hookSpecificOutput"]
    self.assertEqual(spec["permissionDecision"], "deny")
    self.assertEqual(out.exit_code, 0)

  def test_overwrite_is_native_updated_input_without_approval(self) -> None:
    call = self.call("Edit", {"file_path": "/a", "old_string": "x​",
                              "new_string": "y", "replace_all": False})
    cleaned = dict(call.args, TargetContent="x")
    spec = decoded(harness.render(call, Verdict(overwrite=cleaned)))
    hso = spec["hookSpecificOutput"]
    self.assertNotIn("permissionDecision", hso)
    self.assertEqual(hso["updatedInput"], {
        "file_path": "/a", "old_string": "x", "new_string": "y",
        "replace_all": False})

  def test_reason_speaks_claude_tool_names(self) -> None:
    v = Verdict("deny", "send the edit after view_file; or write_to_file.")
    reason = decoded(harness.render(self.call(), v))["hookSpecificOutput"][
        "permissionDecisionReason"]
    self.assertEqual(reason, "send the edit after Read; or Write.")

  def test_stop_block_is_top_level_decision(self) -> None:
    call = harness.parse(json.dumps({"session_id": "s", "hook_event_name":
                                     "Stop"}), "claude", harness.STOP)
    self.assertEqual(decoded(harness.render(call, Verdict("block", "r"))),
                     {"decision": "block", "reason": "r"})
    self.assertEqual(harness.render(call, harness.ALLOW).stdout, "")


class RenderCodexTest(unittest.TestCase):

  def call(self, tool: str = "Bash", tool_input: Any = None) -> harness.Call:
    return harness.parse(codex(tool, tool_input or {"command": "ls"}),
                         "codex")

  def test_allow_and_auto_approve_are_silent(self) -> None:
    self.assertEqual(harness.render(self.call(), harness.ALLOW).stdout, "")
    self.assertEqual(
        harness.render(self.call(), Verdict("auto_approve")).stdout, "")

  def test_force_ask_becomes_deny_and_says_why(self) -> None:
    v = Verdict("force_ask", "[PAWL git] reset. Approve to run anyway; the run"
                " is logged as a human override.", gate="git")
    spec = decoded(harness.render(self.call(), v))["hookSpecificOutput"]
    self.assertEqual(spec["permissionDecision"], "deny")
    reason = spec["permissionDecisionReason"]
    self.assertNotIn("Approve", reason)
    self.assertIn("Codex hooks cannot ask", reason)
    self.assertIn("PAWL_DISABLE=git", reason)

  def test_deny_reason_is_never_empty(self) -> None:
    spec = decoded(harness.render(self.call(), Verdict("deny")))
    self.assertTrue(spec["hookSpecificOutput"]["permissionDecisionReason"])

  def test_overwrite_needs_permission_allow(self) -> None:
    call = self.call("apply_patch", {"command": "+a​\n"})
    spec = decoded(harness.render(call, Verdict(overwrite={"command": "+a\n"})))
    self.assertEqual(spec["hookSpecificOutput"]["permissionDecision"],
                     "allow")
    self.assertEqual(spec["hookSpecificOutput"]["updatedInput"],
                     {"command": "+a\n"})

  def test_stop_block(self) -> None:
    raw = codex("", {}, hook_event_name="Stop", stop_hook_active=False)
    call = harness.parse(raw, "codex", harness.STOP)
    self.assertEqual(decoded(harness.render(call, Verdict("block", "r"))),
                     {"decision": "block", "reason": "r"})


class RenderAntigravityTest(unittest.TestCase):

  def test_decisions_pass_through_in_antigravity_words(self) -> None:
    call = harness.parse(json.dumps({"toolCall": {"name": "run_command"}}),
                         "antigravity")
    for word in ("allow", "auto_approve", "deny", "force_ask"):
      out = decoded(harness.render(call, Verdict(word)))
      self.assertEqual(out, {"decision": word})
    out = decoded(harness.render(call, Verdict(overwrite={"a": 1})))
    self.assertEqual(out, {"decision": "allow", "overwrite": {"a": 1}})

  def test_unknown_decision_is_refused(self) -> None:
    with self.assertRaises(ValueError):
      Verdict("approve")


if __name__ == "__main__":
  unittest.main()
