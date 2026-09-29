#!/usr/bin/env python3
"""pawl_hook.py: one PreToolUse hook running the three send-time gates in order.

Reads the tool call JSON on stdin (Jetski shape: {"toolCall": {"name",
"args"}}, also the {"tool_name", "tool_input"} shape) and prints one decision:

    {"decision": "allow"}
    {"decision": "deny", "reason": "..."}
    {"decision": "force_ask", "reason": "..."}   budget ceiling: only a human
                                                 click passes

Only command lines that look like a send (chat, mail, social) are gated.
Everything else is allowed without touching any state. For a send, the order
is:

    1. egress firewall   fails closed: a broken rules file blocks the send
    2. prose gate        fails open:  scores the longest quoted string when it
                                      is long enough
    3. send budget       fails open:  spends one unit per channel per local day

Configuration lives under PAWL_DATA (default ~/.pawl, the same default every
piece uses):
    egress_rules.json    firewall rules; created from the bundled default on
                         first run
    send_budget.json     send budget counter (SEND_BUDGET_STATE_DIR defaults to
                         PAWL_DATA here)
    PAWL_DISABLE         comma list of gates to skip: egress,prose,budget
"""

from __future__ import annotations

import json
import os
from pathlib import Path  # pylint: disable=g-importing-member
import re
import shlex
import shutil
import sys
import time
from typing import Any, Dict, List, Optional, Set, Tuple

HERE = Path(__file__).resolve().parent
PIECES = HERE.parent / "pieces"
for sub in ("send-budget", "egress-firewall", "prose-gate"):
  sys.path.insert(0, str(PIECES / sub))

# pylint: disable=g-import-not-at-top
import egress_firewall  # noqa: E402
import prose_gate  # noqa: E402
import send_budget  # noqa: E402

# pylint: enable=g-import-not-at-top

DEFAULT_RULES = HERE / "egress_rules.default.json"
PROSE_MIN_WORDS = 40
_QUOTED = re.compile(r"'((?:[^'\\]|\\.)*)'|\"((?:[^\"\\]|\\.)*)\"", re.S)


def data_dir() -> Path:
  return Path(os.environ.get("PAWL_DATA", os.path.expanduser("~/.pawl")))


# The budget piece keeps its own state dir env; point it at PAWL_DATA unless
# the caller set it.
os.environ.setdefault("SEND_BUDGET_STATE_DIR", str(data_dir()))


def disabled() -> Set[str]:
  raw = os.environ.get("PAWL_DISABLE", "")
  return {g.strip() for g in raw.split(",") if g.strip()}


DECISIONS = ("allow", "deny", "force_ask")


def emit(decision: str, reason: str = "") -> None:
  """Writes one PreToolUse decision: allow, deny, or force_ask."""
  assert decision in DECISIONS, decision
  out = {"decision": decision}
  if reason:
    out["reason"] = reason
  sys.stdout.write(json.dumps(out))
  sys.stdout.flush()


def command_from(payload: Dict[str, Any]) -> str:
  call = payload.get("toolCall") or payload.get("tool_call") or {}
  args = (
      call.get("args")
      or call.get("arguments")
      or payload.get("tool_input")
      or {}
  )
  return str(args.get("CommandLine") or args.get("command") or "")


def rules_path() -> Path:
  path = data_dir() / "egress_rules.json"
  if not path.exists():
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(DEFAULT_RULES, path)
  return path


def longest_quoted(command: str) -> str:
  best = ""
  for m in _QUOTED.finditer(command):
    s = m.group(1) or m.group(2) or ""
    if len(s) > len(best):
      best = s
  return best


_PAYLOAD_FLAGS = frozenset({
    "--text", "--message", "--body", "--subject", "--title", "--to", "--cc",
    "--bcc", "--space", "--user", "-m",
})
_HEREDOC = re.compile(
    r"<<-?\s*(['\"]?)(\w+)\1([^\n]*)\n(.*?)^\2[ \t]*$", re.S | re.M
)
_REDIRECT_TARGET = re.compile(r">>?\s*([^\s;|&]+)")
_FILE_READ = re.compile(r"\$\(\s*(?:cat\s+|<\s*)([^\s)]+)\s*\)")
_OPAQUE = re.compile(r"\$\(|`")


class OpaqueSubstitutionError(Exception):
  """A message value embeds a command whose output is unknown at hook time."""


def heredocs(command: str) -> Dict[str, str]:
  """Collects heredoc bodies from a command.

  Args:
    command: the shell command line.

  Returns:
    Heredoc bodies keyed by the file they are redirected into ("" if none).
  """
  found: Dict[str, str] = {}
  for m in _HEREDOC.finditer(command):
    target = _REDIRECT_TARGET.search(m.group(3))
    key = os.path.expanduser(target.group(1)) if target else ""
    found[key] = found.get(key, "") + m.group(4)
  return found


