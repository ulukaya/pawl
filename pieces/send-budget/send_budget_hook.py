#!/usr/bin/env python3
"""send_budget_hook.py: PreToolUse hook that runs the send budget before any shell command.

Reads the tool call JSON on stdin, classifies the command line, spends one unit when it is a
send, and prints a decision the harness understands:

    {"decision": "allow"}
    {"decision": "deny", "reason": "..."}

Fail-open on every error path: a broken counter must never block ordinary work. The budget
only ever says no to a send, and only after it has counted the denial.

Payload shape (Jetski / Antigravity style, other harnesses need a 3-line adapter):

    {"toolCall": {"name": "run_command", "args": {"CommandLine": "gchat mutate send-message ..."}}}
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import send_budget  # noqa: E402


def _emit(decision: str, reason: str = "") -> None:
    payload = {"decision": decision}
    if reason:
        payload["reason"] = reason
    sys.stdout.write(json.dumps(payload))
    sys.stdout.flush()


def _command_from(payload: dict) -> str:
    call = payload.get("toolCall") or payload.get("tool_call") or {}
    args = call.get("args") or call.get("arguments") or {}
    return str(args.get("CommandLine") or args.get("command") or "")


def main() -> None:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        command = _command_from(payload if isinstance(payload, dict) else {})
    except Exception:
        _emit("allow")
        return
    if not command:
        _emit("allow")
        return
    try:
        channel = send_budget.classify_command(command)
        if channel is None:
            _emit("allow")
            return
        allowed, reason = send_budget.spend(channel, reason=send_budget.describe(command))
    except Exception:
        _emit("allow")
        return
    if allowed:
        _emit("allow")
        return
    _emit("deny", f"[SEND BUDGET] {reason}. Hold it for the next local day, fold it into an "
                  f"existing thread, or set {send_budget.OVERRIDE_ENV}=1 for a send the human "
                  f"asked for right now. Inspect with `send_budget.py status`.")


if __name__ == "__main__":
    main()
