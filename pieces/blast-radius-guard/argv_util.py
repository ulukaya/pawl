"""argv_util.py: the words around a command that are not the command.

Prefixes that run their argument (`sudo`, `env`, `timeout 5`, `nohup`, ...),
shell keywords and function headers, and package runners (`npx`, `bunx`,
`pnpm dlx`) that name the real program one word later.

Standard library only.
"""

from __future__ import annotations

import posixpath
import re
from typing import List, Optional

import runs

RUNNERS = frozenset({"npx", "bunx", "pnpx"})
RUNNER_SUBCMDS = frozenset({"dlx", "exec", "x"})
PREFIX_CMDS = frozenset({"sudo", "doas", "command", "builtin", "exec",
                         "nohup", "time", "nice", "ionice", "stdbuf",
                         "timeout", "env", "caffeinate"})
VALUE_OPTS = {"sudo": "ugCDhpTrt", "doas": "uC", "nice": "n", "ionice": "cnp",
              "timeout": "sk", "env": "uSC", "stdbuf": "ioe"}
KEYWORDS = frozenset({"{", "}", "(", ")", "then", "do", "else", "elif", "if",
                      "while", "until", "!", "fi", "done", "esac", "time",
                      "function"})
DECLARES = frozenset({"export", "local", "readonly", "declare", "typeset"})
FUNC_DEF_RE = re.compile(
    r"(?m)^\s*(?:function\s+)?([A-Za-z_][\w:-]*)\s*\(\)\s*\{?"
    r"|^\s*function\s+([A-Za-z_][\w:-]*)")

def strip_prefixes(argv: List[str]) -> List[str]:
  while argv:
    name = posixpath.basename(argv[0].lstrip("\\"))
    if name not in PREFIX_CMDS:
      return argv
    if name == "command" and len(argv) > 1 and argv[1] in ("-v", "-V"):
      return []
    argv, takes = argv[1:], VALUE_OPTS.get(name, "")
    while argv and (argv[0].startswith("-") or (name == "env" and "=" in
                                                 argv[0])):
      flag = argv[0]
      argv = argv[2:] if flag[1:] and flag[-1] in takes else argv[1:]
    if name == "timeout" and argv:
      argv = argv[1:]
  return argv


def function_header(argv: List[str]) -> Optional[str]:
  """The name a function definition line opens, or None."""
  if argv and argv[0] == "function" and len(argv) > 1:
    return argv[1].rstrip("()")
  if argv and argv[0].endswith("()") and len(argv[0]) > 2:
    return argv[0][:-2]
  if len(argv) > 1 and argv[1] == "()":
    return argv[0]
  return None


def strip_keywords(argv: List[str]) -> List[str]:
  while argv and (argv[0] in KEYWORDS or argv[0].endswith("()")
                  or (argv[0].endswith(")") and "(" not in argv[0])):
    argv = argv[1:]
  return argv


def runner_argv(argv: List[str]) -> Optional[List[str]]:
  """`npx rimraf x` -> ['rimraf', 'x']; None when argv is no package runner."""
  name = posixpath.basename(argv[0])
  if name in RUNNERS:
    rest = argv[1:]
  elif name in runs.PACKAGE_MANAGERS and len(argv) > 1 and \
      argv[1] in RUNNER_SUBCMDS:
    rest = argv[2:]
  else:
    return None
  while rest and rest[0].startswith("-"):
    rest = rest[2:] if rest[0] in ("-p", "--package") else rest[1:]
  return rest
