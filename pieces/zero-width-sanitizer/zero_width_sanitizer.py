#!/usr/bin/env python3
"""zero_width_sanitizer.py: strip invisible characters out of file writes.

An invisible character in an edit target makes the match fail against a clean
file, and the agent then rewrites a file that was already correct. This piece
removes zero width space (U+200B), non-joiner (U+200C), joiner (U+200D outside
emoji sequences), byte order mark (U+FEFF), word joiner (U+2060) and soft
hyphen (U+00AD) from content fields of write_to_file (CodeContent),
replace_file_content (TargetContent, ReplacementContent) and
multi_replace_file_content (ReplacementChunks).

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
NON_ZWJ_ZERO_WIDTH = re.compile(r"[\u200b\u200c\ufeff\u2060\u00ad]")
EMOJI_RANGE = (
    r"[\U0001f000-\U0001faff"
    r"\U00002600-\U000027bf"
    r"\U00002300-\U000023ff"
    r"\U00002b00-\U00002bff"
    r"\U0000fe00-\U0000fe0f"
    r"]"
)
ZWJ_EMOJI_PAIR = re.compile(f"(?<={EMOJI_RANGE})\u200d(?={EMOJI_RANGE})")
_ZWJ_PROTECTED = "\x00_ZWJ_\x00"

CONTENT_FIELDS = {
    "write_to_file": ("CodeContent",),
    "replace_file_content": ("TargetContent", "ReplacementContent"),
}


def strip(text: str) -> str:
  text = NON_ZWJ_ZERO_WIDTH.sub("", text)
  if "\u200d" not in text:
    return text
  text = ZWJ_EMOJI_PAIR.sub(_ZWJ_PROTECTED, text)
  text = text.replace("\u200d", "")
  return text.replace(_ZWJ_PROTECTED, "\u200d")


def _clean_field(val: Any) -> Tuple[Any, bool]:
  if isinstance(val, str) and ZERO_WIDTH_PATTERN.search(val):
    cleaned = strip(val)
    if cleaned != val:
      return cleaned, True
  return val, False


def _clean_chunk(chunk: Any) -> Tuple[Any, bool]:
  if not isinstance(chunk, dict):
    return chunk, False
  new_chunk = dict(chunk)
  changed = False
  for key in ("TargetContent", "ReplacementContent"):
    val, c = _clean_field(new_chunk.get(key))
    if c:
      new_chunk[key] = val
      changed = True
  return new_chunk, changed


def _sanitize_chunks(chunks: List[Any]) -> Tuple[List[Any], bool]:
  new_chunks: List[Any] = []
  changed = False
  for chunk in chunks:
    c_out, c_changed = _clean_chunk(chunk)
    new_chunks.append(c_out)
    if c_changed:
      changed = True
  return new_chunks, changed


def sanitize(tool: str, args: Any) -> Optional[Dict[str, Any]]:
  """Cleaned copy of `args` when a content field changed, else None."""
  if not isinstance(args, dict):
    return None
  if tool == "multi_replace_file_content":
    raw_chunks = args.get("ReplacementChunks")
    if not isinstance(raw_chunks, list):
      return None
    new_chunks, changed = _sanitize_chunks(raw_chunks)
    if not changed:
      return None
    out = dict(args)
    out["ReplacementChunks"] = new_chunks
    return out

  fields = CONTENT_FIELDS.get(tool)
  if not fields:
    return None
  out = dict(args)
  changed = False
  for key in fields:
    val, c = _clean_field(out.get(key))
    if c:
      out[key] = val
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
