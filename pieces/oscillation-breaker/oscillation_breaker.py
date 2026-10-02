#!/usr/bin/env python3
"""oscillation_breaker.py: prompt when an agent repeats the same tool calls.

An agent stuck in a loop calls the same tool with the same arguments over and
over, or alternates between two or three calls that never change. The host
cannot see it; each call looks fine on its own. This piece keeps a short ring
of recent (tool, args) pairs per conversation and asks a human to confirm
before the next call when the ring shows a repeat:

    same (tool, args) 3 times in a row
    2-gram: the last two calls equal the two before them
    3-gram: the last three calls equal the three before them

Arguments are hashed (sha1 of canonical JSON), never stored. Intent fields
and a growing view_file EndLine are dropped first, and manage_task counts only
as a status check on one task (call_shape.py). For a task status or a .log
read the reason adds that background tasks report when they finish. State
lives at PAWL_DATA/oscillation/<conversation>.json, written with a temp file
and os.replace. Missing conversation id, unparsable stdin, or any internal
error fails open: the call runs and nothing is written.

Each subagent (a payload agentId) keeps its own ring inside the conversation.
The ring is cleared when a turn ends (the `stop` hook entry) and on a
`schedule` call, so cron and timer wakeups, each its own turn, never add up to
a loop. A slow retry loop inside one turn still does. PAWL_OSCILLATION_IDLE_S
also expires a ring idle that long, measured from the last call's start; it is
off by default because a slow retry would expire its own ring.

CLI:
    oscillation_breaker.py check <conversation> <tool> [json-args]
        record one call; exit 0 clear, 1 repeat (reason on stdout)
    oscillation_breaker.py show <conversation>
        print the ring as `tool sha1` lines
    oscillation_breaker.py reset <conversation>
        delete the ring
    oscillation_breaker_hook.py stop
        Stop hook: delete the ring of the payload's conversation, allow

Environment:
    PAWL_DATA                       state dir (default ~/.pawl)
    PAWL_OSCILLATION_WINDOW         ring size (default 16, min 6)
    PAWL_OSCILLATION_IDLE_S         expire a ring idle this long (default 0,
                                    off)
    PAWL_OSCILLATION_WATCHDOG_S     watchdog seconds (default host timeout 15 -
                                    1)

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

import call_shape

HOOK_NAME = "oscillation_breaker"
GATE = "OSCILLATION"
WINDOW_ENV = "PAWL_OSCILLATION_WINDOW"
WATCHDOG_ENV = "PAWL_OSCILLATION_WATCHDOG_S"
IDLE_ENV = "PAWL_OSCILLATION_IDLE_S"
CONV_ENV = "ANTIGRAVITY_CONVERSATION_ID"
HOST_TIMEOUT_S = 15.0
DEFAULT_WINDOW = 16
MIN_WINDOW = 6
TRIPLE = 3

EXEMPT_TOOLS = call_shape.EXEMPT_TOOLS
POLL_HINT = (
    " Background tasks report when they finish; end the turn and wait for"
    " that instead of polling."
)

_SAFE_CONV_RE = re.compile(r"[^A-Za-z0-9._-]+")


# --- ring ---------------------------------------------------------------------


def data_dir() -> Path:
  return Path(os.environ.get("PAWL_DATA") or os.path.expanduser("~/.pawl"))


def window() -> int:
  """Ring size from PAWL_OSCILLATION_WINDOW, floored at MIN_WINDOW."""
  try:
    return max(MIN_WINDOW, int(os.environ.get(WINDOW_ENV) or DEFAULT_WINDOW))
  except ValueError:
    return DEFAULT_WINDOW


def idle_s() -> float:
  """Idle expiry from PAWL_OSCILLATION_IDLE_S; 0 (off) when unset or bad."""
  try:
    return max(0.0, float(os.environ.get(IDLE_ENV) or 0))
  except ValueError:
    return 0.0


def _expired(raw: Dict[str, Any]) -> bool:
  limit = idle_s()
  updated = raw.get("updated")
  if limit <= 0 or not isinstance(updated, (int, float)):
    return False
  return time.time() - updated >= limit


def ring_path(conv: str) -> Path:
  safe = _SAFE_CONV_RE.sub("_", conv)[:120] or "unknown"
  return data_dir() / "oscillation" / f"{safe}.json"


def args_digest(args: Any) -> str:
  """sha1 of the canonical JSON form of a tool call's arguments."""
  canon = json.dumps(args, sort_keys=True, default=str, separators=(",", ":"))
  return hashlib.sha1(canon.encode("utf-8", "replace")).hexdigest()


