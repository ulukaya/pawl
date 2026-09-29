#!/usr/bin/env python3
"""pawl_oscillation_hook.py: PreToolUse entry for the oscillation-breaker piece.

Reads the tool call JSON on stdin and prints allow, or force_ask with a `[PAWL
loop]` reason when the same tool call repeats. Fails open on everything. See
pieces/oscillation-breaker/README.md.
"""

from __future__ import annotations

from pathlib import Path  # pylint: disable=g-importing-member
import sys

HERE = Path(__file__).resolve().parent
PIECES = HERE.parent / "pieces"
sys.path.insert(0, str(PIECES / "oscillation-breaker"))

import oscillation_breaker  # noqa: E402  # pylint: disable=g-import-not-at-top

if __name__ == "__main__":
  oscillation_breaker.hook_main()
