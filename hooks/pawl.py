#!/usr/bin/env python3
"""pawl.py: the one hook entry point for every harness and every gate.

    pawl.py pre  [--harness H] [--only g1,g2]   PreToolUse: run the gates
    pawl.py stop [--harness H] [--only g1,g2]   Stop: clear turn state, idle gate
    pawl.py stats                               per send-gate allow/deny counts
    pawl.py gates                               list gate names
    pawl.py demo [--verbose | --json]           every gate on canned calls,
                                                in all three harnesses

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

import os
from pathlib import Path  # pylint: disable=g-importing-member
import signal
import sys
from types import ModuleType
from typing import List, NamedTuple, Optional, Sequence, Tuple

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
  sys.path.insert(0, str(HERE))


def _cache_bytecode() -> None:
  """Compile pawl's modules once, into PAWL_DATA, not into the plugin.

  Hooks run as `python3 -B` so nothing is written beside the plugin's
  sources; without a cache every tool call recompiles every module. The
  cache lives under PAWL_DATA/pycache instead. Python skips writing it,
  silently, when that directory is not writable. `pawl.py demo` writes no
  cache at all: it promises to leave nothing outside its scratch tree.
  """
  if sys.argv[1:2] == ["demo"]:
    sys.dont_write_bytecode = True
    return
  data = os.environ.get("PAWL_DATA") or os.path.expanduser("~/.pawl")
  sys.pycache_prefix = os.path.join(data, "pycache")
  sys.dont_write_bytecode = False


_cache_bytecode()

# Claude Code exports each plugin option the user set in /config as
# CLAUDE_PLUGIN_OPTION_<KEY>; each maps onto the setting the gates read.
TRUE = frozenset({"1", "true", "yes", "on"})
PLUGIN_OPTIONS = (
    ("DISABLE", "PAWL_DISABLE", lambda v: v),
    ("PROTECTED_ROOTS", "PAWL_GIT_PROTECTED_ROOTS", lambda v: v),
    ("STRICT_FENCE", "PAWL_CONVERSATION_FENCE_STRICT",
     lambda v: "1" if v.lower() in TRUE else ""),
    ("READONLY_AUTO_APPROVE", "PAWL_READONLY_PASS_OFF",
     lambda v: "" if v.lower() in TRUE else "1"),
)


def apply_plugin_options(env=os.environ) -> None:
  """Copies Claude Code plugin options into PAWL_* settings.

  A PAWL_* variable the user exported wins over the plugin option, and an
  option that maps to "" (a default) sets nothing.

  Args:
    env: the environment to read and update.
  """
  for key, target, convert in PLUGIN_OPTIONS:
    raw = env.get(f"CLAUDE_PLUGIN_OPTION_{key}")
    if raw is None or target in env:
      continue
    value = convert(raw.strip())
    if value:
      env[target] = value


apply_plugin_options()

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


USAGE = (
    "usage: pawl.py {pre,stop} [--harness {auto,antigravity,claude,codex}]"
    " [--only g1,g2] | pawl.py {stats,gates} | pawl.py demo [--verbose |"
    " --json]"
)
COMMANDS = ("pre", "stop", "stats", "gates", "demo")


class Args(NamedTuple):
  command: str
  harness: str = "auto"
  only: Optional[List[str]] = None


def parse_args(argv: Sequence[str]) -> Args:
  """The command line; ValueError with the reason when it is not valid.

  A hook runs on every tool call, so this stays a few lines of plain
  parsing rather than argparse, which costs more to import than the rest
  of the dispatcher.

  Args:
    argv: arguments after the program name.

  Returns:
    The parsed Args.
  """
  if not argv or argv[0] not in COMMANDS:
    raise ValueError(f"expected one of {', '.join(COMMANDS)}")
  opts = {"--harness": "auto", "--only": None}
  rest = list(argv[1:])
  while rest:
    flag = rest.pop(0)
    name, eq, value = flag.partition("=")
    if name not in opts:
      raise ValueError(f"unknown option {flag!r}")
    if not eq:
      if not rest:
        raise ValueError(f"{name} needs a value")
      value = rest.pop(0)
    opts[name] = value
  harness_name = str(opts["--harness"])
  if harness_name not in ("auto",) + harness.HARNESSES:
    raise ValueError(f"unknown harness {harness_name!r}")
  raw = opts["--only"]
  only = None if raw is None else [n.strip() for n in raw.split(",")
                                   if n.strip()]
  return Args(argv[0], harness_name, only)


def main(argv: Optional[Sequence[str]] = None) -> int:
  argv = sys.argv[1:] if argv is None else list(argv)
  if argv[:1] == ["demo"]:
    import demo  # pylint: disable=g-import-not-at-top
    return demo.main(argv[1:])
  try:
    args = parse_args(argv)
  except ValueError as exc:
    _warn(f"{exc}\n{USAGE}")
    return 2
  if args.command == "gates":
    for gate in gates.GATES:
      events = [e for e, r in ((PRE, gate.pre), (STOP, gate.stop)) if r]
      print(f"{gate.name:<11} {'/'.join(events)}")
    return 0
  if args.command == "stats":
    gates.BY_NAME["send"].load().print_stats()
    return 0
  only = args.only
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