def load_ring(conv: str) -> List[Tuple[str, str]]:
  """The stored ring for `conv` as (tool, digest) pairs; [] when absent."""
  try:
    raw = json.loads(ring_path(conv).read_text(encoding="utf-8"))
  except (OSError, ValueError):
    return []
  if not isinstance(raw, dict) or _expired(raw):
    return []
  items = raw.get("calls")
  out: List[Tuple[str, str]] = []
  for item in items or []:
    if (
        isinstance(item, list)
        and len(item) == 2
        and all(isinstance(x, str) for x in item)
    ):
      out.append((item[0], item[1]))
  return out


def save_ring(conv: str, ring: List[Tuple[str, str]]) -> None:
  """Write the ring atomically (temp file + os.replace)."""
  path = ring_path(conv)
  path.parent.mkdir(parents=True, exist_ok=True)
  tmp = path.with_suffix(f".{os.getpid()}.tmp")
  body = {"calls": [list(x) for x in ring], "updated": time.time()}
  tmp.write_text(json.dumps(body, sort_keys=True), encoding="utf-8")
  os.replace(tmp, path)


def reset_ring(conv: str) -> None:
  try:
    ring_path(conv).unlink()
  except OSError:
    return


# --- detection ----------------------------------------------------------------


def detect(ring: List[Tuple[str, str]]) -> Optional[Tuple[str, int, int]]:
  """A repeat in `ring`, newest last.

  Args:
    ring: (tool, digest) pairs, oldest first, newest last.

  Returns:
    (tool, repeats, span) or None. repeats is how many times the newest
    call's pattern appeared in a row; span is how many trailing calls the
    pattern covers.
  """
  n = len(ring)
  if n >= TRIPLE and len(set(ring[-TRIPLE:])) == 1:
    tool = ring[-1][0]
    count = 0
    for item in reversed(ring):
      if item != ring[-1]:
        break
      count += 1
    return tool, count, count
  for k in (2, 3):
    if n >= 2 * k and ring[-k:] == ring[-2 * k : -k]:
      tools = sorted({t for t, _ in ring[-k:]})
      return "/".join(tools), 2, 2 * k
  return None


def reason_for(tool: str, repeats: int, span: int, poll: bool = False) -> str:
  hint = POLL_HINT if poll else ""
  return (
      f"[PAWL loop] {tool} repeated with identical args {repeats} times in a"
      f" row (last {span} calls).{hint} Approve to continue."
  )


def observe(conv: str, tool: str, args: Any) -> Optional[str]:
  """Record one call and return a reason when the ring shows a repeat.

  Args:
    conv: conversation id; the ring key.
    tool: tool name.
    args: tool arguments, hashed before storage.

  Returns:
    The reason text on a repeat, else None. A non-exempt call is appended
    either way; a schedule call clears the ring.
  """
  if tool == call_shape.SCHEDULE_TOOL:
    reset_ring(conv)
    return None
  if call_shape.is_exempt(tool, args):
    return None
  ring = load_ring(conv)
  ring.append((tool, args_digest(call_shape.normalize(tool, args))))
  ring = ring[-window() :]
  save_ring(conv, ring)
  hit = detect(ring)
  if hit is None:
    return None
  return reason_for(*hit, poll=call_shape.is_poll(tool, args))


# --- payload ------------------------------------------------------------------


def resolve_conversation_id(payload: Dict[str, Any]) -> str:
  vals = [
      payload.get(k)
      for k in (
          "conversationId",
          "conversation_id",
          "session_id",
          "notify_conversation",
      )
  ]
  vals += [
      os.environ.get(k, "")
      for k in ("CONVERSATION_ID", CONV_ENV, "JETSKI_CONVERSATION_ID")
  ]
  return next((v.strip() for v in vals if isinstance(v, str) and v.strip()), "")


def ring_key(payload: Dict[str, Any], conv: str) -> str:
  """The ring a call lands in: the conversation, or one subagent inside it.

  Claude Code and Codex run subagents under the parent's session id and add
  an agent id; parallel subagents that each run `git status` once are not
  a loop, so each agent gets its own ring.

  Args:
    payload: the hook payload, read for agentId.
    conv: the conversation id.

  Returns:
    conv, or conv.agent when the payload names an agent.
  """
  agent = payload.get("agentId")
  return f"{conv}.{agent}" if isinstance(agent, str) and agent else conv


