"""expand.py: what a shell word can expand to, without running anything.

A variable holds the union of every value assigned to it anywhere in the
text being scanned (flow-insensitive on purpose: a trap that deletes "$D"
must see the later `D="$HOME"`). A value pawl cannot know becomes UNKNOWN
inside the word, and targets.py judges what it could be.

Substitutions are evaluated only where the answer is certain: `mktemp`
(a temp path), `pwd`, `dirname`, `realpath`, `git rev-parse
--show-toplevel`, and `echo`/`printf`, also piped through `base64 -d`.

Standard library only.
"""

from __future__ import annotations

import base64
import binascii
import fnmatch
import posixpath
import re
from typing import Dict, List, Mapping, Optional, Tuple

import runs
import shellparse
from targets import UNKNOWN, Policy

MAX_VALUES = 8
SEEDED = frozenset({"HOME", "PWD", "TMPDIR", "USERPROFILE"})
_PARAM_RE = re.compile(
    r"\$\{([A-Za-z_]\w*|\d+|[@*#?$])(?:(:?[-=+?]|%%?|##?)([^}]*))?\}"
    r"|\$([A-Za-z_]\w*|\d|[@*#?$])")
TILDE_USER_RE = re.compile(r"^~([a-z_][\w.-]*)(/.*)?$")
_BRACE_RE = re.compile(r"\{([^{}\s]*,[^{}\s]*)\}")
_ASSIGN_RE = re.compile(r"^([A-Za-z_]\w*)=(.*)$", re.S)


class Ctx:
  """What a scan knows: variables, arguments, directory, files written."""

  def __init__(self, policy: Policy, env: Mapping[str, str],
               cwd: Optional[str]) -> None:
    self.policy = policy
    self.env = dict(env)
    self.cwd = cwd
    self.vars: Dict[str, List[str]] = {}
    self.args: List[str] = []
    self.script: Optional[str] = None
    self.virtual: Dict[str, str] = {}  # files this command writes, by path
    self.via: List[str] = []
    self.funcs: Dict[str, List[List[str]]] = {}  # name -> args per call
    self.func: Optional[str] = None  # the function a line belongs to
    # inside a container: (container path, host path) per bind mount
    self.mounts: Optional[List[Tuple[str, str]]] = None
    self.depth = 0
    self.budget = [1 << 20]  # bytes of scripts left to read, shared

  def child(self, script: Optional[str], args: List[str], via: str,
            cwd: Optional[str] = None) -> "Ctx":
    """A fresh scope for a script: env and budget kept, variables reset."""
    sub = Ctx(self.policy, self.env, cwd if cwd is not None else self.cwd)
    sub.args, sub.script = list(args), script
    sub.virtual, sub.budget = self.virtual, self.budget
    sub.via, sub.depth = self.via + [via], self.depth + 1
    sub.funcs, sub.mounts = self.funcs, self.mounts
    return sub

  def assign(self, name: str, values: List[str]) -> None:
    if name not in self.vars and name in SEEDED:
      # flow-insensitive: the value before any assignment is still possible
      self.vars[name] = [v for v in self.lookup(name) if v != UNKNOWN]
    known = self.vars.setdefault(name, [])
    for v in values:
      if v not in known and len(known) < MAX_VALUES:
        known.append(v)

  def lookup(self, name: str) -> List[str]:
    if name in self.vars:
      return self.vars[name]
    if name.isdigit() and int(name) and self.func is not None:
      i = int(name)
      calls = self.funcs.get(self.func, [])
      return [c[i - 1] for c in calls if len(c) >= i][:MAX_VALUES] or [UNKNOWN]
    if name.isdigit():
      i = int(name)
      if i == 0:
        return [self.script] if self.script else [UNKNOWN]
      return [self.args[i - 1]] if i <= len(self.args) else [UNKNOWN]
    if name in ("@", "*"):
      return [" ".join(self.args)] if self.args else [UNKNOWN]
    if name == "HOME":
      return [self.policy.home]
    if name == "PWD" and self.cwd:
      return [self.cwd]
    if name in self.env and name in ("TMPDIR", "USER", "USERPROFILE"):
      return [self.env[name]]
    return [UNKNOWN]


