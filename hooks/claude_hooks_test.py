#!/usr/bin/env python3
"""Integration tests for pawl hooks invoked by Claude Code.

Verifies that Claude Code tool invocations (Bash, Edit, Write, Read) trigger
the gates, return exit code 2 with stderr on denial, exit code 0 on allow, and
updatedInput on sanitized writes.

Run: python3 -m unittest claude_hooks_test -v (from hooks/).
"""

from __future__ import annotations

import json
import os
from pathlib import Path  # pylint: disable=g-importing-member
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent


def run_claude_hook(
    hook_name: str, payload: dict, env_extra: dict | None = None
) -> subprocess.CompletedProcess[str]:
  hook_path = HERE / hook_name
  env = dict(os.environ)
  if env_extra:
    env.update(env_extra)
  return subprocess.run(
      [sys.executable, "-B", str(hook_path)],
      input=json.dumps(payload),
      text=True,
      capture_output=True,
      env=env,
      timeout=15,
      check=False,
  )


class ClaudeHooksIntegrationTest(unittest.TestCase):

  def setUp(self) -> None:
    super().setUp()
    self.tmp = tempfile.mkdtemp(prefix="pawl_claude_")
    self.env = {
        "PAWL_DATA": self.tmp,
        "PAWL_GIT_PROTECTED_ROOTS": self.tmp,
    }

  def test_git_hook_blocks_destructive_bash(self) -> None:
    payload = {
        "session_id": "claude-s1",
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "git reset --hard", "cwd": self.tmp},
    }
    proc = run_claude_hook("pawl_git_hook.py", payload, self.env)
    self.assertEqual(proc.returncode, 2)
    self.assertIn("[PAWL git]", proc.stderr)
    out = json.loads(proc.stdout)
    self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")

  def test_git_hook_allows_benign_bash(self) -> None:
    payload = {
        "session_id": "claude-s1",
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "git status", "cwd": self.tmp},
    }
    proc = run_claude_hook("pawl_git_hook.py", payload, self.env)
    self.assertEqual(proc.returncode, 0)
    out = json.loads(proc.stdout)
    self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "allow")

  def test_poll_hook_blocks_tail_f(self) -> None:
    payload = {
        "session_id": "claude-s1",
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "tail -f server.log"},
    }
    proc = run_claude_hook("pawl_poll_hook.py", payload, self.env)
    self.assertEqual(proc.returncode, 2)
    self.assertIn("[PAWL poll]", proc.stderr)
    out = json.loads(proc.stdout)
    self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")

  def test_noop_edit_hook_blocks_identical_edit(self) -> None:
    payload = {
        "session_id": "claude-s1",
        "hook_event_name": "PreToolUse",
        "tool_name": "Edit",
        "tool_input": {
            "file_path": "/app/code.py",
            "old_string": "same",
            "new_string": "same",
        },
    }
    proc = run_claude_hook("pawl_noop_edit_hook.py", payload, self.env)
    self.assertEqual(proc.returncode, 2)
    self.assertIn("[PAWL no-op]", proc.stderr)
    out = json.loads(proc.stdout)
    self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")

  def test_noop_edit_hook_allows_real_edit(self) -> None:
    payload = {
        "session_id": "claude-s1",
        "hook_event_name": "PreToolUse",
        "tool_name": "Edit",
        "tool_input": {
            "file_path": "/app/code.py",
            "old_string": "before",
            "new_string": "after",
        },
    }
    proc = run_claude_hook("pawl_noop_edit_hook.py", payload, self.env)
    self.assertEqual(proc.returncode, 0)
    out = json.loads(proc.stdout)
    self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "allow")

  def test_zero_width_sanitizer_cleans_claude_write(self) -> None:
    payload = {
        "session_id": "claude-s1",
        "hook_event_name": "PreToolUse",
        "tool_name": "Write",
        "tool_input": {
            "file_path": "/app/code.py",
            "content": "hello\u200bworld",
        },
    }
    proc = run_claude_hook("pawl_zero_width_hook.py", payload, self.env)
    self.assertEqual(proc.returncode, 0)
    out = json.loads(proc.stdout)
    self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "allow")
    self.assertEqual(
        out["hookSpecificOutput"]["updatedInput"],
        {"file_path": "/app/code.py", "content": "helloworld"},
    )

  def test_readonly_pass_auto_approves_cat(self) -> None:
    payload = {
        "session_id": "claude-s1",
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "cat README.md"},
    }
    proc = run_claude_hook("pawl_readonly_hook.py", payload, self.env)
    self.assertEqual(proc.returncode, 0)
    out = json.loads(proc.stdout)
    self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "allow")

  def test_oscillation_breaker_blocks_repeated_bash(self) -> None:
    payload = {
        "session_id": "claude-loop-1",
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "pytest test_foo.py"},
    }
    run_claude_hook("pawl_oscillation_hook.py", payload, self.env)
    run_claude_hook("pawl_oscillation_hook.py", payload, self.env)
    proc = run_claude_hook("pawl_oscillation_hook.py", payload, self.env)
    self.assertEqual(proc.returncode, 2)
    self.assertIn("[PAWL loop]", proc.stderr)
    out = json.loads(proc.stdout)
    self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")


if __name__ == "__main__":
  unittest.main()
