#!/usr/bin/env python3
"""noop_edit_guard.py: deny an edit that would leave the file as it was.

A replace_file_content whose ReplacementContent equals its TargetContent, or a
multi_replace_file_content where every chunk is such a no-op, costs a tool
call and a round of context and changes nothing; the agent then resends it with
a different anchor. Refusing the first one sends it back to view_file, the step
it skipped. Equality is exact: a whitespace change is a real edit.

Claude Code's Edit (old_string, new_string) is read the same way. A Codex
apply_patch is a no-op when it only updates files (no Add, Delete or Move)
and every hunk's old side (context and `-` lines) equals its new side
(context and `+` lines), compared hunk by hunk so a line moved past context
is still an edit.

Decision on stdout:

    {"decision": "allow"}
    {"decision": "deny", "reason": "[PAWL no-op] ..."}

Fails open on bad stdin, an unexpected argument shape, an internal error and
the watchdog. Each deny appends one row to PAWL_DATA/denials.jsonl with gate
NOOP_EDIT and the sha1 of the target path, never the path or the content.

CLI:
    noop_edit_guard.py check <tool> <json-args>
        exit 0 allow, 1 no-op (reason on stdout), 2 usage

Environment:
    PAWL_DATA                    denial log dir (default ~/.pawl)
    PAWL_NOOP_EDIT_WATCHDOG_S    watchdog seconds (default host timeout 15 - 1)

Standard library only.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path  # pylint: disable=g-importing-member
import signal
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

HOOK_NAME = "noop_edit_guard"
GATE = "NOOP_EDIT"
PREFIX = "[PAWL no-op]"
WATCHDOG_ENV = "PAWL_NOOP_EDIT_WATCHDOG_S"
HOST_TIMEOUT_S = 15.0
ALLOW = {"decision": "allow"}
REPLACE_TOOL = "replace_file_content"
MULTI_TOOL = "multi_replace_file_content"
PATCH_TOOL = "apply_patch"
CLAUDE_EDIT_TOOLS = frozenset({"Edit", "edit"})
_FILE_OPS = ("*** Add File: ", "*** Delete File: ", "*** Move to: ")
_UPDATE = "*** Update File: "


# --- rule ---------------------------------------------------------------------


def _is_noop(chunk: Any) -> bool:
  """True when a chunk carries two equal strings for target and replacement."""
  if not isinstance(chunk, dict):
    return False
  target = chunk.get("TargetContent")
  if target is None:
    target = chunk.get("old_string")
  repl = chunk.get("ReplacementContent")
  if repl is None:
    repl = chunk.get("new_string")
  return isinstance(target, str) and isinstance(repl, str) and target == repl


def _chunks(tool: str, args: Any) -> List[Any]:
  """The edit chunks of an edit or replace call, or [] for anything else."""
  if not isinstance(args, dict):
    return []
  if tool == REPLACE_TOOL or tool in CLAUDE_EDIT_TOOLS:
    return [args]
  chunks = args.get("ReplacementChunks") if tool == MULTI_TOOL else None
  return chunks if isinstance(chunks, list) else []


def _patch_updates(patch: str) -> Optional[List[Tuple[str, List[List[str]]]]]:
  """(path, hunks) per Update File; None when the patch does anything else.

  Args:
    patch: apply_patch text (`*** Begin Patch` ... `*** End Patch`).

  Returns:
    Hunk bodies split at `@@` lines, or None for an Add, Delete or Move, a
    body line before any Update, or no Update at all.
  """
  files: List[Tuple[str, List[List[str]]]] = []
  for line in patch.splitlines():
    if line.startswith(_FILE_OPS):
      return None
    if line.startswith(_UPDATE):
      files.append((line[len(_UPDATE):].strip(), [[]]))
    elif line.startswith("***"):
      continue  # Begin Patch, End Patch, End of File
    elif not files:
      return None
    elif line.startswith("@@"):
      files[-1][1].append([])
    else:
      files[-1][1][-1].append(line)
  return files or None


def _hunk_is_noop(lines: List[str]) -> bool:
  old = [l[1:] for l in lines if l[:1] in ("", " ", "-")]
  new = [l[1:] for l in lines if l[:1] in ("", " ", "+")]
  return old == new


def patch_noop_reason(args: Any) -> str:
  """Deny reason when an apply_patch changes nothing, else ''."""
  text = args.get("command") if isinstance(args, dict) else None
  files = _patch_updates(text) if isinstance(text, str) else None
  if not files:
    return ""
  if not all(_hunk_is_noop(h) for _, hunks in files for h in hunks):
    return ""
  names = ", ".join(os.path.basename(path) or path for path, _ in files)
  return (
      f"{PREFIX} {PATCH_TOOL} on {names}: every hunk puts back the lines it"
      " takes out, so the patch changes nothing. Re-read the region you"
      " meant to change and send the patch with the new text."
  )


def noop_reason(tool: str, args: Any) -> str:
  """Deny reason when every chunk of this edit is a no-op, else ''."""
  if tool == PATCH_TOOL:
    return patch_noop_reason(args)
  chunks = _chunks(tool, args)
  if not chunks or not all(_is_noop(c) for c in chunks):
    return ""
  raw_file = args.get("TargetFile") or args.get("file_path") or ""
  name = os.path.basename(str(raw_file)) or "the file"
  if tool in CLAUDE_EDIT_TOOLS:
    what = "new_string equals old_string"
  elif tool == REPLACE_TOOL:
    what = "ReplacementContent equals TargetContent"
  else:
    what = (
        f"all {len(chunks)} chunks have ReplacementContent equal to"
        " TargetContent"
    )
  action = "Read" if tool in CLAUDE_EDIT_TOOLS else "view_file"
  return (
      f"{PREFIX} {tool} on {name}: {what}, so the edit changes nothing."
      f" {action} the region you meant to change and send the edit with the"
      " new text."
  )


# --- payload ------------------------------------------------------------------


def data_dir() -> Path:
  return Path(os.environ.get("PAWL_DATA") or os.path.expanduser("~/.pawl"))


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


def record_denial(payload: Dict[str, Any], subject: str) -> None:
  """Append one JSON line to PAWL_DATA/denials.jsonl. Never raises."""
  try:
    conv = payload.get("conversationId") or payload.get("conversation_id")
    row = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "conv": conv if isinstance(conv, str) else "",
        "hook": HOOK_NAME,
        "gate": GATE,
        "cmd_sha1": hashlib.sha1(
            subject.encode("utf-8", "replace")
        ).hexdigest(),
        "outcome": "deny",
    }
    path = data_dir() / "denials.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
      fh.write(json.dumps(row, sort_keys=True) + "\n")
  except Exception:  # pylint: disable=broad-exception-caught
    # fail open: telemetry must never break a hook
    return


# --- hook entry ---------------------------------------------------------------


def decide(payload: Dict[str, Any]) -> Dict[str, str]:
  tool, args = tool_and_args(payload)
  reason = noop_reason(tool, args)
  if not reason:
    return dict(ALLOW)
  subject = args.get("TargetFile") or args.get("file_path") or args.get(
      "command") or tool
  record_denial(payload, str(subject))
  return {"decision": "deny", "reason": reason}


def run_hook(raw: str) -> Dict[str, str]:
  """Full hook decision for raw stdin text. Fails open on any error."""
  try:
    return decide(read_payload(raw))
  except Exception as exc:  # pylint: disable=broad-exception-caught
    # fail open: a no-op check never blocks on its own bug
    sys.stderr.write(f"[{HOOK_NAME}] internal error, failing open: {exc!r}\n")
    return dict(ALLOW)


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


# --- CLI ----------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
  argv = list(sys.argv[1:] if argv is None else argv)
  if len(argv) != 3 or argv[0] != "check":
    sys.stderr.write("usage: noop_edit_guard.py check <tool> <json-args>\n")
    return 2
  try:
    args = json.loads(argv[2])
  except ValueError:
    sys.stderr.write("json-args must be valid JSON\n")
    return 2
  reason = noop_reason(argv[1], args)
  print(reason or "allow")
  return 1 if reason else 0


if __name__ == "__main__":
  sys.exit(main())
