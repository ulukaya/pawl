#!/usr/bin/env python3
"""send_budget.py: a per-channel daily send budget that fails closed.

One file, standard library only. Every outbound message an agent sends (chat DM, room post,
email, social post) burns one unit from a per-channel daily ceiling. At the ceiling the next
send is denied until the local day rolls over. A quiet window denies the owner-DM channel
overnight. The counter is a single JSON file written atomically under an exclusive flock, so
parallel processes cannot lose a count.

CLI:

    send_budget.py status [--json|--compact]
    send_budget.py check    --channel dm_owner
    send_budget.py spend    --channel dm_owner [--reason ...] [--dry-run]
    send_budget.py classify --command '<shell command line>'
    send_budget.py reset    --channel dm_owner | --all

Exit codes for check/spend: 0 allowed, 3 denied. SEND_BUDGET_OVERRIDE=1 forces allow and
records the burn as overridden, for a send the human asked for right now.

Configuration (all optional, all environment variables):

    SEND_BUDGET_STATE_DIR   where the counter lives (default ~/.local/state/send_budget)
    SEND_BUDGET_TZ          IANA zone for the local day (default: system local time)
    SEND_BUDGET_OWNER_SPACE substring that marks a chat send as a DM to the owner
    SEND_BUDGET_CEILINGS    JSON object overriding ceilings, e.g. {"email": 2}
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shlex
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

ALLOW = 0
DENY = 3
SCHEMA_VERSION = 1
HISTORY_DAYS = 30
OVERRIDE_ENV = "SEND_BUDGET_OVERRIDE"

DEFAULT_CEILINGS: Dict[str, int] = {
    "dm_owner": 12,
    "chat_space": 8,
    "email": 6,
    "social": 3,
}

QUIET_START_HOUR = 22
QUIET_END_HOUR = 7
QUIET_CHANNELS = frozenset({"dm_owner"})

# Command shapes that count as a send: the executable basename must be a send
# tool AND one of its first bare subcommand tokens must be a send verb. Reads
# (list, get, search), --help, and mentions of a tool inside message text,
# heredocs, grep patterns or file paths never match.
_CHAT_TOOL = re.compile(r"^(gchat\w*|slack|chat)$", re.I)
_MAIL_TOOL = re.compile(r"^(gmail\w*|mail|sendmail)$", re.I)
_SOCIAL_TOOL = re.compile(r"^(bsky|bluesky|linkedin|x_post|post_social|tweet)$", re.I)
_CHAT_VERBS = frozenset({"send", "send-message", "send-direct-message", "post"})
_MAIL_VERBS = frozenset({"send", "send-message", "reply", "reply-all"})
_SOCIAL_VERBS = frozenset({"post", "publish", "send"})
_VERB_WINDOW = 3
_HELP_FLAGS = frozenset({"--help", "-h"})


def _tz() -> Any:
    name = os.environ.get("SEND_BUDGET_TZ")
    if not name:
        return None
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:
        return None


def _now(now: Optional[datetime]) -> datetime:
    if now is not None:
        return now
    tz = _tz()
    return datetime.now(tz) if tz else datetime.now()


def state_dir() -> Path:
    return Path(os.environ.get("SEND_BUDGET_STATE_DIR", os.path.expanduser("~/.local/state/send_budget")))


def ceilings() -> Dict[str, int]:
    merged = dict(DEFAULT_CEILINGS)
    raw = os.environ.get("SEND_BUDGET_CEILINGS")
    if raw:
        try:
            override = json.loads(raw)
            if isinstance(override, dict):
                merged.update({str(k): int(v) for k, v in override.items()})
        except (ValueError, TypeError):
            pass
    return merged


def today_key(now: Optional[datetime] = None) -> str:
    return _now(now).strftime("%Y-%m-%d")


def in_quiet_window(now: Optional[datetime] = None) -> bool:
    hour = _now(now).hour
    return hour >= QUIET_START_HOUR or hour < QUIET_END_HOUR


_ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_INTERPRETER = re.compile(r"^(python(\d+(\.\d+)?)?|env)$")
_SELF_NAMES = frozenset({"send_budget.py", "send_budget"})
DESCRIBE_MAX = 60


def _tokens(command: str) -> list:
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def _tool_tokens(command: str) -> list:
    """Tokens from the executable onward: leading X=Y assignments, interpreters and their flags dropped."""
    tokens = _tokens(command)
    i = 0
    while i < len(tokens):
        if _ENV_ASSIGN.match(tokens[i]):
            i += 1
        elif _INTERPRETER.match(os.path.basename(tokens[i])):
            i += 1
            while i < len(tokens) and tokens[i].startswith("-"):
                i += 1
        else:
            break
    return tokens[i:]


def _invokes_self(command: str) -> bool:
    """True when the executable being run is this script, not when its name appears in a message."""
    tokens = _tool_tokens(command)
    return bool(tokens) and os.path.basename(tokens[0]) in _SELF_NAMES


def _clauses(command: str) -> list:
    """Splits a command line into simple commands at |, ||, &&, ; and newlines.

    Quoted operators stay inside their token. A heredoc body is dropped so the
    words in it are never mistaken for an executable.
    """
    text = _strip_heredocs(command)
    lexer = shlex.shlex(text, posix=True, punctuation_chars=_OPERATOR_CHARS)
    lexer.whitespace_split = True
    lexer.whitespace = " \t\r"
    lexer.commenters = ""
    clauses: list = [[]]
    try:
        for tok in lexer:
            if tok and all(ch in _OPERATOR_CHARS for ch in tok):
                clauses.append([])
            else:
                clauses[-1].append(tok)
    except ValueError:
        return [command.split()]
    return [c for c in clauses if c]


_OPERATOR_CHARS = "|&;\n"


_HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1[^\n]*\n.*?^\2[ \t]*$", re.S | re.M)


def _strip_heredocs(command: str) -> str:
    """Removes heredoc bodies (from the << marker line to the terminator)."""
    return _HEREDOC.sub("<<HEREDOC", command)


def _clause_tool_tokens(clause: list) -> list:
    """Tokens from the executable onward for one clause (see _tool_tokens)."""
    i = 0
    while i < len(clause):
        if _ENV_ASSIGN.match(clause[i]):
            i += 1
        elif _INTERPRETER.match(os.path.basename(clause[i])):
            i += 1
            while i < len(clause) and clause[i].startswith("-"):
                i += 1
        else:
            break
    return clause[i:]


def _classify_clause(clause: list) -> Optional[str]:
    """Channel for one simple command, or None when it is not a send."""
    tokens = _clause_tool_tokens(clause)
    if not tokens or any(t in _HELP_FLAGS for t in tokens):
        return None
    exe = os.path.basename(tokens[0])
    if _CHAT_TOOL.match(exe):
        verbs = _CHAT_VERBS
        channel = "chat_space"
    elif _MAIL_TOOL.match(exe):
        verbs = _MAIL_VERBS
        channel = "email"
    elif _SOCIAL_TOOL.match(exe):
        verbs = _SOCIAL_VERBS
        channel = "social"
    else:
        return None
    bare = [t.lower() for t in tokens[1:] if not t.startswith("-")][:_VERB_WINDOW]
    return channel if any(v in verbs for v in bare) else None


def classify_command(command: str) -> Optional[str]:
    """Maps a shell command line to a budget channel, or None when it is not a send."""
    if not command or _invokes_self(command):
        return None
    for clause in _clauses(command):
        channel = _classify_clause(clause)
        if channel == "chat_space":
            owner = os.environ.get("SEND_BUDGET_OWNER_SPACE")
            return "dm_owner" if owner and owner in " ".join(clause) else "chat_space"
        if channel:
            return channel
    return None


def describe(command: str) -> str:
    """Tool path only, safe to persist: executable basename plus up to two bare subcommand tokens.

    Stops at the first token that starts with '-' or carries '@', '/', ':', a quote or whitespace,
    so no flag value, address, space id or message text ever reaches the state file. Max 60 chars.
    """
    tokens = _tool_tokens(command)
    if not tokens:
        return ""
    parts = [os.path.basename(tokens[0])]
    for tok in tokens[1:3]:
        if not tok or tok.startswith("-") or any(c in tok for c in "@/:'\"") or any(c.isspace() for c in tok):
            break
        parts.append(tok)
    return " ".join(parts)[:DESCRIBE_MAX]


def _empty(day: str, history: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {"schema": SCHEMA_VERSION, "day": day, "counts": {}, "denials": {},
            "last_spend": {}, "history": history or {}}


def _counter_path() -> Path:
    return state_dir() / "send_budget.json"


def _read_unlocked(day: str) -> Dict[str, Any]:
    path = _counter_path()
    if not path.exists():
        return _empty(day)
    try:
        state = json.loads(path.read_text())
    except (OSError, ValueError):
        return _empty(day)
    if not isinstance(state, dict):
        return _empty(day)
    if state.get("day") != day:
        history = dict(state.get("history") or {})
        if state.get("day") and (state.get("counts") or state.get("denials")):
            history[str(state["day"])] = {"counts": state.get("counts") or {},
                                          "denials": state.get("denials") or {}}
        for stale in sorted(history)[:-HISTORY_DAYS]:
            history.pop(stale, None)
        return _empty(day, history)
    for key in ("counts", "denials", "last_spend", "history"):
        state.setdefault(key, {})
    return state


def _write_unlocked(state: Dict[str, Any]) -> None:
    directory = state_dir()
    directory.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(directory), prefix=".send_budget.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(state, handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, _counter_path())
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


class _Lock:
    """Exclusive flock around read-modify-write so parallel processes cannot lose a count."""

    def __enter__(self) -> "_Lock":
        directory = state_dir()
        directory.mkdir(parents=True, exist_ok=True)
        self._fh = open(directory / "send_budget.lock", "a+")
        fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc: Any) -> None:
        fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        self._fh.close()


def read_state(now: Optional[datetime] = None) -> Dict[str, Any]:
    with _Lock():
        return _read_unlocked(today_key(now))


def check(channel: str, now: Optional[datetime] = None) -> Tuple[bool, str]:
    """Reports whether one more send on channel is allowed, without burning a unit."""
    limits = ceilings()
    if channel not in limits:
        return True, f"channel '{channel}' is not budgeted"
    if os.environ.get(OVERRIDE_ENV) == "1":
        return True, f"{OVERRIDE_ENV}=1 in effect"
    if channel in QUIET_CHANNELS and in_quiet_window(now):
        return False, f"quiet window {QUIET_START_HOUR:02d}:00-{QUIET_END_HOUR:02d}:00 blocks '{channel}'"
    used = int(read_state(now)["counts"].get(channel, 0))
    if used >= limits[channel]:
        return False, f"daily ceiling reached for '{channel}': {used}/{limits[channel]}"
    return True, f"{used}/{limits[channel]} used on '{channel}'"


def spend(channel: str, reason: str = "", now: Optional[datetime] = None,
          dry_run: bool = False) -> Tuple[bool, str]:
    """Burns one unit on channel when allowed; records the denial otherwise."""
    limits = ceilings()
    if channel not in limits:
        return True, f"channel '{channel}' is not budgeted"
    overridden = os.environ.get(OVERRIDE_ENV) == "1"
    quiet = channel in QUIET_CHANNELS and in_quiet_window(now)
    day = today_key(now)
    stamp = _now(now).isoformat()
    with _Lock():
        state = _read_unlocked(day)
        used = int(state["counts"].get(channel, 0))
        ceiling = limits[channel]
        allowed = overridden or (not quiet and used < ceiling)
        if not allowed:
            state["denials"][channel] = int(state["denials"].get(channel, 0)) + 1
            if not dry_run:
                _write_unlocked(state)
            why = (f"quiet window {QUIET_START_HOUR:02d}:00-{QUIET_END_HOUR:02d}:00"
                   if quiet else f"daily ceiling reached: {used}/{ceiling}")
            return False, f"{channel}: {why}"
        if dry_run:
            return True, f"{channel}: would spend, {used + 1}/{ceiling} after"
        state["counts"][channel] = used + 1
        state["last_spend"][channel] = {"at": stamp, "reason": reason[:200], "overridden": overridden}
        _write_unlocked(state)
    return True, f"{channel}: {used + 1}/{ceiling} used" + (" (override)" if overridden else "")


def status(now: Optional[datetime] = None) -> Dict[str, Any]:
    state = read_state(now)
    limits = ceilings()
    return {
        "day": state.get("day", today_key(now)),
        "quiet_window": f"{QUIET_START_HOUR:02d}:00-{QUIET_END_HOUR:02d}:00",
        "in_quiet_window": in_quiet_window(now),
        "channels": {
            name: {
                "used": int(state["counts"].get(name, 0)),
                "ceiling": ceiling,
                "remaining": max(0, ceiling - int(state["counts"].get(name, 0))),
                "denials": int(state["denials"].get(name, 0)),
            }
            for name, ceiling in sorted(limits.items())
        },
        "history": dict(state.get("history") or {}),
    }


def reset(channel: Optional[str] = None, now: Optional[datetime] = None) -> None:
    day = today_key(now)
    with _Lock():
        state = _read_unlocked(day)
        if channel is None:
            state = _empty(day, dict(state.get("history") or {}))
        else:
            for key in ("counts", "denials", "last_spend"):
                state[key].pop(channel, None)
        _write_unlocked(state)


def _print_status(compact: bool, as_json: bool) -> int:
    data = status()
    if as_json:
        print(json.dumps(data, indent=2, sort_keys=True))
        return 0
    if compact:
        parts = [f"{n}={c['used']}/{c['ceiling']}" for n, c in data["channels"].items()]
        print(f"{data['day']} " + " ".join(parts) + (" QUIET" if data["in_quiet_window"] else ""))
        return 0
    print(f"Send budget for {data['day']} (quiet {data['quiet_window']}"
          f"{', ACTIVE' if data['in_quiet_window'] else ''})")
    for name, c in data["channels"].items():
        flag = "  OVER" if c["remaining"] == 0 else ""
        print(f"  {name:<12} {c['used']:>3}/{c['ceiling']:<3} remaining={c['remaining']:<3}"
              f" denials={c['denials']}{flag}")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    names = sorted(ceilings())
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true")
    common.add_argument("--compact", action="store_true")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0], parents=[common])
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("status", parents=[common])
    p_check = sub.add_parser("check", parents=[common])
    p_check.add_argument("--channel", required=True, choices=names)
    p_spend = sub.add_parser("spend", parents=[common])
    p_spend.add_argument("--channel", required=True, choices=names)
    p_spend.add_argument("--reason", default="")
    p_spend.add_argument("--dry-run", action="store_true")
    p_classify = sub.add_parser("classify", parents=[common])
    p_classify.add_argument("--command", required=True)
    p_reset = sub.add_parser("reset", parents=[common])
    p_reset.add_argument("--channel", choices=names)
    p_reset.add_argument("--all", action="store_true")

    args = parser.parse_args(argv)
    cmd = args.cmd or "status"
    if cmd == "status":
        return _print_status(args.compact, args.json)
    if cmd == "classify":
        channel = classify_command(args.command)
        print(json.dumps({"channel": channel}) if args.json else (channel or "none"))
        return 0
    if cmd == "reset":
        if not args.channel and not args.all:
            parser.error("reset needs --channel or --all")
        reset(None if args.all else args.channel)
        return _print_status(True, args.json)
    allowed, reason = (check(args.channel) if cmd == "check"
                       else spend(args.channel, args.reason, dry_run=args.dry_run))
    if args.json:
        print(json.dumps({"allowed": allowed, "channel": args.channel, "reason": reason}))
    else:
        print(f"{'ALLOW' if allowed else 'DENY'}: {reason}")
    return ALLOW if allowed else DENY


if __name__ == "__main__":
    sys.exit(main())
