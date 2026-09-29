#!/usr/bin/env python3
"""idle_task_gate.py: Stop hook that blocks a turn end while stale tasks run.

When the agent stops, this piece walks /proc (or PAWL_IDLE_TASK_PROC_ROOT) for
task-root processes tagged with this conversation's id that have run longer
than the age limit. On the first stop with a given set of stale pids it blocks
the stop and names them so the agent can clean up. On the second stop with the
same set it terminates the ones whose command line is an unbounded wait shape
(a poll loop, `tail -f`, a long `sleep`) and lets the stop through. Anything
else that is old but not a wait shape is left running and reported on stderr.

A task root is a pid whose own environ carries the conversation id and whose
parent does not. The process group is signalled (`os.killpg`) so a shell
wrapper's children go with it.

Payload (Stop event, Jetski / Antigravity style):
    {"conversationId": "..."}      also accepted: conversation_id,
                                   notify_conversation,
                                   or env CONVERSATION_ID /
                                   ANTIGRAVITY_CONVERSATION_ID /
                                   JETSKI_CONVERSATION_ID
Output:
    {"decision": "allow"}
    {"decision": "block", "reason": "[IDLE TASK] ..."}

CLI:
    idle_task_gate.py list <conversation-id>      print stale roots as JSON
                                                  rows, exit 0
    idle_task_gate.py classify <command...>       LOOP | TAIL | SLEEP | none

Environment:
    PAWL_IDLE_TASK_MINUTES     age limit in minutes (default 10)
    PAWL_IDLE_TASK_ALLOW_RE    regex; matching command lines are never reported
                               or signalled
    PAWL_IDLE_TASK_PROC_ROOT   procfs root (default /proc); tests point it at a
                               fake tree
    PAWL_IDLE_TASK_STATE       breaker state file
                               (default PAWL_DATA/idle_task_gate.json)
    PAWL_DATA                  state and denial log dir (default ~/.pawl)
    PAWL_IDLE_TASK_WATCHDOG_S  watchdog seconds (default host timeout 15 - 1)

A Stop hook fails open: unparsable stdin, a missing /proc, or an internal error
all allow. The watchdog also prints allow when the scan runs past its budget.
Standard library only.

The block between `# CLASSIFY BEGIN` and `# CLASSIFY END` is a verbatim copy of
the same block in the poll-loop-guard piece; test_idle_task_gate.py asserts the
two are identical.
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
from typing import Any, Dict, List, Optional

HOOK_NAME = "idle_task_gate"
GATE = "IDLE_TASK"
ALLOW: Dict[str, Any] = {"decision": "allow"}
CONV_ENV = "ANTIGRAVITY_CONVERSATION_ID"
MINUTES_ENV = "PAWL_IDLE_TASK_MINUTES"
ALLOW_RE_ENV = "PAWL_IDLE_TASK_ALLOW_RE"
PROC_ROOT_ENV = "PAWL_IDLE_TASK_PROC_ROOT"
STATE_ENV = "PAWL_IDLE_TASK_STATE"
WATCHDOG_ENV = "PAWL_IDLE_TASK_WATCHDOG_S"
HOST_TIMEOUT_S = 15.0
DEFAULT_MINUTES = 10.0
MAX_BLOCKS_PER_FINGERPRINT = 1  # block once per identical pid set, then act
MAX_SLEEP_S = 600  # referenced by the vendored classify block
_SELF_MARK = "idle_task_gate"

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


def _warn(message: str) -> None:
  sys.stderr.write(f"[{HOOK_NAME}] {message}\n")


# --- configuration ------------------------------------------------------------


def data_dir() -> Path:
  return Path(os.environ.get("PAWL_DATA") or os.path.expanduser("~/.pawl"))


def _proc_root() -> Path:
  return Path(os.environ.get(PROC_ROOT_ENV) or "/proc")


def max_age_s() -> float:
  """Age limit in seconds from PAWL_IDLE_TASK_MINUTES (default 10 minutes)."""
  try:
    minutes = float(os.environ.get(MINUTES_ENV) or DEFAULT_MINUTES)
  except ValueError:
    minutes = DEFAULT_MINUTES
  return max(0.0, minutes) * 60.0


def _state_path() -> Path:
  return Path(os.environ.get(STATE_ENV) or data_dir() / "idle_task_gate.json")


def resolve_conversation_id(payload: Dict[str, Any]) -> str:
  vals = [
      payload.get(k)
      for k in ("conversationId", "conversation_id", "notify_conversation")
  ]
  vals += [
      os.environ.get(k, "")
      for k in ("CONVERSATION_ID", CONV_ENV, "JETSKI_CONVERSATION_ID")
  ]
  return next((v.strip() for v in vals if isinstance(v, str) and v.strip()), "")


# --- procfs readers -----------------------------------------------------------


def _read(path: Path) -> bytes:
  try:
    return path.read_bytes()
  except OSError:
    return b""


def _conv_of(proc: Path, pid: str) -> str:
  m = re.search(
      rb"(?:^|\0)" + CONV_ENV.encode() + rb"=([^\0]*)",
      _read(proc / pid / "environ"),
  )
  return m.group(1).decode("utf-8", "replace") if m else ""


def _stat_fields(proc: Path, pid: str) -> List[str]:
  """Fields after `(comm)` in /proc/<pid>/stat: 1 ppid, 2 pgid, 19 start."""
  return (
      _read(proc / pid / "stat")
      .decode("utf-8", "replace")
      .rsplit(")", 1)[-1]
      .split()
  )


def _age_s(proc: Path, pid: str, uptime_s: float, hz: float) -> float:
  fields = _stat_fields(proc, pid)
  try:
    return uptime_s - int(fields[19]) / hz
  except (IndexError, ValueError):
    return 0.0


def _ppid(proc: Path, pid: str) -> str:
  fields = _stat_fields(proc, pid)
  return fields[1] if len(fields) > 1 else ""


def _pgid(proc: Path, pid: str) -> int:
  fields = _stat_fields(proc, pid)
  try:
    return int(fields[2])
  except (IndexError, ValueError):
    return 0


def _uptime_s(proc: Path) -> float:
  try:
    return float(_read(proc / "uptime").split()[0])
  except (IndexError, ValueError):
    return 0.0


def _clock_ticks() -> float:
  try:
    return float(os.sysconf("SC_CLK_TCK"))
  except (ValueError, OSError, AttributeError):
    return 100.0


def idle_task_roots(conv: str) -> List[Dict[str, Any]]:
  """Task roots owned by `conv` past the age limit: [{pid, pgid, age, cmd}]."""
  proc = _proc_root()
  if not proc.is_dir():
    return []
  uptime, hz = _uptime_s(proc), _clock_ticks()
  limit = max_age_s()
  allow_re = (
      re.compile(os.environ[ALLOW_RE_ENV])
      if os.environ.get(ALLOW_RE_ENV)
      else None
  )
  out: List[Dict[str, Any]] = []
  for pid in (p.name for p in proc.iterdir() if p.name.isdigit()):
    if pid == str(os.getpid()) or _conv_of(proc, pid) != conv:
      continue
    cmd = (
        _read(proc / pid / "cmdline")
        .replace(b"\0", b" ")
        .decode("utf-8", "replace")
        .strip()
    )
    if _SELF_MARK in cmd or _conv_of(proc, _ppid(proc, pid)) == conv:
      continue
    age = _age_s(proc, pid, uptime, hz)
    if age <= limit or (allow_re and allow_re.search(cmd)):
      continue
    out.append(
        {"pid": int(pid), "pgid": _pgid(proc, pid), "age": int(age), "cmd": cmd}
    )
  return sorted(out, key=lambda r: r["pid"])


# --- breaker: block once per pid set ------------------------------------------


def breaker_permits_block(conv_id: str, fingerprint: str) -> bool:
  """Decide whether this stop may block or must act.

  Args:
    conv_id: Conversation the stop belongs to.
    fingerprint: Sorted pid set of the stale tasks seen on this stop.

  Returns:
    True on the first stop with this pid set; False (act instead) on
    repeats. The state file is written atomically.
  """
  path = _state_path()
  state: Dict[str, Any] = {}
  try:
    loaded = (
        json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    )
    state = loaded if isinstance(loaded, dict) else {}
  except (OSError, ValueError) as err:
    _warn(f"state read warning: {err}")
  entry = state.get(conv_id)
  blocks = (
      int(entry.get("blocks", 0))
      if isinstance(entry, dict) and entry.get("fingerprint") == fingerprint
      else 0
  )
  permitted = blocks < MAX_BLOCKS_PER_FINGERPRINT
  state[conv_id] = {"fingerprint": fingerprint, "blocks": blocks + 1}
  try:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)
  except OSError as err:
    _warn(f"state write warning: {err}")
  return permitted


# --- termination --------------------------------------------------------------


def _signal_root(pid: int, pgid: int) -> None:
  """SIGTERM the task's process group, falling back to the pid."""
  if pgid > 1 and pgid != os.getpgrp():
    try:
      os.killpg(pgid, signal.SIGTERM)
      return
    except OSError as err:
      _warn(f"killpg {pgid} failed ({err}); signalling pid {pid}")
  os.kill(pid, signal.SIGTERM)


