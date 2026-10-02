#!/usr/bin/env python3
"""pawl_fence_hook.py: PreToolUse entry for the conversation-fence piece.

Reads the tool call JSON on stdin and prints allow, or force_ask with a
`[PAWL fence]` reason before a read of another conversation's files. Fails
open on everything. See pieces/conversation-fence/README.md.
"""

from __future__ import annotations

import json
from pathlib import Path  # pylint: disable=g-importing-member
import sys
from typing import Any, Dict

HERE = Path(__file__).resolve().parent
PIECES = HERE.parent / "pieces"
sys.path.insert(0, str(PIECES / "conversation-fence"))

# pylint: disable=g-import-not-at-top
import conversation_fence  # noqa: E402
import pawl_harness  # noqa: E402
# pylint: enable=g-import-not-at-top


def _parse_payload(raw: str) -> Dict[str, Any]:
  try:
    data = json.loads(raw or "{}")
  except ValueError:
    return {}
  return data if isinstance(data, dict) else {}


def main() -> None:
  conversation_fence.arm_watchdog(conversation_fence.watchdog_budget_s())
  raw = sys.stdin.read()
  try:
    result = conversation_fence.run_hook(raw)
  finally:
    conversation_fence.disarm_watchdog()
  payload = _parse_payload(raw)
  pawl_harness.emit_decision(
      result.get("decision", "allow"),
      reason=result.get("reason", ""),
      payload=payload,
  )


if __name__ == "__main__":
  main()