def _product(prefixes: List[str], options: List[str]) -> List[str]:
  out = [p + o for p in prefixes for o in options]
  return out[:MAX_VALUES]


def _trim(value: str, op: str, pattern: str) -> str:
  """`${v%pat}`, `${v%%pat}`, `${v#pat}`, `${v##pat}` with a glob pattern."""
  if UNKNOWN in value:
    return UNKNOWN
  cuts = range(len(value) + 1)
  if op.startswith("%"):
    order = cuts if op == "%" else reversed(cuts)
    hits = (value[:len(value) - n] for n in order
            if fnmatch.fnmatchcase(value[len(value) - n:], pattern))
  else:
    order = cuts if op == "#" else reversed(cuts)
    hits = (value[n:] for n in order if fnmatch.fnmatchcase(value[:n], pattern))
  return next(hits, value)


def _param(m: "re.Match[str]", ctx: Ctx) -> List[str]:
  name = m.group(1) or m.group(4)
  values = ctx.lookup(name)
  op, arg = m.group(2), m.group(3)
  if op and op.lstrip(":") in ("-", "=") and arg is not None:
    alt = expand(arg, [], ctx)
    values = [v for v in values if v != UNKNOWN] + alt or [UNKNOWN]
  elif op and op[0] in "%#":
    values = [_trim(v, op, arg or "") for v in values]
  return values


def braces(word: str) -> List[str]:
  """`a/{b,c}` -> [a/b, a/c]; one level, up to MAX_VALUES results."""
  m = _BRACE_RE.search(word)
  if not m:
    return [word]
  out = []
  for option in m.group(1).split(","):
    out.extend(braces(word[:m.start()] + option + word[m.end():]))
  return out[:MAX_VALUES]


def expand(word: str, substs: List[str], ctx: Ctx) -> List[str]:
  """Every value `word` can take; unresolvable parts become UNKNOWN."""
  words = braces(word)
  if len(words) > 1:
    return [v for w in words for v in expand(w, substs, ctx)][:MAX_VALUES]
  if word == "~" or word.startswith(("~/", "~:")):
    word = ctx.policy.home + word[1:]
  elif TILDE_USER_RE.match(word):
    user, rest = TILDE_USER_RE.match(word).groups()
    rest = rest or ""
    homes = posixpath.dirname(ctx.policy.home)
    word = ("/root" if user == "root" else posixpath.join(homes, user)) + rest
  results = [""]
  pos = 0
  pattern = re.compile(_PARAM_RE.pattern + r"|" + shellparse.SUBST_RE.pattern)
  for m in pattern.finditer(word):
    results = _product(results, [word[pos:m.start()]])
    if m.group(5) is not None:
      i = int(m.group(5))
      values = evaluate(substs[i], ctx) if i < len(substs) else [UNKNOWN]
    else:
      values = _param(m, ctx)
    results = _product(results, values)
    pos = m.end()
  return _product(results, [word[pos:]])


def _decode_b64(text: str) -> Optional[str]:
  try:
    return base64.b64decode(text.strip(), validate=True).decode("utf-8")
  except (binascii.Error, ValueError, UnicodeDecodeError):
    return None


def static_output(pipeline: shellparse.Pipeline, ctx: Ctx) -> List[str]:
  """What a pipeline prints, when that is certain; [] otherwise."""
  out: List[str] = []
  for cmd in pipeline:
    w = shellparse.words(cmd.text)
    argv = w.argv
    if not argv:
      return []
    name = posixpath.basename(argv[0])
    if name in ("echo", "printf"):
      args = [a for a in argv[1:] if not (name == "echo" and a in ("-n", "-e"))]
      out = [" ".join(v) for v in _words_product(args, w.substs, ctx)]
    elif name == "base64" and any(a in ("-d", "--decode", "-D") for a in argv):
      decoded = [_decode_b64(o) for o in out]
      out = [d for d in decoded if d is not None]
    elif name == "cat" and len(argv) == 1 and cmd.heredoc is not None:
      out = [cmd.heredoc]
    elif name == "cat" and len(argv) >= 2:
      texts = [runs.read_file(_abs(a, ctx), ctx.virtual, ctx.budget)
               for a in argv[1:] if not a.startswith("-")]
      if any(t is None for t in texts):
        return []
      out = ["".join(texts)]
    else:
      return []
  return out