def terminate_idle_waits(rows: List[Dict[str, Any]]) -> List[int]:
  """SIGTERM the roots whose argv is a wait shape; returns pids signalled."""
  killed: List[int] = []
  for row in rows:
    if classify(str(row["cmd"])) is None:
      _warn(
          f"left running (not a wait shape): pid {row['pid']} {row['cmd'][:80]}"
      )
      continue
    try:
      _signal_root(int(row["pid"]), int(row.get("pgid", 0)))
      killed.append(int(row["pid"]))
    except OSError as err:
      _warn(f"kill {row['pid']} failed: {err}")
  return killed


def _describe(rows: List[Dict[str, Any]]) -> str:
  return "; ".join(
      f"pid {r['pid']} ({r['age'] // 60}m):"
      f" {' '.join(str(r['cmd']).split())[:90]}"
      for r in rows
  )


# --- denial log ---------------------------------------------------------------


def record_denial(
    gate: str, detail: str, payload: Optional[Dict[str, Any]]
) -> None:
  """Append one JSON line to PAWL_DATA/denials.jsonl. Never raises."""
  try:
    conv = ""
    if isinstance(payload, dict):
      conv = resolve_conversation_id(payload)
    row = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "conv": conv,
        "hook": HOOK_NAME,
        "gate": gate,
        "cmd_sha1": hashlib.sha1(detail.encode("utf-8", "replace")).hexdigest(),
    }
    path = data_dir() / "denials.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
      fh.write(json.dumps(row, sort_keys=True) + "\n")
  except Exception:  # pylint: disable=broad-exception-caught
    # fail open: telemetry must never break a hook
    return


