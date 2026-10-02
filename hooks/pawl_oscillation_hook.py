#!/usr/bin/env python3
"""pawl_oscillation_hook.py: PreToolUse entry for the oscillation-breaker piece.

Reads the tool call JSON on stdin and prints allow, or force_ask with a `[PAWL
loop]` reason when the same tool call repeats. Fails open on everything. See
pieces/oscillation-breaker/README.md.
"""

from __future__ import annotations

from pathlib import Path  # pylint: disable=g-importing-member
import sys

HERE = Path(__file__).resolve().parent
PIECES = HERE.parent / "pieces"
sys.path.insert(0, str(PIECES / "oscillation-breaker"))

# pylint: disable=g-import-not-at-top
import oscillation_breaker  # noqa: E402
import pawl_harness  # noqa: E402
# pylint: enable=g-import-not-at-top


def main() -> None:
  stop = sys.argv[1:] == ["stop"]
  handler = oscillation_breaker.end_turn if stop else oscillation_breaker.run_hook
  oscillation_breaker.arm_watchdog(oscillation_breaker.watchdog_budget_s())
  raw = sys.stdin.read()
  try:
    result = handler(raw)
  finally:
    oscillation_breaker.disarm_watchdog()
  payload = oscillation_breaker.read_payload(raw)
  pawl_harness.emit_decision(
      result.get("decision", "allow"),
      reason=result.get("reason", ""),
      payload=payload,
  )


if __name__ == "__main__":
  main()
