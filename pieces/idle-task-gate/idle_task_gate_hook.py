#!/usr/bin/env python3
"""idle_task_gate_hook.py: Stop hook blocking a turn end while tasks are stale.

Reads the Stop payload JSON on stdin and prints a decision:

    {"decision": "allow"}
    {"decision": "block", "reason": "[IDLE TASK] N background task(s) ... pid P
    (Am): cmd ..."}

First stop with a given stale pid set: block and name them. Second stop with
the same set: SIGTERM the unbounded-wait ones (poll loop, `tail -f`, long
`sleep`) and allow. A Stop hook fails open: unparsable stdin, a missing procfs,
an internal error, or a watchdog timeout all print allow.

Payload shape (Jetski / Antigravity style):

    {"conversationId": "..."}
"""

from __future__ import annotations

from pathlib import Path  # pylint: disable=g-importing-member
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

import idle_task_gate  # noqa: E402  # pylint: disable=g-import-not-at-top

if __name__ == "__main__":
  idle_task_gate.hook_main()
