"""scanner.py: walk shell text and collect every deletion it would make.

Three passes over each text: calls to the functions it defines (so `$1`
inside a function is what its callers pass), assignments (a variable holds
every value it is ever given), then each command in order (so `cd` moves
later relative paths). deletes.py and follows.py hold the per-command
handlers.

Standard library only.
"""

from __future__ import annotations

import posixpath
import re
from typing import List, NamedTuple, Optional

import argv_util
import code_scan
import deletes
import follows
import expand
import runs
import shellparse
from argv_util import DECLARES, FUNC_DEF_RE
from deletes import DELETERS, DISK_TOOLS, WIN_DELETE_RE
from targets import ALLOW, ASK, DENY, UNKNOWN, classify

MAX_DEPTH = 5
GIT_LISTS = frozenset({"ls-files", "diff", "status", "ls-tree",
                       "diff-tree", "show"})
READ_VALUE_OPTS = frozenset({"-d", "-p", "-t", "-n", "-N", "-u", "-i"})


def _read_names(args: List[str]) -> List[str]:
  """The variable names a `read` fills, its options skipped."""
  names, skip = [], False
  for a in args:
    if skip:
      skip = False
    elif a in READ_VALUE_OPTS:
      skip = True
    elif a == "-a":
      continue
    elif not a.startswith("-"):
      names.append(a)
  return names or ["REPLY"]


class Finding(NamedTuple):
  level: int
  what: str
  how: str  # the command that deletes it
  via: tuple  # the commands that led to it, outermost first


