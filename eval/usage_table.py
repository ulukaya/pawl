#!/usr/bin/env python3
"""usage_table.py: what an eval run cost, and what the full suite would.

Reads every runs/<pass>/<arm>/<case>/stream.jsonl under a run_arms.sh output
directory (claude_agent.sh writes them) and prints turns, tokens and cost
per arm, the models that ran, the mean cost of one run, and what the full suite (every case,
both arms, --passes passes) would cost at those means.

A run is flagged when:
  - it has no result event: the session died or never started;
  - it stopped early: the turn cap, the spend cap, an API error or a
    refusal;
  - its arm loaded the wrong plugins: pawl missing on the on arm, or loaded
    on the off arm, either of which makes the comparison measure nothing.

On a Claude plan the cost is Claude Code's API-price estimate of the usage,
not a bill.

  usage_table.py <out-dir> [--passes N] [--eval EVAL.json]

Exit 0, 1 when any run is flagged, 2 when the directory holds no
stream.jsonl. Standard library only.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path  # pylint: disable=g-importing-member
import sys
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

HERE = Path(__file__).resolve().parent
EVAL_JSON = HERE / "EVAL.json"
ARMS = ("on", "off")
PLUGIN = "pawl"
DEFAULT_PASSES = 3  # run_arms.sh's PAWL_EVAL_PASSES default
TOKENS = (
    ("input", "input_tokens"),
    ("cache write", "cache_creation_input_tokens"),
    ("cache read", "cache_read_input_tokens"),
    ("output", "output_tokens"),
)

Event = Dict[str, Any]


class Run(NamedTuple):
  """One agent session: where it ran, what it used, what went wrong."""

  label: str  # <pass>/<arm>/<case>
  arm: str
  model: str
  turns: int
  cost: float
  tokens: Tuple[int, ...]  # in TOKENS order
  flags: Tuple[str, ...]


def events(path: Path) -> List[Event]:
  """The JSON objects in a stream file; other lines are skipped."""
  out: List[Event] = []
  for line in path.read_text(errors="replace").splitlines():
    try:
      event = json.loads(line)
    except ValueError:
      continue
    if isinstance(event, dict):
      out.append(event)
  return out


def split(stream: List[Event]) -> Tuple[Optional[Event], Optional[Event]]:
  """The session's init event and its last result event."""
  init = next((e for e in stream if e.get("type") == "system"
               and e.get("subtype") == "init"), None)
  results = [e for e in stream if e.get("type") == "result"]
  return init, (results[-1] if results else None)


def plugin_names(init: Event) -> Optional[List[str]]:
  """Plugin names the init event lists, without @source; None if absent."""
  plugins = init.get("plugins")
  if not isinstance(plugins, list):
    return None
  names = [p.get("name") if isinstance(p, dict) else p for p in plugins]
  return [str(n).split("@", 1)[0] for n in names if n]


def arm_flags(arm: str, init: Optional[Event]) -> List[str]:
  """What is wrong with the plugins this arm loaded, or []."""
  if init is None:
    return ["no init event: cannot check which plugins loaded"]
  names = plugin_names(init)
  if names is None:
    return ["init event lists no plugins: cannot check the arm"]
  if arm == "on" and PLUGIN not in names:
    return ["pawl not loaded on the on arm"]
  if arm == "off" and PLUGIN in names:
    return ["pawl loaded on the off arm"]
  return []


def _number(value: Any) -> float:
  return float(value) if isinstance(value, (int, float)) else 0.0


def stop_detail(result: Event) -> str:
  """Why a session ended early ('error_max_turns', 'api_error (refusal)'),
  or '' for a clean finish. An API error can carry subtype "success"."""
  subtype = str(result.get("subtype") or "")
  if subtype and subtype != "success":
    return subtype
  if not result.get("is_error"):
    return ""
  terminal = str(result.get("terminal_reason") or "error")
  reason = result.get("stop_reason")
  return f"{terminal} ({reason})" if reason else terminal


