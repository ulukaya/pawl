#!/usr/bin/env python3
"""readonly_pass.py: auto-approve a shell command that provably only reads.

Answers a run_command with the host's auto_approve decision when every clause
of the command only reads: ls, cat, head, tail, wc, grep, rg, find without
-exec or -delete, sed -n '<N>,<M>p', sort, uniq, tree, file, echo, cd, and the
read-only subcommands of git, hg, jj and g4 (readonly_rules.py). Anything it
cannot prove gets allow, so the host prompts as it does today. It never denies
or asks; any other hook's deny, force_ask or ask still wins.

Left to the prompt: `$` outside single quotes (command substitution,
variables), backticks, process substitution, heredocs and here-strings,
subshells, braces, `#`, newlines, background `&`, a leading VAR=value, a
program named by path, output redirects other than to /dev/null or between
stdout and stderr, secret paths, agent conversation stores (brain/,
conversations/, transcript*.jsonl), and calls that ask to bypass the sandbox.
Secret paths are checked per shell word after quote removal and `~` expansion,
and on every redirect target.

Decision on stdout: {"decision": "auto_approve"} or {"decision": "allow"}.
Each approval appends one row to PAWL_DATA/denials.jsonl with gate
READONLY_PASS, outcome auto_approve and the command sha1 only. Fails open
(allow) on bad stdin, an internal error and the watchdog.

CLI:
    readonly_pass.py check <command...>
        exit 0 read-only, 1 prompt, 2 usage

Environment:
    PAWL_DATA                      log dir (default ~/.pawl)
    PAWL_READONLY_PASS_OFF         1 turns the piece off (always allow)
    PAWL_READONLY_PASS_WATCHDOG_S  watchdog seconds (default host timeout 15
                                   - 1)

Standard library only.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path  # pylint: disable=g-importing-member
import re
import shlex
import signal
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

import readonly_rules

HOOK_NAME = "readonly_pass"
GATE = "READONLY_PASS"
OFF_ENV = "PAWL_READONLY_PASS_OFF"
WATCHDOG_ENV = "PAWL_READONLY_PASS_WATCHDOG_S"
HOST_TIMEOUT_S = 15.0
ALLOW = {"decision": "allow"}
APPROVE = {"decision": "auto_approve"}
SHELL_TOOLS = frozenset({"run_command", "run_shell_command"})
SANDBOX_BYPASS_KEYS = frozenset({
    "BypassSandbox", "bypass_sandbox", "DangerouslyDisableSandbox",
    "dangerouslyDisableSandbox", "dangerously_disable_sandbox",
})

SEPARATORS = frozenset({";", "&&", "||", "|", "|&"})
REDIRECTS = frozenset({">", ">>", ">&", "&>", "&>>", "<", "<>", ">|", "<<",
                       "<<<", "<&"})
WRITE_REDIRECTS = frozenset({">", ">>", ">&", "&>", "&>>"})
SAFE_REDIRECT_TARGETS = frozenset({"/dev/null", "&1", "&2"})
_PUNCT = frozenset("();<>|&")
_UNQUOTED_BAD = frozenset("$`(){}#\n")
FENCED_PATH_RE = re.compile(
    r"(?:^|[/=])\.(?:ssh|gnupg|aws|docker|kube)(?:[/*=-]|$|[?*])"
    r"|(?:^|[/=])\.(?:netrc|pgpass|git-credentials|npmrc|pypirc|env(?:[._-].*|[*-].*)?)$"
    r"|(?:^|[/=])\.config/gcloud(?:/|$)"
    r"|(?:^|[/=])id_(?:rsa|dsa|ecdsa|ed25519)"
    r"|(?:^|[/=])(?:brain|conversations)(?:/|$)"
    r"|transcript[^/]*\.jsonl"
    r"|(?:^|/)proc/[^/]+/environ$"
    r"|^(?:~|/|(?:/usr/local/google)?/(?:home|Users)/[^/]+)/(?:\*|\.\*|\.[a-z]*\*.*)$"
    r"|^(?:/\*|/\.\*)$"
)
SENSITIVE_ROOT_RE = re.compile(
    r"^(?:~[/]?|/(?:home|Users)(?:/[^/]+(?:/(?:\.gemini|\.antigravity|\.jetski"
    r"|\.config|brain|conversations)(?:/.*)?)?)?/?|/|~/(?:\.gemini|\.antigravity"
    r"|\.jetski|\.config|brain|conversations)(?:/.*)?|/(?:proc|etc)(?:/.*)?)$"
)


# --- command analysis ---------------------------------------------------------


def unsafe_text(cmd: str) -> bool:
  """True for shell syntax the parser below cannot prove harmless."""
  quote = ""
  i = 0
  while i < len(cmd):
    c = cmd[i]
    i += 1
    if quote == "'":
      quote = "" if c == "'" else quote
      continue
    if c == "\\":
      i += 1
      continue
    if quote == '"':
      if c in "$`":
        return True
      quote = "" if c == '"' else quote
      continue
    if c in _UNQUOTED_BAD:
      return True
    if c in "'\"":
      quote = c
  return bool(quote)


def tokens(cmd: str) -> Optional[List[str]]:
  """Shell words and operators (`2>&1` -> `2`, `>&`, `1`), quotes removed."""
  lex = shlex.shlex(cmd, posix=True, punctuation_chars=True)
  lex.whitespace_split = True
  lex.commenters = ""
  try:
    return list(lex)
  except ValueError:
    return None


def split_clauses(toks: List[str]) -> List[List[str]]:
  clauses: List[List[str]] = [[]]
  for tok in toks:
    if tok in SEPARATORS:
      clauses.append([])
    else:
      clauses[-1].append(tok)
  if len(clauses) > 1 and not clauses[-1]:
    clauses.pop()  # a trailing `;`
  return clauses


def _redirect_ok(op: str, target: str) -> bool:
  if op == "<":
    return True  # a read; the target is fence-checked by the caller
  if op not in WRITE_REDIRECTS:
    return False
  if op.endswith("&") and target.isdigit():
    target = "&" + target
  return target in SAFE_REDIRECT_TARGETS


def strip_redirects(clause: List[str]) -> Optional[Tuple[List[str], List[str]]]:
  """(words, redirect targets) of one clause, or None when unsafe."""
  words: List[str] = []
  targets: List[str] = []
  i = 0
  while i < len(clause):
    tok = clause[i]
    if tok not in REDIRECTS:
      if set(tok) <= _PUNCT:
        return None  # background `&` or an operator the parser does not know
      words.append(tok)
      i += 1
      continue
    if words and words[-1].isdigit():
      words.pop()  # the fd of `2>`
    target = clause[i + 1] if i + 1 < len(clause) else ""
    if not target or not _redirect_ok(tok, target):
      return None
    targets.append(target)
    i += 2
  return words, targets


def fenced(word: str) -> bool:
  expanded = os.path.expanduser(word)
  return bool(FENCED_PATH_RE.search(expanded)) or bool(
      FENCED_PATH_RE.search(word)
  )


def _is_sensitive_root(path: str) -> bool:
  if SENSITIVE_ROOT_RE.match(path):
    return True
  return bool(SENSITIVE_ROOT_RE.match(os.path.expanduser(path)))


def _is_recursive_grep(words: List[str]) -> bool:
  if not words or words[0] not in ("grep", "egrep", "fgrep"):
    return False
  for w in words[1:]:
    if w in ("-r", "-R", "--recursive", "--directories=recurse"):
      return True
    if w.startswith("-") and not w.startswith("--") and any(
        c in "rR" for c in w[1:]
    ):
      return True
  return False


def _is_sensitive_walk(words: List[str]) -> bool:
  if not words:
    return False
  prog = words[0]
  if prog in ("find", "tree", "du"):
    return any(
        not w.startswith("-") and _is_sensitive_root(w) for w in words[1:]
    )
  if _is_recursive_grep(words):
    return any(
        not w.startswith("-") and _is_sensitive_root(w) for w in words[1:]
    )
  if prog == "rg":
    operands = [w for w in words[1:] if not w.startswith("-")]
    if len(operands) >= 2:
      return any(_is_sensitive_root(w) for w in operands[1:])
  return False


def clause_read_only(clause: List[str]) -> bool:
  parsed = strip_redirects(clause)
  if parsed is None:
    return False
  words, targets = parsed
  if any(fenced(w) for w in words + targets):
    return False
  if _is_sensitive_walk(words):
    return False
  return readonly_rules.clause_ok(words)


def is_read_only(command: str) -> bool:
  """True only when every clause of `command` provably only reads."""
  cmd = command.strip()
  if not cmd or unsafe_text(cmd) or FENCED_PATH_RE.search(cmd):
    return False
  toks = tokens(cmd)
  if not toks:
    return False
  return all(clause_read_only(c) for c in split_clauses(toks))


# --- payload ------------------------------------------------------------------


def data_dir() -> Path:
  return Path(os.environ.get("PAWL_DATA") or os.path.expanduser("~/.pawl"))


def read_payload(raw: str) -> Dict[str, Any]:
  """Parse the hook payload; anything unparsable is {} (fail open)."""
  try:
    data = json.loads(raw or "{}")
  except ValueError:
    return {}
  return data if isinstance(data, dict) else {}


def tool_and_args(payload: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
  """(tool name, args dict) from a tool-call payload."""
  call = payload.get("toolCall") or payload.get("tool_call") or {}
  if not isinstance(call, dict):
    call = {}
  name = str(call.get("name") or payload.get("tool_name") or "")
  args = call.get("args") or call.get("arguments") or payload.get("tool_input")
  return name, args if isinstance(args, dict) else {}


def record_approval(payload: Dict[str, Any], cmd: str) -> None:
  """Append one JSON line to PAWL_DATA/denials.jsonl. Never raises."""
  try:
    conv = payload.get("conversationId") or payload.get("conversation_id")
    row = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "conv": conv if isinstance(conv, str) else "",
        "hook": HOOK_NAME,
        "gate": GATE,
        "cmd_sha1": hashlib.sha1(cmd.encode("utf-8", "replace")).hexdigest(),
        "outcome": "auto_approve",
    }
    path = data_dir() / "denials.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
      fh.write(json.dumps(row, sort_keys=True) + "\n")
  except Exception:  # pylint: disable=broad-exception-caught
    # fail open: telemetry must never break a hook
    return


# --- hook entry ---------------------------------------------------------------


def decide(payload: Dict[str, Any]) -> Dict[str, str]:
  if os.environ.get(OFF_ENV) == "1":
    return dict(ALLOW)
  tool, args = tool_and_args(payload)
  if tool not in SHELL_TOOLS:
    return dict(ALLOW)
  if any(args.get(k) for k in SANDBOX_BYPASS_KEYS):
    return dict(ALLOW)
  cmd = str(args.get("CommandLine") or args.get("command") or "")
  if not is_read_only(cmd):
    return dict(ALLOW)
  record_approval(payload, cmd)
  return dict(APPROVE)


def run_hook(raw: str) -> Dict[str, str]:
  """Full hook decision for raw stdin text. Fails open (allow) on errors."""
  try:
    return decide(read_payload(raw))
  except Exception as exc:  # pylint: disable=broad-exception-caught
    # fail open: unprovable means the host prompts as usual
    sys.stderr.write(f"[{HOOK_NAME}] internal error, failing open: {exc!r}\n")
    return dict(ALLOW)


def watchdog_budget_s() -> float:
  try:
    return max(
        0.5, float(os.environ.get(WATCHDOG_ENV) or (HOST_TIMEOUT_S - 1.0))
    )
  except ValueError:
    return HOST_TIMEOUT_S - 1.0


def arm_watchdog(seconds: float) -> None:
  """Fail open past the hook budget: print allow before the host times out."""

  def _fire(unused_signum, unused_frame):
    sys.stdout.write(json.dumps(ALLOW))
    sys.stdout.flush()
    os._exit(0)

  try:
    signal.signal(signal.SIGALRM, _fire)
    signal.setitimer(signal.ITIMER_REAL, seconds)
  except (ValueError, OSError, AttributeError):
    return


def disarm_watchdog() -> None:
  try:
    signal.setitimer(signal.ITIMER_REAL, 0)
  except (ValueError, OSError, AttributeError):
    return


def hook_main() -> None:
  arm_watchdog(watchdog_budget_s())
  try:
    result = run_hook(sys.stdin.read())
  finally:
    disarm_watchdog()
  sys.stdout.write(json.dumps(result))
  sys.stdout.flush()


def main(argv: Optional[List[str]] = None) -> int:
  argv = list(sys.argv[1:] if argv is None else argv)
  if len(argv) < 2 or argv[0] != "check":
    sys.stderr.write("usage: readonly_pass.py check <command...>\n")
    return 2
  ok = is_read_only(" ".join(argv[1:]))
  print("read-only" if ok else "prompt")
  return 0 if ok else 1


if __name__ == "__main__":
  sys.exit(main())