class Scanner(deletes.DeleteRules, follows.FollowRules):
  """Walks shell text, collecting every deletion and where it lands."""

  def __init__(self, ctx: expand.Ctx) -> None:
    self.ctx = ctx
    self.findings: List[Finding] = []

  # --- entry points ------------------------------------------------------

  def text(self, text: str) -> None:
    """Scans shell text in three passes: function calls, assignments, then
    every command in order (flow-insensitive for values, ordered for cd)."""
    if self.ctx.depth > MAX_DEPTH:
      return
    pipes = shellparse.pipelines(text)
    outer = self.ctx.func
    for m in FUNC_DEF_RE.finditer(text):
      self.ctx.funcs.setdefault(m.group(1) or m.group(2), [])
    for _, _, w in self._walk(pipes):
      self._record_call(w)
    for _, before, w in self._walk(pipes):
      self._collect(w, before)
    for cmd, before, w in self._walk(pipes):
      self._command(cmd, before, w)
    self.ctx.func = outer

  def _walk(self, pipes):
    """Each command with ctx.func set to the function whose body holds it."""
    braces = 0
    for pipe in pipes:
      for i, cmd in enumerate(pipe):
        w = shellparse.words(cmd.text)
        header = argv_util.function_header(w.argv)
        if header:
          self.ctx.func, braces = header, 0
        braces += w.argv.count("{") - w.argv.count("}")
        yield cmd, pipe[:i], w
        if self.ctx.func and braces <= 0 and "}" in w.argv:
          self.ctx.func = None

  def _record_call(self, w: shellparse.Words) -> None:
    argv = argv_util.strip_keywords(list(w.argv))
    if argv and argv[0] in self.ctx.funcs and not argv_util.function_header(w.argv):
      args = [expand.expand(a, w.substs, self.ctx)[0] for a in argv[1:]]
      self.ctx.funcs[argv[0]].append(args)

  def job_in(self, sub: expand.Ctx, text: str) -> None:
    """Scans shell text in a prepared child scope."""
    inner = Scanner(sub)
    inner.text(text)
    self.findings.extend(inner.findings)

  def job(self, job: runs.Job, args: List[str], via: str) -> None:
    sub = self.ctx.child(job.path, args, via, job.cwd)
    if job.lang == "shell":
      inner = Scanner(sub)
      inner.text(job.code)
      self.findings.extend(inner.findings)
      return
    for kind, value, snippet in code_scan.deletions(job.code, job.lang):
      self._code_finding(kind, value, snippet, sub)
    for text in code_scan.shell_outs(job.code, job.lang):
      self.job_in(sub, text)

  # --- per command -------------------------------------------------------

  def _collect(self, w: shellparse.Words,
               before: List[shellparse.Command]) -> None:
    argv = argv_util.strip_keywords(list(w.argv))
    if argv and argv[0] == "read":
      listed = self._listed(before)
      for name in _read_names(argv[1:]):
        self.ctx.assign(name, listed)
      return
    if len(argv) >= 3 and argv[0] == "for" and argv[2] == "in":
      values = [v for a in argv[3:] for v in expand.expand(a, w.substs,
                                                           self.ctx)]
      self.ctx.assign(argv[1], values or [UNKNOWN])
      return
    if argv and argv[0] in DECLARES:
      argv = [a for a in argv[1:] if not a.startswith("-")]
    for word in argv:
      m = expand.assignment(word)
      if not m:
        return
      self.ctx.assign(m.group(1), expand.expand(m.group(2), w.substs,
                                                self.ctx))

  def _listed(self, before: List[shellparse.Command]) -> List[str]:
    """What a `read` loop reads, from the command that feeds it."""
    if not before:
      return [UNKNOWN]
    pw = shellparse.words(before[-1].text)
    argv = argv_util.strip_prefixes(list(pw.argv))
    name = posixpath.basename(argv[0]) if argv else ""
    sub = argv[1] if len(argv) > 1 else ""
    if name == "git" and sub in GIT_LISTS:
      return ["<listed>"]  # paths relative to the repo: inside it
    if name in ("find", "ls"):
      roots = [a for a in argv[1:] if not a.startswith(("-", "(", "!"))]
      if name == "find":
        roots = roots[:1] or ["."]
      dirs = [v for r in roots or ["."] for v in expand.expand(r, pw.substs,
                                                              self.ctx)]
      return [posixpath.join(d, "<listed>") for d in dirs]
    printed = expand.static_output(before[-1:], self.ctx)
    return [p for text in printed for p in text.split()] or [UNKNOWN]

  def _command(self, cmd: shellparse.Command,
               before: List[shellparse.Command],
               w: shellparse.Words) -> None:
    argv = argv_util.strip_keywords(list(w.argv))
    if argv and argv[0] in DECLARES:
      return
    while argv and expand.assignment(argv[0]):
      argv = argv[1:]
    if not argv:
      return
    self._remember_writes(cmd, w)
    head = expand.expand(argv[0], w.substs, self.ctx)
    spelled = next((h for h in head if " " in h.strip()), None)
    if spelled:  # $(echo cm0g... | base64 -d): the command is a value
      self.text(spelled + " " + shellparse.plain(argv[1:], w.substs))
      return
    argv = argv_util.strip_prefixes([head[0]] + argv[1:])
    if argv:
      self._dispatch(argv, w, cmd, before)

  def _remember_writes(self, cmd: shellparse.Command,
                       w: shellparse.Words) -> None:
    """`cat > x.sh <<EOF` and `echo ... > x.sh` define x.sh for later."""
    if not w.writes:
      return
    if cmd.heredoc is not None:
      content = [cmd.heredoc]
    else:
      content = expand.static_output([cmd], self.ctx)
    for target in w.writes:
      for path in expand.expand(target, w.substs, self.ctx):
        resolved = runs._resolve(path, self.ctx.cwd)  # pylint: disable=protected-access
        if resolved and content:
          self.ctx.virtual[resolved] = content[0]

  def _dispatch(self, argv: List[str], w: shellparse.Words,
                cmd: shellparse.Command,
                before: List[shellparse.Command]) -> None:
    name = posixpath.basename(argv[0].lstrip("\\"))
    handler = {
        "cd": self._cd, "pushd": self._cd, "trap": self._trap,
        "eval": self._eval, "find": self._find, "xargs": self._xargs,
        "dd": self._dd, "mv": self._mv, "make": self._make,
        "gmake": self._make, "source": self._source, ".": self._source,
        "rsync": self._rsync, "docker": self._container,
        "podman": self._container, "nerdctl": self._container,
        "cmd": self._cmd, "cmd.exe": self._cmd,
        "powershell": self._powershell, "powershell.exe": self._powershell,
        "pwsh": self._powershell, "just": self._just,
        "diskutil": self._diskutil,
    }.get(name)
    runner = argv_util.runner_argv(argv)
    if runner is not None:
      if runner:
        self._dispatch(runner, w, cmd, before)
      return
    if WIN_DELETE_RE.match(cmd.text) and re.search(r"(?i)\s/s\b|-recurse",
                                                     cmd.text):
      self._windows(cmd.text)
    elif handler:
      handler(argv, w, before)
    elif name in DELETERS:
      self._delete(argv, w, cmd.text)
    elif DISK_TOOLS.match(name):
      self._disk([a for a in argv[1:] if not a.startswith("-")], cmd.text)
    elif name in runs.SHELLS:
      self._shell(argv, w, cmd, before)
    elif name in runs.INTERPRETERS:
      self._interpreter(argv, w, cmd)
    elif name in runs.PACKAGE_MANAGERS:
      self._package(argv)
    elif "/" in argv[0]:
      self._exec_path(argv, w)

  def _host_path(self, target: str) -> Optional[str]:
    """Inside a container, the host path a target reaches through a bind
    mount; None when it stays in the container's own filesystem."""
    if not target.startswith("/"):
      target = posixpath.join(self.ctx.cwd or "/", target)
    for dst, src in sorted(self.ctx.mounts, key=lambda m: -len(m[0])):
      if target == dst or target.startswith(dst.rstrip("/") + "/"):
        return src + target[len(dst.rstrip("/")):]
    return None

  def _judge(self, target: str, how: str, downgrade: bool = False) -> None:
    if self.ctx.mounts is not None:
      target = self._host_path(target)
      if target is None:
        return
    level, what = classify(target, self.ctx.cwd if self.ctx.mounts is None
                           else None, self.ctx.policy)
    if downgrade and level == DENY:
      level = ASK
    if level > ALLOW:
      self.findings.append(Finding(level, what, how, tuple(self.ctx.via)))

  def found(self, level: int, what: str, how: str) -> None:
    self.findings.append(Finding(level, what, how, tuple(self.ctx.via)))

  def _targets(self, words: List[str], w: shellparse.Words) -> List[str]:
    out, options = [], True
    for word in words:
      if options and word == "--":
        options = False
        continue
      if options and word.startswith("-") and word != "-":
        continue
      out.extend(expand.expand(word, w.substs, self.ctx))
    return out

  def _code_finding(self, kind: str, value: Optional[str], snippet: str,
                    sub: expand.Ctx) -> None:
    via = tuple(sub.via)
    if kind == "home":
      self.findings.append(Finding(DENY, f"your home directory"
                                   f" ({sub.policy.home})", snippet, via))
    elif kind == "home-child":
      self.findings.append(Finding(ASK, "a path in your home directory",
                                   snippet, via))
    elif value is not None:
      path = sub.policy.home + value[1:] if value.startswith("~") else value
      level, what = classify(path, sub.cwd, sub.policy)
      if level > ALLOW:
        self.findings.append(Finding(level, what, snippet, via))
