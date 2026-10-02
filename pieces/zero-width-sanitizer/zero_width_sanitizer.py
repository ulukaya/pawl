#!/usr/bin/env python3
"""zero_width_sanitizer.py: strip invisible characters out of file writes.

An invisible character in an edit target makes the match fail against a clean
file, and the agent then rewrites a file that was already correct. This piece
removes zero width space (U+200B), non-joiner (U+200C), joiner (U+200D), byte
order mark (U+FEFF), word joiner (U+2060) and soft hyphen (U+00AD) from the
content fields of write_to_file (CodeContent) and replace_file_content
(TargetContent, ReplacementContent).

When something was stripped the hook answers with an overwrite block holding
the full argument object, cleaned, so the write still lands:

    {"decision": "allow", "overwrite": {...every arg, content cleaned...}}

Otherwise {"decision": "allow"}. Never blocks; fails open on bad stdin, an
internal error and the watchdog. Paths and other fields are never touched.

CLI:
    zero_width_sanitizer.py strip     copy stdin to stdout, characters removed

Environment:
    PAWL_ZERO_WIDTH_WATCHDOG_S    watchdog seconds (default host timeout 15 - 1)

Standard library only.
"""

from __future__ import annotations

import json
import os
import re
import signal
import sys
from typing import Any, Dict, List, Optional, Tuple

HOOK_NAME = "zero_width_sanitizer"
WATCHDOG_ENV = "PAWL_ZERO_WIDTH_WATCHDOG_S"
HOST_TIMEOUT_S = 15.0
ALLOW = {"decision": "allow"}
ZERO_WIDTH_PATTERN = re.compile(r"[\u200b-\u200d\ufeff\u2060\u00ad]")
CONTENT_FIELDS = {
    "write_to_file": ("CodeContent",),
    "replace_file_content": ("TargetContent", "ReplacementContent"),
}


def strip(text: str) -> str:
  return ZERO_WIDTH_PATTERN.sub("", text)


def sanitize(tool: str, args: Any) -> Optional[Dict[str, Any]]:
  """Cleaned copy of `args` when a content field changed, else None."""
  fields = CONTENT_FIELDS.get(tool)
  if not fields or not isinstance(args, dict):
    return None
  out = dict(args)
  changed = False
  for key in fields:
    value = out.get(key)
    if isinstance(value, str) and ZERO_WIDTH_PATTERN.search(value):
      out[key] = strip(value)
      changed = True
  return out if changed else None


# --- payload ------------------------------------------------------------------


def read_payload(raw: str) -> Dict[str, Any]:
  """Parse the hook payload; anything unparsable is {} (fail open)."""
  try:
    data = json.loads(raw or "{}")
  except ValueError:
    return {}
  return data if isinstance(data, dict) else {}


def tool_and_args(payload: Dict[str, Any]) -> Tuple[str, Any]:
  """(tool name, args) from a tool-call payload; name is '' when absent."""
  call = payload.get("toolCall") or payload.get("tool_call") or {}
  if not isinstance(call, dict):
    call = {}
  name = str(call.get("name") or payload.get("tool_name") or "")
  args = call.get("args")
  if args is None:
    args = call.get("arguments")
  if args is None:
    args = payload.get("tool_input")
  return name, args


def overwrite_block(cleaned: Dict[str, Any]) -> Dict[str, Any]:
  """The host decision that replaces the call's args with `cleaned`."""
  return {"decision": "allow", "overwrite": cleaned}


# --- hook entry ---------------------------------------------------------------


def run_hook(raw: str) -> Dict[str, Any]:
  """Full hook decision for raw stdin text. Never blocks."""
  try:
    cleaned = sanitize(*tool_and_args(read_payload(raw)))
  except Exception as exc:  # pylint: disable=broad-exception-caught
    # fail open: a sanitizer never blocks on its own bug
    sys.stderr.write(f"[{HOOK_NAME}] internal error, failing open: {exc!r}\n")
    return dict(ALLOW)
  return dict(ALLOW) if cleaned is None else overwrite_block(cleaned)


def watchdog_budget_s() -> float:
  try:
    return max(
        0.5, float(os.environ.get(WATCHDOG_ENV) or (HOST_TIMEOUT_S - 1.0))
    )
  except ValueError:
    return HOST_TIMEOUT_S - 1.0


def arm_watchdog(seconds: float) -> None:
  """Fail open past the hook budget: print allow before the host times out."""

  def _fire(unused_signum, unused_frame):
    sys.stdout.write(json.dumps(ALLOW))
    sys.stdout.flush()
    os._exit(0)

  try:
    signal.signal(signal.SIGALRM, _fire)
    signal.setitimer(signal.ITIMER_REAL, seconds)
  except (ValueError, OSError, AttributeError):
    return


def disarm_watchdog() -> None:
  try:
    signal.setitimer(signal.ITIMER_REAL, 0)
  except (ValueError, OSError, AttributeError):
    return


def hook_main() -> None:
  arm_watchdog(watchdog_budget_s())
  try:
    result = run_hook(sys.stdin.read())
  finally:
    disarm_watchdog()
  sys.stdout.write(json.dumps(result))
  sys.stdout.flush()


def main(argv: Optional[List[str]] = None) -> int:
  argv = list(sys.argv[1:] if argv is None else argv)
  if argv != ["strip"]:
    sys.stderr.write("usage: zero_width_sanitizer.py strip < in > out\n")
    return 2
  sys.stdout.write(strip(sys.stdin.read()))
  return 0


if __name__ == "__main__":
  sys.exit(main())