# --- decision -----------------------------------------------------------------


def decide(payload: Dict[str, Any]) -> Dict[str, Any]:
  """Stop decision: block once per stale pid set, then terminate and allow."""
  conv = resolve_conversation_id(payload)
  if not conv:
    return ALLOW
  rows = idle_task_roots(conv)
  if not rows:
    return ALLOW
  fingerprint = ",".join(str(r["pid"]) for r in rows)
  if breaker_permits_block(conv, fingerprint):
    record_denial(GATE, fingerprint, payload)
    return {
        "decision": "block",
        "reason": (
            f"[IDLE TASK] {len(rows)} background task(s) from this conversation"
            f" have run past {int(max_age_s()) // 60}m: {_describe(rows)}. Kill"
            " them with manage_task (or `kill <pid>`), or stop again to let"
            " the gate terminate the unbounded-wait ones."
        ),
    }
  killed = terminate_idle_waits(rows)
  _warn(f"second stop with same idle set; terminated {killed or 'none'}")
  return ALLOW


def read_payload(raw: str) -> Dict[str, Any]:
  """Stop payload as a dict; anything unparsable becomes {} (fail open)."""
  try:
    data = json.loads(raw or "{}")
  except ValueError:
    return {}
  return data if isinstance(data, dict) else {}


def run_hook(raw: str) -> Dict[str, Any]:
  """Full hook decision for raw stdin text.

  Args:
    raw: The JSON text read from stdin.

  Returns:
    The hook decision dict; any internal error allows the stop.
  """
  try:
    return decide(read_payload(raw))
  except Exception as err:  # pylint: disable=broad-exception-caught
    # fail open: a Stop hook must never block on its own error
    _warn(f"unexpected error, allowing stop: {err!r}")
    return ALLOW


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
    sys.stdout.write(json.dumps(ALLOW))
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
  if len(argv) >= 2 and argv[0] == "list":
    for row in idle_task_roots(argv[1]):
      print(json.dumps(row, sort_keys=True))
    return 0
  if len(argv) >= 2 and argv[0] == "classify":
    print(classify(" ".join(argv[1:])) or "none")
    return 0
  sys.stderr.write(
      "usage: idle_task_gate.py list <conversation-id> | classify"
      " <command...>\n"
  )
  return 2


if __name__ == "__main__":
  sys.exit(main())
