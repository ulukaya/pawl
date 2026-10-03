#!/usr/bin/env python3
"""demo.py: pawl's gates on eleven canned calls, in all three harnesses.

    python3 hooks/pawl.py demo [--verbose | --json]

Each scenario is one agent mistake, or one harmless call, written the way
Antigravity, Claude Code and Codex each send it. The demo pipes every call
through the real dispatcher (`pawl.py pre|stop --harness H`) in a scratch
home, project and PAWL_DATA that it deletes afterwards, and prints what each
harness would be told. Your own settings are left out: the run clears every
PAWL_* variable and Claude Code plugin option.

--verbose adds each answer's reason; --json prints every raw answer.
Exits 0, or 1 when a dispatcher run fails.

Standard library only.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path  # pylint: disable=g-importing-member
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, NamedTuple, Optional, Sequence, Tuple

HERE = Path(__file__).resolve().parent
PAWL = HERE / "pawl.py"
HARNESSES = ("antigravity", "claude", "codex")
TITLES = {"antigravity": "antigravity", "claude": "claude code",
          "codex": "codex"}
CODEX_SELF = "0199a0de-0000-7000-8000-00000000c0de"
CODEX_OTHER = "0199a0de-0000-7000-8000-0000000b0b0b"
RUN_TIMEOUT_S = 30


class Scenario(NamedTuple):
  label: str
  kind: str  # shell, edit, write, read-other, stop
  args: Tuple[str, ...] = ()
  times: int = 1  # identical calls in a row; the last answer is shown


SCENARIOS = (
    Scenario("make build", "shell", ("make build",)),
    Scenario("git log --oneline -5", "shell", ("git log --oneline -5",)),
    Scenario("git reset --hard", "shell", ("git reset --hard",)),
    Scenario("script that runs rm -rf ~/", "shell",
             ("cat > clean.sh <<'EOF'\nrm -rf build ~/\nEOF\nbash clean.sh",)),
    Scenario("tail -f server.log", "shell", ("tail -f server.log",)),
    Scenario("same pytest run, 3rd time", "shell",
             ("pytest -q tests/test_x.py",), 3),
    Scenario("edit that changes nothing", "edit",
             ("app.py", "retries = 3", "retries = 3")),
    Scenario("write with a U+200B", "write", ("app.py", "x = 1​\n")),
    Scenario("send naming ~/.deploy/", "shell",
             ('gchat send --space spaces/A --text "keys are in'
              ' ~/.deploy/prod.env"',)),
    Scenario("read another session", "read-other"),
    Scenario("stop with tail -f running", "stop"),
)
NOT_SHOWN = {
    ("stop", "antigravity"): "reads /proc",
    ("stop", "codex"): "no task list",
}


class Answer(NamedTuple):
  scenario: str
  harness: str
  stdout: str
  error: str = ""


# --- payloads, as each harness sends them ----------------------------------------


class Scratch(NamedTuple):
  home: Path
  project: Path


def _patch(path: str, body: str) -> str:
  return f"*** Begin Patch\n{body}*** End Patch\n".replace("{path}", path)


def tool_call(harness: str, kind: str, args: Sequence[str],
              s: Scratch) -> Tuple[str, Dict[str, Any]]:
  """The native tool name and input for one scenario."""
  if kind == "shell":
    if harness == "antigravity":
      return "run_command", {"CommandLine": args[0], "Cwd": str(s.project)}
    return "Bash", {"command": args[0]}
  path = str(s.project / args[0]) if args else ""
  if kind == "edit":
    if harness == "antigravity":
      return "replace_file_content", {"TargetFile": path,
                                      "TargetContent": args[1],
                                      "ReplacementContent": args[2]}
    if harness == "claude":
      return "Edit", {"file_path": path, "old_string": args[1],
                      "new_string": args[2]}
    body = f"*** Update File: {{path}}\n@@\n-{args[1]}\n+{args[2]}\n"
    return "apply_patch", {"command": _patch(path, body)}
  if kind == "write":
    if harness == "antigravity":
      return "write_to_file", {"TargetFile": path, "CodeContent": args[1]}
    if harness == "claude":
      return "Write", {"file_path": path, "content": args[1]}
    lines = "".join(f"+{line}\n" for line in args[1].splitlines())
    return "apply_patch", {"command": _patch(path, "*** Add File: {path}\n"
                                             + lines)}
  return other_session_read(harness, s)


def other_session_read(harness: str,
                       s: Scratch) -> Tuple[str, Dict[str, Any]]:
  """A read of another conversation's transcript, in that harness's store."""
  if harness == "antigravity":
    other = s.home / ".gemini/antigravity/brain/other-conversation/task.md"
    return "view_file", {"AbsolutePath": str(other)}
  if harness == "claude":
    other = s.home / ".claude/projects/-work/other-session.jsonl"
    return "Read", {"file_path": str(other)}
  other = (s.home / ".codex/sessions/2026/10/02"
           / f"rollout-2026-10-02T09-00-00-{CODEX_OTHER}.jsonl")
  return "Bash", {"command": f"cat {other}"}


def payload(harness: str, conv: str, tool: str, args: Dict[str, Any],
            s: Scratch) -> Dict[str, Any]:
  """The PreToolUse payload around one tool call."""
  if harness == "antigravity":
    return {"conversationId": conv, "turnId": "t1",
            "toolCall": {"name": tool, "args": args}}
  base = {"session_id": conv, "cwd": str(s.project),
          "hook_event_name": "PreToolUse", "permission_mode": "default",
          "tool_name": tool, "tool_input": args, "tool_use_id": "call_demo"}
  if harness == "claude":
    base["transcript_path"] = str(s.home / f".claude/projects/-demo/{conv}"
                                  ".jsonl")
    return base
  base["session_id"] = CODEX_SELF
  base["turn_id"] = f"turn-{conv}"
  base["transcript_path"] = str(s.home / ".codex/sessions/2026/10/02"
                                / f"rollout-2026-10-02T08-00-00-{CODEX_SELF}"
                                ".jsonl")
  return base


def stop_payload(conv: str, s: Scratch) -> Dict[str, Any]:
  """A Claude Code Stop with an unbounded wait still running."""
  task = {"id": "b7", "type": "shell", "status": "running",
          "description": "follow the log", "command": "tail -f server.log"}
  return {"session_id": conv, "cwd": str(s.project),
          "transcript_path": str(s.home / f".claude/projects/-demo/{conv}"
                                 ".jsonl"),
          "hook_event_name": "Stop", "stop_hook_active": False,
          "background_tasks": [task]}


# --- running the dispatcher ------------------------------------------------------


def scratch_env(root: Path, harness: str) -> Dict[str, str]:
  env = {k: v for k, v in os.environ.items()
         if not k.startswith(("PAWL_", "CLAUDE_PLUGIN_OPTION_", "CLAUDE_",
                              "CODEX_"))}
  env.update(HOME=str(root / "home"), PAWL_DATA=str(root / f"data-{harness}"),
             PAWL_GIT_PROTECTED_ROOTS=str(root / "project"))
  return env


def dispatch(event: str, harness: str, raw: Dict[str, Any], env: Dict[str, str],
             cwd: Path) -> Tuple[str, str]:
  """One run of pawl.py; (stdout, error)."""
  try:
    proc = subprocess.run(
        [sys.executable, "-B", str(PAWL), event, "--harness", harness],
        input=json.dumps(raw), capture_output=True, text=True, env=env,
        cwd=str(cwd), timeout=RUN_TIMEOUT_S, check=False)
  except (OSError, subprocess.TimeoutExpired) as exc:
    return "", f"could not run pawl.py: {exc}"
  if proc.returncode != 0 or "Traceback" in proc.stderr:
    return proc.stdout, (f"pawl.py exited {proc.returncode}:"
                         f" {proc.stderr.strip()[-300:]}")
  return proc.stdout, ""


def run_harness(harness: str, root: Path) -> List[Answer]:
  """Every scenario through one harness's dispatcher, in order."""
  s = Scratch(root / "home", root / "project")
  env = scratch_env(root, harness)
  answers = []
  for n, sc in enumerate(SCENARIOS):
    conv = f"demo-{n}"
    if (sc.kind, harness) in NOT_SHOWN:
      answers.append(Answer(sc.label, harness, ""))
      continue
    if sc.kind == "stop":
      out, err = dispatch("stop", harness, stop_payload(conv, s), env,
                          s.project)
      answers.append(Answer(sc.label, harness, out, err))
      continue
    tool, args = tool_call(harness, sc.kind, sc.args, s)
    raw = payload(harness, conv, tool, args, s)
    for _ in range(sc.times):
      out, err = dispatch("pre", harness, raw, env, s.project)
    answers.append(Answer(sc.label, harness, out, err))
  return answers


