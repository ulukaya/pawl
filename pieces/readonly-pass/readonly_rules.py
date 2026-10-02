"""readonly_rules.py: which programs, flags and paths readonly-pass trusts.

A clause is read-only only when its program is listed here and none of its
arguments is a flag that writes a file or runs another program. Long flags
match their abbreviations (`--compress-prog`); short flags match inside a
cluster (`sort -uo out`) up to the first flag that takes a value (`tail -n5`).

VCS calls accept only allowlisted global options before the subcommand, so
`git --work-tree X status` or `g4 -u USER diff` never pass, and only
read-only subcommands after it. Standard library only.
"""

from __future__ import annotations

import re
from typing import Callable, Dict, List, NamedTuple, Sequence


class Flags(NamedTuple):
  shorts: str = ""  # short letters that write or run a program
  longs: Sequence[str] = ()  # long flags that do the same
  value_shorts: str = ""  # short letters that take a value; stop the cluster


SIMPLE_READERS = frozenset({
    "basename", "cat", "cd", "dirname", "du", "echo", "egrep", "fgrep",
    "grep", "head", "ls", "pwd", "readlink", "realpath", "stat", "tail", "wc",
})
FLAGGED_READERS: Dict[str, Flags] = {
    "sort": Flags("o", ("--output", "--compress-program"), "kStT"),
    "rg": Flags(
        "z", ("--pre", "--search-zip", "--hostname-bin"), "efgrtTABCmMjE"
    ),
    "tree": Flags("oR", (), "LPIHT"),
    "file": Flags("C", ("--compile",), "mfFeP"),
}
FIND_BAD = frozenset({
    "-exec", "-execdir", "-ok", "-okdir", "-delete", "-fprint", "-fprint0",
    "-fprintf", "-fls",
})
_SED_SCRIPT = re.compile(r"^\d+(?:,(?:\d+|\$))?p$")
_UNIQ_VALUE_OPTS = frozenset({"-f", "-s", "-w"})

# Global options allowed before a VCS subcommand -> whether they take a value.
VCS_GLOBALS: Dict[str, Dict[str, bool]] = {
    "git": {"-C": True, "--no-pager": False, "-P": False,
            "--no-optional-locks": False},
    "hg": {"-R": True, "--repository": True, "--cwd": True, "--pager": True},
    "jj": {"-R": True, "--repository": True, "--no-pager": False,
           "--ignore-working-copy": False},
    "g4": {},
}
VCS_READ: Dict[str, frozenset] = {
    "git": frozenset({
        "blame", "cat-file", "describe", "diff", "for-each-ref", "grep",
        "log", "ls-files", "ls-tree", "merge-base", "reflog", "rev-list",
        "rev-parse", "shortlog", "show", "show-ref", "stash", "status",
    }),
    "hg": frozenset({
        "annotate", "blame", "cat", "diff", "files", "grep", "heads", "id",
        "identify", "log", "manifest", "parents", "paths", "root", "st",
        "status", "sum", "summary",
    }),
    "jj": frozenset({
        "diff", "evolog", "log", "obslog", "root", "show", "st", "status",
    }),
    "g4": frozenset({
        "changes", "describe", "diff", "filelog", "files", "fstat", "have",
        "info", "opened", "pending", "print", "status", "where",
    }),
}
VCS_BAD_FLAGS = (
    "--config", "--config-file", "--config-toml", "--exec", "--ext-diff",
    "--filters", "--open-files-in-pager", "--output", "--textconv", "--tool",
)
VCS_BAD_SHORTS = {
    ("git", "grep"): "O", ("hg", "cat"): "o", ("g4", "print"): "o",
}
GIT_STASH_READS = frozenset({"list", "show"})
GIT_REFLOG_WRITES = frozenset({"delete", "drop", "expire", "write"})


def _bad_long(arg: str, longs: Sequence[str]) -> bool:
  """`--name[=v]` equal to, or an abbreviation of, a bad long flag."""
  name = arg.split("=", 1)[0]
  return any(bad == name or bad.startswith(name) for bad in longs)


def _bad_short(arg: str, flags: Flags) -> bool:
  for ch in arg[1:]:
    if ch in flags.shorts:
      return True
    if ch in flags.value_shorts:
      return False
  return False


def has_bad_flag(args: Sequence[str], flags: Flags) -> bool:
  """True when any option before `--` writes or runs a program."""
  for arg in args:
    if arg == "--":
      return False
    if arg.startswith("--") and _bad_long(arg, flags.longs):
      return True
    if arg.startswith("-") and not arg.startswith("--"):
      if _bad_short(arg, flags):
        return True
  return False


def sed_ok(args: List[str]) -> bool:
  """Only `sed -n '<N>[,<M>|$]p' [files]`."""
  if len(args) < 2 or args[0] != "-n" or not _SED_SCRIPT.match(args[1]):
    return False
  return not any(a.startswith("-") for a in args[2:])


def find_ok(args: List[str]) -> bool:
  return not any(a in FIND_BAD for a in args)


def uniq_ok(args: List[str]) -> bool:
  """At most one file operand; `-` and anything after `--` count as files."""
  files, i, operands_only = 0, 0, False
  while i < len(args):
    arg = args[i]
    if operands_only or arg == "-" or not arg.startswith("-"):
      files += 1
    elif arg == "--":
      operands_only = True
    elif arg in _UNIQ_VALUE_OPTS:
      i += 1
    i += 1
  return files <= 1


def _vcs_rest(prog: str, args: List[str]) -> List[str]:
  """Args from the subcommand on, or [] when a global option is not allowed."""
  allowed = VCS_GLOBALS[prog]
  i = 0
  while i < len(args) and args[i].startswith("-"):
    name = args[i].split("=", 1)[0]
    if name not in allowed:
      return []
    takes_value = allowed[name] and "=" not in args[i]
    i += 2 if takes_value else 1
  return args[i:]


def vcs_ok(prog: str, args: List[str]) -> bool:
  rest = _vcs_rest(prog, args)
  if not rest or rest[0] not in VCS_READ[prog]:
    return False
  sub, sub_args = rest[0], rest[1:]
  flags = Flags(VCS_BAD_SHORTS.get((prog, sub), ""), VCS_BAD_FLAGS)
  if has_bad_flag(sub_args, flags):
    return False
  operands = [a for a in sub_args if not a.startswith("-")]
  first = operands[0] if operands else ""
  if (prog, sub) == ("git", "stash"):
    return first in GIT_STASH_READS
  if (prog, sub) == ("git", "reflog"):
    return first not in GIT_REFLOG_WRITES
  return True


SPECIAL: Dict[str, Callable[[List[str]], bool]] = {
    "sed": sed_ok,
    "find": find_ok,
    "uniq": uniq_ok,
}


def clause_ok(words: List[str]) -> bool:
  """True when one simple command (redirects removed) only reads."""
  if not words:
    return False
  prog, args = words[0], words[1:]
  if "=" in prog or "/" in prog:
    return False  # VAR=value prefix, or a program named by path
  if prog in SIMPLE_READERS:
    return True
  if prog in FLAGGED_READERS:
    return not has_bad_flag(args, FLAGGED_READERS[prog])
  if prog in SPECIAL:
    return SPECIAL[prog](args)
  if prog in VCS_GLOBALS:
    return vcs_ok(prog, args)
  return False
