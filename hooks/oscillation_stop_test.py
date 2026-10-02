#!/usr/bin/env python3
"""Tests for the oscillation-breaker Stop entry wired in hooks.json.

A cron or timer wakeup is its own turn, so the ring is cleared when a turn
ends; without that, three wakeups that each poll once read as a retry loop
(a reported false positive).

Run: python3 -m unittest oscillation_stop_test -v (from hooks/).
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
from typing import Any, Dict, List
import unittest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
GROUP = "pawl-oscillation-breaker"
CONV = "hooks-test-osc-stop"


def group_command(event: str) -> List[str]:
  """argv of the group's hook for `event`, python3 swapped for this python."""
  hooks = json.loads((ROOT / "hooks.json").read_text())
  entries = hooks[GROUP].get(event) or []
  assert len(entries) == 1, entries
  entry = entries[0]["hooks"][0] if "hooks" in entries[0] else entries[0]
  argv = shlex.split(entry["command"])
  assert argv[0] == "python3", argv
  return [sys.executable] + argv[1:]


class OscillationStopTest(unittest.TestCase):

  def setUp(self) -> None:
    super().setUp()
    self.tmp = tempfile.mkdtemp(prefix="pawl-osc-stop-")
    self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
    self.env = dict(os.environ, PAWL_DATA=self.tmp)

  def run_entry(self, argv: List[str], raw: str) -> Dict[str, Any]:
    proc = subprocess.run(
        argv, input=raw, text=True, capture_output=True, env=self.env,
        cwd=str(ROOT), timeout=20, check=False,
    )
    self.assertEqual(proc.returncode, 0, proc.stderr)
    return json.loads(proc.stdout)

  def test_group_stop_entry_clears_the_ring_between_turns(self) -> None:
    pre = group_command("PreToolUse")
    stop = group_command("Stop")
    poll = json.dumps({
        "conversationId": CONV,
        "toolCall": {"name": "run_command", "args": {"CommandLine": "st"}},
    })
    for _ in range(4):
      self.assertEqual(self.run_entry(pre, poll), {"decision": "allow"})
      out = self.run_entry(stop, json.dumps({"conversationId": CONV}))
      self.assertEqual(out, {"decision": "allow"})
    self.assertFalse(
        (Path(self.tmp) / "oscillation" / f"{CONV}.json").exists()
    )


if __name__ == "__main__":
  unittest.main()
