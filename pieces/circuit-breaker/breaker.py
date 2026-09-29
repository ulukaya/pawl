#!/usr/bin/env python3
"""breaker.py: a per-job circuit breaker for scheduled work.

One file, standard library only. Each named job has a breaker with three states:

    CLOSED     the job runs on every tick
    OPEN       the job is skipped until a cooldown passes
    HALF_OPEN  one probe run is allowed; success closes, failure re-opens

Three consecutive failures open the breaker. After the cooldown (default 3600 s) the next
should-run flips it to HALF_OPEN and allows exactly one probe. A probe that fails re-opens the
breaker and doubles the cooldown, up to a cap (default 6 h). A success in any state closes the
breaker and resets the failure count. All breakers live in one JSON file, written atomically
under an exclusive flock.

CLI:

    breaker.py should-run <name>                 exit 0 run, exit 3 skip; prints the state
    breaker.py record <name> ok|fail [--exit-code N]
    breaker.py status [--compact]
    breaker.py reset <name>
    breaker.py run <name> -- <command...>        should-run, exec, record, exit with its code

Every subcommand accepts --state FILE. Environment (all optional):

    BREAKER_STATE      state file (default ~/.local/state/breaker/breakers.json)
    BREAKER_THRESHOLD  consecutive failures that open the breaker (default 3)
    BREAKER_COOLDOWN   first cooldown in seconds (default 3600)
    BREAKER_MAX_COOLDOWN  cooldown cap in seconds (default 21600)
    BREAKER_NOW        epoch seconds to use as the clock; for tests
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

RUN = 0
SKIP = 3
SCHEMA_VERSION = 1

CLOSED = "CLOSED"
OPEN = "OPEN"
HALF_OPEN = "HALF_OPEN"

DEFAULT_THRESHOLD = 3
DEFAULT_COOLDOWN = 3600.0
DEFAULT_MAX_COOLDOWN = 6 * 3600.0

_state_override: Optional[Path] = None


def now() -> float:
    """Current epoch seconds. BREAKER_NOW overrides it so tests can move the clock."""
    raw = os.environ.get("BREAKER_NOW")
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return time.time()


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def threshold() -> int:
    return max(1, int(_env_float("BREAKER_THRESHOLD", DEFAULT_THRESHOLD)))


def base_cooldown() -> float:
    return max(1.0, _env_float("BREAKER_COOLDOWN", DEFAULT_COOLDOWN))


def max_cooldown() -> float:
    return max(base_cooldown(), _env_float("BREAKER_MAX_COOLDOWN", DEFAULT_MAX_COOLDOWN))


def state_path() -> Path:
    if _state_override is not None:
        return _state_override
    return Path(os.environ.get("BREAKER_STATE",
                               os.path.expanduser("~/.local/state/breaker/breakers.json")))


def set_state_path(path: Optional[str]) -> None:
    global _state_override
    _state_override = Path(path).expanduser() if path else None


def _iso(ts: Optional[float]) -> Optional[str]:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fresh() -> Dict[str, Any]:
    return {"state": CLOSED, "failures": 0, "cooldown": base_cooldown(), "opened_at": None,
            "next_probe_at": None, "probe_started_at": None, "last_failure_at": None,
            "last_exit_code": None, "last_ok_at": None}


def _empty_file() -> Dict[str, Any]:
    return {"schema": SCHEMA_VERSION, "breakers": {}}


def _read_unlocked() -> Dict[str, Any]:
    path = state_path()
    if not path.exists():
        return _empty_file()
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return _empty_file()
    if not isinstance(data, dict) or not isinstance(data.get("breakers"), dict):
        return _empty_file()
    return data


def _write_unlocked(data: Dict[str, Any]) -> None:
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".breakers.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


class _Lock:
    """Exclusive flock around read-modify-write so parallel processes cannot lose an update."""

    def __enter__(self) -> "_Lock":
        path = state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(str(path) + ".lock", "a+")
        fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc: Any) -> None:
        fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        self._fh.close()


def _entry(data: Dict[str, Any], name: str) -> Dict[str, Any]:
    entry = _fresh()
    entry.update(data["breakers"].get(name) or {})
    data["breakers"][name] = entry
    return entry


def should_run(name: str) -> Tuple[bool, str]:
    """Decides whether the job may run now. May flip OPEN to HALF_OPEN and persist that."""
    t = now()
    with _Lock():
        data = _read_unlocked()
        entry = data["breakers"].get(name)
        if entry is None:
            return True, CLOSED
        entry = _entry(data, name)
        state = entry["state"]
        if state == CLOSED:
            return True, CLOSED
        if state == OPEN:
            next_probe = entry.get("next_probe_at")
            if next_probe is None:
                next_probe = (entry.get("opened_at") or t) + float(entry["cooldown"])
            if t < next_probe:
                return False, OPEN
            entry["state"] = HALF_OPEN
            entry["probe_started_at"] = t
            _write_unlocked(data)
            return True, HALF_OPEN
        # HALF_OPEN: one probe is out. Allow a second only if the first never reported back
        # within a full cooldown, so a crashed probe cannot wedge the breaker forever.
        started = entry.get("probe_started_at") or t
        if t - started >= float(entry["cooldown"]):
            entry["probe_started_at"] = t
            _write_unlocked(data)
            return True, HALF_OPEN
        return False, HALF_OPEN


def record(name: str, ok: bool, exit_code: Optional[int] = None) -> Dict[str, Any]:
    """Records one outcome and returns the breaker after the transition."""
    t = now()
    with _Lock():
        data = _read_unlocked()
        entry = _entry(data, name)
        if ok:
            entry.update({"state": CLOSED, "failures": 0, "cooldown": base_cooldown(),
                          "opened_at": None, "next_probe_at": None, "probe_started_at": None,
                          "last_ok_at": t, "last_exit_code": 0})
        else:
            entry["failures"] = int(entry["failures"]) + 1
            entry["last_failure_at"] = t
            entry["last_exit_code"] = exit_code
            prior = entry["state"]
            if prior == HALF_OPEN:
                entry["cooldown"] = min(float(entry["cooldown"]) * 2, max_cooldown())
                _open(entry, t)
            elif prior == CLOSED and entry["failures"] >= threshold():
                entry["cooldown"] = base_cooldown()
                _open(entry, t)
        _write_unlocked(data)
        return dict(entry)


def _open(entry: Dict[str, Any], t: float) -> None:
    entry["state"] = OPEN
    entry["opened_at"] = t
    entry["next_probe_at"] = t + float(entry["cooldown"])
    entry["probe_started_at"] = None


def reset(name: str) -> None:
    with _Lock():
        data = _read_unlocked()
        data["breakers"].pop(name, None)
        _write_unlocked(data)


def status() -> List[Dict[str, Any]]:
    with _Lock():
        data = _read_unlocked()
    rows = []
    for name in sorted(data["breakers"]):
        entry = _fresh()
        entry.update(data["breakers"][name])
        rows.append({"name": name, "state": entry["state"], "failures": int(entry["failures"]),
                     "cooldown": float(entry["cooldown"]), "opened_at": entry["opened_at"],
                     "next_probe_at": entry["next_probe_at"],
                     "last_exit_code": entry["last_exit_code"]})
    return rows


def _print_status(compact: bool) -> int:
    rows = status()
    if compact:
        if not rows:
            print("no breakers")
            return 0
        for r in rows:
            print(f"{r['name']}={r['state']} failures={r['failures']}"
                  f" opened_at={_iso(r['opened_at']) or '-'}"
                  f" next_probe_at={_iso(r['next_probe_at']) or '-'}")
        return 0
    if not rows:
        print("No breakers recorded.")
        return 0
    width = max(len(r["name"]) for r in rows)
    print(f"{'name':<{width}}  {'state':<9} {'failures':>8}  {'opened_at':<20} {'next_probe_at':<20}")
    for r in rows:
        print(f"{r['name']:<{width}}  {r['state']:<9} {r['failures']:>8}  "
              f"{_iso(r['opened_at']) or '-':<20} {_iso(r['next_probe_at']) or '-':<20}")
    return 0


def run_wrapped(name: str, command: List[str]) -> int:
    """should-run, execute, record from the exit code, and exit with that code (3 if skipped)."""
    if not command:
        print("run: missing command after --", file=sys.stderr)
        return 2
    allowed, state = should_run(name)
    if not allowed:
        print(f"SKIP {name}: breaker {state}", file=sys.stderr)
        return SKIP
    try:
        code = subprocess.run(command).returncode
    except OSError as err:
        print(f"{name}: could not start {command[0]}: {err}", file=sys.stderr)
        code = 127
    record(name, code == 0, exit_code=code)
    return code


def main(argv: Optional[List[str]] = None) -> int:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--state", default=None, help="state file (default BREAKER_STATE)")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0], parents=[common])
    sub = parser.add_subparsers(dest="cmd")

    p = sub.add_parser("should-run", parents=[common])
    p.add_argument("name")
    p = sub.add_parser("record", parents=[common])
    p.add_argument("name")
    p.add_argument("outcome", choices=["ok", "fail"])
    p.add_argument("--exit-code", type=int, default=None)
    p = sub.add_parser("status", parents=[common])
    p.add_argument("--compact", action="store_true")
    p = sub.add_parser("reset", parents=[common])
    p.add_argument("name")
    p = sub.add_parser("run", parents=[common])
    p.add_argument("name")

    # Everything after the first "--" is the wrapped command, untouched by argparse.
    argv = list(sys.argv[1:] if argv is None else argv)
    command: List[str] = []
    if "--" in argv:
        split = argv.index("--")
        argv, command = argv[:split], argv[split + 1:]

    args = parser.parse_args(argv)
    set_state_path(args.state)
    cmd = args.cmd or "status"

    if cmd == "should-run":
        allowed, state = should_run(args.name)
        print(state)
        return RUN if allowed else SKIP
    if cmd == "record":
        entry = record(args.name, args.outcome == "ok", exit_code=args.exit_code)
        print(f"{args.name} {entry['state']} failures={entry['failures']}")
        return 0
    if cmd == "reset":
        reset(args.name)
        print(f"{args.name} CLOSED failures=0")
        return 0
    if cmd == "run":
        return run_wrapped(args.name, command)
    return _print_status(args.compact)


if __name__ == "__main__":
    sys.exit(main())
