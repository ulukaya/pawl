#!/usr/bin/env python3
"""pawl_poll_hook.py: PreToolUse entry for the poll-loop-guard piece.

Reads the tool call JSON on stdin and prints one of:

    {"decision": "allow"}
    {"decision": "force_ask", "reason": "[PAWL poll] ..."}
    {"decision": "deny", "reason": "[HOOK PAYLOAD] ..."}

A guard hit is a prompt, not a wall: the host shows the reason and only a human
click runs the command. The row in PAWL_DATA/denials.jsonl carries outcome
force_ask. Unparsable stdin stays a deny; the watchdog fails open on time. See
pieces/poll-loop-guard/README.md.
"""

from __future__ import annotations

import json
from pathlib import Path  # pylint: disable=g-importing-member
import sys
from typing import Dict

HERE = Path(__file__).resolve().parent
PIECES = HERE.parent / "pieces"
sys.path.insert(0, str(PIECES / "poll-loop-guard"))

# pylint: disable=g-import-not-at-top
import poll_loop_guard as piece  # noqa: E402

# pylint: enable=g-import-not-at-top

import pawl_harness  # noqa: E402

PREFIX = "[PAWL poll]"
TAIL = " Approve to run anyway; the run is logged as a human override."


def decide(raw: str) -> Dict[str, str]:
  """Plugin decision for raw stdin text.

  Args:
    raw: the hook payload text.

  Returns:
    The decision dict the host reads on stdout.
  """
  decision, reason, cmd, payload = piece.evaluate(raw)
  if decision == "allow":
    return {"decision": "allow"}
  if payload is None:
    return {"decision": "deny", "reason": reason}
  piece.record_denial(piece.GATE, cmd, payload, outcome="force_ask")
  return {
      "decision": "force_ask",
      "reason": f"{PREFIX} {reason.rstrip('.')}.{TAIL}",
  }


def main() -> None:
  piece.arm_watchdog(piece.watchdog_budget_s())
  raw = sys.stdin.read()
  try:
    result = decide(raw)
  finally:
    piece.disarm_watchdog()
  payload, _ = piece.read_payload(raw)
  pawl_harness.emit_decision(
      result["decision"],
      reason=result.get("reason", ""),
      payload=payload or {},
  )


if __name__ == "__main__":
  main()
