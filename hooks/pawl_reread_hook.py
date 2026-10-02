#!/usr/bin/env python3
"""pawl_reread_hook.py: PreToolUse entry for the reread-guard piece.

Reads the tool call JSON on stdin and prints allow, or deny with a
`[PAWL reread]` reason past the reread limits. Fails open on everything. See
pieces/reread-guard/README.md.
"""

from __future__ import annotations

import json
from pathlib import Path  # pylint: disable=g-importing-member
import sys
from typing import Any, Dict

HERE = Path(__file__).resolve().parent
PIECES = HERE.parent / "pieces"
sys.path.insert(0, str(PIECES / "reread-guard"))

# pylint: disable=g-import-not-at-top
import pawl_harness  # noqa: E402
import reread_guard  # noqa: E402
# pylint: enable=g-import-not-at-top


def _parse_payload(raw: str) -> Dict[str, Any]:
  try:
    data = json.loads(raw or "{}")
  except ValueError:
    return {}
  return data if isinstance(data, dict) else {}


def main() -> None:
  reread_guard.arm_watchdog(reread_guard.watchdog_budget_s())
  raw = sys.stdin.read()
  try:
    result = reread_guard.run_hook(raw)
  finally:
    reread_guard.disarm_watchdog()
  payload = _parse_payload(raw)
  pawl_harness.emit_decision(
      result.get("decision", "allow"),
      reason=result.get("reason", ""),
      payload=payload,
  )


if __name__ == "__main__":
  main()
