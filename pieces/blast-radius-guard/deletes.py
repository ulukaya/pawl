"""deletes.py: the commands that delete, and what each one deletes.

rm, rmdir, unlink, shred, rimraf; find -delete / -exec rm (a filtered find
deletes some entries below its roots, never a root); xargs rm fed by find,
echo or ls; mv to /dev/null or of a dangerous path; rsync --delete (empties
the destination); dd of=<disk> and mkfs; the cmd and PowerShell forms.

Standard library only.
"""

from __future__ import annotations

import posixpath
import re
from typing import List

import argv_util
import expand
import shellparse
import winpath
from targets import ALLOW, ASK, DENY, classify

DELETERS = frozenset({"rm", "unlink", "rmdir", "shred", "srm", "rimraf"})
FIND_DELETE = frozenset({"-delete"})
FIND_EXEC = frozenset({"-exec", "-execdir", "-ok", "-okdir"})
FIND_FILTERS = frozenset({"-name", "-iname", "-path", "-ipath", "-regex",
                          "-iregex", "-type", "-newer", "-mtime", "-mmin",
                          "-size", "-user", "-perm", "-empty", "-wholename"})
DISKUTIL_ERASE = frozenset({"eraseDisk", "eraseVolume", "partitionDisk",
                            "zeroDisk", "randomDisk", "secureErase",
                            "reformat"})
DISK_RE = re.compile(r"^/dev/(?:sd|hd|vd|xvd|nvme|mmcblk|disk|rdisk|md|dm-)")
DISK_TOOLS = re.compile(r"^(?:mkfs(?:\.\w+)?|mke2fs|wipefs|mkswap)$")
WIN_DELETE_RE = re.compile(
    r"(?i)^\s*(?:rmdir|rd|del|erase|remove-item|ri)\b(.*)$")
WIN_DANGER_RE = re.compile(
    r"(?i)(?:^|[\s\"'])(?:[a-z]:\\?\*?|[a-z]:\\users(?:\\[^\\\s\"']+"
    r"(?:\\(?:documents|desktop|downloads|pictures|videos|music|onedrive"
    r"|appdata|\.ssh|source|repos))?)?\\?|%userprofile%|\$env:userprofile"
    r"|\$home|~)(?=$|[\s\"'])")


