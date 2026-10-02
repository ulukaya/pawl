"""stores.py: where each harness keeps its conversations, and whose a path is.

    antigravity  <root>/brain/<id>/...             a conversation's brain
                 <root>/conversations/<id>.*       its transcript
                 <root>/conversation_summaries.db  every title and summary
                 roots: PAWL_CONVERSATION_ROOTS, else ~/.gemini/antigravity,
                 ~/.gemini/jetski, ~/.antigravity, ~/.jetski
    claude       <cfg>/projects/<project>/<id>.jsonl and <id>/...
                 <cfg>/file-history/<id>/...       a session's file backups
                 <cfg>/history.jsonl               every session's prompts
                 <cfg>/projects/<project>/memory/  shared by the project's
                                                   sessions: never fenced
                 cfg: CLAUDE_CONFIG_DIR, else ~/.claude
    codex        <home>/sessions/.../rollout-<ts>-<id>.jsonl (and
                 archived_sessions/)
                 <home>/history.jsonl              every session's prompts
                 home: CODEX_HOME, else ~/.codex

A path that reaches a store without naming one conversation (the store
directory, a project directory, a date directory, or a glob in place of
the id) is a sweep: a Hit with no conversation. Standard library only.
"""

from __future__ import annotations

import os
import re
from typing import (
    Callable, Iterator, List, NamedTuple, Optional, Sequence, Tuple,
)

ROOTS_ENV = "PAWL_CONVERSATION_ROOTS"
DEFAULT_ROOTS = (
    "~/.gemini/antigravity", "~/.gemini/jetski", "~/.antigravity", "~/.jetski",
)
SUMMARIES_DB = "conversation_summaries.db"
PROMPTS = "every conversation's prompts"
_GLOB = re.compile(r"[*?\[]")
_ROLLOUT = re.compile(r"^rollout-.*?([0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}"
                      r"-[0-9a-fA-F]{12})\.jsonl$")


class Hit(NamedTuple):
  """A path inside a conversation store."""

  layout: str  # antigravity, claude or codex
  root: str
  what: str  # e.g. "brain/", "transcript", "session log"
  conv: str  # the conversation the path belongs to; "" for a sweep
  index: str = ""  # set for a file that indexes every conversation


def _id(part: str) -> str:
  """A conversation id from a path part; '' for a glob."""
  return "" if _GLOB.search(part) else part


def antigravity(parts: Sequence[str], root: str) -> Optional[Hit]:
  if not parts:
    return None
  if parts[0] == SUMMARIES_DB:
    return Hit("antigravity", root, "conversation summaries", "",
               "every conversation's title and summary")
  if parts[0] in ("brain", "conversations"):
    conv = _id(parts[1].split(".", 1)[0]) if len(parts) > 1 else ""
    return Hit("antigravity", root, f"{parts[0]}/", conv)
  return None


def claude(parts: Sequence[str], root: str) -> Optional[Hit]:
  if list(parts) == ["history.jsonl"]:
    return Hit("claude", root, "prompt history", "", PROMPTS)
  if parts and parts[0] == "file-history":
    conv = _id(parts[1]) if len(parts) > 1 else ""
    return Hit("claude", root, "file history", conv)
  if not parts or parts[0] != "projects":
    return None
  if len(parts) > 2 and parts[2] == "memory":
    return None
  if len(parts) < 3:
    return Hit("claude", root, "projects/", "")
  conv = _id(parts[2].removesuffix(".jsonl"))
  return Hit("claude", root, "transcript" if conv else "projects/", conv)


def codex(parts: Sequence[str], root: str) -> Optional[Hit]:
  if list(parts) == ["history.jsonl"]:
    return Hit("codex", root, "prompt history", "", PROMPTS)
  if not parts or parts[0] not in ("sessions", "archived_sessions"):
    return None
  m = _ROLLOUT.match(parts[-1]) if len(parts) > 1 else None
  if m:
    return Hit("codex", root, "session log", m.group(1))
  return Hit("codex", root, f"{parts[0]}/", "")


Classifier = Callable[[Sequence[str], str], Optional[Hit]]


def layouts() -> List[Tuple[str, Classifier]]:
  """(root, classifier) for every store on this machine, env applied."""
  raw = os.environ.get(ROOTS_ENV)
  ag = raw.split(":") if raw is not None else list(DEFAULT_ROOTS)
  out: List[Tuple[str, Classifier]] = [
      (r.strip(), antigravity) for r in ag if r.strip()
  ]
  out.append((os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude", claude))
  out.append((os.environ.get("CODEX_HOME") or "~/.codex", codex))
  return [(os.path.expanduser(r), fn) for r, fn in out]


def _forms(path: str) -> List[str]:
  return list(dict.fromkeys([os.path.normpath(path), os.path.realpath(path)]))


def _inside(path: str, root: str) -> bool:
  return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def _hit(path: str, root: str, classify: Classifier) -> Optional[Hit]:
  pairs = [(p, r) for p in _forms(path) for r in _forms(root) if _inside(p, r)]
  for p, r in pairs:
    rel = os.path.relpath(p, r)
    hit = classify([] if rel == "." else rel.split(os.sep), r)
    if hit is not None:
      return hit
  return None


def hits(path: str) -> Iterator[Hit]:
  """Every store hit for an absolute path, literal and resolved forms."""
  for root, classify in layouts():
    hit = _hit(path, root, classify)
    if hit is not None:
      yield hit
