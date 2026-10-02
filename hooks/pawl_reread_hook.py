#!/usr/bin/env python3
"""pawl_reread_hook.py: PreToolUse entry for the reread-guard piece.

Reads the tool call JSON on stdin and prints allow, or deny with a
`[PAWL reread]` reason past the reread limits. Fails open on everything. See
pieces/reread-guard/README.md.
"""

from __future__ import annotations

from pathlib import Path  # pylint: disable=g-importing-member
import sys

HERE = Path(__file__).resolve().parent
PIECES = HERE.parent / "pieces"
sys.path.insert(0, str(PIECES / "reread-guard"))

import reread_guard  # noqa: E402  # pylint: disable=g-import-not-at-top

if __name__ == "__main__":
  reread_guard.hook_main()
