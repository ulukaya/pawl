#!/usr/bin/env python3
"""Tests for background-task polls and ring resets in oscillation_breaker.

Polls (an earlier change): intent fields and a growing view_file EndLine must not
make a repeat look new; manage_task status on one task counts.
Resets (an earlier change, a reported false positive): a schedule call and the end of a turn
clear the ring, so cron ticks never add up; an idle expiry is opt-in only, so
a slow retry loop inside one turn still trips.

Every test carries at least one assertion that fails on the tree before these
changes.

Run: python3 -m pytest -q test_polls_and_resets.py
"""

# pylint: disable=redefined-outer-name,unused-argument

from __future__ import annotations

import json
import os
from pathlib import Path  # pylint: disable=g-importing-member
import subprocess
import sys
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# pylint: disable=g-import-not-at-top,g-bad-import-order
import oscillation_breaker as ob  # noqa: E402
import pytest  # noqa: E402

# pylint: enable=g-import-not-at-top

HOOK = HERE / "oscillation_breaker_hook.py"
CONV = "osc-poll-conv"
LOG = "/work/build/task-7.log"
NOTIFY = "report when they finish"
POLL = {"Action": "status", "TaskId": "task-7"}


@pytest.fixture(autouse=True)
def data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
  monkeypatch.setenv("PAWL_DATA", str(tmp_path))
  monkeypatch.delenv("PAWL_OSCILLATION_WINDOW", raising=False)
  monkeypatch.delenv("PAWL_OSCILLATION_IDLE_S", raising=False)
  return tmp_path


def calls(tool: str, args_list: List[Dict[str, Any]]) -> List[Optional[str]]:
  return [ob.observe(CONV, tool, args) for args in args_list]


def run_hook(raw: str, *argv: str) -> Dict[str, Any]:
  proc = subprocess.run(
      [sys.executable, "-B", str(HOOK), *argv],
      input=raw, text=True, capture_output=True, env=dict(os.environ),
      timeout=20, check=False,
  )
  assert proc.returncode == 0, proc.stderr
  return json.loads(proc.stdout)


def tool_call(tool: str, args: Dict[str, Any]) -> str:
  return json.dumps(
      {"conversationId": CONV, "toolCall": {"name": tool, "args": args}}
  )


def end_turn(conv: str = CONV) -> Dict[str, Any]:
  return run_hook(json.dumps({"conversationId": conv}), "stop")


# --- normalization ------------------------------------------------------------


def test_reworded_intent_fields_still_count_as_a_repeat() -> None:
  out = calls("run_command", [
      {"CommandLine": "make", "toolSummary": f"try {i}", "toolAction": f"a{i}"}
      for i in range(3)
  ])
  assert out[:2] == [None, None]
  assert out[2] and out[2].startswith("[PAWL loop] run_command repeated")


def test_view_file_end_line_dropped_only_when_start_line_is_set() -> None:
  growing = [{"AbsolutePath": LOG, "StartLine": 1, "EndLine": e}
             for e in (100, 160, 230)]
  assert calls("view_file", growing)[2] is not None
  ob.reset_ring(CONV)
  paging = [{"AbsolutePath": LOG, "StartLine": s, "EndLine": s + 99}
            for s in (1, 101, 201)]
  assert calls("view_file", paging) == [None, None, None]
  ob.reset_ring(CONV)
  end_only = [{"AbsolutePath": LOG, "EndLine": e} for e in (100, 160, 230)]
  assert calls("view_file", end_only) == [None, None, None]


# --- manage_task --------------------------------------------------------------


def test_three_status_checks_on_one_task_prompt() -> None:
  polls = [dict(POLL, toolSummary=f"s{i}") for i in range(3)]
  out = calls("manage_task", polls)
  assert out[:2] == [None, None]
  assert out[2] and "manage_task repeated" in out[2]


def test_other_task_actions_and_other_tasks_stay_exempt() -> None:
  for action in ("list", "kill", "send_input"):
    assert calls("manage_task", [{"Action": action, "TaskId": "t"}] * 4) == [
        None
    ] * 4
  assert calls("manage_task", [{"Action": "status"}] * 4) == [None] * 4
  assert not ob.load_ring(CONV)
  other = [{"Action": "status", "TaskId": f"t{i}"} for i in range(4)]
  assert calls("manage_task", other) == [None] * 4
  assert calls("manage_task", [POLL] * 3)[2] is not None


