#!/usr/bin/env python3
"""poll_loop_guard_hook.py: PreToolUse hook denying unbounded-wait commands.

Reads the tool call JSON on stdin, classifies the command line, and prints a
decision:

    {"decision": "allow"}
    {"decision": "deny", "reason": "[POLL LOOP] LOOP: ..."}

Unparsable stdin is a deny (fail closed on the payload). A hook that runs past
its watchdog budget prints allow and exits (fail open on time).

Payload shape (Jetski / Antigravity style; `command_of()` in poll_loop_guard.py
is the one function to adapt for another harness):

    {"toolCall": {"name": "run_command", "args": {"CommandLine": "tail -f
    build.log"}}}
"""

from __future__ import annotations

from pathlib import Path  # pylint: disable=g-importing-member
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

import poll_loop_guard  # noqa: E402  # pylint: disable=g-import-not-at-top

if __name__ == "__main__":
  poll_loop_guard.hook_main()
