#!/usr/bin/env python3
"""conversation_fence_hook.py: PreToolUse hook fencing other conversations.

Reads the tool call JSON on stdin and prints {"decision": "allow"} or
{"decision": "force_ask", "reason": "[PAWL fence] ..."} (deny under
PAWL_CONVERSATION_FENCE_STRICT=1). Fails open on everything.

Payload shape (Jetski / Antigravity style; `tool_and_args()` in
conversation_fence.py is the one function to adapt for another harness):

    {"conversationId": "...", "toolCall": {"name": "view_file", "args":
    {"AbsolutePath": "..."}}}
"""

from __future__ import annotations

from pathlib import Path  # pylint: disable=g-importing-member
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

import conversation_fence  # noqa: E402  # pylint: disable=g-import-not-at-top

if __name__ == "__main__":
  conversation_fence.hook_main()