def collect() -> List[Answer]:
  """All answers, harness by harness, from a scratch tree deleted after."""
  root = Path(tempfile.mkdtemp(prefix="pawl-demo-"))
  try:
    (root / "project").mkdir()
    (root / "home").mkdir()
    with ThreadPoolExecutor(len(HARNESSES)) as pool:
      runs = pool.map(lambda h: run_harness(h, root), HARNESSES)
      return [a for answers in runs for a in answers]
  finally:
    shutil.rmtree(root, ignore_errors=True)


# --- the table -------------------------------------------------------------------


GATE_RE = re.compile(r"\[PAWL ([\w-]+)\]|\[(IDLE TASK)\]")
# Reason prefixes that differ from the gate name PAWL_DISABLE takes.
PREFIX_GATES = {"no-op": "noop", "IDLE TASK": "idle"}


def summarize(answer: Answer) -> Tuple[str, str]:
  """(cell, reason) for one answer: the decision as that harness sees it."""
  if answer.error:
    return "ERROR", answer.error
  if not answer.stdout.strip():
    return "silent", ""
  data = json.loads(answer.stdout)
  if answer.harness == "antigravity":
    word = str(data.get("decision", "?"))
    word += " +rewrite" if data.get("overwrite") else ""
    return word, str(data.get("reason", ""))
  spec = data.get("hookSpecificOutput")
  if not isinstance(spec, dict):  # a Stop answer
    return str(data.get("decision", "?")), str(data.get("reason", ""))
  word = str(spec.get("permissionDecision", ""))
  if "updatedInput" in spec:
    word = f"{word} +rewrite".strip()
  return word or "silent", str(spec.get("permissionDecisionReason", ""))


