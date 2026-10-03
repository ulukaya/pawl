#!/usr/bin/env python3
"""blast_radius_hook.py: PreToolUse hook for the blast-radius guard.

Reads the tool call JSON on stdin and prints one decision:

    {"decision": "allow"}
    {"decision": "force_ask", "reason": "[PAWL blast] ..."}
    {"decision": "deny", "reason": "[PAWL blast] ..."}

Payload shape (Antigravity style; Claude Code's tool_input also works):

    {"toolCall": {"name": "run_command", "args": {"CommandLine": "rm -rf ~",
     "Cwd": "/repo"}}}

Unparsable stdin allows: this piece judges commands, not payloads.
"""

from __future__ import annotations

from pathlib import Path  # pylint: disable=g-importing-member
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

import blast_radius  # noqa: E402  # pylint: disable=g-import-not-at-top

if __name__ == "__main__":
  sys.exit(blast_radius.main(["hook"]))
