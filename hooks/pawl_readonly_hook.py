#!/usr/bin/env python3
"""pawl_readonly_hook.py: PreToolUse entry for the readonly-pass piece.

Reads the tool call JSON on stdin and prints auto_approve when the command
provably only reads, else allow. Never denies or asks. See
pieces/readonly-pass/README.md.
"""

from __future__ import annotations

from pathlib import Path  # pylint: disable=g-importing-member
import sys

HERE = Path(__file__).resolve().parent
PIECES = HERE.parent / "pieces"
sys.path.insert(0, str(PIECES / "readonly-pass"))

# pylint: disable=g-import-not-at-top
import pawl_harness  # noqa: E402
import readonly_pass  # noqa: E402
# pylint: enable=g-import-not-at-top


def main() -> None:
  readonly_pass.arm_watchdog(readonly_pass.watchdog_budget_s())
  raw = sys.stdin.read()
  try:
    result = readonly_pass.run_hook(raw)
  finally:
    readonly_pass.disarm_watchdog()
  payload = readonly_pass.read_payload(raw)
  pawl_harness.emit_decision(
      result.get("decision", "allow"),
      reason=result.get("reason", ""),
      payload=payload,
  )


if __name__ == "__main__":
  main()
