#!/usr/bin/env python3
"""pawl_zero_width_hook.py: PreToolUse entry for the zero-width-sanitizer piece.

Reads the tool call JSON on stdin and prints allow, with an overwrite block
holding the cleaned args when a write carried zero-width characters. Never
blocks. See pieces/zero-width-sanitizer/README.md.
"""

from __future__ import annotations

from pathlib import Path  # pylint: disable=g-importing-member
import sys

HERE = Path(__file__).resolve().parent
PIECES = HERE.parent / "pieces"
sys.path.insert(0, str(PIECES / "zero-width-sanitizer"))

# pylint: disable=g-import-not-at-top
import pawl_harness  # noqa: E402
import zero_width_sanitizer  # noqa: E402
# pylint: enable=g-import-not-at-top


def main() -> None:
  zero_width_sanitizer.arm_watchdog(zero_width_sanitizer.watchdog_budget_s())
  raw = sys.stdin.read()
  try:
    result = zero_width_sanitizer.run_hook(raw)
  finally:
    zero_width_sanitizer.disarm_watchdog()
  payload = zero_width_sanitizer.read_payload(raw)
  pawl_harness.emit_decision(
      result.get("decision", "allow"),
      reason=result.get("reason", ""),
      payload=payload,
      overwrite=result.get("overwrite"),
  )


if __name__ == "__main__":
  main()