def gate_of(reasons: Sequence[str], cells: Sequence[str]) -> str:
  """The gate name behind a row, as PAWL_DISABLE spells it; - for none."""
  for reason in reasons:
    m = GATE_RE.search(reason)
    if m:
      name = m.group(1) or m.group(2)
      return PREFIX_GATES.get(name, name)
  if any("+rewrite" in cell for cell in cells):
    return "zero-width"  # the one gate that rewrites; it gives no reason
  return "-"


LEGEND = """\
silent        no opinion: the harness prompts or runs as it always would
allow         Claude Code runs it without the permission prompt
auto_approve  the same on Antigravity
ask           the human decides (force_ask on Antigravity)
deny          refused; the agent is told why and what to do instead
+rewrite      runs with the input cleaned
block         the turn may not end yet; the agent is told why
Codex hooks can neither ask nor approve: an ask there is a deny that says
how to proceed, and an approval stays silent."""


def table(answers: Sequence[Answer], verbose: bool = False) -> str:
  by_key = {(a.scenario, a.harness): a for a in answers}
  head = f"{'call':<27} {'gate':<12}" + "".join(
      f"{TITLES[h]:<17}" for h in HARNESSES)
  lines = ["pawl demo: each call as the harness sends it, through"
           " hooks/pawl.py", "", head.rstrip(), "-" * len(head.rstrip())]
  notes = []
  for sc in SCENARIOS:
    cells = []
    reasons = []
    for h in HARNESSES:
      shown = NOT_SHOWN.get((sc.kind, h))
      cell, reason = ("n/a", "") if shown else summarize(by_key[sc.label, h])
      cells.append(f"{cell:<17}")
      reasons.append(reason)
      if verbose and reason:
        notes.append(f"  {sc.label} / {TITLES[h]}: {reason.splitlines()[0]}")
    row = f"{sc.label:<27} {gate_of(reasons, cells):<12}" + "".join(cells)
    lines.append(row.rstrip())
  lines += ["", "n/a: " + "; ".join(
      f"{h} {sc} ({why})" for (sc, h), why in NOT_SHOWN.items()), "", LEGEND]
  if notes:
    lines += ["", "reasons:"] + notes
  return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
  ap = argparse.ArgumentParser(prog="pawl.py demo",
                               description="pawl's gates on canned calls")
  mode = ap.add_mutually_exclusive_group()
  mode.add_argument("--verbose", action="store_true",
                    help="add each answer's reason")
  mode.add_argument("--json", action="store_true",
                    help="print every raw answer as JSON")
  args = ap.parse_args(argv)
  answers = collect()
  if args.json:
    print(json.dumps([a._asdict() for a in answers], indent=2))
  else:
    print(table(answers, args.verbose))
  failed = [a for a in answers if a.error]
  for a in failed:
    sys.stderr.write(f"[pawl demo] {a.scenario} / {a.harness}: {a.error}\n")
  return 1 if failed else 0


if __name__ == "__main__":
  sys.exit(main())
