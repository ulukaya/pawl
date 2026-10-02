#!/usr/bin/env python3
"""pawl_noop_edit_hook.py: PreToolUse entry for the noop-edit-guard piece.

Reads the tool call JSON on stdin and prints allow, or deny with a
`[PAWL no-op]` reason when an edit would change nothing. Fails open on
everything. See pieces/noop-edit-guard/README.md.
"""

from __future__ import annotations

from pathlib import Path  # pylint: disable=g-importing-member
import sys

HERE = Path(__file__).resolve().parent
PIECES = HERE.parent / "pieces"
sys.path.insert(0, str(PIECES / "noop-edit-guard"))

import noop_edit_guard  # noqa: E402  # pylint: disable=g-import-not-at-top

if __name__ == "__main__":
  noop_edit_guard.hook_main()
