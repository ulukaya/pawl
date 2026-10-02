#!/usr/bin/env python3
"""Tests for the git, poll, loop and idle groups through the dispatcher.

Run: python3 -m unittest other_hooks_test -v (from hooks/).
"""

from __future__ import annotations

import json
import os
from pathlib import Path  # pylint: disable=g-importing-member
import shutil
import tempfile
from typing import Any, Dict
import unittest

HERE = Path(__file__).resolve().parent
if str(HERE) not in os.sys.path:
  os.sys.path.insert(0, str(HERE))

from hooks_test import (  # noqa: E402
    GIT_HOOK,
    OSC_HOOK,
    POLL_HOOK,
    STOP_HOOK,
    run_entry,
    tool_payload,
)


class PawlGitHookTest(unittest.TestCase):

  def setUp(self) -> None:
    super().setUp()
    self.tmp = tempfile.mkdtemp(prefix="pawl_git_test_")
    self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
    self.env = {
        "PAWL_DATA": self.tmp,
        "PAWL_GIT_PROTECTED_ROOTS": self.tmp,
    }

  def test_neutral_command_allows(self) -> None:
    self.assertEqual(
        run_entry(GIT_HOOK, tool_payload("git status", self.tmp), self.env),
        {"decision": "allow"},
    )
    self.assertFalse((Path(self.tmp) / "denials.jsonl").exists())

  def test_reset_hard_in_protected_root_force_asks(self) -> None:
    out = run_entry(
        GIT_HOOK, tool_payload("git reset --hard", self.tmp), self.env
    )
    self.assertEqual(out["decision"], "force_ask")
    self.assertTrue(out["reason"].startswith("[PAWL git] [DESTRUCTIVE GIT]"))
    self.assertTrue(
        out["reason"].endswith(
            "Approve to run anyway; the run is logged as a human override."
        )
    )
    rows = [
        json.loads(line)
        for line in (Path(self.tmp) / "denials.jsonl").read_text().splitlines()
    ]
    self.assertEqual(len(rows), 1)
    self.assertEqual(rows[0]["gate"], "DESTRUCTIVE_GIT")
    self.assertEqual(rows[0]["outcome"], "force_ask")

  def test_old_override_env_does_not_change_decision(self) -> None:
    env = dict(self.env, PAWL_GIT_GUARD_OVERRIDE="1")
    out = run_entry(GIT_HOOK, tool_payload("git reset --hard", self.tmp), env)
    self.assertEqual(out["decision"], "force_ask")

  def test_unparsable_payload_fails_closed(self) -> None:
    out = run_entry(GIT_HOOK, "{nope", self.env)
    self.assertEqual(out["decision"], "deny")
    self.assertIn("[HOOK PAYLOAD]", out["reason"])


class PawlPollHookTest(unittest.TestCase):

  def setUp(self) -> None:
    super().setUp()
    self.tmp = tempfile.mkdtemp(prefix="pawl_poll_test_")
    self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
    self.env = {"PAWL_DATA": self.tmp}

  def test_neutral_command_allows(self) -> None:
    self.assertEqual(
        run_entry(POLL_HOOK, tool_payload("ls -la"), self.env),
        {"decision": "allow"},
    )
    self.assertFalse((Path(self.tmp) / "denials.jsonl").exists())

  def test_poll_loop_force_asks(self) -> None:
    out = run_entry(
        POLL_HOOK, tool_payload("while true; do sleep 5; done"), self.env
    )
    self.assertEqual(out["decision"], "force_ask")
    self.assertTrue(out["reason"].startswith("[PAWL poll] [POLL LOOP] LOOP"))
    self.assertTrue(
        out["reason"].endswith(
            "Approve to run anyway; the run is logged as a human override."
        )
    )
    rows = [
        json.loads(line)
        for line in (Path(self.tmp) / "denials.jsonl").read_text().splitlines()
    ]
    self.assertEqual(len(rows), 1)
    self.assertEqual(rows[0]["gate"], "POLL_LOOP")
    self.assertEqual(rows[0]["outcome"], "force_ask")

  def test_old_off_env_does_not_change_decision(self) -> None:
    env = dict(self.env, PAWL_POLL_LOOP_GUARD_OFF="1")
    out = run_entry(POLL_HOOK, tool_payload("tail -f x.log"), env)
    self.assertEqual(out["decision"], "force_ask")

  def test_unparsable_payload_fails_closed(self) -> None:
    out = run_entry(POLL_HOOK, "{nope", self.env)
    self.assertEqual(out["decision"], "deny")
    self.assertIn("[HOOK PAYLOAD]", out["reason"])


