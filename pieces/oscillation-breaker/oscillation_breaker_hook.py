#!/usr/bin/env python3
"""oscillation_breaker_hook.py: PreToolUse hook prompting on repeated calls.

Reads the tool call JSON on stdin, records (tool, sha1 of args) in a
per-conversation ring, and prints a decision:

    {"decision": "allow"}
    {"decision": "force_ask", "reason": "[PAWL loop] ..."}

Missing conversation id, unparsable stdin, and internal errors all fail open. A
hook that runs past its watchdog budget prints allow and exits (fail open on
time).

Payload shape (Jetski / Antigravity style; `tool_and_args()` and
`resolve_conversation_id()` in oscillation_breaker.py are the two functions to
adapt for another harness):

    {"conversationId": "...", "toolCall": {"name": "view_file", "args": {...}}}
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import oscillation_breaker  # noqa: E402  # pylint: disable=g-import-not-at-top

if __name__ == "__main__":
  oscillation_breaker.hook_main()
