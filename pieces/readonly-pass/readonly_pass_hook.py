#!/usr/bin/env python3
"""readonly_pass_hook.py: PreToolUse hook auto-approving read-only commands.

Reads the tool call JSON on stdin and prints {"decision": "auto_approve"} when
every clause of the command provably only reads, else {"decision": "allow"}.
Never denies or asks; fails open (allow) on everything.

Payload shape (Jetski / Antigravity style; `tool_and_args()` in
readonly_pass.py is the one function to adapt for another harness):

    {"toolCall": {"name": "run_command", "args": {"CommandLine": "git log"}}}
"""

from __future__ import annotations

from pathlib import Path  # pylint: disable=g-importing-member
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

import readonly_pass  # noqa: E402  # pylint: disable=g-import-not-at-top

if __name__ == "__main__":
  readonly_pass.hook_main()
