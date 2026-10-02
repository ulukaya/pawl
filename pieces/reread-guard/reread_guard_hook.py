#!/usr/bin/env python3
"""reread_guard_hook.py: PreToolUse hook denying repeated recovery reads.

Reads the tool call JSON on stdin and prints {"decision": "allow"} or
{"decision": "deny", "reason": "[PAWL reread] ..."}. Fails open on everything.

Payload shape (Jetski / Antigravity style; `tool_and_args()` and `turn_key()`
in reread_guard.py are the functions to adapt for another harness):

    {"conversationId": "...", "turnId": "...", "transcriptPath": "...",
     "toolCall": {"name": "view_file", "args": {"AbsolutePath": "..."}}}
"""

from __future__ import annotations

from pathlib import Path  # pylint: disable=g-importing-member
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

import reread_guard  # noqa: E402  # pylint: disable=g-import-not-at-top

if __name__ == "__main__":
  reread_guard.hook_main()