def payload_values(command: str) -> List[str]:
  """Collects the values of send flags from a command.

  Args:
    command: the shell command line.

  Returns:
    Values given as `--text hi` or `--text=hi`, heredoc bodies removed.
  """
  stripped = _HEREDOC.sub("<<HEREDOC", command)
  try:
    tokens = shlex.split(stripped)
  except ValueError:
    tokens = stripped.split()
  values = []
  for i, tok in enumerate(tokens):
    if tok in _PAYLOAD_FLAGS and i + 1 < len(tokens):
      values.append(tokens[i + 1])
    elif "=" in tok and tok.split("=", 1)[0] in _PAYLOAD_FLAGS:
      values.append(tok.split("=", 1)[1])
  return values


def resolve_file_reads(value: str, written: Dict[str, str]) -> str:
  """Replaces $(cat FILE) and $(< FILE) in a flag value with the file text.

  Args:
    value: one send-flag value.
    written: heredoc bodies keyed by the file the same command writes them
      to; a read of such a file resolves to that body.

  Returns:
    The value with file reads expanded.

  Raises:
    OpaqueSubstitutionError: a `$(...)` or backtick remains after expansion.
    OSError: a read file does not exist or cannot be read.
  """

  def read(m: "re.Match[str]") -> str:
    path = os.path.expanduser(m.group(1))
    if path in written:
      return written[path]
    return Path(path).read_text(encoding="utf-8")

  resolved = _FILE_READ.sub(read, value)
  if _OPAQUE.search(resolved):
    raise OpaqueSubstitutionError(resolved.strip()[:60])
  return resolved


def outbound_text(command: str) -> str:
  """Extracts the text a send would put on the wire.

  Collected: send-flag values, heredoc bodies, and the contents of files a
  value reads with $(cat FILE) or $(< FILE). Tool paths, redirects and pipes
  never leave the machine, so they are not part of the scanned text.

  Args:
    command: the shell command line.

  Returns:
    The outbound text, or the whole command when nothing could be extracted.
  """
  docs = heredocs(command)
  parts = [resolve_file_reads(v, docs) for v in payload_values(command)]
  parts += list(docs.values())
  return "\n".join(parts) if parts else command


def gate_egress(command: str) -> str:
  """Returns a deny reason or an empty string. Any failure is a deny."""
  try:
    rules = egress_firewall.load_rules(str(rules_path()))
    hits = egress_firewall.scan(outbound_text(command), rules)
  except OpaqueSubstitutionError as exc:
    return (
        "[PAWL egress] command substitution in message text cannot be"
        f" scanned before it runs: {exc}"
    )
  except Exception as exc:  # pylint: disable=broad-exception-caught
    # fail closed
    return f"[PAWL egress] internal error, failing closed: {exc}"
  if not hits:
    return ""
  shown = hits[:5]
  more = f" (+{len(hits) - len(shown)} more)" if len(hits) > len(shown) else ""
  return "[PAWL egress] " + "; ".join(shown) + more


def gate_prose(command: str) -> str:
  """Returns a deny reason or an empty string. Any failure is an allow."""
  try:
    text = longest_quoted(command)
    if len(text.split()) < PROSE_MIN_WORDS:
      return ""
    result = prose_gate.score(text, "chat", prose_gate.load_catalog())
    if not result.get("is_fail"):
      return ""
    tells = ", ".join(d["code"] for d in result.get("details", [])[:6])
    return (
        f"[PAWL prose] draft scored {result.get('score')} over the chat"
        f" threshold {result.get('threshold')}; tells: {tells}. Rewrite in"
        " plain words and resend."
    )
  except Exception:  # pylint: disable=broad-exception-caught
    # fail open: a broken prose gate must not block sends
    return ""


ASK = "ask"


def _spend(channel: str, reason: str, override: bool) -> Tuple[bool, str]:
  """Calls send_budget.spend with the override env pinned for this call only.

  The hook never reads that env itself; whatever the caller set is scrubbed
  first, so nothing in the agent's environment or command text changes the
  budget decision.

  Args:
    channel: budget channel from send_budget.classify_command.
    reason: tool path from send_budget.describe, persisted as last_spend.
    override: True records the unit past the ceiling as a human override.

  Returns:
    The (allowed, message) pair from send_budget.spend.
  """
  prior = os.environ.pop(send_budget.OVERRIDE_ENV, None)
  try:
    if override:
      os.environ[send_budget.OVERRIDE_ENV] = "1"
    return send_budget.spend(channel, reason=reason)
  finally:
    os.environ.pop(send_budget.OVERRIDE_ENV, None)
    if prior is not None:
      os.environ[send_budget.OVERRIDE_ENV] = prior


