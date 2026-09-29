#!/usr/bin/env python3
"""Tests for hooks/pawl_hook.py.

Run: python3 -m unittest hooks_test -v (from hooks/).
"""

from __future__ import annotations

import datetime
import json
import os
from pathlib import Path  # pylint: disable=g-importing-member
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Optional
import unittest

HERE = Path(__file__).resolve().parent
HOOK = HERE / "pawl_hook.py"

SLOP = (
    "It is not just a migration, it is a fundamental shift in how we think"
    " about data. Lets dive in. Heres the kicker: the real question is not"
    " whether to migrate but when. In other words, the choice is clear. It is"
    " worth noting that this seamlessly delivers a game-changing result. To be"
    " honest, the most important thing is the paradigm. Think of it as a"
    " tapestry of possibilities. In conclusion, ultimately, this fundamentally"
    " reshapes the future of the team."
)


def run_hook(
    command: str,
    env_extra: Optional[Dict[str, str]] = None,
    raw: Optional[str] = None,
) -> Dict[str, Any]:
  """Runs pawl_hook.py on a run_command payload and parses its JSON."""
  if raw is not None:
    payload = raw
  else:
    payload = json.dumps(
        {"toolCall": {"name": "run_command", "args": {"CommandLine": command}}}
    )
  env = dict(os.environ)
  env.update(env_extra or {})
  proc = subprocess.run(
      [sys.executable, "-B", str(HOOK)],
      input=payload,
      text=True,
      capture_output=True,
      env=env,
      timeout=20,
      check=False,
  )
  return json.loads(proc.stdout)


def quiet_window_zone() -> str:
  """Returns an IANA zone whose wall clock is inside 22:00-07:00 right now.

  Picks an hour at least one hour away from both edges so a test that
  straddles the top of the hour cannot flip.
  """
  utc = datetime.datetime.now(datetime.timezone.utc)
  for offset in range(-12, 15):
    name = f"Etc/GMT{-offset:+d}"
    if (utc.hour + offset) % 24 in (23, 0, 1, 2, 3, 4, 5):
      return name
  raise AssertionError("no zone in the quiet window")


