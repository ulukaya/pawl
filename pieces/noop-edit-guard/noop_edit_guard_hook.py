#!/usr/bin/env python3
"""noop_edit_guard_hook.py: PreToolUse hook denying zero-diff edits.

Reads the tool call JSON on stdin and prints {"decision": "allow"} or
{"decision": "deny", "reason": "[PAWL no-op] ..."}. Fails open on everything.

Payload shape (Jetski / Antigravity style; `tool_and_args()` in
noop_edit_guard.py is the one function to adapt for another harness):

    {"toolCall": {"name": "replace_file_content", "args": {"TargetFile": "...",
    "TargetContent": "x", "ReplacementContent": "x"}}}
"""

from __future__ import annotations

from pathlib import Path  # pylint: disable=g-importing-member
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

import noop_edit_guard  # noqa: E402  # pylint: disable=g-import-not-at-top

if __name__ == "__main__":
  noop_edit_guard.hook_main()
