#!/usr/bin/env python3
"""destructive_git_guard_hook.py: PreToolUse hook denying destructive git.

Reads the tool call JSON on stdin, scans the command line for destructive git
in a protected repo (and commit-gate bypasses in any repo), and prints a
decision:

    {"decision": "allow"}
    {"decision": "deny", "reason": "[DESTRUCTIVE GIT] ..."}

Unparsable stdin is a deny (fail closed on the payload). An internal error
while scanning a git command is a deny. A hook that runs past its watchdog
budget prints allow and exits (fail open on time).

Payload shape (Jetski / Antigravity style; `command_and_cwd()` in
destructive_git_guard.py is the one function to adapt for another harness):

    {"toolCall": {"name": "run_command", "args": {"CommandLine": "git reset
    --hard", "Cwd": "/repo"}}}
"""

from __future__ import annotations

from pathlib import Path  # pylint: disable=g-importing-member
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

# pylint: disable=g-import-not-at-top
import destructive_git_guard  # noqa: E402

# pylint: enable=g-import-not-at-top

if __name__ == "__main__":
  destructive_git_guard.hook_main()
