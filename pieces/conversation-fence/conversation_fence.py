#!/usr/bin/env python3
"""conversation_fence.py: ask before a tool call reads another conversation.

Jetski and Antigravity keep every conversation's transcript, artifacts and
scratch files on local disk, so an agent in one conversation can read a
sibling's private context with a plain view_file or shell read. This piece
asks the user first when a tool call's arguments point at, under a store root:

  brain/<id>/...                another conversation's brain directory;
  conversations/<id>.*          another conversation's transcript;
  brain/ or conversations/      a listing or sweep of every conversation;
  conversation_summaries.db     every conversation's title and summary.

This conversation and its direct parent or child pass without a prompt. The
lineage lookup opens conversation_summaries.db read-only (mode=ro, 0.5 s
timeout) and reads only conversation_id and parent_conversation_id; a failed
lookup counts as not lineage. Paths come from every string argument, and from
every shell word (quotes removed, `~` expanded, `--flag=` values split,
relative paths joined to the call's Cwd); symlinks are followed.

Decision on stdout: {"decision": "allow"} or
{"decision": "force_ask", "reason": "[PAWL fence] ..."}; force_ask prompts
even under auto-execution. Each hit appends one row to
PAWL_DATA/denials.jsonl with gate CONVERSATION_FENCE and the argument sha1,
never the path. Fails open on bad stdin, a missing conversation id, an
internal error and the watchdog.

Limits: the fence reads tool arguments, so a script that builds the path at
run time is not fenced. It stops casual reads and broad sweeps; it is not a
sandbox.

Environment:
    PAWL_DATA                         log dir (default ~/.pawl)
    PAWL_CONVERSATION_ROOTS           colon-separated store roots (default
                                      ~/.gemini/antigravity, ~/.gemini/jetski,
                                      ~/.antigravity, ~/.jetski)
    PAWL_CONVERSATION_FENCE_STRICT    1 turns the prompt into a deny
    PAWL_CONVERSATION_FENCE_OFF       1 turns the piece off
    PAWL_CONVERSATION_FENCE_WATCHDOG_S  watchdog seconds (default 14)

Standard library only.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path  # pylint: disable=g-importing-member
import shlex
import signal
import sqlite3
import sys
import time
from typing import Any, Dict, Iterator, List, Optional, Tuple
import urllib.parse

HOOK_NAME = "conversation_fence"
GATE = "CONVERSATION_FENCE"
PREFIX = "[PAWL fence]"
ROOTS_ENV = "PAWL_CONVERSATION_ROOTS"
STRICT_ENV = "PAWL_CONVERSATION_FENCE_STRICT"
OFF_ENV = "PAWL_CONVERSATION_FENCE_OFF"
WATCHDOG_ENV = "PAWL_CONVERSATION_FENCE_WATCHDOG_S"
HOST_TIMEOUT_S = 15.0
LOOKUP_TIMEOUT_S = 0.5
ALLOW = {"decision": "allow"}
DEFAULT_ROOTS = (
    "~/.gemini/antigravity", "~/.gemini/jetski", "~/.antigravity", "~/.jetski",
)
SUMMARIES_DB = "conversation_summaries.db"
STORES = ("brain", "conversations")
SHELL_KEYS = frozenset({"CommandLine", "command"})


# --- paths --------------------------------------------------------------------


def roots() -> List[str]:
  raw = os.environ.get(ROOTS_ENV)
  items = raw.split(":") if raw is not None else list(DEFAULT_ROOTS)
  return [os.path.expanduser(r.strip()) for r in items if r.strip()]


def _strings(value: Any, key: str = "") -> Iterator[Tuple[str, str]]:
  """(arg key, string) for every string anywhere in the arguments."""
  if isinstance(value, str):
    yield key, value
  elif isinstance(value, dict):
    for k, v in value.items():
      yield from _strings(v, str(k))
  elif isinstance(value, list):
    for v in value:
      yield from _strings(v, key)


def _shell_words(cmd: str) -> List[str]:
  try:
    words = shlex.split(cmd)
  except ValueError:
    words = cmd.split()
  return words + [w.split("=", 1)[1] for w in words if "=" in w]


def candidates(args: Dict[str, Any]) -> List[str]:
  """Absolute path candidates named by the call's arguments."""
  cwd = str(args.get("Cwd") or args.get("cwd") or "")
  out: List[str] = []
  for key, text in _strings(args):
    words = _shell_words(text) if key in SHELL_KEYS else [text]
    for word in words:
      path = os.path.expanduser(word)
      if not os.path.isabs(path) and cwd:
        path = os.path.join(cwd, path)
      if os.path.isabs(path):
        out.append(path)
  return out


