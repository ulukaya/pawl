#!/usr/bin/env python3
"""destructive_git_guard.py: deny git that discards work or hides a commit.

Git has no pre-reset, pre-checkout, pre-clean or pre-stash hook, so the only
place to stop `git reset --hard` in a repo several agents write to at once is
the tool call that would run it. This piece tokenizes a shell command line
(shlex, per segment split on `;`, `&&`, `||`, `|`, newline), finds each git
invocation, resolves the repo it targets, and denies when:

  reset       anything but `--soft` in a protected root
  checkout    a pathspec (`--`, `.`, `-f`/`--force`) in a protected root
  restore     anything but index-only `--staged` in a protected root
  stash       anything but `list`/`show` in a protected root
  clean       anything but `-n`/`--dry-run` in a protected root (`-fdx` clusters
              included)
  rm          always, in a protected root
  commit      `--no-verify` or a short cluster carrying `n` (`-n`, `-nm`,
              `-anm`); output piped into `tail`/`head`; output sent to
              `/dev/null`. Applies in every repo.

Protected roots come from PAWL_GIT_PROTECTED_ROOTS (colon-separated). When
unset, the guard protects the git toplevel of the tool call's working
directory; outside a repo nothing is protected and only the commit rules apply.
`cd X` in an earlier segment, `-C`, `--git-dir` and `--work-tree` are honored
when resolving the target. Common prefixes (`env`, `sudo`, `timeout N`,
`VAR=val`) are skipped when locating the `git` token.

CLI:
    destructive_git_guard.py check [--cwd DIR] <command...>
        exit 0 allow, 1 deny (reason on stdout)
    destructive_git_guard.py roots [--cwd DIR]
        print the protected roots in effect

Environment:
    PAWL_GIT_PROTECTED_ROOTS   colon-separated repo roots; default: toplevel of
                               the call's cwd
    PAWL_DATA                  denial log dir (default ~/.pawl)
    PAWL_GIT_GUARD_WATCHDOG_S  watchdog seconds (default host timeout 15 - 1)

Fails closed on an unparsable payload and on an internal error while scanning a
git command. Fails open on time (watchdog). Standard library only.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path  # pylint: disable=g-importing-member
import re
import shlex
import signal
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

HOOK_NAME = "destructive_git_guard"
GATE = "DESTRUCTIVE_GIT"
ROOTS_ENV = "PAWL_GIT_PROTECTED_ROOTS"
WATCHDOG_ENV = "PAWL_GIT_GUARD_WATCHDOG_S"
HOST_TIMEOUT_S = 15.0
TOPLEVEL_TIMEOUT_S = 3.0

READ_ONLY_SUBCOMMANDS = frozenset({
    "log",
    "show",
    "status",
    "diff",
    "rev-parse",
    "blame",
    "ls-files",
    "cat-file",
    "rev-list",
    "describe",
    "shortlog",
    "show-ref",
    "ls-tree",
    "for-each-ref",
    "config",
    "remote",
    "help",
    "version",
    "grep",
    "reflog",
})
GUARDED_SUBCOMMANDS = frozenset(
    {"reset", "checkout", "restore", "stash", "clean", "rm"}
)
_GLOBAL_VALUE_OPTS = frozenset({
    "-C",
    "-c",
    "--git-dir",
    "--work-tree",
    "--namespace",
    "--exec-path",
    "--super-prefix",
    "--config-env",
})
_SEPARATORS = (";", "&&", "||", "|", "\n")
_PREFIX_CMDS = frozenset(
    {"env", "sudo", "nohup", "command", "timeout", "nice", "time"}
)

REMEDY = (
    "Rewrite the file with write_to_file instead of reverting it through git."
    " To unstage without touching the working tree use `git restore --staged"
    " <paths>`. To commit use `git commit --only <paths>`, which snapshots from"
    " the working tree and is immune to another process writing .git/index at"
    " the same time."
)

# --- commit rules (quote-aware regex scan) ------------------------------------
_GIT_OPTS = r"\bgit\b(?:\s+-[cC]\s*\S+|\s+--[\w-]+(?:=\S+)?)*\s+commit\b"
# `--no-verify[=..]` or any single-dash short-option cluster containing `n`
# (`-n`, `-nm`, `-anm`, `-qn`). `(?!-)` keeps `--no-edit`/`--amend` out.
_NO_VERIFY_RE = re.compile(
    _GIT_OPTS
    + r"[^;&|]*(?:--no-verify\b|(?:^|\s)-(?!-)[a-zA-Z]*n[a-zA-Z]*(?=\s|$))"
)
_QUOTE_RE = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'')
_SEP_RE = re.compile(r";|&&|\|\|")
_COMMIT_RE = re.compile(_GIT_OPTS)
_TRUNC_PIPE_RE = re.compile(r"\|&?\s*\(?\s*[\"']?(?:tail|head)\b")
_SUPPRESS_REDIR_RE = re.compile(r"(?:\d*>&?|&>)\s*/dev/null\b")
_BARE_PIPE_CMDS = ("tail", "head")


def _commit_segments(cmd: str) -> List[str]:
  """Split on `;` / `&&` / `||` outside quotes, slicing the original text."""
  masked = _QUOTE_RE.sub(lambda m: " " * len(m.group(0)), cmd)
  segments, start = [], 0
  for sep in _SEP_RE.finditer(masked):
    segments.append(cmd[start : sep.start()])
    start = sep.end()
  segments.append(cmd[start:])
  return segments


def _strip_quotes(segment: str) -> str:
  """Blanks quoted text so `-m "drop -n"` or `-m "a | tail"` cannot trip a rule.

  A bare quoted `"tail"`/`"head"` stays visible. A quote wrapping its own
  `git commit` (eval "...", "$(...)") collapses to a `git commit` marker so
  the outer pipe check still sees a commit; its inner text is checked by
  `_inner_commit_reason`.

  Args:
    segment: one pipeline segment of the command.

  Returns:
    The segment with quoted spans blanked or collapsed.
  """

  def blank(m: re.Match[str]) -> str:
    inner = m.group(0)[1:-1]
    if inner in _BARE_PIPE_CMDS:
      return m.group(0)
    return "git commit" if _COMMIT_RE.search(inner) else '""'

  return _QUOTE_RE.sub(blank, segment)


def _inner_commit_reason(segment: str) -> str:
  for m in _QUOTE_RE.finditer(segment):
    inner = m.group(0)[1:-1].replace('\\"', '"')
    if _COMMIT_RE.search(inner):
      reason = unsafe_commit_reason(inner)
      if reason:
        return reason
  return ""


def unsafe_commit_reason(cmd: str) -> str:
  """Reason string if `cmd` bypasses or hides a commit gate, else ''."""
  if not cmd:
    return ""
  for segment in _commit_segments(cmd):
    stripped = _strip_quotes(segment)
    if _NO_VERIFY_RE.search(stripped):
      return (
          "`git commit --no-verify` (or a short cluster carrying -n) skips the"
          " pre-commit gate; fix what the gate reported instead."
      )
    if _COMMIT_RE.search(stripped) and _TRUNC_PIPE_RE.search(stripped):
      return (
          "`git commit` piped into tail/head hides the rejecting gate; use `>"
          " /tmp/commit.log 2>&1; echo exit=$?; git log -1`."
      )
    if _COMMIT_RE.search(stripped) and _SUPPRESS_REDIR_RE.search(stripped):
      return (
          "`git commit` redirected to /dev/null discards the commit receipt;"
          " use `git commit -F <file>` followed by `git log -1 --oneline`."
      )
    reason = _inner_commit_reason(segment)
    if reason:
      return reason
  return ""


# --- protected roots ----------------------------------------------------------


def _resolve(path: str, cwd: str) -> str:
  """Absolute realpath of `path` relative to `cwd`, with ~ expanded."""
  expanded = os.path.expanduser(path)
  if not os.path.isabs(expanded):
    expanded = os.path.join(cwd, expanded)
  return os.path.realpath(expanded)


def _inside(resolved: str, root: str) -> bool:
  return resolved == root or resolved.startswith(root + os.sep)


def git_toplevel(cwd: str) -> str:
  """Realpath of the repo containing `cwd`, or '' outside a git work tree."""
  if not os.path.isdir(cwd):
    return ""
  try:
    proc = subprocess.run(
        ["git", "-C", cwd, "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        timeout=TOPLEVEL_TIMEOUT_S,
        check=False,
    )
  except (OSError, subprocess.SubprocessError):
    return ""
  top = proc.stdout.strip()
  return os.path.realpath(top) if proc.returncode == 0 and top else ""


def protected_roots(cwd: str) -> List[str]:
  """Realpaths this guard protects.

  Args:
    cwd: directory the command runs in.

  Returns:
    PAWL_GIT_PROTECTED_ROOTS when set, else the toplevel of `cwd`.
  """
  raw = os.environ.get(ROOTS_ENV)
  if raw is not None:
    return [_resolve(p, cwd) for p in raw.split(":") if p.strip()]
  top = git_toplevel(cwd)
  return [top] if top else []


# --- git command scan ---------------------------------------------------------


def _split_segments(command: str) -> List[str]:
  segments = [command]
  for sep in _SEPARATORS:
    nxt: List[str] = []
    for seg in segments:
      nxt.extend(seg.split(sep))
    segments = nxt
  return segments


def _tokenize(segment: str) -> List[str]:
  try:
    return shlex.split(segment)
  except ValueError:
    return segment.split()


def _git_index(tokens: List[str]) -> int:
  """Index of the `git` token, skipping common prefixes (env, sudo, timeout)."""
  for i, tok in enumerate(tokens):
    if os.path.basename(tok) == "git":
      return i
    if "=" in tok and not tok.startswith("-"):
      continue
    if tok in _PREFIX_CMDS:
      continue
    if tok.startswith("-") or tok.rstrip("smh").isdigit():
      continue
    return -1
  return -1


def _git_dir_to_root(path: str) -> str:
  return os.path.dirname(path) if os.path.basename(path) == ".git" else path


def _parse_git(
    tokens: List[str], start: int, cwd: str
) -> Tuple[Optional[str], List[str], Optional[str]]:
  """Return (subcommand, remaining args, explicit target dir) for a git call."""
  target: Optional[str] = None
  i = start + 1
  n = len(tokens)
  while i < n:
    tok = tokens[i]
    if tok == "-C" and i + 1 < n:
      target = _resolve(tokens[i + 1], target or cwd)
      i += 2
    elif tok.startswith("--git-dir=") or tok.startswith("--work-tree="):
      target = _git_dir_to_root(_resolve(tok.split("=", 1)[1], cwd))
      i += 1
    elif tok in {"--git-dir", "--work-tree"} and i + 1 < n:
      target = _git_dir_to_root(_resolve(tokens[i + 1], cwd))
      i += 2
    elif tok in _GLOBAL_VALUE_OPTS:
      i += 2
    elif tok.startswith("-"):
      i += 1
    else:
      return tok, tokens[i + 1 :], target
  return None, [], target


def _short_flags(flags: List[str]) -> str:
  """Letters from clustered short options: ['-fdx', '--force'] -> 'fdx'."""
  return "".join(
      f[1:] for f in flags if f.startswith("-") and not f.startswith("--")
  )


def destructive_reason(subcmd: str, args: List[str]) -> str:
  """Reason string if this subcommand+args discards state, else ''."""
  flags = [a for a in args if a.startswith("-") and a != "--"]
  operands = [a for a in args if not a.startswith("-")]
  letters = _short_flags(flags)

  if subcmd == "reset":
    if "--soft" in flags:
      return ""
    return (
        "`git reset` rewrites the shared index and (with --hard/--merge) the"
        " working tree"
    )
  if subcmd == "checkout":
    if "--" in args or "." in operands or "--force" in flags or "f" in letters:
      return (
          "`git checkout` of a pathspec overwrites working-tree files from the"
          " index"
      )
    return ""
  if subcmd == "restore":
    staged = "--staged" in flags or "S" in letters
    if staged and "--worktree" not in flags and "W" not in letters:
      return ""
    return "`git restore` without --staged overwrites working-tree files"
  if subcmd == "stash":
    if operands and operands[0] in {"list", "show"}:
      return ""
    return (
        "`git stash` removes working-tree changes, including work another"
        " process has not committed"
    )
  if subcmd == "clean":
    if "--dry-run" in flags or "n" in letters:
      return ""
    return "`git clean` deletes untracked files outright"
  if subcmd == "rm":
    return "`git rm` deletes tracked files and stages the deletion"
  return ""


def _targets_root(
    args: List[str], cwd: str, explicit: Optional[str], root: str
) -> bool:
  if explicit is not None:
    return _inside(explicit, root)
  if _inside(os.path.realpath(cwd), root):
    return True
  return any(
      _inside(_resolve(a, cwd), root) for a in args if not a.startswith("-")
  )


def _effective_cwd(segments: List[str], up_to: int, cwd: str) -> str:
  current = cwd
  for seg in segments[:up_to]:
    tokens = _tokenize(seg)
    if len(tokens) == 2 and tokens[0] == "cd":
      current = _resolve(tokens[1], current)
  return current


def scan(command: str, cwd: str, roots: Optional[List[str]] = None) -> str:
  """Deny reason for `command` run from `cwd`, or '' to allow."""
  if "git" not in command:
    return ""
  commit = unsafe_commit_reason(command)
  if commit:
    return f"[DESTRUCTIVE GIT] {commit}"
  if roots is None:
    roots = protected_roots(cwd)
  if not roots:
    return ""
  segments = _split_segments(command)
  for idx, segment in enumerate(segments):
    if "git" not in segment:
      continue
    tokens = _tokenize(segment)
    gi = _git_index(tokens)
    if gi < 0:
      continue
    subcmd, rest, explicit = _parse_git(tokens, gi, cwd)
    if (
        subcmd is None
        or subcmd in READ_ONLY_SUBCOMMANDS
        or subcmd not in GUARDED_SUBCOMMANDS
    ):
      continue
    reason = destructive_reason(subcmd, rest)
    if not reason:
      continue
    seg_cwd = _effective_cwd(segments, idx, cwd)
    for root in roots:
      if _targets_root(rest, seg_cwd, explicit, root):
        return (
            f"[DESTRUCTIVE GIT] `git {subcmd}` targets the protected repo"
            f" {root}. {reason}. Other processes may hold uncommitted work"
            f" there that this call would discard. {REMEDY}"
        )
  return ""


# --- payload ------------------------------------------------------------------


def command_and_cwd(payload: Dict[str, Any]) -> Tuple[str, str]:
  """Pulls (command, cwd) out of a tool-call payload.

  Args:
    payload: the parsed PreToolUse payload.

  Returns:
    (command, cwd); command is '' when the call is not run_command.
  """
  call = payload.get("toolCall") or payload.get("tool_call") or {}
  if not isinstance(call, dict):
    call = {}
  name = str(
      call.get("name")
      or payload.get("toolName")
      or payload.get("tool_name")
      or ""
  )
  if name not in {"run_command", "run_shell_command"}:
    return "", ""
  args = (
      call.get("args")
      or call.get("arguments")
      or payload.get("toolArgs")
      or payload.get("tool_input")
      or {}
  )
  if not isinstance(args, dict):
    return "", ""
  cmd = str(args.get("CommandLine") or args.get("command") or "")
  cwd = str(args.get("Cwd") or args.get("cwd") or "") or os.getcwd()
  return cmd, cwd


def read_payload(raw: str) -> Tuple[Optional[Dict[str, Any]], str]:
  """Parses the hook payload.

  Args:
    raw: the stdin text.

  Returns:
    (payload, '') or (None, deny reason): unparsable input is a deny.
  """
  try:
    data = json.loads(raw or "{}")
  except ValueError as err:
    return (
        None,
        (
            f"[HOOK PAYLOAD] {HOOK_NAME}: stdin is not JSON ({err}); refusing"
            " the call."
        ),
    )
  if not isinstance(data, dict):
    return (
        None,
        (
            f"[HOOK PAYLOAD] {HOOK_NAME}: payload is {type(data).__name__}, not"
            " an object; refusing the call."
        ),
    )
  return data, ""


def decide(payload: Dict[str, Any]) -> Optional[str]:
  """Deny reason for this payload, or None to allow."""
  cmd, cwd = command_and_cwd(payload)
  if not cmd:
    return None
  return scan(cmd, cwd) or None


# --- denial log ---------------------------------------------------------------


def data_dir() -> Path:
  return Path(os.environ.get("PAWL_DATA") or os.path.expanduser("~/.pawl"))


def record_denial(
    gate: str,
    cmd: str,
    payload: Optional[Dict[str, Any]],
    outcome: str = "deny",
) -> None:
  """Append one JSON line to PAWL_DATA/denials.jsonl. Never raises.

  Args:
    gate: gate name, e.g. DESTRUCTIVE_GIT.
    cmd: the command line; only its sha1 is stored.
    payload: the hook payload, read for a conversation id.
    outcome: "deny" for the piece hook, "force_ask" for the plugin hook.
  """
  try:
    conv = ""
    if isinstance(payload, dict):
      conv = str(
          payload.get("conversationId") or payload.get("conversation_id") or ""
      )
    conv = conv or os.environ.get("CONVERSATION_ID", "")
    row = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "conv": conv,
        "hook": HOOK_NAME,
        "gate": gate,
        "cmd_sha1": hashlib.sha1(cmd.encode("utf-8", "replace")).hexdigest(),
        "outcome": outcome,
    }
    path = data_dir() / "denials.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
      fh.write(json.dumps(row, sort_keys=True) + "\n")
  except Exception:  # pylint: disable=broad-exception-caught
    # fail open: telemetry must never break a hook
    return


# --- watchdog -----------------------------------------------------------------


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
    sys.stdout.write(json.dumps({"decision": "allow"}))
    sys.stdout.flush()
    sys.stderr.write(
        f"[{HOOK_NAME}] watchdog fired after {seconds}s; failing open\n"
    )
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


# --- hook entry ---------------------------------------------------------------


def evaluate(
    raw: str,
) -> Tuple[str, str, str, Optional[Dict[str, Any]]]:
  """Decision for raw stdin text without touching the denial log.

  Args:
    raw: the hook payload text.

  Returns:
    (decision, reason, cmd, payload). decision is "allow" or "deny"; reason
    is "" on allow; payload is None when stdin was unparsable.
  """
  payload, err = read_payload(raw)
  if payload is None:
    return "deny", err, "", None
  cmd = command_and_cwd(payload)[0]
  try:
    reason = decide(payload)
  except Exception as exc:  # pylint: disable=broad-exception-caught
    # fail closed: a scanner bug must not let a destructive command run

    reason = (
        f"[DESTRUCTIVE GIT] internal error while scanning `{cmd[:80]}`, failing"
        f" closed: {exc!r}"
    )
  if reason is None:
    return "allow", "", cmd, payload
  return "deny", reason, cmd, payload


def run_hook(raw: str) -> Dict[str, str]:
  """Full hook decision for raw stdin text: payload, then scan, then log."""
  decision, reason, cmd, payload = evaluate(raw)
  if decision == "allow":
    return {"decision": "allow"}
  if payload is not None:
    record_denial(GATE, cmd, payload)
  return {"decision": "deny", "reason": reason}


def hook_main() -> None:
  arm_watchdog(watchdog_budget_s())
  try:
    result = run_hook(sys.stdin.read())
  finally:
    disarm_watchdog()
  sys.stdout.write(json.dumps(result))
  sys.stdout.flush()


# --- CLI ----------------------------------------------------------------------


def _pop_cwd(argv: List[str]) -> Tuple[str, List[str]]:
  cwd = os.getcwd()
  if len(argv) >= 2 and argv[0] == "--cwd":
    cwd, argv = argv[1], argv[2:]
  return cwd, argv


def main(argv: Optional[List[str]] = None) -> int:
  argv = list(sys.argv[1:] if argv is None else argv)
  if not argv or argv[0] not in {"check", "roots"}:
    sys.stderr.write(
        "usage: destructive_git_guard.py check [--cwd DIR] <command...> | roots"
        " [--cwd DIR]\n"
    )
    return 2
  cwd, rest = _pop_cwd(argv[1:])
  if argv[0] == "roots":
    for root in protected_roots(cwd):
      print(root)
    return 0
  reason = scan(" ".join(rest), cwd)
  if not reason:
    print("allow")
    return 0
  print(reason)
  return 1


if __name__ == "__main__":
  sys.exit(main())