def test_poll_reason_says_background_tasks_notify() -> None:
  assert NOTIFY in calls("manage_task", [POLL] * 3)[2]
  ob.reset_ring(CONV)
  log_read = calls("view_file", [{"AbsolutePath": LOG, "StartLine": 1}] * 3)
  assert NOTIFY in log_read[2]
  ob.reset_ring(CONV)
  plain = calls("grep_search", [{"Query": "x"}] * 3)
  assert plain[2] and NOTIFY not in plain[2]


def test_task_output_file_reads_are_polls() -> None:
  out = "/tmp/claude-1000/proj/tasks/b7.output"
  reads = calls("view_file", [{"AbsolutePath": out, "StartLine": 40}] * 3)
  assert NOTIFY in reads[2]
  ob.reset_ring(CONV)
  other = calls("view_file", [{"AbsolutePath": "/w/output.txt"}] * 3)
  assert other[2] and NOTIFY not in other[2]


# --- schedule -----------------------------------------------------------------


def test_schedule_call_resets_the_ring() -> None:
  setup = {"CommandLine": "python3 poll_presubmit.py"}
  assert calls("run_command", [setup] * 2) == [None, None]
  assert ob.observe(CONV, "schedule", {"CronExpression": "*/5 * * * *"}) is None
  assert not ob.load_ring(CONV)
  assert calls("run_command", [setup] * 3)[:2] == [None, None]


# --- turn end (Stop) ----------------------------------------------------------


def test_cron_ticks_in_separate_turns_never_trip() -> None:
  """a reported false positive: one poll per cron wakeup, five wakeups."""
  poll = tool_call("run_command", {"CommandLine": "python3 poll_presubmit.py"})
  for _ in range(5):
    assert run_hook(poll) == {"decision": "allow"}
    assert end_turn() == {"decision": "allow"}
  assert not ob.load_ring(CONV)


def test_slow_calls_trip_in_one_turn_but_not_across_turns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
  """Three 90 s calls in one turn still prompt; one per turn never does."""
  clock = [1_000_000.0]
  monkeypatch.setattr(ob.time, "time", lambda: clock[0])
  slow = {"CommandLine": "make test"}
  out = []
  for _ in range(3):
    out.append(ob.observe(CONV, "run_command", slow))
    clock[0] += 90
  assert out[2] is not None
  ob.reset_ring(CONV)
  for _ in range(3):
    assert ob.observe(CONV, "run_command", slow) is None
    assert end_turn() == {"decision": "allow"}
    clock[0] += 90


def test_stop_resets_only_its_conversation_and_fails_open(data: Path) -> None:
  calls("view_file", [{"AbsolutePath": "/a"}] * 2)
  ob.observe("other-conv", "view_file", {"AbsolutePath": "/a"})
  assert end_turn() == {"decision": "allow"}
  assert not ob.load_ring(CONV)
  assert ob.load_ring("other-conv")
  assert run_hook("{not json", "stop") == {"decision": "allow"}
  assert run_hook("", "stop") == {"decision": "allow"}
  assert ob.load_ring("other-conv")


# --- idle expiry (opt-in) -----------------------------------------------------


def _age_ring(seconds: float) -> None:
  path = ob.ring_path(CONV)
  body = json.loads(path.read_text())
  body["updated"] -= seconds
  path.write_text(json.dumps(body))


@pytest.mark.parametrize("value", [None, "0", "junk", "-5"])
def test_idle_expiry_is_off_unless_set(
    value: Optional[str], monkeypatch: pytest.MonkeyPatch
) -> None:
  if value is not None:
    monkeypatch.setenv("PAWL_OSCILLATION_IDLE_S", value)
  calls("view_file", [{"AbsolutePath": "/a"}] * 2)
  _age_ring(3600)
  assert ob.observe(CONV, "view_file", {"AbsolutePath": "/a"}) is not None
  monkeypatch.setenv("PAWL_OSCILLATION_IDLE_S", "60")
  ob.reset_ring(CONV)
  calls("view_file", [{"AbsolutePath": "/a"}] * 2)
  _age_ring(61)
  assert ob.observe(CONV, "view_file", {"AbsolutePath": "/a"}) is None


def test_idle_expiry_keeps_a_fresh_ring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
  monkeypatch.setenv("PAWL_OSCILLATION_IDLE_S", "60")
  calls("view_file", [{"AbsolutePath": "/a"}] * 2)
  _age_ring(30)
  assert ob.observe(CONV, "view_file", {"AbsolutePath": "/a"}) is not None
  _age_ring(120)
  assert ob.load_ring(CONV) == []