def gate_budget(command: str) -> str:
  """Spends one unit; when the piece refuses, hands the decision to the human.

  Any failure is an allow. When send_budget.spend refuses (daily ceiling or
  the owner-DM quiet window) the unit is spent here anyway, marked as
  overridden, and the decision goes to the human as force_ask with the
  piece's own refusal text. The hook does not run again after the click, so a
  human "deny" still counts as one over-ceiling send in send_budget.json.

  Args:
    command: the shell command line under review.

  Returns:
    "" to allow, a reason to deny, or ASK + reason to force_ask.
  """
  try:
    channel = send_budget.classify_command(command)
    if channel is None:
      return ""
    describe = send_budget.describe(command)
    allowed, why = _spend(channel, describe, override=False)
    if allowed:
      return ""
    used = int(send_budget.read_state()["counts"].get(channel, 0))
    ceiling = send_budget.ceilings().get(channel, 0)
    _spend(channel, describe, override=True)
  except Exception:  # pylint: disable=broad-exception-caught
    # fail open: a broken budget piece must not block sends

    return ""
  return (  # pylint: disable=broad-exception-caught
      # fail open: a broken prose gate must not block sends
      f"{ASK}[PAWL budget] {why} ({used}/{ceiling} today). Approve to send"
      " anyway; the send is logged as a human override."
  )


def events_path() -> Path:
  return data_dir() / "gate_events.jsonl"


def record_event(gate: str, decision: str, override: bool = False) -> None:
  """Appends one gate outcome to gate_events.jsonl.

  Never raises; telemetry fails open. A gate that denies everything and a
  gate that never denies are both broken; the log exists so `pawl_hook.py
  stats` can show the deny rate and the override rate per gate.

  Args:
    gate: gate name from GATES.
    decision: allow, deny, or ask.
    override: True when the unit was spent as a human override.
  """
  try:
    path = events_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "gate": gate,
        "decision": decision,
        "override": bool(override),
    }
    with path.open("a", encoding="utf-8") as fh:
      fh.write(json.dumps(row) + "\n")
  except Exception:  # pylint: disable=broad-exception-caught
    # fail open: telemetry must never break a hook

    return


def gate_stats(path: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
  """Per-gate counts from gate_events.jsonl.

  Args:
    path: events file to read; defaults to PAWL_DATA/gate_events.jsonl.

  Returns:
    {gate: {allow, deny, override, deny_rate}}.
  """
  stats: Dict[str, Dict[str, Any]] = {}
  src = path or events_path()
  if not src.exists():
    return stats
  for line in src.read_text(encoding="utf-8", errors="replace").splitlines():
    try:
      row = json.loads(line)
    except ValueError:
      continue
    g = stats.setdefault(
        str(row.get("gate")), {"allow": 0, "deny": 0, "override": 0}
    )
    g["allow" if row.get("decision") in ("allow", ASK) else "deny"] += 1
    if row.get("override"):
      g["override"] += 1
  for g in stats.values():
    total = g["allow"] + g["deny"]
    g["deny_rate"] = round(g["deny"] / total, 3) if total else 0.0
  return stats


GATES = (
    ("egress", gate_egress),
    ("prose", gate_prose),
    ("budget", gate_budget),
)


def decide(command: str) -> Tuple[str, str]:
  """Runs the gates in order and returns the first non-allow outcome.

  Args:
    command: the shell command line under review.

  Returns:
    (decision, reason): allow with "", deny or force_ask with a reason.
  """
  if not command or send_budget.classify_command(command) is None:
    return "allow", ""
  off = disabled()
  for name, gate in GATES:
    if name in off:
      continue
    reason = gate(command)
    if reason.startswith(ASK):
      record_event(name, ASK, override=True)
      return "force_ask", reason[len(ASK) :]
    record_event(name, "deny" if reason else "allow")
    if reason:
      return "deny", reason
  return "allow", ""


def print_stats() -> None:
  stats = gate_stats()
  if not stats:
    print("no gate events recorded yet")
    return
  print(f"{'gate':<8}{'allow':>7}{'deny':>7}{'override':>10}{'deny_rate':>11}")
  for name in sorted(stats):
    g = stats[name]
    print(
        f"{name:<8}{g['allow']:>7}{g['deny']:>7}{g['override']:>10}"
        f"{g['deny_rate']:>11}"
    )


def main() -> None:
  if sys.argv[1:] == ["stats"]:
    print_stats()
    return
  try:
    payload = json.loads(sys.stdin.read() or "{}")
    command = command_from(payload if isinstance(payload, dict) else {})
  except Exception:  # pylint: disable=broad-exception-caught
    # fail open: unreadable input is not the agent's fault

    emit("allow")
    return
  decision, reason = decide(command)
  emit(decision, reason)


if __name__ == "__main__":
  main()
