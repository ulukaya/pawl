#!/usr/bin/env python3
"""End-to-end tests: the shipped Claude Code and Codex hook configs.

Each case reads the command out of hooks/hooks.json (Claude Code) or
hooks/codex.json (Codex), runs it through a shell from an unrelated project
directory with the plugin-root variable the harness exports, and checks the
stdout and exit code against that harness's contract. This is what catches a
relative script path (Python exits 2, which both harnesses read as a block on
every call) and an "allow" that skips the user's permission prompt.

Run: python3 -m pytest -q e2e_test.py (from hooks/).
"""

from __future__ import annotations

import json
import os
from pathlib import Path  # pylint: disable=g-importing-member
import shlex
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Dict, Optional
import unittest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CONFIGS = {
    "claude": (HERE / "hooks.json", "CLAUDE_PLUGIN_ROOT"),
    "codex": (HERE / "codex.json", "PLUGIN_ROOT"),
}


def shipped_command(harness: str, event: str) -> str:
  """The one command the shipped config runs for `event`, python swapped."""
  path, _ = CONFIGS[harness]
  groups = json.loads(path.read_text())["hooks"][event]
  assert len(groups) == 1 and len(groups[0]["hooks"]) == 1, groups
  assert "matcher" not in groups[0], "pawl's gates watch every tool"
  command = groups[0]["hooks"][0]["command"]
  assert command.startswith("python3 "), command
  return shlex.quote(sys.executable) + command[len("python3"):]


class HarnessCase(unittest.TestCase):
  """Runs shipped hook commands for one harness in a scratch project."""

  HARNESS = ""

  def setUp(self) -> None:
    super().setUp()
    self.tmp = Path(tempfile.mkdtemp(prefix=f"pawl-e2e-{self.HARNESS}-"))
    self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
    self.project = self.tmp / "project"
    self.project.mkdir()
    var = CONFIGS[self.HARNESS][1]
    self.env = dict(os.environ, PAWL_DATA=str(self.tmp / "data"),
                    PAWL_GIT_PROTECTED_ROOTS=str(self.project),
                    PAWL_CONVERSATION_ROOTS=str(self.tmp / "stores"),
                    PAWL_HARNESS="", PAWL_DISABLE="")
    self.env[var] = str(ROOT)
    self.env.pop("CLAUDE_PLUGIN_ROOT" if var == "PLUGIN_ROOT" else
                 "PLUGIN_ROOT", None)

  def payload(self, tool: str, tool_input: Dict[str, Any],
              **extra: Any) -> Dict[str, Any]:
    raise NotImplementedError

  def run_hook(self, event: str, raw: str) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        ["/bin/sh", "-c", shipped_command(self.HARNESS, event)],
        input=raw, text=True, capture_output=True, env=self.env,
        cwd=str(self.project), timeout=30, check=False,
    )
    self.assertEqual(proc.returncode, 0, proc.stderr)
    self.assertNotIn("Traceback", proc.stderr)
    return proc

  def pre(self, tool: str, tool_input: Dict[str, Any],
          **extra: Any) -> Optional[Dict[str, Any]]:
    """The hookSpecificOutput of one PreToolUse call; None when silent."""
    raw = json.dumps(self.payload(tool, tool_input, **extra))
    out = self.run_hook("PreToolUse", raw).stdout
    if not out:
      return None
    spec = json.loads(out)["hookSpecificOutput"]
    self.assertEqual(spec["hookEventName"], "PreToolUse")
    return spec

  def stop(self, **extra: Any) -> Optional[Dict[str, Any]]:
    raw = dict(self.payload("", {}), hook_event_name="Stop",
               stop_hook_active=False)
    raw.update(extra)
    for key in ("tool_name", "tool_input", "tool_use_id"):
      raw.pop(key, None)
    out = self.run_hook("Stop", json.dumps(raw)).stdout
    return json.loads(out) if out else None


