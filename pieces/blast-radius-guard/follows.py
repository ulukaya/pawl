"""follows.py: following a command into what it runs.

`cd` (so later relative paths land right), `trap`, `eval`, shells with -c
or a script file or a heredoc or a pipe, `source`, interpreters, an
executable path, package scripts and make recipes. Each runs as a job in a
child scope (see Scanner.job).

Standard library only.
"""

from __future__ import annotations

import base64
import binascii
import posixpath
import re
from typing import List, Optional, Tuple

import code_scan
import expand
import runs
import shellparse
from targets import UNKNOWN

_PARALLEL_VALUE_OPTS = frozenset({"-I", "-j", "--jobs", "-P", "-N", "--delay",
                                  "--timeout", "-S", "--sshlogin"})
_PLACEHOLDER_RE = re.compile(r"\{[^{}]*\}")
_FISH_CMD_FLAGS = ("--command", "--init-command")


def _fish_cmd_flag(flag: str) -> bool:
  """fish's `--command`/`--init-command`, in full or any abbreviation."""
  stem = flag.split("=", 1)[0]
  return stem.startswith("--") and len(stem) > 3 and \
      any(full.startswith(stem) for full in _FISH_CMD_FLAGS)


class FollowRules:
  """Mixed into Scanner: one handler per command that runs other code."""

  def _cd(self, argv: List[str], w: shellparse.Words, _before) -> None:
    dests = expand.expand(argv[1], w.substs, self.ctx) if len(argv) > 1 \
        else [self.ctx.policy.home]
    dest = dests[0] if len(dests) == 1 else UNKNOWN
    self.ctx.cwd = None if UNKNOWN in dest or dest == "-" else \
        runs._resolve(dest, self.ctx.cwd)  # pylint: disable=protected-access

  def _trap(self, argv: List[str], w: shellparse.Words, _before) -> None:
    if len(argv) > 1:
      self.text(shellparse.restore(argv[1], w.substs))

  def _eval(self, argv: List[str], w: shellparse.Words, _before) -> None:
    rest = argv[2:] if argv[1:2] == ["--"] else argv[1:]
    self.text(shellparse.plain(rest, w.substs))

  def _args(self, words: List[str], w: shellparse.Words) -> List[str]:
    return [expand.expand(a, w.substs, self.ctx)[0] for a in words]

  def _run_file(self, path: str, args: List[str], label: str,
                lang: Optional[str] = None) -> None:
    code = runs.read_file(path, self.ctx.virtual, self.ctx.budget)
    if code is None:
      return
    first = code.split("\n", 1)[0]
    lang = lang or code_scan.language(path, first) or "shell"
    self.job(runs.Job(lang, code, label, path=path), args, label)

  def _shell(self, argv: List[str], w: shellparse.Words,
             cmd: shellparse.Command, before) -> None:
    job = runs.shell_job(argv, self.ctx.cwd)
    label = " ".join(argv)
    if job and job[0] == "inline":
      code = shellparse.restore(job[1], w.substs)
      self.job(runs.Job("shell", code, label), self._args(job[2], w), label)
    elif job and self._procsub(argv, w, label):
      return
    elif job:
      self._run_file(job[1], self._args(job[2], w), label, "shell")
    elif cmd.heredoc is not None or w.herestring is not None:
      body = cmd.heredoc if cmd.heredoc is not None else w.herestring
      self.job(runs.Job("shell", body, label), [], label)
    elif w.stdin is not None:
      path = runs._resolve(w.stdin, self.ctx.cwd)  # pylint: disable=protected-access
      if path:
        self._run_file(path, [], label, "shell")
    elif before:
      for text in expand.static_output(before, self.ctx):
        self.job(runs.Job("shell", text, label), [], label)

  def _procsub(self, argv: List[str], w: shellparse.Words,
               label: str) -> bool:
    """`bash <(cmd)`, `source <(cmd)`: scan what cmd prints, when known."""
    for word in argv[1:]:
      text = expand.procsub(word, w.substs, self.ctx)
      if text is not None:
        self.job(runs.Job("shell", text, label), [], label)
        return True
    return False

  def _source(self, argv: List[str], w: shellparse.Words, _before) -> None:
    if len(argv) > 1 and self._procsub(argv, w, " ".join(argv)):
      return
    if len(argv) > 1:
      path = runs._resolve(expand.expand(argv[1], w.substs, self.ctx)[0],  # pylint: disable=protected-access
                           self.ctx.cwd)
      if path:
        self._run_file(path, self._args(argv[2:], w), " ".join(argv),
                       "shell")

  def _interpreter(self, argv: List[str], w: shellparse.Words,
                   cmd: shellparse.Command) -> None:
    lang = runs.INTERPRETERS[posixpath.basename(argv[0])]
    job = runs.interpreter_job(lang, argv, self.ctx.cwd)
    label = " ".join(argv)
    if job and job[0] == "inline":
      code = shellparse.restore(job[1], w.substs)
      self.job(runs.Job(lang, code, label), [], label)
    elif job:
      self._run_file(job[1], self._args(job[2], w), label)
    elif cmd.heredoc is not None:
      self.job(runs.Job(lang, cmd.heredoc, label), [], label)

  def _exec_path(self, argv: List[str], w: shellparse.Words) -> None:
    path = runs._resolve(argv[0], self.ctx.cwd)  # pylint: disable=protected-access
    if path:
      self._run_file(path, self._args(argv[1:], w), " ".join(argv))

  def _package(self, argv: List[str]) -> None:
    stop = self.ctx.policy.workspace
    for job in runs.package_jobs(argv, self.ctx.cwd, stop):
      self.job(job, [], job.label)

  def _fish(self, argv: List[str], w: shellparse.Words,
            cmd: shellparse.Command, before) -> None:
    """fish runs code from `-c`/`-C`/`--command`/`--init-command`, also when
    the value is attached (`-c'...'`), clustered (`-ic'...'`), given with `=`
    or abbreviated (`--com=`). Every other form is an ordinary shell call."""
    codes: List[str] = []
    i = 1
    while i < len(argv):
      arg = argv[i]
      if "=" in arg and (_fish_cmd_flag(arg) or arg.split("=", 1)[0]
                         in _FISH_CMD_FLAGS):
        codes.append(arg.split("=", 1)[1])
      elif arg in _FISH_CMD_FLAGS and i + 1 < len(argv):
        codes.append(argv[i + 1])
        i += 1
      elif arg.startswith("-") and not arg.startswith("--"):
        pos = next((j for j, ch in enumerate(arg) if j and ch in "cC"), None)
        if pos is None:
          pass
        elif len(arg) > pos + 1:
          codes.append(arg[pos + 1:])
        elif i + 1 < len(argv):
          codes.append(argv[i + 1])
          i += 1
      else:
        break  # a script file or the end of the options
      i += 1
    for code in codes:
      self.text(shellparse.restore(code, w.substs))
    if not codes:
      self._shell(argv, w, cmd, before)

  def _parallel(self, argv: List[str], w: shellparse.Words, before) -> None:
    """GNU `parallel TEMPLATE ::: args`: TEMPLATE run once per arg, with
    `{}`/`{1}`/`{.}` replaced. Without `:::`, the args come from stdin."""
    if ":::" in argv:
      idx = argv.index(":::")
      template, inputs = argv[1:idx], [a for a in argv[idx + 1:] if a != ":::"]
    else:
      template, inputs = argv[1:], None
    while template and template[0].startswith("-"):
      template = template[2:] if template[0] in _PARALLEL_VALUE_OPTS \
          else template[1:]
    if not template:
      return
    tmpl = shellparse.plain(template, w.substs)
    if inputs is not None:
      args = [v for a in inputs for v in expand.expand(a, w.substs, self.ctx)]
    else:
      args = self._listed(before) if before else [UNKNOWN]
    for arg in args[:8]:
      run = _PLACEHOLDER_RE.sub(lambda _m, a=arg: a, tmpl) if "{" in tmpl \
          else f"{tmpl} {arg}"
      self.text(run)

  def _just(self, argv: List[str], w: shellparse.Words, _before) -> None:
    def read(path):
      return runs.read_file(path, self.ctx.virtual, self.ctx.budget) \
          if path else None
    for job in runs.just_jobs(argv, self.ctx.cwd, read):
      self.job(job, [], job.label)

  def _make(self, argv: List[str], w: shellparse.Words, _before) -> None:
    def read(path):
      return runs.read_file(path, self.ctx.virtual, self.ctx.budget) \
          if path else None
    jobs, variables = runs.make_jobs(argv, self.ctx.cwd, read)
    for job in jobs:
      code = runs.make_to_shell(job.code, variables)
      self.job(job._replace(code=code), [], job.label)

  # --- containers and Windows shells ----------------------------------------

  def _container(self, argv: List[str], w: shellparse.Words, _before) -> None:
    """`docker run -v SRC:DST IMAGE CMD`: CMD's deletions, seen on the host."""
    rest = argv[1:]
    if rest[:2] == ["container", "run"]:
      rest = rest[1:]
    if not rest or rest[0] != "run":
      return
    mounts, workdir, command = _container_args(rest[1:], w, self.ctx)
    if not command:
      return
    label = " ".join(argv)
    sub = self.ctx.child(None, [], label, workdir)
    sub.mounts = mounts
    self.job_in(sub, shellparse.quoted(command, w.substs))

  def _cmd(self, argv: List[str], w: shellparse.Words, _before) -> None:
    """`cmd /c <command>`: the command after /c or /k."""
    flags = [i for i, a in enumerate(argv) if a.lower() in ("/c", "/k")]
    if flags:
      self.text(shellparse.plain(argv[flags[0] + 1:], w.substs))

  def _powershell(self, argv: List[str], w: shellparse.Words,
                  _before) -> None:
    """`powershell -Command ...` and `-EncodedCommand <base64 UTF-16>`."""
    lowered = [a.lower() for a in argv]
    for flag in ("-encodedcommand", "-enc", "-e", "-ec"):
      if flag in lowered and lowered.index(flag) + 1 < len(argv):
        decoded = _decode_utf16(argv[lowered.index(flag) + 1])
        if decoded:
          self.text(decoded)
        return
    for flag in ("-command", "-c"):
      if flag in lowered:
        self.text(shellparse.plain(argv[lowered.index(flag) + 1:],
                                   w.substs))
        return


