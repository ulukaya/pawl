#!/usr/bin/env python3
"""pawl_zero_width_hook.py: PreToolUse entry for the zero-width-sanitizer piece.

Reads the tool call JSON on stdin and prints allow, with an overwrite block
holding the cleaned args when a write carried zero-width characters. Never
blocks. See pieces/zero-width-sanitizer/README.md.
"""

from __future__ import annotations

from pathlib import Path  # pylint: disable=g-importing-member
import sys

HERE = Path(__file__).resolve().parent
PIECES = HERE.parent / "pieces"
sys.path.insert(0, str(PIECES / "zero-width-sanitizer"))

import zero_width_sanitizer  # noqa: E402  # pylint: disable=g-import-not-at-top

if __name__ == "__main__":
  zero_width_sanitizer.hook_main()