class DeleteRules:
  """Mixed into Scanner: one handler per deleting command."""

  def _delete(self, argv: List[str], w: shellparse.Words, how: str) -> None:
    for target in self._targets(argv[1:], w):
      self._judge(target, how)
    self._windows_paths(how)

  def _windows_paths(self, how: str) -> None:
    """Backslash Windows paths a shell would mangle, read from the raw text."""
    for path in winpath.PATH_RE.findall(how):
      if "\\" not in path:
        continue  # forward-slash and MSYS paths come through argv already
      res = winpath.classify(path)
      if res and res[0] > ALLOW:
        self.found(res[0], res[1], how.strip())

  def _find(self, argv: List[str], w: shellparse.Words, _before) -> None:
    rest = argv[1:]
    roots = []
    while rest and not rest[0].startswith(("-", "(", "!")):
      roots.append(rest.pop(0))
    deletes = any(a in FIND_DELETE for a in rest) or any(
        a in FIND_EXEC and i + 1 < len(rest) and
        posixpath.basename(rest[i + 1]) in DELETERS
        for i, a in enumerate(rest))
    if not deletes:
      return
    filtered = any(a in FIND_FILTERS for a in rest)
    how = " ".join(argv)
    for target in self._targets(roots or ["."], w):
      if filtered:  # some entries below the root, never the root itself
        level, _ = classify(target, self.ctx.cwd, self.ctx.policy)
        target = target if level == DENY else posixpath.join(target, "<match>")
      self._judge(target, how, downgrade=filtered)

  def _xargs(self, argv: List[str], w: shellparse.Words, before) -> None:
    rest = argv_util.strip_prefixes(["env"] + [a for a in argv[1:]
                                      if not a.startswith("-")])
    if not rest or posixpath.basename(rest[0]) not in DELETERS:
      return
    if w.herestring is not None:
      for target in self._targets(w.herestring.split(), w):
        self._judge(target, f"{' '.join(argv)} <<< {w.herestring}")
      return
    if not before:
      return
    producer = before[-1]
    pw = shellparse.words(producer.text)
    name = posixpath.basename(pw.argv[0]) if pw.argv else ""
    how = f"{producer.text} | {' '.join(argv)}"
    if name == "find":
      self._find(pw.argv + ["-delete"], pw, [])
    elif name in ("echo", "printf", "ls"):
      for target in self._targets(pw.argv[1:], pw):
        self._judge(target, how)

  def _mv(self, argv: List[str], w: shellparse.Words, _before) -> None:
    paths = self._targets(argv[1:], w)
    if len(paths) < 2:
      return
    for source in paths[:-1]:
      level, what = classify(source, self.ctx.cwd, self.ctx.policy)
      if paths[-1] == "/dev/null" or level == DENY:
        self._judge(source, " ".join(argv))

  def _rsync(self, argv: List[str], w: shellparse.Words, _before) -> None:
    """`--delete` empties the destination of anything the source lacks."""
    if not any(a.startswith("--del") for a in argv[1:]):
      return
    operands = [a for a in argv[1:] if not a.startswith("-")]
    if len(operands) < 2 or re.match(r"^[^/]*:", operands[-1]):
      return  # a remote destination is out of pawl's view
    for dest in expand.expand(operands[-1], w.substs, self.ctx):
      self._judge(dest.rstrip("/") + "/*", " ".join(argv))

  def _dd(self, argv: List[str], w: shellparse.Words, _before) -> None:
    """`of=PATH` is overwritten: a disk is denied, any other path is judged
    where it lands (so `of=/etc/passwd` is denied, `of=./out.img` allowed)."""
    how = " ".join(argv)
    for raw in [a[3:] for a in argv[1:] if a.startswith("of=")]:
      for target in expand.expand(raw, w.substs, self.ctx):
        target = re.sub(r"^/{2,}", "/", target)  # //dev/sda -> /dev/sda
        if DISK_RE.match(target):
          self.found(DENY, f"the disk {target}", how)
        else:
          self._judge(target, how)

  def _diskutil(self, argv: List[str], w: shellparse.Words, _before) -> None:
    """macOS `diskutil eraseDisk|eraseVolume|partitionDisk|zeroDisk ...`."""
    if len(argv) > 1 and argv[1] in DISKUTIL_ERASE:
      self.found(DENY, f"the disk {argv[-1]}", " ".join(argv))

  def _disk(self, devices: List[str], how: str) -> None:
    for dev in devices:
      if DISK_RE.match(dev):
        self.found(DENY, f"the disk {dev}", how)

  def _windows(self, text: str, argv: List[str],
               w: shellparse.Words) -> None:
    judged = False
    for path in winpath.PATH_RE.findall(text):
      res = winpath.classify(path)
      if res is None:
        continue
      judged = True
      if res[0] > ALLOW:
        self.found(res[0], res[1], text.strip())
    if judged:
      return
    m = WIN_DELETE_RE.match(text)
    if m and WIN_DANGER_RE.search(m.group(1)):  # %USERPROFILE%, $env:..., ~
      self.found(DENY, "a drive root or the user profile", text.strip())
      return
    targets = [a for a in argv[1:]
               if not a.startswith("-") and not re.match(r"^/[a-zA-Z]$", a)]
    if targets:  # `Remove-Item build`, `rd /s /q ./out`: judge like any path
      for target in self._targets(targets, w):
        self._judge(target, text.strip())
    elif m:
      self.found(ASK, "a Windows path outside pawl's view", text.strip())
