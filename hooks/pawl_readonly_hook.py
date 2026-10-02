#!/usr/bin/env python3
"""pawl_readonly_hook.py: PreToolUse entry for the readonly-pass piece.

Reads the tool call JSON on stdin and prints auto_approve when the command
provably only reads, else allow. Never denies or asks. See
pieces/readonly-pass/README.md.
"""

from __future__ import annotations

from pathlib import Path  # pylint: disable=g-importing-member
import sys

HERE = Path(__file__).resolve().parent
PIECES = HERE.parent / "pieces"
sys.path.insert(0, str(PIECES / "readonly-pass"))

import readonly_pass  # noqa: E402  # pylint: disable=g-import-not-at-top

if __name__ == "__main__":
  readonly_pass.hook_main()
