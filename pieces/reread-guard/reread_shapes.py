"""reread_shapes.py: which files a tool call reads, and whether the read is
bounded.

A bounded read never counts toward a reread limit:
  view_file spanning under 10 lines (StartLine and EndLine both set);
  head or tail capped at 10 lines or 20000 bytes (bare head/tail is 10);
  sed -n over at most 10 lines;
  counts: wc, grep -c, rg -c.
A read whose output is piped into one of those is bounded too, so
`cat transcript.jsonl | tail -5` does not count. Standard library only.
"""

from __future__ import annotations

import os
import re
import shlex
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

LINE_CAP = 10
BYTE_CAP = 20000
VIEW_SPAN_UNDER = 10


class Read(NamedTuple):
  path: str
  bounded: bool
  from_top: bool


DUMPERS = frozenset({"cat", "less", "more", "nl", "bat", "strings", "jq"})
GREPS = frozenset({"grep", "egrep", "fgrep", "rg"})
COUNTERS = frozenset({"wc"})
_STAGE_SPLIT = frozenset({"|", "|&"})
_PIPELINE_SPLIT = frozenset({";", "&&", "||", "&"})
_REDIRECTS = frozenset({">", ">>", ">&", "&>", "&>>", "<", "<>", ">|", "<<",
                        "<<<", "<&"})
_GREP_VALUE = frozenset({
    "-e", "-f", "-m", "-A", "-B", "-C", "-g", "-t", "-T", "--regexp",
    "--file", "--max-count", "--glob", "--type",
})
_GREP_PATTERN_OPTS = frozenset({"-e", "-f", "--regexp", "--file"})
_SED_RANGE = re.compile(r"^(\d+|\$)(?:,(\d+|\$))?p$")


def _int(value: Any) -> Optional[int]:
  if isinstance(value, bool):
    return None
  if isinstance(value, int):
    return value
  return int(value) if isinstance(value, str) and value.isdigit() else None


def view_reads(args: Dict[str, Any]) -> List[Read]:
  path = args.get("AbsolutePath") or args.get("path") or args.get("file_path")
  if not isinstance(path, str) or not path:
    return []
  start, end = _int(args.get("StartLine")), _int(args.get("EndLine"))
  limit = _int(args.get("limit"))
  if start is not None and end is not None:
    bounded = end - start + 1 < VIEW_SPAN_UNDER
  elif limit is not None:
    bounded = limit < VIEW_SPAN_UNDER
  else:
    bounded = False
  offset = _int(args.get("offset"))
  from_top = (
      start is None or start <= 1
      if start is not None
      else (offset is None or offset <= 1)
  )
  return [Read(path, bounded, from_top)]


# --- shell --------------------------------------------------------------------


def _tokens(cmd: str) -> List[str]:
  lex = shlex.shlex(cmd, posix=True, punctuation_chars=True)
  lex.whitespace_split = True
  lex.commenters = ""
  try:
    return list(lex)
  except ValueError:
    return cmd.split()


def _stages(cmd: str) -> List[List[List[str]]]:
  """Pipelines of stages of words; `< F` keeps F, other redirects drop."""
  pipelines: List[List[List[str]]] = [[[]]]
  toks = _tokens(cmd)
  i = 0
  while i < len(toks):
    tok = toks[i]
    i += 1
    if tok in _PIPELINE_SPLIT:
      pipelines.append([[]])
    elif tok in _STAGE_SPLIT:
      pipelines[-1].append([])
    elif tok in _REDIRECTS:
      target = toks[i] if i < len(toks) else ""
      i += 1
      if tok == "<" and target:
        pipelines[-1][-1].append(target)
    else:
      pipelines[-1][-1].append(tok)
  return pipelines


def _head_tail(args: List[str]) -> Tuple[bool, List[str]]:
  """(bounded, files) for head or tail arguments."""
  lines, nbytes, files = None, None, []
  i = 0
  while i < len(args):
    arg = args[i]
    i += 1
    if arg in ("-n", "--lines") and i < len(args):
      lines = args[i]
      i += 1
    elif arg in ("-c", "--bytes") and i < len(args):
      nbytes = args[i]
      i += 1
    elif arg.startswith(("--lines=", "-n")):
      lines = arg.split("=", 1)[1] if "=" in arg else arg[2:]
    elif arg.startswith(("--bytes=", "-c")):
      nbytes = arg.split("=", 1)[1] if "=" in arg else arg[2:]
    elif re.fullmatch(r"-\d+", arg):
      lines = arg[1:]
    elif arg == "-" or not arg.startswith("-"):
      files.append(arg)
  if nbytes is not None:
    cap, limit = _int(nbytes), BYTE_CAP
  else:
    cap, limit = (LINE_CAP if lines is None else _int(lines)), LINE_CAP
  return cap is not None and cap <= limit, files


def _grep(args: List[str]) -> Tuple[bool, List[str]]:
  """(is a count, files) for grep or rg arguments."""
  count, given, operands = False, False, []
  i = 0
  while i < len(args):
    arg = args[i]
    i += 1
    if arg in _GREP_VALUE:
      given = given or arg in _GREP_PATTERN_OPTS
      i += 1
    elif arg.startswith(("--regexp=", "--file=")):
      given = True
    elif arg in ("--count", "--count-matches"):
      count = True
    elif arg.startswith("-") and not arg.startswith("--") and arg != "-":
      count = count or "c" in arg[1:]
    elif not arg.startswith("-"):
      operands.append(arg)
  return count, operands if given else operands[1:]


def _sed(args: List[str]) -> Tuple[bool, bool, List[str]]:
  """(bounded, from_top, files) for sed arguments; -i is a write, no read."""
  if any(a.startswith("-i") or a.startswith("--in-place") for a in args):
    return True, False, []
  quiet = any(a in ("-n", "--quiet", "--silent") for a in args)
  ops = [a for a in args if not a.startswith("-")]
  if not ops:
    return True, False, []
  m = _SED_RANGE.match(ops[0])
  if not quiet or not m:
    return False, True, ops[1:]
  first, last = m.group(1), m.group(2) or m.group(1)
  if first == "$":
    return True, False, ops[1:]
  start = int(first)
  if last == "$":
    return False, start <= 1, ops[1:]
  return int(last) - start + 1 <= LINE_CAP, start <= 1, ops[1:]


def _stage(words: List[str]) -> Tuple[bool, List[Read]]:
  """(caps the output of earlier stages, reads of this stage)."""
  if not words:
    return False, []
  prog, args = os.path.basename(words[0]), words[1:]
  if prog in COUNTERS:
    return True, []
  if prog in GREPS:
    count, files = _grep(args)
    return count, [] if count else [Read(f, False, True) for f in files]
  if prog in ("head", "tail"):
    bounded, files = _head_tail(args)
    return bounded, [Read(f, bounded, prog == "head") for f in files]
  if prog == "sed":
    bounded, top, files = _sed(args)
    return bounded, [Read(f, bounded, top) for f in files]
  if prog in DUMPERS:
    return False, [Read(a, False, True) for a in args if not a.startswith("-")]
  return False, []


def shell_reads(cmd: str) -> List[Read]:
  """Every file the command reads; capped pipelines mark them bounded."""
  reads: List[Read] = []
  for pipeline in _stages(cmd):
    staged = [_stage(words) for words in pipeline]
    capped = any(caps for caps, _ in staged[1:])
    for _, stage_reads in staged:
      reads += [r._replace(bounded=r.bounded or capped) for r in stage_reads
                if r.path != "-"]
  return reads