class ClaudeCodeTest(HarnessCase):

  HARNESS = "claude"

  def payload(self, tool: str, tool_input: Dict[str, Any],
              **extra: Any) -> Dict[str, Any]:
    base = {
        "session_id": "claude-e2e", "cwd": str(self.project),
        "transcript_path": str(self.tmp / "claude-e2e.jsonl"),
        "permission_mode": "default", "hook_event_name": "PreToolUse",
        "tool_name": tool, "tool_input": tool_input, "tool_use_id": "toolu_1",
    }
    return dict(base, **extra)

  def test_ordinary_calls_are_silent(self) -> None:
    self.assertIsNone(self.pre("Bash", {"command": "make build"}))
    self.assertIsNone(self.pre("Read", {"file_path": str(self.project / "a")}))
    self.assertIsNone(self.pre("Edit", {"file_path": "/a", "old_string": "x",
                                        "new_string": "y"}))
    self.assertIsNone(self.pre("WebFetch", {"url": "https://x.example"}))

  def test_destructive_git_asks_the_user(self) -> None:
    spec = self.pre("Bash", {"command": "git reset --hard"})
    self.assertEqual(spec["permissionDecision"], "ask")
    self.assertTrue(spec["permissionDecisionReason"].startswith("[PAWL git]"))

  def test_force_push_and_unmerged_branch_delete_ask_the_user(self) -> None:
    for cmd in ("git push --force origin main", "git branch -D feature"):
      spec = self.pre("Bash", {"command": cmd})
      self.assertEqual(spec["permissionDecision"], "ask", cmd)
      self.assertTrue(spec["permissionDecisionReason"].startswith(
          "[PAWL git] [DESTRUCTIVE GIT] `git "), cmd)
    self.assertIsNone(self.pre("Bash", {"command": "git push origin main"}))

  def test_home_wipe_hidden_in_a_script_is_denied(self) -> None:
    self.env["HOME"] = str(self.tmp / "home")
    (self.project / "clean.sh").write_text("rm -rf build\nrm -rf ~/\n")
    spec = self.pre("Bash", {"command": "bash clean.sh"})
    self.assertEqual(spec["permissionDecision"], "deny")
    self.assertTrue(spec["permissionDecisionReason"].startswith(
        "[PAWL blast] `bash clean.sh` runs `rm -rf ~/`"))
    spec = self.pre("Bash", {"command": "rm -rf ~/elsewhere"})
    self.assertEqual(spec["permissionDecision"], "ask")
    self.assertIsNone(self.pre("Bash", {"command": "rm -rf build"}))

  def test_poll_loop_asks_the_user(self) -> None:
    spec = self.pre("Bash", {"command": "tail -f server.log"})
    self.assertEqual(spec["permissionDecision"], "ask")
    self.assertIn("[PAWL poll]", spec["permissionDecisionReason"])

  def test_noop_edit_is_denied_in_claude_words(self) -> None:
    spec = self.pre("Edit", {"file_path": "/app/x.py", "old_string": "same",
                             "new_string": "same"})
    self.assertEqual(spec["permissionDecision"], "deny")
    reason = spec["permissionDecisionReason"]
    self.assertTrue(reason.startswith("[PAWL no-op]"))
    self.assertIn("Read the region", reason)
    self.assertIn("new_string equals old_string", reason)
    self.assertNotIn("view_file", reason)
    self.assertNotIn("TargetContent", reason)

  def test_rewriting_a_file_with_its_own_bytes_is_denied(self) -> None:
    target = self.project / "app.py"
    target.write_text("x = 1\n")
    spec = self.pre("Write", {"file_path": str(target), "content": "x = 1\n"})
    self.assertEqual(spec["permissionDecision"], "deny")
    reason = spec["permissionDecisionReason"]
    self.assertTrue(reason.startswith("[PAWL no-op] Write on app.py:"))
    self.assertIn("Read it", reason)
    self.assertIsNone(self.pre("Write", {"file_path": str(target),
                                         "content": "x = 2\n"}))

  def test_zero_width_write_is_rewritten_without_approval(self) -> None:
    spec = self.pre("Write", {"file_path": "/app/x.py",
                              "content": "a​b"})
    self.assertNotIn("permissionDecision", spec)
    self.assertEqual(spec["updatedInput"],
                     {"file_path": "/app/x.py", "content": "ab"})

  def test_read_only_command_is_approved(self) -> None:
    spec = self.pre("Bash", {"command": "git status"})
    self.assertEqual(spec["permissionDecision"], "allow")

  def test_unsandboxed_retry_is_never_auto_approved(self) -> None:
    self.assertIsNone(self.pre("Bash", {"command": "git status",
                                        "dangerouslyDisableSandbox": True}))

  def test_third_identical_call_asks_and_stop_clears_it(self) -> None:
    call = {"command": "pytest -q tests/test_x.py"}
    self.assertIsNone(self.pre("Bash", call))
    self.assertIsNone(self.pre("Bash", dict(call, description="again")))
    spec = self.pre("Bash", call)
    self.assertEqual(spec["permissionDecision"], "ask")
    self.assertIn("[PAWL loop]", spec["permissionDecisionReason"])
    self.assertIsNone(self.stop())
    self.assertIsNone(self.pre("Bash", call))

  def test_parallel_subagents_running_one_command_are_not_a_loop(
      self) -> None:
    call = {"command": "make build"}
    for agent in ("agent-a", "agent-b", "agent-c"):
      self.assertIsNone(self.pre("Bash", call, agent_id=agent,
                                 agent_type="Explore"))
    self.assertIsNone(self.pre("Bash", call))

  def test_loop_ask_outranks_read_only_approval(self) -> None:
    for _ in range(2):
      self.assertEqual(self.pre("Bash", {"command": "ls"})[
          "permissionDecision"], "allow")
    self.assertEqual(self.pre("Bash", {"command": "ls"})["permissionDecision"],
                     "ask")

  def test_skill_reread_denied_until_the_turn_ends(self) -> None:
    self.env["PAWL_DISABLE"] = "loop"  # identical reads also trip the ring
    skill = self.project / "SKILL.md"
    skill.write_text("# s\n" * 30)
    read = {"file_path": str(skill)}
    for _ in range(5):
      self.assertIsNone(self.pre("Read", read))
    spec = self.pre("Read", read)
    self.assertEqual(spec["permissionDecision"], "deny")
    self.assertIn("[PAWL reread]", spec["permissionDecisionReason"])
    self.stop()
    self.assertIsNone(self.pre("Read", read))

  def test_send_with_a_local_path_is_denied(self) -> None:
    spec = self.pre("Bash", {"command": 'gchat send --space spaces/A --text'
                                        ' "see /home/someone/x/"'})
    self.assertEqual(spec["permissionDecision"], "deny")
    self.assertIn("[PAWL egress]", spec["permissionDecisionReason"])

  def test_reading_another_sessions_transcript_asks(self) -> None:
    cfg = self.tmp / "claude-config"
    self.env["CLAUDE_CONFIG_DIR"] = str(cfg)
    project = cfg / "projects" / "-work"
    spec = self.pre("Read", {"file_path": str(project / "other-session.jsonl")})
    self.assertEqual(spec["permissionDecision"], "ask")
    self.assertIn("[PAWL fence]", spec["permissionDecisionReason"])
    self.assertIsNone(self.pre("Read", {
        "file_path": str(project / "claude-e2e" / "tool-results" / "r.txt")}))

  def test_disable_env_turns_a_gate_off(self) -> None:
    self.env["PAWL_DISABLE"] = "git"
    self.assertIsNone(self.pre("Bash", {"command": "git reset --hard"}))

  def test_unreadable_stdin_fails_closed(self) -> None:
    spec = json.loads(self.run_hook("PreToolUse", "{nope").stdout)
    self.assertEqual(spec["hookSpecificOutput"]["permissionDecision"], "deny")

  def test_quiet_stop_is_silent(self) -> None:
    self.assertIsNone(self.stop())

  def test_stop_blocks_once_on_a_background_wait(self) -> None:
    tasks = [{"id": "b7", "type": "shell", "status": "running",
              "description": "follow log", "command": "tail -f app.log"},
             {"id": "b8", "type": "shell", "status": "running",
              "description": "build", "command": "npm run build"}]
    out = self.stop(background_tasks=tasks)
    self.assertEqual(out["decision"], "block")
    self.assertIn("task b7: tail -f app.log", out["reason"])
    self.assertIn("TaskStop", out["reason"])
    self.assertNotIn("manage_task", out["reason"])
    self.assertIsNone(self.stop(background_tasks=tasks, stop_hook_active=True))


