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

import idle_task_gate  # noqa: E402  # pylint: disable=g-import-not-at-top

if __name__ == "__main__":
  idle_task_gate.hook_main()
