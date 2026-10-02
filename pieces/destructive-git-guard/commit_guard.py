#!/usr/bin/env python3
"""commit_guard.py: rules that detect bypassing or hiding a commit gate."""

from __future__ import annotations

import re
from typing import List

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
