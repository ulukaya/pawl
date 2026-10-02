"""egress_payload.py: the text a send-shaped command puts on the wire.

The egress gate in pawl_hook.py scans only this text. Tool paths, redirects
(`2>&1 | tail`) and the paths of files a message is read from never leave the
machine, so scanning them only produced false positives.

Collected:
  values of the send flags in _PAYLOAD_FLAGS (`--flag v` and `--flag=v`);
  heredoc bodies;
  the contents of files read with `$(cat F)` or `$(< F)`; a file the same
  command writes with a heredoc resolves to that body; quotes around F are
  stripped on both sides so `> "F"` pairs with `$(cat 'F')`.

Any other `$(...)` or backtick in a value raises OpaqueSubstitutionError: its
output is unknown at hook time. Shell syntax inside a read file is text, since
bash never re-evaluates command output. Standard library only.
"""

from __future__ import annotations

import os
from pathlib import Path  # pylint: disable=g-importing-member
import re
import shlex
from typing import Dict, List

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
_QUOTES = ("'", '"')


class OpaqueSubstitutionError(Exception):
  """A message value embeds a command whose output is unknown at hook time."""


def _unquote(path: str) -> str:
  """Strips one pair of surrounding quotes, then expands a leading ~."""
  if len(path) >= 2 and path[0] == path[-1] and path[0] in _QUOTES:
    path = path[1:-1]
  return os.path.expanduser(path)


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
    key = _unquote(target.group(1)) if target else ""
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
    OpaqueSubstitutionError: a `$(...)` or backtick outside the recognised
      file reads. Checked before splicing, so file contents are never parsed.
    OSError: a read file does not exist or cannot be read.
  """
  if _OPAQUE.search(_FILE_READ.sub("", value)):
    raise OpaqueSubstitutionError(value.strip()[:60])

  def read(m: "re.Match[str]") -> str:
    path = _unquote(m.group(1))
    if path in written:
      return written[path]
    return Path(path).read_text(encoding="utf-8")

  return _FILE_READ.sub(read, value)


def outbound_text(command: str) -> str:
  """Extracts the text a send would put on the wire.

  Args:
    command: the shell command line.

  Returns:
    The outbound text, or the whole command when nothing could be extracted.
  """
  docs = heredocs(command)
  parts = [resolve_file_reads(v, docs) for v in payload_values(command)]
  parts += list(docs.values())
  return "\n".join(parts) if parts else command