def read_run(path: Path, label: str, arm: str) -> Run:
  """One run from its stream.jsonl."""
  init, result = split(events(path))
  flags = arm_flags(arm, init)
  model = str((init or {}).get("model") or "unknown")
  if result is None:
    flags.append("no result event: the session died or never started")
    return Run(label, arm, model, 0, 0.0, (0,) * len(TOKENS), tuple(flags))
  stop = stop_detail(result)
  if stop:
    flags.append(f"stopped: {stop}")
  usage = result.get("usage")
  usage = usage if isinstance(usage, dict) else {}
  tokens = tuple(int(_number(usage.get(key))) for _, key in TOKENS)
  return Run(label, arm, model, int(_number(result.get("num_turns"))),
             _number(result.get("total_cost_usd")), tokens, tuple(flags))


def collect(out_dir: Path) -> List[Run]:
  """Every run under out_dir/runs/<pass>/<arm>/<case>/stream.jsonl."""
  runs: List[Run] = []
  for path in sorted(out_dir.glob("runs/*/*/*/stream.jsonl")):
    case_dir = path.parent
    arm = case_dir.parent.name
    label = f"{case_dir.parent.parent.name}/{arm}/{case_dir.name}"
    runs.append(read_run(path, label, arm))
  return runs


def case_count(eval_path: Path = EVAL_JSON) -> int:
  return len(json.loads(eval_path.read_text())["cases"])


def mean_cost(runs: List[Run]) -> float:
  return sum(r.cost for r in runs) / len(runs) if runs else 0.0


def _row(name: str, runs: List[Run]) -> str:
  tokens = [sum(r.tokens[i] for r in runs) for i in range(len(TOKENS))]
  cells = [str(len(runs)), str(sum(r.turns for r in runs))]
  cells += [f"{t:,}" for t in tokens]
  cells.append(f"${sum(r.cost for r in runs):.2f}")
  return f"| {name} | {' | '.join(cells)} |"


def projection(runs: List[Run], cases: int, passes: int) -> str:
  """The full suite's cost at this run's per-arm means."""
  overall = mean_cost(runs)
  means = {}
  for arm in ARMS:
    arm_runs = [r for r in runs if r.arm == arm]
    means[arm] = mean_cost(arm_runs) if arm_runs else overall
  total = passes * cases * sum(means.values())
  full = passes * cases * len(ARMS)
  return (
      f"mean per run: on ${means['on']:.3f}, off ${means['off']:.3f}\n"
      f"full suite: {cases} cases x {len(ARMS)} arms x {passes} passes ="
      f" {full} runs, about ${total:.2f} at these means\n"
  )


def render(runs: List[Run], cases: int, passes: int) -> str:
  head = ["arm", "runs", "turns", *(n for n, _ in TOKENS), "cost"]
  out = [f"| {' | '.join(head)} |", "|" + "---|" * len(head)]
  out += [_row(arm, [r for r in runs if r.arm == arm]) for arm in ARMS]
  out += [_row("total", runs), ""]
  out.append(f"models: {', '.join(sorted({r.model for r in runs}))}")
  out.append(projection(runs, cases, passes))
  flagged = [r for r in runs if r.flags]
  if flagged:
    out.append(f"flagged ({len(flagged)}):")
    out += [f"  {r.label}: {'; '.join(r.flags)}" for r in flagged]
  return "\n".join(out).rstrip("\n") + "\n"


def main(argv: List[str]) -> int:
  parser = argparse.ArgumentParser(prog="usage_table.py")
  parser.add_argument("out_dir", type=Path)
  parser.add_argument("--passes", type=int, default=DEFAULT_PASSES)
  parser.add_argument("--eval", type=Path, default=EVAL_JSON)
  ns = parser.parse_args(argv)
  runs = collect(ns.out_dir)
  if not runs:
    print(f"usage_table: no runs/*/*/*/stream.jsonl under {ns.out_dir};"
          " run eval/run_arms.sh with PAWL_EVAL_AGENT=eval/claude_agent.sh",
          file=sys.stderr)
    return 2
  sys.stdout.write(render(runs, case_count(ns.eval), max(1, ns.passes)))
  return 1 if any(r.flags for r in runs) else 0


if __name__ == "__main__":
  sys.exit(main(sys.argv[1:]))
