#!/usr/bin/env python3
"""Tests for the five guard-piece hook groups wired in hooks.json.

Each group must exist with its matcher and run its hooks/ entry; each entry
must answer one hit and one miss the way its piece does.

Run: python3 -m unittest guard_hooks_test -v (from hooks/).
"""

from __future__ import annotations

import json
import os
from pathlib import Path  # pylint: disable=g-importing-member
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from typing import Any, Dict
import unittest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
GROUPS = {
    "pawl-noop-edit-guard": (
        "replace_file_content|multi_replace_file_content",
        "pawl_noop_edit_hook.py",
    ),
    "pawl-zero-width-sanitizer": (
        "write_to_file|replace_file_content|multi_replace_file_content",
        "pawl_zero_width_hook.py",
    ),
    "pawl-conversation-fence": (".*", "pawl_fence_hook.py"),
    "pawl-reread-guard": (
        "view_file|run_command|run_shell_command",
        "pawl_reread_hook.py",
    ),
    "pawl-readonly-pass": (
        "run_command|run_shell_command",
        "pawl_readonly_hook.py",
    ),
}
ORDER = [
    "pawl-oscillation-breaker", "pawl-noop-edit-guard",
    "pawl-zero-width-sanitizer", "pawl-conversation-fence",
    "pawl-reread-guard", "pawl-readonly-pass", "pawl-idle-task-gate",
]


def call(tool: str, args: Dict[str, Any], **extra: Any) -> str:
  return json.dumps(dict(conversationId="own-1", turnId="t1",
                         toolCall={"name": tool, "args": args}, **extra))


class GuardHooksTest(unittest.TestCase):

  def setUp(self) -> None:
    super().setUp()
    self.tmp = Path(tempfile.mkdtemp(prefix="pawl-guards-"))
    self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
    self.store = self.tmp / "store"
    (self.store / "brain" / "sib-1").mkdir(parents=True)
    sqlite3.connect(self.store / "conversation_summaries.db").close()
    self.env = dict(os.environ, PAWL_DATA=str(self.tmp / "data"),
                    PAWL_CONVERSATION_ROOTS=str(self.store))
    self.hooks = json.loads((ROOT / "hooks.json").read_text())

  def entry(self, group: str) -> list:
    pre = self.hooks[group]["PreToolUse"]
    self.assertEqual(len(pre), 1)
    self.assertEqual(pre[0]["matcher"], GROUPS[group][0])
    argv = shlex.split(pre[0]["hooks"][0]["command"])
    self.assertEqual(argv[:2], ["python3", "-B"])
    self.assertEqual(argv[2], f"hooks/{GROUPS[group][1]}")
    return [sys.executable] + argv[1:]

  def run_group(self, group: str, raw: str) -> Dict[str, Any]:
    proc = subprocess.run(
        self.entry(group), input=raw, text=True, capture_output=True,
        env=self.env, cwd=str(ROOT), timeout=20, check=False,
    )
    self.assertEqual(proc.returncode, 0, proc.stderr)
    return json.loads(proc.stdout)

  def test_groups_sit_before_the_idle_task_gate_in_order(self) -> None:
    names = [n for n in self.hooks if n in ORDER]
    self.assertEqual(names, ORDER)

  def test_noop_edit_group(self) -> None:
    same = {"TargetFile": "/a", "TargetContent": "x", "ReplacementContent": "x"}
    out = self.run_group("pawl-noop-edit-guard",
                         call("replace_file_content", same))
    self.assertEqual(out["decision"], "deny")
    self.assertTrue(out["reason"].startswith("[PAWL no-op]"))
    edit = dict(same, ReplacementContent="y")
    self.assertEqual(self.run_group(
        "pawl-noop-edit-guard", call("replace_file_content", edit)),
                     {"decision": "allow"})

  def test_zero_width_group(self) -> None:
    dirty = {"TargetFile": "/a", "CodeContent": "a\u200bb"}
    out = self.run_group("pawl-zero-width-sanitizer",
                         call("write_to_file", dirty))
    cleaned = {"TargetFile": "/a", "CodeContent": "ab"}
    self.assertEqual(out, {"decision": "allow", "overwrite": cleaned})
    clean = {"TargetFile": "/a", "CodeContent": "ab"}
    self.assertEqual(self.run_group(
        "pawl-zero-width-sanitizer", call("write_to_file", clean)),
                     {"decision": "allow"})

  def test_conversation_fence_group(self) -> None:
    sib = str(self.store / "brain" / "sib-1" / "notes.md")
    out = self.run_group("pawl-conversation-fence",
                         call("view_file", {"AbsolutePath": sib}))
    self.assertEqual(out["decision"], "force_ask")
    self.assertTrue(out["reason"].startswith("[PAWL fence]"))
    own = str(self.store / "brain" / "own-1" / "notes.md")
    self.assertEqual(self.run_group(
        "pawl-conversation-fence", call("view_file", {"AbsolutePath": own})),
                     {"decision": "allow"})

  def test_reread_guard_group(self) -> None:
    skill = self.tmp / "SKILL.md"
    skill.write_text("# s\n" * 30)
    raw = call("view_file", {"AbsolutePath": str(skill)})
    outs = [self.run_group("pawl-reread-guard", raw)["decision"]
            for _ in range(6)]
    self.assertEqual(outs, ["allow"] * 5 + ["deny"])

  def test_readonly_pass_group(self) -> None:
    out = self.run_group("pawl-readonly-pass",
                         call("run_command", {"CommandLine": "git status"}))
    self.assertEqual(out, {"decision": "auto_approve"})
    out = self.run_group("pawl-readonly-pass",
                         call("run_command", {"CommandLine": "git push"}))
    self.assertEqual(out, {"decision": "allow"})


if __name__ == "__main__":
  unittest.main()