def _forms(path: str) -> List[str]:
  return list({os.path.normpath(path), os.path.realpath(path)})


def store_hit(path: str, root: str) -> Optional[Tuple[str, str]]:
  """(what, conversation id or '') when `path` reaches into `root`'s stores."""
  for p in _forms(path):
    for r in _forms(root):
      if p != r and not p.startswith(r.rstrip(os.sep) + os.sep):
        continue
      parts = Path(os.path.relpath(p, r)).parts
      if parts and parts[0] == SUMMARIES_DB:
        return "conversation summaries", ""
      if parts and parts[0] in STORES:
        conv = parts[1].split(".", 1)[0] if len(parts) > 1 else ""
        return f"{parts[0]}/", conv
  return None


# --- lineage ------------------------------------------------------------------


def parents(root: str, own: str, other: str) -> Dict[str, str]:
  """conversation_id -> parent_conversation_id from the summaries db."""
  db = os.path.join(root, SUMMARIES_DB)
  if not os.path.isfile(db):
    return {}
  uri = f"file:{urllib.parse.quote(db)}?mode=ro"
  try:
    con = sqlite3.connect(uri, uri=True, timeout=LOOKUP_TIMEOUT_S)
    try:
      rows = con.execute(
          "SELECT conversation_id, parent_conversation_id FROM"
          " conversation_summaries WHERE conversation_id IN (?, ?)",
          (own, other),
      ).fetchall()
    finally:
      con.close()
  except sqlite3.Error:
    return {}
  return {str(c): str(p or "") for c, p in rows}


def lineage(root: str, own: str, other: str) -> bool:
  """True when `other` is `own`'s direct parent or child."""
  known = parents(root, own, other)
  return known.get(own) == other or known.get(other) == own


# --- decision -----------------------------------------------------------------


def fence_reason(tool: str, args: Dict[str, Any], own: str) -> str:
  """Reason when the call reads outside this conversation's lineage."""
  for path in candidates(args):
    for root in roots():
      hit = store_hit(path, root)
      if hit is None:
        continue
      what, conv = hit
      if conv == own or (conv and lineage(root, own, conv)):
        continue
      whose = f"conversation {conv}'s {what}" if conv else f"all of {what}"
      if what == "conversation summaries":
        whose = "every conversation's title and summary"
      return (
          f"{PREFIX} {tool} reads {whose}, outside this conversation and its"
          " parent or child. Each conversation's context stays its own unless"
          " the user says otherwise; approve only if the user asked for it."
      )
  return ""


def data_dir() -> Path:
  return Path(os.environ.get("PAWL_DATA") or os.path.expanduser("~/.pawl"))


def record_hit(conv: str, args: Dict[str, Any], outcome: str) -> None:
  """Append one JSON line to PAWL_DATA/denials.jsonl. Never raises."""
  try:
    blob = json.dumps(args, sort_keys=True, default=str)
    row = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "conv": conv,
        "hook": HOOK_NAME,
        "gate": GATE,
        "cmd_sha1": hashlib.sha1(blob.encode("utf-8", "replace")).hexdigest(),
        "outcome": outcome,
    }
    path = data_dir() / "denials.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
      fh.write(json.dumps(row, sort_keys=True) + "\n")
  except Exception:  # pylint: disable=broad-exception-caught
    # fail open: telemetry must never break a hook
    return


def tool_and_args(payload: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
  call = payload.get("toolCall") or payload.get("tool_call") or {}
  if not isinstance(call, dict):
    call = {}
  name = str(call.get("name") or payload.get("tool_name") or "")
  args = call.get("args") or call.get("arguments") or payload.get("tool_input")
  return name, args if isinstance(args, dict) else {}


def _evaluate(payload: Dict[str, Any]) -> Dict[str, str]:
  own = payload.get("conversationId") or payload.get("conversation_id")
  if os.environ.get(OFF_ENV) == "1" or not isinstance(own, str) or not own:
    return dict(ALLOW)
  tool, args = tool_and_args(payload)
  reason = fence_reason(tool, args, own)
  if not reason:
    return dict(ALLOW)
  decision = "deny" if os.environ.get(STRICT_ENV) == "1" else "force_ask"
  record_hit(own, args, decision)
  return {"decision": decision, "reason": reason}


def decide(payload: Dict[str, Any]) -> Dict[str, str]:
  """Hook decision for a parsed payload. Fails open on any error."""
  try:
    return _evaluate(payload)
  except Exception as exc:  # pylint: disable=broad-exception-caught
    # fail open: the fence is a prompt, never a crash
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
