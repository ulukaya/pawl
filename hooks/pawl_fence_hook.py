#!/usr/bin/env python3
"""pawl_fence_hook.py: PreToolUse entry for the conversation-fence piece.

Reads the tool call JSON on stdin and prints allow, or force_ask with a
`[PAWL fence]` reason before a read of another conversation's files. Fails
open on everything. See pieces/conversation-fence/README.md.
"""

from __future__ import annotations

from pathlib import Path  # pylint: disable=g-importing-member
import sys

HERE = Path(__file__).resolve().parent
PIECES = HERE.parent / "pieces"
sys.path.insert(0, str(PIECES / "conversation-fence"))

import conversation_fence  # noqa: E402  # pylint: disable=g-import-not-at-top

if __name__ == "__main__":
  conversation_fence.hook_main()