class CodexTest(HarnessCase):

  HARNESS = "codex"

  def payload(self, tool: str, tool_input: Dict[str, Any],
              **extra: Any) -> Dict[str, Any]:
    base = {
        "session_id": "codex-e2e", "turn_id": "turn-1",
        "transcript_path": str(self.tmp / "rollout-codex-e2e.jsonl"),
        "cwd": str(self.project), "hook_event_name": "PreToolUse",
        "model": "m", "permission_mode": "default", "tool_name": tool,
        "tool_input": tool_input, "tool_use_id": "call_1",
    }
    return dict(base, **extra)

  def test_ordinary_and_read_only_calls_are_silent(self) -> None:
    self.assertIsNone(self.pre("Bash", {"command": "make build"}))
    self.assertIsNone(self.pre("Bash", {"command": "git status"}))

  def test_destructive_git_is_denied_with_the_reason(self) -> None:
    spec = self.pre("Bash", {"command": "git reset --hard"})
    self.assertEqual(spec["permissionDecision"], "deny")
    reason = spec["permissionDecisionReason"]
    self.assertTrue(reason.startswith("[PAWL git]"))
    self.assertIn("Codex hooks cannot ask", reason)
    self.assertNotIn("Approve to run anyway", reason)

  def test_poll_loop_is_denied(self) -> None:
    spec = self.pre("Bash", {"command": "while true; do sleep 5; done"})
    self.assertEqual(spec["permissionDecision"], "deny")

  def test_quiet_stop_is_silent(self) -> None:
    self.assertIsNone(self.stop())

  def test_noop_patch_is_denied(self) -> None:
    patch = ("*** Begin Patch\n*** Update File: a.py\n@@\n-x\n+x\n"
             "*** End Patch\n")
    spec = self.pre("apply_patch", {"command": patch})
    self.assertEqual(spec["permissionDecision"], "deny")
    self.assertIn("[PAWL no-op] apply_patch on a.py",
                  spec["permissionDecisionReason"])

  def test_zero_width_patch_is_rewritten(self) -> None:
    patch = ("*** Begin Patch\n*** Add File: a.py\n+a\u200bb\n"
             "*** End Patch\n")
    spec = self.pre("apply_patch", {"command": patch})
    self.assertEqual(spec["permissionDecision"], "allow")
    self.assertEqual(spec["updatedInput"],
                     {"command": patch.replace("\u200b", "")})

  def test_blast_asks_become_denies_with_the_reason(self) -> None:
    self.env["HOME"] = str(self.tmp / "home")
    spec = self.pre("Bash", {"command": "rm -rf ~/elsewhere"})
    self.assertEqual(spec["permissionDecision"], "deny")
    self.assertIn("[PAWL blast]", spec["permissionDecisionReason"])
    self.assertIn("Codex hooks cannot ask", spec["permissionDecisionReason"])
    spec = self.pre("Bash", {"command": "python3 -c \"import shutil, os;"
                                        " shutil.rmtree(os.path.expanduser('~'))\""})
    self.assertEqual(spec["permissionDecision"], "deny")

  def test_third_identical_call_is_denied(self) -> None:
    for _ in range(2):
      self.assertIsNone(self.pre("Bash", {"command": "make test"}))
    spec = self.pre("Bash", {"command": "make test"})
    self.assertEqual(spec["permissionDecision"], "deny")
    self.assertIn("[PAWL loop]", spec["permissionDecisionReason"])


if __name__ == "__main__":
  unittest.main()
