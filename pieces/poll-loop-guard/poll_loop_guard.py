#!/usr/bin/env python3
"""poll_loop_guard.py: deny shell commands that are unbounded waits.

The host wakes the agent when a background task finishes, so a hand-rolled
watcher (`while True: ... sleep(2)`, `tail -f`, `sleep 3600`) only burns a
process slot and, when the phrase it polls for never appears, sits idle for
hours. This piece classifies a command line and returns a denial label:

    LOOP    an unbounded loop (`while True`, `while :`, `while true`, `until`)
            in the same command as a sleep (`time.sleep(`, `asyncio.sleep(`,
            `sleep N`)
    TAIL    `tail -f` / `tail -F`, or `watch`
    SLEEP   a single `sleep N` where N exceeds MAX_SLEEP_S

Allowed when the command is bounded: a leading `timeout N` (N <= MAX_SLEEP_S,
optional `s`/`m`/`h` suffix, optional `cd X &&` prefix) wraps it, or the loop
is a counted `for ... in range(...)` with no unbounded loop beside it. Matching
is literal on the command text: a heredoc that merely mentions `while True`
still denies. Wrap it in `timeout`.

CLI:
    poll_loop_guard.py classify <command...>   print LOOP, TAIL, SLEEP or none;
                                               exit 0
    poll_loop_guard.py check <command...>      exit 0 bounded, 1 unbounded
                                               (reason on stdout)

Environment:
    PAWL_DATA                      denial log dir (default ~/.pawl)
    PAWL_POLL_LOOP_GUARD_WATCHDOG_S  watchdog seconds (default host timeout 15 -
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

HOOK_NAME = "poll_loop_guard"
GATE = "POLL_LOOP"
WATCHDOG_ENV = "PAWL_POLL_LOOP_GUARD_WATCHDOG_S"
HOST_TIMEOUT_S = 15.0
MAX_SLEEP_S = 600

# --- classify -----------------------------------------------------------------
# The block between the CLASSIFY BEGIN/END markers is vendored verbatim into
# pieces/idle-task-gate/idle_task_gate.py; a test in that piece asserts the
# two copies match.
# CLASSIFY BEGIN
_LOOP_RE = re.compile(
    r"\bwhile\s+(?:True|true|1|:)(?![\w.])"
    r"|\bwhile\s*\(\s*(?:true|1)\s*\)|\buntil\s+\S"
)
_SLEEP_CALL_RE = re.compile(
    r"\b(?:time\.|asyncio\.)?sleep\s*\(\s*\d|\bsleep\s+\d"
)
_TAIL_RE = re.compile(
    r"\btail\b[^;&|\n]*\s(?:-[a-zA-Z]*[fF]\b|--follow\b)|(?:^|[;&|]\s*)watch\s+"
)
_BARE_SLEEP_RE = re.compile(r"\bsleep\s+(\d+(?:\.\d+)?)([smh]?)\b")
_TIMEOUT_PREFIX_RE = re.compile(
    r"^\s*(?:cd\s+\S+\s*&&\s*)?(?:[A-Z_][A-Z0-9_]*=\S*\s+)*timeout\s+"
    r"(?:-\S+\s+)*(\d+(?:\.\d+)?)([smh]?)\b"
)
_UNIT_S = {"": 1.0, "s": 1.0, "m": 60.0, "h": 3600.0}


def _seconds(value: str, unit: str) -> float:
  return float(value) * _UNIT_S.get(unit, 1.0)


def bounded_by_timeout(cmd: str) -> bool:
  """True when the whole command sits under `timeout N`, N <= MAX_SLEEP_S."""
  m = _TIMEOUT_PREFIX_RE.match(cmd)
  return bool(m) and _seconds(m.group(1), m.group(2)) <= MAX_SLEEP_S


def _longest_bare_sleep(cmd: str) -> float:
  return max(
      (_seconds(v, u) for v, u in _BARE_SLEEP_RE.findall(cmd)), default=0.0
  )


def classify(cmd: str) -> Optional[str]:
  """The denial label for `cmd` (LOOP, TAIL, SLEEP); None when bounded."""
  if not cmd.strip() or bounded_by_timeout(cmd):
    return None
  if _LOOP_RE.search(cmd) and _SLEEP_CALL_RE.search(cmd):
    return "LOOP"
  if _TAIL_RE.search(cmd):
    return "TAIL"
  if _longest_bare_sleep(cmd) > MAX_SLEEP_S:
    return "SLEEP"
  return None


# CLASSIFY END


def deny_reason(label: str, cmd: str) -> str:
  head = " ".join(cmd.split())[:120]
  return (
      f"[POLL LOOP] {label}: unbounded wait in `{head}`. Background tasks"
      " notify on completion; end the turn, or use the schedule tool with"
      f" TimerCondition=<task-id>. Bounded form: `timeout {MAX_SLEEP_S} <cmd>`."
  )


# --- payload ------------------------------------------------------------------


def command_of(payload: Dict[str, Any]) -> str:
  """The shell command in a tool-call payload; '' when not run_command."""
  call = payload.get("toolCall") or payload.get("tool_call") or {}
  if not isinstance(call, dict):
    call = {}
  name = str(
      call.get("name")
      or payload.get("toolName")
      or payload.get("tool_name")
      or ""
  )
  if name not in {
      "run_command", "run_shell_command", "Bash", "bash", "exec_command",
      "shell",
  }:
    return ""
  args = (
      call.get("args")
      or call.get("arguments")
      or payload.get("toolArgs")
      or payload.get("tool_input")
      or {}
  )
  if not isinstance(args, dict):
    return ""
  return str(args.get("CommandLine") or args.get("command") or "")


def read_payload(raw: str) -> Tuple[Optional[Dict[str, Any]], str]:
  """Parse the hook payload.

  Args:
    raw: The JSON text read from stdin.

  Returns:
    (payload, '') on success or (None, deny reason); unparsable input is
    a deny.
  """
  try:
    data = json.loads(raw or "{}")
  except ValueError as err:
    return (
        None,
        (
            f"[HOOK PAYLOAD] {HOOK_NAME}: stdin is not JSON ({err}); refusing"
            " the call."
        ),
    )
  if not isinstance(data, dict):
    return (
        None,
        (
            f"[HOOK PAYLOAD] {HOOK_NAME}: payload is {type(data).__name__}, not"
            " an object; refusing the call."
        ),
    )
  return data, ""


def decide(payload: Dict[str, Any]) -> Optional[str]:
  """Deny reason for this payload, or None to allow."""
  cmd = command_of(payload)
  label = classify(cmd) if cmd else None
  return deny_reason(label, cmd) if label else None


# --- denial log ---------------------------------------------------------------


def data_dir() -> Path:
  return Path(os.environ.get("PAWL_DATA") or os.path.expanduser("~/.pawl"))


def record_denial(
    gate: str,
    cmd: str,
    payload: Optional[Dict[str, Any]],
    outcome: str = "deny",
) -> None:
  """Append one JSON line to PAWL_DATA/denials.jsonl. Never raises.

  Args:
      gate: gate name, e.g. POLL_LOOP.
      cmd: the command line; only its sha1 is stored.
      payload: the hook payload, read for a conversation id.
      outcome: "deny" for the piece hook, "force_ask" for the plugin hook.
  """
  try:
    conv = ""
    if isinstance(payload, dict):
      conv = str(
          payload.get("conversationId") or payload.get("conversation_id") or ""
      )
    conv = conv or os.environ.get("CONVERSATION_ID", "")
    row = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "conv": conv,
        "hook": HOOK_NAME,
        "gate": gate,
        "cmd_sha1": hashlib.sha1(cmd.encode("utf-8", "replace")).hexdigest(),
        "outcome": outcome,
    }
    path = data_dir() / "denials.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
      fh.write(json.dumps(row, sort_keys=True) + "\n")
  except Exception:  # pylint: disable=broad-exception-caught
    # fail open: telemetry must never break a hook
    return


# --- watchdog -----------------------------------------------------------------


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


# --- hook entry ---------------------------------------------------------------


def evaluate(raw: str) -> Tuple[str, str, str, Optional[Dict[str, Any]]]:
  """Decision for raw stdin text without touching the denial log.

  Args:
      raw: the hook payload text.

  Returns:
      (decision, reason, cmd, payload). decision is "allow" or "deny"; reason
      is "" on allow; payload is None when stdin was unparsable.
  """
  payload, err = read_payload(raw)
  if payload is None:
    return "deny", err, "", None
  cmd = command_of(payload)
  reason = decide(payload)
  if reason is None:
    return "allow", "", cmd, payload
  return "deny", reason, cmd, payload


def run_hook(raw: str) -> Dict[str, str]:
  """Full hook decision for raw stdin text: payload, then classify, then log."""
  decision, reason, cmd, payload = evaluate(raw)
  if decision == "allow":
    return {"decision": "allow"}
  if payload is not None:
    record_denial(GATE, cmd, payload)
  return {"decision": "deny", "reason": reason}


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
  if not argv or argv[0] not in {"classify", "check"}:
    sys.stderr.write("usage: poll_loop_guard.py classify|check <command...>\n")
    return 2
  cmd = " ".join(argv[1:])
  label = classify(cmd)
  if argv[0] == "classify":
    print(label or "none")
    return 0
  if label is None:
    print("bounded")
    return 0
  print(deny_reason(label, cmd))
  return 1


if __name__ == "__main__":
  sys.exit(main())
