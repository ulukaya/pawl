#!/usr/bin/env python3
"""blast_radius.py: judge a shell command by what it would delete.

A guard that reads the command line misses the deletion one step away: in
the script the agent wrote a minute ago, in a `trap`, an `npm run clean`, a
Makefile recipe, `python -c`, or a variable reassigned to "$HOME". This
piece follows each of those (runs.py), expands what every word can be
(expand.py), and judges each deletion target by where it lands
(targets.py):

  deny   home, root, system directories, folders such as ~/.ssh or
         ~/Documents, a drive root, a disk device, or all of their contents
  ask    the whole workspace, anything outside it and the temp dirs, or a
         path that starts with something pawl cannot resolve
  allow  inside the workspace (git toplevel of the command's directory) or
         a temp directory

Deleters: rm, rmdir, unlink, shred, find -delete / -exec rm, xargs rm, mv to
/dev/null, dd of=<disk>, mkfs, wipefs, the cmd and PowerShell forms, and the
delete APIs of Python, JavaScript, Ruby and Perl (code_scan.py).

CLI:
    blast_radius.py check [--cwd DIR] -- <command...>
        exit 0 allow, 1 ask or deny (decision and reason on stdout), 2 usage

Standard library only.
"""

from __future__ import annotations

import json
import os
import posixpath
import sys
from typing import Any, Dict, List, Mapping, NamedTuple, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
  sys.path.insert(0, HERE)

# pylint: disable=g-import-not-at-top,wrong-import-position
import expand  # noqa: E402
from scanner import Finding, Scanner  # noqa: E402
from targets import DENY, WORDS, Policy  # noqa: E402

PREFIX = "[PAWL blast]"


class Verdict(NamedTuple):
  decision: str  # allow, ask, deny
  reason: str = ""


def _reason(findings: List[Finding]) -> str:
  worst = max(findings, key=lambda f: f.level)
  route = "".join(f"`{v}` runs " for v in worst.via)
  more = len(findings) - 1
  tail = f" ({more} more deletion{'s' if more > 1 else ''} flagged.)" \
      if more else ""
  if worst.level == DENY:
    return (f"{PREFIX} {route}`{worst.how}`, which would delete {worst.what}."
            " pawl never lets an agent delete home, root, system or drive"
            " folders; if this is really wanted, the user runs it by hand."
            + tail)
  return (f"{PREFIX} {route}`{worst.how}`, which would delete {worst.what}."
          " Approve only if the user asked for this deletion." + tail)


def assess(command: str, cwd: Optional[str],
           env: Optional[Mapping[str, str]] = None) -> Verdict:
  """The verdict on one shell command run from `cwd`."""
  env = dict(os.environ if env is None else env)
  cwd = posixpath.normpath(cwd) if cwd else None
  ctx = expand.Ctx(Policy(cwd, env), env, cwd)
  scanner = Scanner(ctx)
  try:
    scanner.text(command)
  except Exception as exc:  # pylint: disable=broad-exception-caught
    return Verdict("ask", f"{PREFIX} pawl could not finish reading this"
                   f" command ({type(exc).__name__}: {exc}); approve only if"
                   " you know what it deletes.")
  if not scanner.findings:
    return Verdict("allow")
  level = max(f.level for f in scanner.findings)
  return Verdict(WORDS[level], _reason(scanner.findings))


# --- hook ------------------------------------------------------------------

def command_and_cwd(payload: Dict[str, Any]):
  call = payload.get("toolCall") or payload.get("tool_call") or {}
  args = call.get("args") if isinstance(call, dict) else None
  args = args if isinstance(args, dict) else payload.get("tool_input") or {}
  if not isinstance(args, dict):
    return "", None
  cmd = args.get("CommandLine") or args.get("command") or ""
  cwd = args.get("Cwd") or args.get("cwd") or payload.get("cwd") or None
  return (cmd if isinstance(cmd, str) else ""), cwd


def decide(payload: Dict[str, Any]) -> Dict[str, str]:
  """Antigravity-style decision for one tool-call payload."""
  cmd, cwd = command_and_cwd(payload)
  if not cmd.strip():
    return {"decision": "allow"}
  verdict = assess(cmd, cwd or os.getcwd())
  if verdict.decision == "allow":
    return {"decision": "allow"}
  word = "deny" if verdict.decision == "deny" else "force_ask"
  return {"decision": word, "reason": verdict.reason}


def main(argv: Optional[List[str]] = None) -> int:
  argv = list(sys.argv[1:] if argv is None else argv)
  if argv[:1] == ["hook"]:
    try:
      payload = json.loads(sys.stdin.read() or "{}")
    except ValueError:
      payload = {}
    print(json.dumps(decide(payload if isinstance(payload, dict) else {})))
    return 0
  if argv[:1] != ["check"]:
    print("usage: blast_radius.py check [--cwd DIR] -- <command...>",
          file=sys.stderr)
    return 2
  cwd = os.getcwd()
  rest = argv[1:]
  if rest[:1] == ["--cwd"] and len(rest) > 1:
    cwd, rest = rest[1], rest[2:]
  if rest[:1] == ["--"]:
    rest = rest[1:]
  verdict = assess(" ".join(rest), cwd)
  print(verdict.decision if verdict.decision == "allow"
        else f"{verdict.decision}: {verdict.reason}")
  return 0 if verdict.decision == "allow" else 1


if __name__ == "__main__":
  sys.exit(main())
