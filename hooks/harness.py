#!/usr/bin/env python3
"""harness.py: one adapter between pawl's gates and each agent harness.

The pieces speak one dialect: the Antigravity / Jetski tool call
({"toolCall": {"name": "run_command", "args": {"CommandLine": ...}}}) and the
decision words allow, auto_approve, deny, force_ask and block. This module
translates both directions, so no piece needs to know which harness called
it:

    parse()   stdin text from any harness -> Call (canonical tool and args)
    render()  Verdict -> the stdout and exit code that harness expects

Harness contracts (PreToolUse unless noted):

    decision      antigravity        claude                 codex
    allow         {"decision":...}   no output              no output
    auto_approve  {"decision":...}   permissionDecision     no output (hooks
                                     allow                  cannot approve)
    deny          {"decision":...}   permissionDecision     permissionDecision
                                     deny                   deny
    force_ask     {"decision":...}   permissionDecision     permissionDecision
                                     ask                    deny (hooks cannot
                                                            ask)
    overwrite     "overwrite"        updatedInput, no       updatedInput with
                                     permission change      permissionDecision
                                                            allow
    Stop block    {"decision":       {"decision":"block",   {"decision":"block",
                  "block",...}       "reason":...}          "reason":...}

"No output" matters: on Claude Code a PreToolUse permissionDecision of allow
skips the user's permission prompt, so a gate with no objection must stay
silent. Every rendering exits 0; a hook that exits 2 is read as a block.

Standard library only.
"""

from __future__ import annotations

import json
import os
import re
import shlex
from typing import Any, Dict, List, Mapping, NamedTuple, Optional, Tuple

ANTIGRAVITY = "antigravity"
CLAUDE = "claude"
CODEX = "codex"
HARNESSES = (ANTIGRAVITY, CLAUDE, CODEX)
HARNESS_ENV = "PAWL_HARNESS"
PRE, STOP = "pre", "stop"

DECISIONS = frozenset({"allow", "auto_approve", "deny", "force_ask", "block"})

# Claude Code tool -> (canonical tool, native field -> canonical field, fixed
# canonical args). Fields not named are dropped: Bash's `description` is
# reworded on every call, like Antigravity's toolSummary, and would make two
# identical commands hash apart in the oscillation ring.
CLAUDE_TOOLS: Dict[str, Tuple[str, Dict[str, str], Dict[str, Any]]] = {
    "Bash": (
        "run_command",
        {
            "command": "CommandLine",
            # readonly-pass never approves a call that asks to leave the
            # sandbox, so the flag must survive translation.
            "dangerouslyDisableSandbox": "dangerouslyDisableSandbox",
        },
        {},
    ),
    "Write": (
        "write_to_file",
        {"file_path": "TargetFile", "content": "CodeContent"},
        {},
    ),
    "Edit": (
        "replace_file_content",
        {
            "file_path": "TargetFile",
            "old_string": "TargetContent",
            "new_string": "ReplacementContent",
            "replace_all": "AllowMultiple",
        },
        {},
    ),
    "TaskOutput": ("manage_task", {"task_id": "TaskId"}, {"Action": "status"}),
    "TaskStop": ("manage_task", {"task_id": "TaskId"}, {"Action": "kill"}),
    "CronCreate": ("schedule", {}, {}),
    "ScheduleWakeup": ("schedule", {}, {}),
    "AskUserQuestion": ("ask_question", {}, {}),
    "SendMessage": ("send_message", {}, {}),
    "PushNotification": ("send_message", {}, {}),
}
CODEX_TOOLS: Dict[str, Tuple[str, Dict[str, str], Dict[str, Any]]] = {
    "Bash": ("run_command", {"command": "CommandLine"}, {}),
}
SHELL_TOOLS = frozenset({"run_command", "run_shell_command"})


class Call(NamedTuple):
  """One hook invocation, translated to the canonical dialect."""

  harness: str
  event: str
  tool: str
  args: Dict[str, Any]
  payload: Dict[str, Any]
  native_tool: str
  native_args: Dict[str, Any]
  error: str = ""  # set when stdin was not a JSON object


def failed_call(harness: str, event: str, error: str) -> Call:
  return Call(harness, event, "", {}, {}, "", {}, error)


class Verdict:
  """A gate outcome in the canonical decision words."""

  __slots__ = ("decision", "reason", "overwrite", "gate")

  def __init__(self, decision: str = "allow", reason: str = "",
               overwrite: Optional[Dict[str, Any]] = None,
               gate: str = "") -> None:
    if decision not in DECISIONS:
      raise ValueError(f"unknown decision {decision!r}")
    self.decision, self.reason = decision, reason
    self.overwrite, self.gate = overwrite, gate

  def __repr__(self) -> str:
    return (f"Verdict({self.decision!r}, {self.reason!r}, {self.overwrite!r},"
            f" {self.gate!r})")


ALLOW = Verdict()


# --- inbound ------------------------------------------------------------------


