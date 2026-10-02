#!/usr/bin/env python3
"""zero_width_sanitizer_hook.py: PreToolUse hook cleaning invisible characters.

Reads the tool call JSON on stdin and prints {"decision": "allow"}, or
{"decision": "allow", "overwrite": {...}} carrying the full argument object
with zero-width characters removed from its content fields. Never blocks.

Payload shape (Jetski / Antigravity style; `tool_and_args()` in
zero_width_sanitizer.py is the one function to adapt for another harness):

    {"toolCall": {"name": "write_to_file", "args": {"TargetFile": "...",
    "CodeContent": "..."}}}
"""

from __future__ import annotations

from pathlib import Path  # pylint: disable=g-importing-member
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

import zero_width_sanitizer  # noqa: E402  # pylint: disable=g-import-not-at-top

if __name__ == "__main__":
  zero_width_sanitizer.hook_main()