def _words_product(args: List[str], substs: List[str],
                   ctx: Ctx) -> List[List[str]]:
  combos: List[List[str]] = [[]]
  for a in args:
    combos = [c + [v] for c in combos for v in expand(a, substs, ctx)]
    combos = combos[:MAX_VALUES]
  return combos


def _abs(path: str, ctx: Ctx) -> str:
  if path.startswith("/") or not ctx.cwd:
    return path
  return posixpath.normpath(posixpath.join(ctx.cwd, path))


def _after_cd(pipes: List[shellparse.Pipeline], ctx: Ctx) -> List[str]:
  """`cd DIR && pwd` (any number of cds first): DIR, resolved."""
  cwd = ctx.cwd
  for pipe in pipes[:-1]:
    w = shellparse.words(pipe[0].text)
    if len(pipe) != 1 or not w.argv or w.argv[0] != "cd":
      return [UNKNOWN]
    dest = expand(w.argv[1], w.substs, ctx)[0] if len(w.argv) > 1 \
        else ctx.policy.home
    if UNKNOWN in dest:
      return [UNKNOWN]
    cwd = posixpath.normpath(posixpath.join(cwd or "/", dest))
  last = shellparse.words(pipes[-1][0].text).argv
  return [cwd] if last == ["pwd"] and cwd else [UNKNOWN]


def evaluate(body: str, ctx: Ctx) -> List[str]:
  """Values of `$( body )` when certain; [UNKNOWN] otherwise."""
  if body.startswith(shellparse.PROC_MARK):
    return ["/dev/fd/63"]  # `<( )`: a pipe's path; its text is procsub()
  pipes = shellparse.pipelines(body)
  if len(pipes) > 1:
    return _after_cd(pipes, ctx)
  if len(pipes) != 1:
    return [UNKNOWN]
  pipe = pipes[0]
  w = shellparse.words(pipe[0].text)
  argv = w.argv
  if not argv:
    return [UNKNOWN]
  name = posixpath.basename(argv[0])
  temp = ctx.policy.temps[-1] if ctx.policy.temps else "/tmp"
  if len(pipe) == 1 and name == "mktemp":
    return [posixpath.join(temp, "pawl-mktemp")]
  if len(pipe) == 1 and name == "pwd":
    return [ctx.cwd] if ctx.cwd else [UNKNOWN]
  if len(pipe) == 1 and name in ("dirname", "realpath", "readlink") and \
      len(argv) >= 2:
    paths = expand(argv[-1], w.substs, ctx)
    fix = posixpath.dirname if name == "dirname" else posixpath.normpath
    return [fix(p) if UNKNOWN not in p else UNKNOWN for p in paths]
  if len(pipe) == 1 and argv[:3] == ["git", "rev-parse", "--show-toplevel"]:
    ws = ctx.policy.workspace
    return [ws] if ws else [UNKNOWN]
  printed = static_output(pipe, ctx)
  return [p.strip() for p in printed] or [UNKNOWN]


def procsub(word: str, substs: List[str], ctx: Ctx) -> Optional[str]:
  """The text a `<( cmd )` word yields, when that is certain."""
  m = shellparse.SUBST_RE.fullmatch(word)
  if not m or int(m.group(1)) >= len(substs):
    return None
  body = substs[int(m.group(1))]
  if not body.startswith(shellparse.PROC_MARK):
    return None
  pipes = shellparse.pipelines(body[len(shellparse.PROC_MARK):])
  printed = static_output(pipes[0], ctx) if len(pipes) == 1 else []
  return printed[0] if printed else None


def assignment(word: str) -> Optional[re.Match]:
  return _ASSIGN_RE.match(word)