def resolve(hint: str, payload: Mapping[str, Any]) -> str:
  """The harness that sent `payload`.

  An explicit hint wins, except that a Codex payload (it always carries
  turn_id) is never rendered for Claude Code: Codex can load a plugin through
  its .claude-plugin manifest, and must still get Codex output.

  Args:
    hint: --harness value: antigravity, claude, codex, or auto/''.
    payload: the parsed stdin object.

  Returns:
    One of HARNESSES.
  """
  hint = (hint or "").lower()
  if hint in ("", "auto"):
    hint = (os.environ.get(HARNESS_ENV) or "").lower()
  if hint == CLAUDE and "turn_id" in payload:
    return CODEX
  if hint in HARNESSES:
    return hint
  if "toolCall" in payload or "tool_call" in payload:
    return ANTIGRAVITY
  if "turn_id" in payload or payload.get("tool_name") == "apply_patch":
    return CODEX
  if "hook_event_name" in payload or "tool_name" in payload:
    return CLAUDE
  return ANTIGRAVITY


def _command_text(value: Any) -> str:
  if isinstance(value, list):
    return shlex.join(str(v) for v in value)
  return "" if value is None else str(value)


def _int(value: Any) -> Optional[int]:
  if isinstance(value, bool) or not isinstance(value, (int, float)):
    return None
  return int(value)


def _read_args(args: Mapping[str, Any]) -> Dict[str, Any]:
  """Claude Read (file_path, offset, limit) -> view_file lines, 1-based."""
  out: Dict[str, Any] = {"AbsolutePath": str(args.get("file_path") or "")}
  offset, limit = _int(args.get("offset")), _int(args.get("limit"))
  if offset is None and limit is None:
    return out
  start = max(1, offset or 1)
  out["StartLine"] = start
  if limit is not None:
    out["EndLine"] = start + max(limit, 1) - 1
  return out


def canonical_tool(
    harness: str, tool: str, args: Mapping[str, Any], cwd: str
) -> Tuple[str, Dict[str, Any]]:
  """(canonical tool, canonical args) for a native tool call."""
  if harness == ANTIGRAVITY:
    return tool, dict(args)
  if harness == CLAUDE and tool == "Read":
    return "view_file", _read_args(args)
  table = CLAUDE_TOOLS if harness == CLAUDE else CODEX_TOOLS
  if tool not in table:
    return tool, dict(args)
  name, fields, fixed = table[tool]
  out = dict(fixed)
  for native, canon in fields.items():
    if native in args:
      out[canon] = args[native]
  if name in SHELL_TOOLS:
    out["CommandLine"] = _command_text(out.get("CommandLine"))
    if cwd:
      out["Cwd"] = cwd
  return name, out


def _antigravity_call(payload: Dict[str, Any], event: str) -> Call:
  call = payload.get("toolCall") or payload.get("tool_call") or {}
  call = call if isinstance(call, dict) else {}
  args = call.get("args")
  if args is None:
    args = call.get("arguments")
  if args is None:
    args = payload.get("toolArgs", payload.get("tool_input"))
  name = str(
      call.get("name") or payload.get("toolName") or payload.get("tool_name")
      or ""
  )
  args = args if isinstance(args, dict) else {}
  return Call(ANTIGRAVITY, event, name, dict(args), payload, name, args)


def _str(value: Any) -> str:
  return value if isinstance(value, str) else ""


def parse(text: str, hint: str = "", event: str = PRE) -> Call:
  """Translates one hook's stdin into a Call. Never raises.

  Args:
    text: the raw stdin.
    hint: --harness value, see resolve().
    event: PRE or STOP.

  Returns:
    The Call; on unparsable stdin, a Call with `error` set and no tool.
  """
  try:
    data = json.loads(text or "{}")
  except ValueError as err:
    return failed_call(resolve(hint, {}), event, f"stdin is not JSON ({err})")
  if not isinstance(data, dict):
    kind = type(data).__name__
    return failed_call(resolve(hint, {}), event,
                       f"payload is {kind}, not an object")
  harness = resolve(hint, data)
  if harness == ANTIGRAVITY:
    return _antigravity_call(data, event)
  native_tool = _str(data.get("tool_name"))
  native_args = data.get("tool_input")
  native_args = native_args if isinstance(native_args, dict) else {}
  cwd = _str(data.get("cwd"))
  tool, args = canonical_tool(harness, native_tool, native_args, cwd)
  payload: Dict[str, Any] = {
      "toolCall": {"name": tool, "args": args},
      "conversationId": _str(data.get("session_id")),
      "transcriptPath": _str(data.get("transcript_path")),
      "cwd": cwd,
      "harness": harness,
  }
  # Codex names the turn; Claude Code names the user prompt being served.
  turn = _str(data.get("turn_id")) or _str(data.get("prompt_id"))
  if turn:
    payload["turnId"] = turn
  if _str(data.get("agent_id")):
    payload["agentId"] = data["agent_id"]  # a subagent: its own ring, counts
  if event == STOP:
    payload["stopHookActive"] = bool(data.get("stop_hook_active"))
    tasks = data.get("background_tasks")
    if isinstance(tasks, list):
      payload["backgroundTasks"] = tasks
  return Call(harness, event, tool, args, payload, native_tool, native_args)


# --- outbound -----------------------------------------------------------------