def tool_and_args(payload: Dict[str, Any]) -> Tuple[str, Any]:
  """(tool name, args) from a tool-call payload; name is '' when absent."""
  call = payload.get("toolCall") or payload.get("tool_call") or {}
  if not isinstance(call, dict):
    call = {}
  name = str(
      call.get("name")
      or payload.get("toolName")
      or payload.get("tool_name")
      or ""
  )
  args = call.get("args")
  if args is None:
    args = call.get("arguments")
  if args is None:
    args = payload.get("toolArgs")
  if args is None:
    args = payload.get("tool_input")
  return name, {} if args is None else args


def read_payload(raw: str) -> Dict[str, Any]:
  """Parse the hook payload; anything unparsable is {} (fail open)."""
  try:
    data = json.loads(raw or "{}")
  except ValueError:
    return {}
  return data if isinstance(data, dict) else {}


# --- denial log ---------------------------------------------------------------


def record_denial(
    gate: str, conv: str, tool: str, outcome: str = "force_ask"
) -> None:
  """Append one JSON line to PAWL_DATA/denials.jsonl. Never raises.

  Args:
    gate: gate name, OSCILLATION.
    conv: conversation id.
    tool: tool name; only its sha1 is stored, matching the other pieces.
    outcome: decision recorded for the row.
  """
  try:
    row = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "conv": conv,
        "hook": HOOK_NAME,
        "gate": gate,
        "cmd_sha1": hashlib.sha1(tool.encode("utf-8", "replace")).hexdigest(),
        "outcome": outcome,
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
  """Hook decision for a parsed payload: allow, or force_ask on a repeat."""
  conv = resolve_conversation_id(payload)
  tool, args = tool_and_args(payload)
  if not conv or not tool:
    return {"decision": "allow"}
  reason = observe(ring_key(payload, conv), tool, args)
  if reason is None:
    return {"decision": "allow"}
  record_denial(GATE, conv, tool)
  return {"decision": "force_ask", "reason": reason}


def run_hook(raw: str) -> Dict[str, str]:
  """Full hook decision for raw stdin text. Fails open on any error."""
  try:
    return decide(read_payload(raw))
  except Exception as exc:  # pylint: disable=broad-exception-caught
    # fail open: a loop breaker never blocks on its own bug
    sys.stderr.write(f"[{HOOK_NAME}] internal error, failing open: {exc!r}\n")
    return {"decision": "allow"}


def end_turn(raw: str) -> Dict[str, str]:
  """Stop hook decision: the turn is over, so clear its ring. Always allow."""
  try:
    payload = read_payload(raw)
    conv = resolve_conversation_id(payload)
    if conv:
      reset_ring(ring_key(payload, conv))
  except Exception as exc:  # pylint: disable=broad-exception-caught
    # fail open: a Stop hook never blocks on its own bug
    sys.stderr.write(f"[{HOOK_NAME}] stop error, failing open: {exc!r}\n")
  return {"decision": "allow"}


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
    sys.stdout.write(json.dumps({"decision": "allow"}))
    sys.stdout.flush()
    sys.stderr.write(
        f"[{HOOK_NAME}] watchdog fired after {seconds}s; failing open\n"
    )
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


def hook_main(stop: bool = False) -> None:
  """PreToolUse entry, or the Stop entry when `stop` is set."""
  handler = end_turn if stop else run_hook
  arm_watchdog(watchdog_budget_s())
  try:
    result = handler(sys.stdin.read())
  finally:
    disarm_watchdog()
  sys.stdout.write(json.dumps(result))
  sys.stdout.flush()


# --- CLI ----------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
  argv = list(sys.argv[1:] if argv is None else argv)
  usage = (
      "usage: oscillation_breaker.py check <conv> <tool> [json-args] |"
      " show <conv> | reset <conv>\n"
  )
  if len(argv) < 2 or argv[0] not in {"check", "show", "reset"}:
    sys.stderr.write(usage)
    return 2
  conv = argv[1]
  if argv[0] == "reset":
    reset_ring(conv)
    return 0
  if argv[0] == "show":
    for tool, digest in load_ring(conv):
      print(f"{tool} {digest}")
    return 0
  if len(argv) < 3:
    sys.stderr.write(usage)
    return 2
  try:
    args = json.loads(argv[3]) if len(argv) > 3 else {}
  except ValueError:
    sys.stderr.write("json-args must be valid JSON\n")
    return 2
  reason = observe(conv, argv[2], args)
  if reason is None:
    print("clear")
    return 0
  print(reason)
  return 1


if __name__ == "__main__":
  sys.exit(main())