class PawlOscillationHookTest(unittest.TestCase):
  CONV = "hooks-test-osc"

  def setUp(self) -> None:
    super().setUp()
    self.tmp = tempfile.mkdtemp(prefix="pawl-osc-")
    self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
    self.env = {"PAWL_DATA": self.tmp}

  def _payload(self, tool: str, args: Dict[str, Any]) -> str:
    return json.dumps({
        "conversationId": self.CONV,
        "toolCall": {"name": tool, "args": args},
    })

  def test_third_identical_call_force_asks(self) -> None:
    raw = self._payload("view_file", {"AbsolutePath": "/a"})
    for _ in range(2):
      self.assertEqual(
          run_entry(OSC_HOOK, raw, self.env), {"decision": "allow"}
      )
    out = run_entry(OSC_HOOK, raw, self.env)
    self.assertEqual(out["decision"], "force_ask")
    self.assertTrue(
        out["reason"].startswith(
            "[PAWL loop] view_file repeated with identical args 3 times"
        )
    )
    self.assertTrue(out["reason"].endswith("Approve to continue."))
    rows = [
        json.loads(line)
        for line in (Path(self.tmp) / "denials.jsonl").read_text().splitlines()
    ]
    self.assertEqual(rows[0]["gate"], "OSCILLATION")
    self.assertEqual(rows[0]["outcome"], "force_ask")

  def test_two_gram_force_asks(self) -> None:
    a = self._payload("view_file", {"AbsolutePath": "/a"})
    b = self._payload("run_command", {"CommandLine": "ls"})
    for raw in (a, b, a):
      self.assertEqual(
          run_entry(OSC_HOOK, raw, self.env), {"decision": "allow"}
      )
    out = run_entry(OSC_HOOK, b, self.env)
    self.assertEqual(out["decision"], "force_ask")
    self.assertIn("(last 4 calls)", out["reason"])

  def test_changing_args_allow(self) -> None:
    for i in range(8):
      raw = self._payload("view_file", {"AbsolutePath": f"/f{i}"})
      self.assertEqual(
          run_entry(OSC_HOOK, raw, self.env), {"decision": "allow"}
      )

  def test_missing_conversation_and_bad_stdin_fail_open(self) -> None:
    env = dict(
        self.env,
        CONVERSATION_ID="",
        ANTIGRAVITY_CONVERSATION_ID="",
        JETSKI_CONVERSATION_ID="",
    )
    raw = json.dumps({"toolCall": {"name": "view_file", "args": {"p": 1}}})
    for _ in range(4):
      self.assertEqual(run_entry(OSC_HOOK, raw, env), {"decision": "allow"})
    self.assertEqual(
        run_entry(OSC_HOOK, "{nope", self.env), {"decision": "allow"}
    )
    self.assertFalse((Path(self.tmp) / "oscillation").exists())


class PawlStopHookTest(unittest.TestCase):
  CONV = "hooks-test-conv"

  def setUp(self) -> None:
    super().setUp()
    self.tmp = tempfile.mkdtemp(prefix="pawl_stop_test_")
    self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
    self.proc = Path(self.tmp) / "proc"
    self.proc.mkdir()
    (self.proc / "uptime").write_text("5000.0 1000.0\n")
    self.env = {
        "PAWL_DATA": self.tmp,
        "PAWL_IDLE_TASK_PROC_ROOT": str(self.proc),
    }

  def _add(self, pid: int, age_s: float, cmd: str) -> None:
    d = self.proc / str(pid)
    d.mkdir()
    (d / "environ").write_bytes(
        f"ANTIGRAVITY_CONVERSATION_ID={self.CONV}\0".encode()
    )
    start = int((5000.0 - age_s) * os.sysconf("SC_CLK_TCK"))
    filler = " ".join(["0"] * 16)
    (d / "stat").write_text(f"{pid} (bash) S 1 {pid} {filler} {start} 0 0 0\n")
    (d / "cmdline").write_bytes(cmd.replace(" ", "\0").encode() + b"\0")

  def test_neutral_tree_allows(self) -> None:
    self._add(4100, 30, "sleep 1")
    out = run_entry(
        STOP_HOOK, json.dumps({"conversationId": self.CONV}), self.env
    )
    self.assertEqual(out, {"decision": "allow"})

  def test_stale_task_blocks(self) -> None:
    self._add(4101, 1500, "sleep 99999")
    out = run_entry(
        STOP_HOOK, json.dumps({"conversationId": self.CONV}), self.env
    )
    self.assertEqual(out["decision"], "block")
    self.assertIn("[IDLE TASK] 1 background task(s)", out["reason"])
    self.assertIn("pid 4101 (25m): sleep 99999", out["reason"])

  def test_old_off_env_does_not_change_decision(self) -> None:
    self._add(4103, 1500, "sleep 99999")
    env = dict(self.env, PAWL_IDLE_TASK_GATE_OFF="1")
    out = run_entry(STOP_HOOK, json.dumps({"conversationId": self.CONV}), env)
    self.assertEqual(out["decision"], "block")

  def test_unparsable_payload_fails_open(self) -> None:
    self._add(4102, 1500, "sleep 99999")
    self.assertEqual(
        run_entry(STOP_HOOK, "{nope", self.env), {"decision": "allow"}
    )


if __name__ == "__main__":
  unittest.main()