class PawlHookTest(unittest.TestCase):

  def setUp(self) -> None:
    super().setUp()
    self.tmp = tempfile.mkdtemp(prefix="pawl_test_")
    self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
    self.env = {
        "PAWL_DATA": self.tmp,
        "SEND_BUDGET_STATE_DIR": self.tmp,
        "SEND_BUDGET_OVERRIDE": "",
    }

  def test_pawl_data_drives_budget_state_dir(self) -> None:
    env = dict(os.environ)
    env.pop("SEND_BUDGET_STATE_DIR", None)
    env.update({"PAWL_DATA": self.tmp, "SEND_BUDGET_OVERRIDE": ""})
    command = 'gchat send --space spaces/A --text "hi"'
    payload = json.dumps(
        {"toolCall": {"name": "run_command", "args": {"CommandLine": command}}}
    )
    proc = subprocess.run(
        [sys.executable, "-B", str(HOOK)],
        input=payload,
        text=True,
        capture_output=True,
        env=env,
        timeout=20,
        check=False,
    )
    self.assertEqual(json.loads(proc.stdout)["decision"], "allow")
    state = json.loads((Path(self.tmp) / "send_budget.json").read_text())
    self.assertEqual(state["counts"]["chat_space"], 1)

  def test_spend_reason_carries_no_recipient_or_message_text(self) -> None:
    env = dict(self.env, PAWL_DISABLE="egress,prose")
    out = run_hook(
        'gchat mutate send-message --space spaces/TEAM --text "hello'
        ' alice@example.com"',
        env,
    )
    self.assertEqual(out["decision"], "allow")
    raw = (Path(self.tmp) / "send_budget.json").read_text()
    for secret in ("alice@example.com", "hello", "spaces/TEAM"):
      self.assertNotIn(secret, raw)
    self.assertEqual(
        json.loads(raw)["last_spend"]["chat_space"]["reason"],
        "gchat mutate send-message",
    )

  def test_non_send_allows_without_state(self) -> None:
    self.assertEqual(run_hook("ls -la", self.env)["decision"], "allow")
    self.assertFalse((Path(self.tmp) / "send_budget.json").exists())

  def test_clean_send_allows_and_spends(self) -> None:
    out = run_hook(
        'gchat send --space spaces/A --text "done, 4120 rows moved"', self.env
    )
    self.assertEqual(out["decision"], "allow")
    state = json.loads((Path(self.tmp) / "send_budget.json").read_text())
    self.assertEqual(state["counts"]["chat_space"], 1)

  def test_egress_denies_local_path(self) -> None:
    out = run_hook(
        'gchat send --space spaces/A --text "see /home/someone/x/"', self.env
    )
    self.assertEqual(out["decision"], "deny")
    self.assertIn("[PAWL egress]", out["reason"])

  def test_egress_ignores_the_tool_path(self) -> None:
    for exe in (
        "/opt/releases/some-vendor-chat-tools-2026/gchat",
        "/home/someone/bin/gchat",
    ):
      out = run_hook(
          f'{exe} mutate send-message --space "spaces/A" --text "hi"'
          " --markdown",
          self.env,
      )
      self.assertEqual(out["decision"], "allow", exe)

  def test_egress_ignores_redirects_after_the_send(self) -> None:
    out = run_hook(
        'gchat send --space spaces/A --text "hi" 2>&1 | tail -5', self.env
    )
    self.assertEqual(out["decision"], "allow")

  def test_egress_scans_the_file_a_message_is_read_from(self) -> None:
    src = Path(self.tmp) / "msg.txt"
    src.write_text("all clean here\n")
    for read in (f"$(cat {src})", f"$(< {src})"):
      out = run_hook(f'gchat send --space spaces/A --text "{read}"', self.env)
      self.assertEqual(out["decision"], "allow", read)
    src.write_text("see /home/someone/x/ for details\n")
    out = run_hook(
        f'gchat send --space spaces/A --text "$(cat {src})"', self.env
    )
    self.assertEqual(out["decision"], "deny")
    self.assertIn("home-dir", out["reason"])

  def test_egress_fails_closed_on_unreadable_message_source(self) -> None:
    out = run_hook(
        f'gchat send --space spaces/A --text "$(cat {self.tmp}/missing.txt)"',
        self.env,
    )
    self.assertEqual(out["decision"], "deny")
    self.assertIn("failing closed", out["reason"])

  def test_egress_reads_a_file_written_by_the_same_command(self) -> None:
    path = f"{self.tmp}/draft.txt"
    template = (
        f"cat <<'EOF' > {path}\n{{body}}\nEOF\n"
        f'gchat send --space spaces/A --text "$(cat {path})"'
    )
    out = run_hook(template.format(body="all clean"), self.env)
    self.assertEqual(out["decision"], "allow", out)
    out = run_hook(template.format(body="see /home/someone/x/"), self.env)
    self.assertEqual(out["decision"], "deny")
    self.assertIn("home-dir", out["reason"])

  def test_egress_denies_opaque_command_substitution(self) -> None:
    out = run_hook(
        'gchat send --space spaces/A --text "done at $(date)"', self.env
    )
    self.assertEqual(out["decision"], "deny")
    self.assertIn("substitution", out["reason"])

  def test_egress_still_scans_recipients(self) -> None:
    out = run_hook(
        "gmail mutate send --to a@b.io --subject hi --body ok", self.env
    )
    self.assertEqual(out["decision"], "deny")
    self.assertIn("email", out["reason"])

  def test_heredoc_that_mentions_a_send_is_not_a_send(self) -> None:
    cmd = (
        "cat <<'EOF' > /tmp/briefing.txt\n"
        "next: gchat mutate send-message --space spaces/A --text hi\n"
        "EOF\n"
    )
    self.assertEqual(run_hook(cmd, self.env)["decision"], "allow")
    self.assertFalse((Path(self.tmp) / "send_budget.json").exists())

  def test_help_and_greps_are_not_sends(self) -> None:
    for cmd in (
        "gchat mutate send-message --help",
        "egrep -n 'gchat.*send' /tmp/notes.txt",
        "diff /tmp/a/SKILL.md ~/.config/skills/pawl-send-gates/SKILL.md",
    ):
      self.assertEqual(run_hook(cmd, self.env)["decision"], "allow", cmd)
    self.assertFalse((Path(self.tmp) / "send_budget.json").exists())

  def test_prose_denies_slop(self) -> None:
    out = run_hook(f'gchat send --space spaces/A --text "{SLOP}"', self.env)
    self.assertEqual(out["decision"], "deny")
    self.assertIn("[PAWL prose]", out["reason"])

  def test_budget_below_ceiling_allows(self) -> None:
    env = dict(self.env, SEND_BUDGET_CEILINGS='{"chat_space": 2}')
    self.assertEqual(
        run_hook('gchat send --space spaces/A --text "one"', env)["decision"],
        "allow",
    )
    self.assertEqual(
        run_hook('gchat send --space spaces/A --text "two"', env)["decision"],
        "allow",
    )
    state = json.loads((Path(self.tmp) / "send_budget.json").read_text())
    self.assertEqual(state["counts"]["chat_space"], 2)
    self.assertFalse(state["last_spend"]["chat_space"]["overridden"])

  def test_budget_at_ceiling_force_asks_and_logs_human_override(self) -> None:
    env = dict(self.env, SEND_BUDGET_CEILINGS='{"chat_space": 1}')
    self.assertEqual(
        run_hook('gchat send --space spaces/A --text "one"', env)["decision"],
        "allow",
    )
    out = run_hook('gchat send --space spaces/A --text "two"', env)
    self.assertEqual(out["decision"], "force_ask")
    self.assertEqual(
        out["reason"],
        "[PAWL budget] chat_space: daily ceiling reached: 1/1 (1/1 today)."
        " Approve to send anyway; the send is logged as a human override.",
    )
    state = json.loads((Path(self.tmp) / "send_budget.json").read_text())
    self.assertEqual(state["counts"]["chat_space"], 2)
    self.assertTrue(state["last_spend"]["chat_space"]["overridden"])
    self.assertEqual(state["denials"]["chat_space"], 1)

  def test_quiet_window_force_asks_with_the_piece_reason(self) -> None:
    """Owner DMs in the quiet window prompt even with budget left."""
    env = dict(
        self.env,
        PAWL_DISABLE="egress,prose",
        SEND_BUDGET_OWNER_SPACE="spaces/OWNER",
        SEND_BUDGET_TZ=quiet_window_zone(),
    )
    out = run_hook('gchat send --space spaces/OWNER --text "hi"', env)
    self.assertEqual(out["decision"], "force_ask")
    self.assertEqual(
        out["reason"],
        "[PAWL budget] dm_owner: quiet window 22:00-07:00 (0/12 today)."
        " Approve to send anyway; the send is logged as a human override.",
    )
    state = json.loads((Path(self.tmp) / "send_budget.json").read_text())
    self.assertEqual(state["counts"]["dm_owner"], 1)
    self.assertTrue(state["last_spend"]["dm_owner"]["overridden"])
    self.assertEqual(state["denials"]["dm_owner"], 1)

  def test_override_env_does_not_change_the_hook_decision(self) -> None:
    env = dict(self.env, SEND_BUDGET_CEILINGS='{"chat_space": 1}')
    run_hook('gchat send --space spaces/A --text "one"', env)
    out = run_hook(
        'gchat send --space spaces/A --text "two"',
        dict(env, SEND_BUDGET_OVERRIDE="1"),
    )
    self.assertEqual(out["decision"], "force_ask")

  def test_override_prefix_in_command_does_not_change_the_hook_decision(
      self,
  ) -> None:
    env = dict(self.env, SEND_BUDGET_CEILINGS='{"chat_space": 1}')
    run_hook('gchat send --space spaces/A --text "one"', env)
    for cmd in (
        'SEND_BUDGET_OVERRIDE=1 gchat send --space spaces/A --text "two"',
        'env SEND_BUDGET_OVERRIDE=1 gchat send --space spaces/A --text "two"',
        'gchat send --space spaces/A --text "SEND_BUDGET_OVERRIDE=1 two"',
    ):
      self.assertEqual(run_hook(cmd, env)["decision"], "force_ask", cmd)

  def test_disable_env_skips_gate(self) -> None:
    env = dict(self.env, PAWL_DISABLE="egress")
    out = run_hook(
        'gchat send --space spaces/A --text "see /home/someone/x/"', env
    )
    self.assertEqual(out["decision"], "allow")

  def test_broken_rules_fail_closed(self) -> None:
    (Path(self.tmp) / "egress_rules.json").write_text("{not json")
    out = run_hook('gchat send --space spaces/A --text "hi"', self.env)
    self.assertEqual(out["decision"], "deny")
    self.assertIn("failing closed", out["reason"])

  def test_garbage_stdin_allows(self) -> None:
    self.assertEqual(
        run_hook("", self.env, raw="not json")["decision"], "allow"
    )

  def test_tool_input_shape(self) -> None:
    raw = json.dumps({
        "tool_name": "run_command",
        "tool_input": {
            "command": 'gmail send --to a@b.io --body "see /tmp/secret"'
        },
    })
    out = run_hook("", self.env, raw=raw)
    self.assertEqual(out["decision"], "deny")

  def _events(self) -> List[Dict[str, Any]]:
    path = Path(self.tmp) / "gate_events.jsonl"
    if not path.exists():
      return []
    return [
        json.loads(line)
        for line in path.read_text().splitlines()
        if line.strip()
    ]

  def test_every_evaluated_gate_records_an_event(self) -> None:
    run_hook('gchat send --space spaces/A --text "hi"', self.env)
    gates = [(e["gate"], e["decision"]) for e in self._events()]
    self.assertEqual(
        gates, [("egress", "allow"), ("prose", "allow"), ("budget", "allow")]
    )
    run_hook(
        'gchat send --space spaces/A --text "see /home/someone/x/"', self.env
    )
    self.assertEqual(
        self._events()[-1],
        {**self._events()[-1], "gate": "egress", "decision": "deny"},
    )
    self.assertEqual(len(self._events()), 4)

  def test_ask_is_recorded_on_budget_event_with_override(self) -> None:
    env = dict(self.env, SEND_BUDGET_CEILINGS='{"chat_space": 1}')
    run_hook('gchat send --space spaces/A --text "one"', env)
    out = run_hook('gchat send --space spaces/A --text "two"', env)
    self.assertEqual(out["decision"], "force_ask")
    budget = [e for e in self._events() if e["gate"] == "budget"]
    self.assertEqual(
        [(e["decision"], e["override"]) for e in budget],
        [("allow", False), ("ask", True)],
    )
    self.assertTrue(
        all(not e["override"] for e in self._events() if e["gate"] != "budget")
    )

  def test_stats_reports_deny_rate_per_gate(self) -> None:
    run_hook('gchat send --space spaces/A --text "hi"', self.env)
    run_hook(
        'gchat send --space spaces/A --text "see /home/someone/x/"', self.env
    )
    proc = subprocess.run(
        [sys.executable, str(HOOK), "stats"],
        capture_output=True,
        text=True,
        env=self.env,
        timeout=30,
        check=False,
    )
    self.assertEqual(proc.returncode, 0, proc.stderr)
    lines = proc.stdout.splitlines()[1:]
    rows = {line.split()[0]: line.split() for line in lines}
    self.assertEqual(rows["egress"][1:], ["1", "1", "0", "0.5"])
    self.assertEqual(rows["budget"][1:], ["1", "0", "0", "0.0"])


GIT_HOOK = HERE / "pawl_git_hook.py"
POLL_HOOK = HERE / "pawl_poll_hook.py"
STOP_HOOK = HERE / "pawl_stop_hook.py"
OSC_HOOK = HERE / "pawl_oscillation_hook.py"


def run_entry(
    hook: Path, raw: str, env_extra: Optional[Dict[str, str]] = None
) -> Dict[str, Any]:
  """Runs one hook entry script on raw stdin and parses its JSON."""
  env = dict(os.environ)
  env.update(env_extra or {})
  proc = subprocess.run(
      [sys.executable, "-B", str(hook)],
      input=raw,
      text=True,
      capture_output=True,
      env=env,
      timeout=20,
      check=False,
  )
  if proc.returncode != 0:
    raise AssertionError(proc.stderr)
  return json.loads(proc.stdout)


def tool_payload(command: str, cwd: str | None = None) -> str:
  args = {"CommandLine": command}
  if cwd:
    args["Cwd"] = cwd
  return json.dumps({"toolCall": {"name": "run_command", "args": args}})


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