CONTAINER_VALUE_OPTS = frozenset({
    "-v", "--volume", "--mount", "-e", "--env", "--env-file", "-w",
    "--workdir", "--name", "-p", "--publish", "-u", "--user", "--network",
    "--net", "--entrypoint", "--platform", "-l", "--label", "--add-host",
    "--cpus", "-m", "--memory", "--pull", "--restart", "-h", "--hostname",
    "--gpus", "--device", "--cap-add", "--cap-drop", "--security-opt",
    "--ulimit", "--log-driver", "--dns", "--ipc", "--pid", "--tmpfs",
    "--volumes-from", "--shm-size", "--expose", "--link", "--runtime",
})


def _mount(spec: str, w: shellparse.Words, ctx) -> Optional[Tuple[str, str]]:
  """(container path, host path) for `-v SRC:DST[:ro]` or `--mount ...`."""
  if spec.startswith("type=") or "source=" in spec or "src=" in spec:
    fields = dict(f.split("=", 1) for f in spec.split(",") if "=" in f)
    src = fields.get("source") or fields.get("src")
    dst = fields.get("target") or fields.get("destination") or fields.get(
        "dst")
  else:
    parts = expand.expand(spec, w.substs, ctx)[0].split(":")
    src, dst = (parts[0], parts[1]) if len(parts) >= 2 else (None, None)
  if not src or not dst or not (src.startswith("/") or src.startswith(".")):
    return None  # a named volume lives inside docker, not in a host folder
  host = expand.expand(src, w.substs, ctx)[0]
  if not host.startswith("/") and ctx.cwd:
    host = posixpath.normpath(posixpath.join(ctx.cwd, host))
  return dst, host


def _container_args(args: List[str], w: shellparse.Words, ctx):
  mounts, workdir, i = [], "/", 0
  while i < len(args) and args[i].startswith("-"):
    flag, value = args[i], None
    if "=" in flag and flag.startswith("--"):
      flag, value = flag.split("=", 1)
      i += 1
    elif flag in CONTAINER_VALUE_OPTS and i + 1 < len(args):
      value = args[i + 1]
      i += 2
    else:
      i += 1
    if flag in ("-v", "--volume", "--mount") and value:
      m = _mount(value, w, ctx)
      mounts += [m] if m else []
    if flag in ("-w", "--workdir") and value:
      workdir = value
  command = args[i + 1:] if i < len(args) else []  # past the image
  return mounts, workdir, command


def _decode_utf16(text: str) -> Optional[str]:
  try:
    return base64.b64decode(text, validate=True).decode("utf-16-le")
  except (binascii.Error, ValueError, UnicodeDecodeError):
    return None
