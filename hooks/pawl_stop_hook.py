#!/usr/bin/env python3
"""pawl_stop_hook.py: Stop entry for the idle-task-gate piece.

Reads the Stop payload JSON on stdin and prints {"decision": "allow"} or
{"decision": "block", "reason": "[IDLE TASK] ..."}. A Stop hook fails open on
every error. See pieces/idle-task-gate/README.md.
"""

from __future__ import annotations

from pathlib import Path  # pylint: disable=g-importing-member
import sys

HERE = Path(__file__).resolve().parent
PIECES = HERE.parent / "pieces"
sys.path.insert(0, str(PIECES / "idle-task-gate"))

# pylint: disable=g-import-not-at-top
import idle_task_gate  # noqa: E402
import pawl_harness  # noqa: E402
# pylint: enable=g-import-not-at-top


def main() -> None:
  idle_task_gate.arm_watchdog(idle_task_gate.watchdog_budget_s())
  raw = sys.stdin.read()
  try:
    result = idle_task_gate.run_hook(raw)
  finally:
    idle_task_gate.disarm_watchdog()
  payload = idle_task_gate.read_payload(raw)
  pawl_harness.emit_decision(
      result.get("decision", "allow"),
      reason=result.get("reason", ""),
      payload=payload,
  )


if __name__ == "__main__":
  main()
