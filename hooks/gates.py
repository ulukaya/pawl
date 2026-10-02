#!/usr/bin/env python3
"""gates.py: the gates the dispatcher can run, and how each maps to a piece.

Every gate is one piece under pieces/ (send is three, fronted by
send_gates.py) plus a small adapter that turns the piece's answer into a
harness.Verdict. Pieces are imported only when a gate applies to the call, so
a Read never loads the send gates.

PreToolUse order is cheap and stateless first, then the gates that write
state (reread counts, the oscillation ring, the send budget): a deny stops
the run before a later gate spends a budget unit on a call that will not run.

    gate        tools                      piece                  on error
    fence       every tool                 conversation-fence     allow
    git         shell                      destructive-git-guard  deny
    poll        shell                      poll-loop-guard        deny
    noop        edits, apply_patch         noop-edit-guard        allow
    zero-width  writes, edits, apply_patch zero-width-sanitizer   allow
    readonly    shell                      readonly-pass          allow
    reread      shell, view_file           reread-guard           allow
    loop        every tool                 oscillation-breaker    allow
    send        shell                      send gates (hooks/)    egress deny
    idle        Stop only                  idle-task-gate         allow

On Stop: loop clears the ring, reread clears the turn's counts, idle may
block. Standard library only.
"""

from __future__ import annotations

import importlib
import json
import os
from pathlib import Path  # pylint: disable=g-importing-member
import sys
from types import ModuleType
from typing import (
    Any, Callable, Dict, FrozenSet, List, NamedTuple, Optional, Tuple,
)

from harness import ALLOW, Call, PRE, STOP, Verdict

HERE = Path(__file__).resolve().parent
PIECES = HERE.parent / "pieces"
DISABLE_ENV = "PAWL_DISABLE"
GUARD_TAIL = " Approve to run anyway; the run is logged as a human override."

SHELL = frozenset({"run_command", "run_shell_command"})
EDITS = frozenset(
    {"replace_file_content", "multi_replace_file_content", "apply_patch"}
)
WRITES = EDITS | {"write_to_file"}
READS = SHELL | {"view_file"}

Runner = Callable[[ModuleType, Call], Verdict]


class Gate(NamedTuple):
  """One gate: where its piece lives and how to ask it."""

  name: str
  piece: str  # directory under pieces/, or "" for a module in hooks/
  module: str
  tools: Optional[FrozenSet[str]]  # None: every tool
  pre: Optional[Runner] = None
  stop: Optional[Runner] = None
  fail_closed: bool = False

  def applies(self, event: str, tool: str) -> bool:
    runner = self.pre if event == PRE else self.stop
    if runner is None:
      return False
    return event == STOP or self.tools is None or tool in self.tools

  def load(self) -> ModuleType:
    if self.piece:
      path = str(PIECES / self.piece)
      if path not in sys.path:
        sys.path.insert(0, path)
    return importlib.import_module(self.module)


def verdict_of(answer: Dict[str, Any], gate: str = "") -> Verdict:
  """A piece's {"decision", "reason", "overwrite"} dict as a Verdict."""
  return Verdict(
      str(answer.get("decision") or "allow"),
      str(answer.get("reason") or ""),
      answer.get("overwrite") or None,
      gate,
  )


def _guard(prefix: str, gate: str) -> Runner:
  """git and poll: the piece denies; the plugin turns that into a prompt."""

  def run(mod: ModuleType, call: Call) -> Verdict:
    decision, reason, cmd, payload = mod.evaluate(json.dumps(call.payload))
    if decision == "allow":
      return ALLOW
    if payload is None:
      return Verdict("deny", reason, gate=gate)
    mod.record_denial(mod.GATE, cmd, payload, outcome="force_ask")
    return Verdict(
        "force_ask", f"{prefix} {reason.rstrip('.')}.{GUARD_TAIL}", gate=gate
    )

  return run


def _decide(gate: str) -> Runner:
  """Pieces whose decide(payload) already returns the decision dict."""

  def run(mod: ModuleType, call: Call) -> Verdict:
    return verdict_of(mod.decide(call.payload), gate)

  return run


def _zero_width(mod: ModuleType, call: Call) -> Verdict:
  cleaned = mod.sanitize(*mod.tool_and_args(call.payload))
  return Verdict(overwrite=cleaned, gate="zero-width") if cleaned else ALLOW


def _send(mod: ModuleType, call: Call) -> Verdict:
  args = call.args
  command = str(args.get("CommandLine") or args.get("command") or "")
  decision, reason = mod.decide(command)
  return Verdict(decision, reason, gate="send")


def _loop_stop(mod: ModuleType, call: Call) -> Verdict:
  mod.end_turn(json.dumps(call.payload))
  return ALLOW


def _reread_stop(mod: ModuleType, call: Call) -> Verdict:
  mod.end_turn(call.payload)
  return ALLOW


GATES: Tuple[Gate, ...] = (
    Gate("fence", "conversation-fence", "conversation_fence", None,
         pre=_decide("fence")),
    Gate("git", "destructive-git-guard", "destructive_git_guard", SHELL,
         pre=_guard("[PAWL git]", "git"), fail_closed=True),
    Gate("poll", "poll-loop-guard", "poll_loop_guard", SHELL,
         pre=_guard("[PAWL poll]", "poll"), fail_closed=True),
    Gate("noop", "noop-edit-guard", "noop_edit_guard", EDITS,
         pre=_decide("noop")),
    Gate("zero-width", "zero-width-sanitizer", "zero_width_sanitizer", WRITES,
         pre=_zero_width),
    Gate("readonly", "readonly-pass", "readonly_pass", SHELL,
         pre=_decide("readonly")),
    Gate("reread", "reread-guard", "reread_guard", READS,
         pre=_decide("reread"), stop=_reread_stop),
    Gate("loop", "oscillation-breaker", "oscillation_breaker", None,
         pre=_decide("loop"), stop=_loop_stop),
    Gate("send", "", "send_gates", SHELL, pre=_send),
    Gate("idle", "idle-task-gate", "idle_task_gate", None,
         stop=_decide("idle")),
)
BY_NAME: Dict[str, Gate] = {g.name: g for g in GATES}


def disabled() -> FrozenSet[str]:
  """Gate names in PAWL_DISABLE (send's egress, prose, budget included)."""
  raw = os.environ.get(DISABLE_ENV, "")
  return frozenset(g.strip() for g in raw.split(",") if g.strip())


def unknown(names: List[str]) -> List[str]:
  return [n for n in names if n not in BY_NAME]


def plan(event: str, tool: str, only: Optional[List[str]] = None,
         any_tool: bool = False) -> List[Gate]:
  """The gates to run for one call, in order.

  Args:
    event: PRE or STOP.
    tool: the canonical tool name.
    only: restrict to these gate names (an Antigravity hook group); None
      runs every gate.
    any_tool: ignore the tool filter (stdin was unreadable, so the tool is
      unknown).

  Returns:
    Gates in run order, PAWL_DISABLE applied.
  """
  off = disabled()
  out = []
  for gate in GATES:
    if gate.name in off or (only is not None and gate.name not in only):
      continue
    has_runner = (gate.pre if event == PRE else gate.stop) is not None
    if has_runner and (any_tool or gate.applies(event, tool)):
      out.append(gate)
  return out
