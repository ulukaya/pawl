#!/usr/bin/env python3
"""pawl.py: the one hook entry point for every harness and every gate.

    pawl.py pre  [--harness H] [--only g1,g2]   PreToolUse: run the gates
    pawl.py stop [--harness H] [--only g1,g2]   Stop: clear turn state, idle gate
    pawl.py stats                               per send-gate allow/deny counts
    pawl.py gates                               list gate names

--harness is antigravity, claude, codex or auto (default; PAWL_HARNESS, then
the payload's shape decides). Shipped configs always pass it. --only limits
the run to named gates, which is how each Antigravity hooks.json group stays
its own switch. PAWL_DISABLE=<gate,...> skips gates for a session.

Gates run in the order gates.py lists. The first deny ends the run; asks are
collected; the merged answer is deny > force_ask > auto_approve > allow, with
any overwrite carried along unless the call is denied. harness.render()
writes it in the calling harness's format; the exit code is always 0.

Failure modes: stdin that is not a JSON object denies when a fail-closed
gate (git, poll) is in the run, else allows. A gate that raises allows,
except git and poll, which deny. A run past the watchdog budget (the lowest
of the gates' own *_WATCHDOG_S settings, default 14 s) allows: a hook must
never stall the host. No path prints a traceback.

Standard library only.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path  # pylint: disable=g-importing-member
import signal
import sys
from types import ModuleType
from typing import List, Optional, Sequence, Tuple

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
  sys.path.insert(0, str(HERE))

# pylint: disable=g-import-not-at-top
import gates  # noqa: E402
import harness  # noqa: E402
from harness import ALLOW, Call, PRE, STOP, Verdict  # noqa: E402

# pylint: enable=g-import-not-at-top

DEFAULT_BUDGET_S = 14.0
MIN_BUDGET_S = 0.5


def _warn(message: str) -> None:
  sys.stderr.write(f"[pawl] {message}\n")


def run_gate(gate: gates.Gate, mod: ModuleType, call: Call) -> Verdict:
  """One gate's verdict; an exception follows the gate's failure mode."""
  runner = gate.pre if call.event == PRE else gate.stop
  try:
    verdict = runner(mod, call)
  except Exception as exc:  # pylint: disable=broad-exception-caught
    if gate.fail_closed:
      return Verdict(
          "deny",
          f"[PAWL {gate.name}] internal error, failing closed: {exc!r}",
          gate=gate.name,
      )
    _warn(f"{gate.name}: internal error, failing open: {exc!r}")
    return ALLOW
  if call.event == STOP and verdict.decision not in ("allow", "block"):
    return ALLOW  # a Stop answer is allow or block, nothing else
  return verdict


def merge(verdicts: Sequence[Verdict]) -> Verdict:
  """deny > force_ask > block > auto_approve > allow; overwrites carried."""
  for v in verdicts:
    if v.decision == "deny":
      return v
  overwrite = next((v.overwrite for v in verdicts if v.overwrite), None)
  for word in ("force_ask", "block"):
    hits = [v for v in verdicts if v.decision == word]
    if hits:
      reason = "\n".join(v.reason for v in hits if v.reason)
      gate = ",".join(v.gate for v in hits if v.gate)
      return Verdict(word, reason, overwrite if word == "force_ask" else None,
                     gate)
  approved = [v for v in verdicts if v.decision == "auto_approve"]
  if approved:
    return Verdict("auto_approve", approved[0].reason, overwrite,
                   approved[0].gate)
  return Verdict(overwrite=overwrite) if overwrite else ALLOW


def run(call: Call, plan: List[Tuple[gates.Gate, ModuleType]]) -> Verdict:
  """Runs the planned gates in order and merges their verdicts."""
  verdicts: List[Verdict] = []
  for gate, mod in plan:
    verdict = run_gate(gate, mod, call)
    if verdict.decision == "deny":
      return verdict
    verdicts.append(verdict)
  return merge(verdicts)


def payload_verdict(call: Call, planned: List[gates.Gate]) -> Verdict:
  """Unreadable stdin: a deny when a fail-closed gate is in the run."""
  closed = [g.name for g in planned if g.fail_closed]
  if call.event != PRE or not closed:
    return ALLOW
  return Verdict(
      "deny",
      f"[HOOK PAYLOAD] pawl {'/'.join(closed)}: {call.error}; refusing the"
      " call.",
      gate=closed[0],
  )


def budget_s(mods: Sequence[ModuleType]) -> float:
  """The lowest watchdog budget any planned piece asks for."""
  budgets = [DEFAULT_BUDGET_S]
  for mod in mods:
    get = getattr(mod, "watchdog_budget_s", None)
    if callable(get):
      try:
        budgets.append(float(get()))
      except (TypeError, ValueError):
        continue
  return max(MIN_BUDGET_S, min(budgets))


def emit(call: Call, verdict: Verdict) -> int:
  out = harness.render(call, verdict)
  if out.stdout:
    sys.stdout.write(out.stdout)
  sys.stdout.flush()
  return out.exit_code


def arm_watchdog(call: Call, seconds: float) -> None:
  """Past the budget, answer allow in the harness's format and exit 0."""

  def fire(unused_signum, unused_frame):
    _warn(f"watchdog fired after {seconds}s; failing open")
    emit(call, ALLOW)
    os._exit(0)

  try:
    signal.signal(signal.SIGALRM, fire)
    signal.setitimer(signal.ITIMER_REAL, seconds)
  except (ValueError, OSError, AttributeError):
    return