def native_overwrite(call: Call, overwrite: Mapping[str, Any]) -> Dict[str, Any]:
  """Maps a canonical overwrite back onto the call's native arguments.

  Starts from the native args, so fields the canonical form dropped (a
  Bash description, an Edit's replace_all) survive, and only fields a piece
  rewrote change.

  Args:
    call: the call the overwrite answers.
    overwrite: the full canonical args the piece returned.

  Returns:
    The full native argument object.
  """
  if call.harness == ANTIGRAVITY:
    return dict(overwrite)
  table = CLAUDE_TOOLS if call.harness == CLAUDE else CODEX_TOOLS
  if call.native_tool not in table:
    return dict(overwrite)
  out = dict(call.native_args)
  for native, canon in table[call.native_tool][1].items():
    if canon in overwrite:
      out[native] = overwrite[canon]
  return out


# Antigravity words in piece reasons, said the way each harness says them.
# Longest first: replace_file_content is inside multi_replace_file_content.
VOCAB: Dict[str, List[Tuple[str, str]]] = {
    CLAUDE: [
        (r"\bmulti_replace_file_content\b", "Edit"),
        (r"\breplace_file_content\b", "Edit"),
        (r"\bwrite_to_file\b", "Write"),
        (r"\bview_file\b", "Read"),
        (r"\brun_command\b", "Bash"),
        (r"\bmanage_task\b", "TaskStop"),
        (r"; end the turn, or use the schedule tool with"
         r" TimerCondition=<task-id>\.", "; end the turn and let it notify"
         " you."),
    ],
    CODEX: [
        (r"\bmulti_replace_file_content\b", "apply_patch"),
        (r"\breplace_file_content\b", "apply_patch"),
        (r"Rewrite the file with write_to_file", "Rewrite the file with"
         " apply_patch"),
        (r"\bwrite_to_file\b", "apply_patch"),
        (r"\bview_file\b", "a bounded read (sed -n)"),
        (r"\brun_command\b", "Bash"),
        (r"\bmanage_task\b", "`kill <pid>`"),
        (r"; end the turn, or use the schedule tool with"
         r" TimerCondition=<task-id>\.", "; end the turn instead."),
        # Codex hooks cannot ask, so a force_ask arrives as a deny.
        (r"\s*Approve to [^.]*\.", ""),
        (r"; approve only if the user asked for it\.", "."),
    ],
}
APPROVED = "[PAWL readonly] every clause of the command only reads"
CODEX_ASK_NOTE = (
    " Codex hooks cannot ask, so pawl denied it: tell the user, who can run"
    " it by hand or set PAWL_DISABLE={gate} for the session."
)


def speak(harness: str, reason: str) -> str:
  """`reason` with Antigravity tool names swapped for the harness's own."""
  for pattern, repl in VOCAB.get(harness, []):
    reason = re.sub(pattern, repl, reason)
  return reason


class Rendered(NamedTuple):
  stdout: str
  exit_code: int = 0


def _antigravity(verdict: Verdict) -> Rendered:
  out: Dict[str, Any] = {"decision": verdict.decision}
  if verdict.reason:
    out["reason"] = verdict.reason
  if verdict.overwrite:
    out["overwrite"] = verdict.overwrite
  return Rendered(json.dumps(out))


def _stop(verdict: Verdict, harness: str) -> Rendered:
  if verdict.decision != "block" or not verdict.reason.strip():
    return Rendered("")
  out = {"decision": "block", "reason": speak(harness, verdict.reason)}
  return Rendered(json.dumps(out))


def _pre_tool_use(call: Call, verdict: Verdict) -> Rendered:
  """Claude Code and Codex PreToolUse output; empty means no opinion."""
  spec: Dict[str, Any] = {"hookEventName": "PreToolUse"}
  decision, reason = verdict.decision, speak(call.harness, verdict.reason)
  codex = call.harness == CODEX
  if decision == "force_ask" and codex:
    decision = "deny"
    reason += CODEX_ASK_NOTE.format(gate=verdict.gate or "<gate>")
  if decision == "auto_approve" and codex:
    decision = "allow"  # Codex: nothing to approve from a hook
  if decision in ("deny", "force_ask", "auto_approve"):
    word = {"deny": "deny", "force_ask": "ask", "auto_approve": "allow"}
    spec["permissionDecision"] = word[decision]
    default = APPROVED if decision == "auto_approve" else "pawl"
    spec["permissionDecisionReason"] = reason.strip() or default
  if verdict.overwrite and decision != "deny":
    spec["updatedInput"] = native_overwrite(call, verdict.overwrite)
    if codex:
      spec["permissionDecision"] = "allow"  # Codex applies it only with allow
  if len(spec) == 1:
    return Rendered("")
  return Rendered(json.dumps({"hookSpecificOutput": spec}))


def render(call: Call, verdict: Verdict) -> Rendered:
  """The stdout text and exit code for `verdict` on `call`'s harness."""
  if call.harness == ANTIGRAVITY:
    return _antigravity(verdict)
  if call.event == STOP:
    return _stop(verdict, call.harness)
  return _pre_tool_use(call, verdict)
