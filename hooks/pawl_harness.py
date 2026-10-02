#!/usr/bin/env python3
"""pawl_harness.py: multi-harness adapter for Antigravity, Claude Code and Codex.

Detects whether a hook is invoked by Claude Code or Antigravity / Jetski, and
formats the decision, JSON response, and process exit code according to each
harness's lifecycle contract.

Standard library only.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, Optional

CLAUDE_TOOLS = frozenset({"Bash", "Edit", "Write", "Read"})
CLAUDE_EVENTS = frozenset(
    {"PreToolUse", "PostToolUse", "Stop", "SessionStart", "SessionEnd"}
)


def is_claude_harness(payload: Dict[str, Any]) -> bool:
  """True when the invocation comes from Claude Code."""
  if os.environ.get("PAWL_HARNESS") == "claude":
    return True
  if payload.get("hook_event_name") in CLAUDE_EVENTS:
    return True
  if payload.get("tool_name") in CLAUDE_TOOLS and "toolCall" not in payload:
    return True
  return False


def emit_decision(
    decision: str,
    reason: str = "",
    payload: Optional[Dict[str, Any]] = None,
    overwrite: Optional[Dict[str, Any]] = None,
) -> None:
  """Emits hook decision according to the active agent harness.

  Antigravity / Jetski contract:
    - stdout: {"decision": decision, "reason": ..., "overwrite": ...}
    - exit: 0

  Claude Code contract:
    - allow / auto_approve (with optional input modification):
      - stdout: {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                 "permissionDecision": "allow", ...}}
      - exit: 0
    - deny / force_ask / block:
      - stderr: reason
      - stdout: {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                 "permissionDecision": "deny",
                 "permissionDecisionReason": reason}}
      - exit: 2
  """
  payload = payload or {}
  if is_claude_harness(payload):
    event = str(payload.get("hook_event_name") or "PreToolUse")
    if decision in ("deny", "force_ask", "block"):
      if reason:
        sys.stderr.write(f"{reason}\n")
        sys.stderr.flush()
      out = {
          "hookSpecificOutput": {
              "hookEventName": event,
              "permissionDecision": "deny",
              "permissionDecisionReason": reason,
          }
      }
      sys.stdout.write(json.dumps(out))
      sys.stdout.flush()
      sys.exit(2)
    hook_out: Dict[str, Any] = {
        "hookEventName": event,
        "permissionDecision": "allow",
    }
    if overwrite:
      hook_out["updatedInput"] = overwrite
    if reason:
      hook_out["permissionDecisionReason"] = reason
    sys.stdout.write(json.dumps({"hookSpecificOutput": hook_out}))
    sys.stdout.flush()
    sys.exit(0)

  out: Dict[str, Any] = {"decision": decision}
  if reason:
    out["reason"] = reason
  if overwrite:
    out["overwrite"] = overwrite
  sys.stdout.write(json.dumps(out))
  sys.stdout.flush()
  sys.exit(0)