def disarm_watchdog() -> None:
  try:
    signal.setitimer(signal.ITIMER_REAL, 0)
  except (ValueError, OSError, AttributeError):
    return


def handle(text: str, event: str, hint: str,
           only: Optional[List[str]]) -> Tuple[Call, Verdict]:
  """Parses one hook invocation and decides it."""
  call = harness.parse(text, hint, event)
  if call.error:
    planned = gates.plan(event, "", only, any_tool=True)
    return call, payload_verdict(call, planned)
  planned = gates.plan(event, call.tool, only)
  loaded = []
  for gate in planned:
    try:
      loaded.append((gate, gate.load()))
    except Exception as exc:  # pylint: disable=broad-exception-caught
      _warn(f"{gate.name}: cannot load ({exc!r}); skipped")
  arm_watchdog(call, budget_s([m for _, m in loaded]))
  try:
    return call, run(call, loaded)
  finally:
    disarm_watchdog()


def _only(raw: Optional[str]) -> Optional[List[str]]:
  if raw is None:
    return None
  return [n.strip() for n in raw.split(",") if n.strip()]


def parser() -> argparse.ArgumentParser:
  ap = argparse.ArgumentParser(
      prog="pawl.py", description="pawl hook dispatcher"
  )
  ap.add_argument("command", choices=("pre", "stop", "stats", "gates"))
  ap.add_argument(
      "--harness", default="auto",
      choices=("auto",) + harness.HARNESSES,
  )
  ap.add_argument("--only", default=None,
                  help="comma list of gate names to run")
  return ap


def main(argv: Optional[List[str]] = None) -> int:
  args = parser().parse_args(argv)
  if args.command == "gates":
    for gate in gates.GATES:
      events = [e for e, r in ((PRE, gate.pre), (STOP, gate.stop)) if r]
      print(f"{gate.name:<11} {'/'.join(events)}")
    return 0
  if args.command == "stats":
    gates.BY_NAME["send"].load().print_stats()
    return 0
  only = _only(args.only)
  bad = gates.unknown(only or [])
  if bad:
    _warn(f"unknown gate(s) {', '.join(bad)}; see `pawl.py gates`")
    return 2
  event = PRE if args.command == "pre" else STOP
  try:
    call, verdict = handle(sys.stdin.read(), event, args.harness, only)
  except Exception as exc:  # pylint: disable=broad-exception-caught
    # fail open: a dispatcher bug must not wedge the host on every call
    _warn(f"internal error, failing open: {exc!r}")
    call, verdict = harness.parse("{}", args.harness, event), ALLOW
  return emit(call, verdict)


if __name__ == "__main__":
  sys.exit(main())
