#!/usr/bin/env python3
"""pawl_noop_edit_hook.py: PreToolUse entry for the noop-edit-guard piece.

Reads the tool call JSON on stdin and prints allow, or deny with a
`[PAWL no-op]` reason when an edit would change nothing. Fails open on
everything. See pieces/noop-edit-guard/README.md.
"""

from __future__ import annotations

from pathlib import Path  # pylint: disable=g-importing-member
import sys

HERE = Path(__file__).resolve().parent
PIECES = HERE.parent / "pieces"
sys.path.insert(0, str(PIECES / "noop-edit-guard"))

# pylint: disable=g-import-not-at-top
import noop_edit_guard  # noqa: E402
import pawl_harness  # noqa: E402
# pylint: enable=g-import-not-at-top


def main() -> None:
  noop_edit_guard.arm_watchdog(noop_edit_guard.watchdog_budget_s())
  raw = sys.stdin.read()
  try:
    result = noop_edit_guard.run_hook(raw)
  finally:
    noop_edit_guard.disarm_watchdog()
  payload = noop_edit_guard.read_payload(raw)
  pawl_harness.emit_decision(
      result.get("decision", "allow"),
      reason=result.get("reason", ""),
      payload=payload,
  )


if __name__ == "__main__":
  main()
