#!/usr/bin/env python3
"""reread_guard.py: stop an agent re-reading its own transcript and skills.

After a context truncation an agent often rebuilds what it lost by dumping its
own transcript and re-reading skill files it already read; each dump refills
the context and brings the next truncation closer. This piece counts two read
shapes per conversation and user turn and denies past a limit ordinary turns
never reach:

  the 11th unbounded read of this conversation's own transcript*.jsonl;
  the 6th read of the same unchanged SKILL.md, or of the same memory file
  (MEMORY.md, GEMINI.md, AGENTS.md, CLAUDE.md, *.md under memory/) read
  from the top.

Bounded reads never count (reread_shapes.py). Other conversations'
transcripts never count. A repeat is keyed by path and mtime, so an edit
starts a fresh count. After three denials in one turn the guard allows
everything until the next turn, so it cannot become its own loop. The turn
key is the payload's turnId, else the number of USER_INPUT lines in its
transcriptPath, read only for calls the rules count.

Decision on stdout: {"decision": "allow"} or
{"decision": "deny", "reason": "[PAWL reread] ..."}. Each deny appends one row
to PAWL_DATA/denials.jsonl with gate REREAD and the path sha1 only. Fails open
on bad stdin, a missing conversation id, an unwritable state dir, an internal
error and the watchdog.

Environment:
    PAWL_DATA                  state and log dir (default ~/.pawl)
    PAWL_REREAD_GUARD_OFF      1 turns the piece off
    PAWL_REREAD_WATCHDOG_S     watchdog seconds (default host timeout 15 - 1)

Standard library only.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path  # pylint: disable=g-importing-member
import re
import signal
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

import reread_shapes

HOOK_NAME = "reread_guard"
GATE = "REREAD"
PREFIX = "[PAWL reread]"
OFF_ENV = "PAWL_REREAD_GUARD_OFF"
WATCHDOG_ENV = "PAWL_REREAD_WATCHDOG_S"
HOST_TIMEOUT_S = 15.0
ALLOW = {"decision": "allow"}
TRANSCRIPT_LIMIT = 10
FILE_LIMIT = 5
STAND_DOWN_AFTER = 3
TRANSCRIPT_RE = re.compile(r"^transcript.*\.jsonl$")
MEMORY_NAMES = frozenset({"MEMORY.md", "GEMINI.md", "AGENTS.md", "CLAUDE.md"})
MEMORY_DIRS = frozenset({"memory", "memories"})
SHELL_TOOLS = frozenset({
    "run_command", "run_shell_command", "Bash", "bash", "exec_command", "shell",
})
RECOVERY = (
    " Keep recovery notes in one scratch file, read it once, answer from what"
    " you already have, and name any fact that is still missing."
)
_SAFE_CONV_RE = re.compile(r"[^A-Za-z0-9._-]+")


# --- classification -----------------------------------------------------------


def _mtime(path: str) -> int:
  try:
    return os.stat(path).st_mtime_ns
  except OSError:
    return -1


def _own_transcript(path: str, payload: Dict[str, Any], conv: str) -> bool:
  tp = payload.get("transcriptPath")
  if isinstance(tp, str) and tp:
    own_dir = os.path.dirname(os.path.realpath(os.path.expanduser(tp)))
    return os.path.dirname(os.path.realpath(path)) == own_dir
  return conv in Path(path).parts


def _is_memory(path: str) -> bool:
  p = Path(path)
  return p.name in MEMORY_NAMES or (
      p.suffix == ".md" and p.parent.name in MEMORY_DIRS
  )


def counted(read: reread_shapes.Read, payload: Dict[str, Any], conv: str,
            cwd: str) -> Optional[Tuple[str, str, int]]:
  """(key, label, limit) when this read counts toward a limit, else None."""
  if read.bounded:
    return None
  path = os.path.join(cwd, os.path.expanduser(read.path))
  name = os.path.basename(path)
  if TRANSCRIPT_RE.match(name):
    if not _own_transcript(path, payload, conv):
      return None
    return "transcript", f"own transcript ({name})", TRANSCRIPT_LIMIT
  if name == "SKILL.md" or (_is_memory(path) and read.from_top):
    real = os.path.realpath(path)
    return f"file:{real}:{_mtime(real)}", f"unchanged {name}", FILE_LIMIT
  return None


def reads_of(tool: str, args: Dict[str, Any]) -> List[reread_shapes.Read]:
  if tool in ("view_file", "Read", "read"):
    return reread_shapes.view_reads(args)
  if tool in SHELL_TOOLS:
    cmd = args.get("CommandLine") or args.get("command") or ""
    return reread_shapes.shell_reads(str(cmd))
  return []


# --- state --------------------------------------------------------------------


def data_dir() -> Path:
  return Path(os.environ.get("PAWL_DATA") or os.path.expanduser("~/.pawl"))


def state_path(conv: str) -> Path:
  safe = _SAFE_CONV_RE.sub("_", conv)[:120] or "unknown"
  return data_dir() / "reread" / f"{safe}.json"


def turn_key(payload: Dict[str, Any]) -> str:
  turn = payload.get("turnId") or payload.get("turn_id")
  if isinstance(turn, (str, int)) and str(turn):
    return f"id:{turn}"
  tp = payload.get("transcriptPath") or payload.get("transcript_path")
  try:
    text = Path(os.path.expanduser(str(tp))).read_text(errors="replace")
  except (OSError, TypeError, ValueError):
    return ""
  return f"user_inputs:{sum('USER_INPUT' in l for l in text.splitlines())}"


def load_state(conv: str, turn: str) -> Dict[str, Any]:
  fresh = {"turn": turn, "counts": {}, "denials": 0}
  try:
    raw = json.loads(state_path(conv).read_text(encoding="utf-8"))
  except (OSError, ValueError):
    return fresh
  if not isinstance(raw, dict) or raw.get("turn") != turn:
    return fresh
  if not isinstance(raw.get("counts"), dict):
    return fresh
  return raw


def save_state(conv: str, state: Dict[str, Any]) -> None:
  path = state_path(conv)
  path.parent.mkdir(parents=True, exist_ok=True)
  tmp = path.with_suffix(f".{os.getpid()}.tmp")
  tmp.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
  os.replace(tmp, path)


def record_denial(conv: str, subject: str) -> None:
  """Append one JSON line to PAWL_DATA/denials.jsonl. Never raises."""
  try:
    row = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "conv": conv,
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


# --- decision -----------------------------------------------------------------


def tool_and_args(payload: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
  call = payload.get("toolCall") or payload.get("tool_call") or {}
  if not isinstance(call, dict):
    call = {}
  name = str(call.get("name") or payload.get("tool_name") or "")
  args = call.get("args") or call.get("arguments") or payload.get("tool_input")
  return name, args if isinstance(args, dict) else {}


def _evaluate(payload: Dict[str, Any]) -> Dict[str, str]:
  conv = (
      payload.get("conversationId")
      or payload.get("conversation_id")
      or payload.get("session_id")
  )
  if os.environ.get(OFF_ENV) == "1" or not isinstance(conv, str) or not conv:
    return dict(ALLOW)
  tool, args = tool_and_args(payload)
  cwd = str(args.get("Cwd") or args.get("cwd") or os.getcwd())
  hits = [counted(r, payload, conv, cwd) for r in reads_of(tool, args)]
  hits = [h for h in hits if h is not None]
  if not hits:
    return dict(ALLOW)
  state = load_state(conv, turn_key(payload))
  if state["denials"] >= STAND_DOWN_AFTER:
    return dict(ALLOW)
  reason, subject = "", ""
  for key, label, limit in hits:
    state["counts"][key] = state["counts"].get(key, 0) + 1
    n = state["counts"][key]
    if n > limit and not reason:
      reason = f"{PREFIX} {label} read {n} times this turn.{RECOVERY}"
      subject = key
  if reason:
    state["denials"] += 1
  save_state(conv, state)
  if not reason:
    return dict(ALLOW)
  record_denial(conv, subject)
  return {"decision": "deny", "reason": reason}


def decide(payload: Dict[str, Any]) -> Dict[str, str]:
  """Hook decision for a parsed payload. Fails open on any error."""
  try:
    return _evaluate(payload)
  except Exception as exc:  # pylint: disable=broad-exception-caught
    # fail open: a reread counter never blocks on its own bug
    sys.stderr.write(f"[{HOOK_NAME}] internal error, failing open: {exc!r}\n")
    return dict(ALLOW)


def run_hook(raw: str) -> Dict[str, str]:
  try:
    payload = json.loads(raw or "{}")
  except ValueError:
    return dict(ALLOW)
  return decide(payload) if isinstance(payload, dict) else dict(ALLOW)


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


if __name__ == "__main__":
  hook_main()
