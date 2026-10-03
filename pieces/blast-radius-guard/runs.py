"""runs.py: the code a command runs besides its own words.

  script files   `bash x.sh`, `sh -e x`, `source x`, `. x`, `./x`, `path/x`,
                 `python x.py`, `node x.js`, `ruby x.rb`, `perl x.pl`, with
                 the language from the extension or the shebang.
  inline code    `bash -c`, `sh -c`, `eval`, `python -c`, `node -e`,
                 `ruby -e`, `perl -e`.
  package jobs   `npm run X`, `npm test`, `pnpm X`, `yarn X`, `bun run X`:
                 the package.json script with its pre and post hooks.
  make           `make [targets]`, `-C dir`, `-f file`: the recipes of each
                 target and its prerequisites, make variables substituted.

A file this same command writes (`cat > x.sh <<EOF`) is read from that
heredoc, so writing a script and running it in one call is seen too.
Standard library only.
"""

from __future__ import annotations

import json
import os
import posixpath
import re
from typing import Dict, List, NamedTuple, Optional, Tuple

SHELLS = frozenset({"bash", "sh", "zsh", "dash", "ksh", "ash", "fish"})
INTERPRETERS = {"python": "python", "python3": "python", "python2": "python",
                "pypy3": "python", "node": "js", "nodejs": "js", "ruby": "ruby",
                "perl": "perl", "deno": "js", "bun": "js", "tsx": "js",
                "ts-node": "js", "go": "go"}
INLINE_FLAGS = {"python": ("-c",), "js": ("-e", "--eval", "-p", "--print"),
                "ruby": ("-e",), "perl": ("-e", "-E"), "go": ()}
PACKAGE_MANAGERS = frozenset({"npm", "pnpm", "yarn", "bun"})
NPM_DIRECT = frozenset({"test", "t", "start", "stop", "restart"})
MAKE_VAR_RE = re.compile(r"^([A-Za-z_][\w.]*)\s*[:+?]?=\s*(.*)$")
MAKE_RULE_RE = re.compile(r"^([^\t#=:][^=:]*?)\s*::?(?!=)\s*(.*)$")
MAKE_REF_RE = re.compile(r"\$\(([A-Za-z_][\w.]*)\)|\$\{([A-Za-z_][\w.]*)\}")
MAX_FILE = 256 * 1024


class Job(NamedTuple):
  lang: str  # shell, python, js, ruby, perl
  code: str
  label: str  # how it is named in a reason: "cleanup.sh", "npm run clean"
  path: Optional[str] = None  # the script file, for $0
  args: Tuple[str, ...] = ()
  cwd: Optional[str] = None


def read_file(path: str, virtual: Dict[str, str],
              budget: List[int]) -> Optional[str]:
  """A script's text: written by this command, or on disk within budget."""
  if path in virtual:
    return virtual[path]
  try:
    if not os.path.isfile(path) or os.path.getsize(path) > MAX_FILE:
      return None
    if budget[0] <= 0:
      return None
    with open(path, encoding="utf-8", errors="replace") as fh:
      text = fh.read(MAX_FILE)
  except OSError:
    return None
  budget[0] -= len(text)
  return text


def _resolve(word: str, cwd: Optional[str]) -> Optional[str]:
  if word.startswith("/"):
    return posixpath.normpath(word)
  return posixpath.normpath(posixpath.join(cwd, word)) if cwd else None


def _has_c(flag: str) -> bool:
  """`-c`, or a short cluster that carries it: `-lc`, `-ec`, `-xc`."""
  return flag.startswith("-") and not flag.startswith("--") and "c" in flag


def shell_job(argv: List[str], cwd: Optional[str]) -> Optional[Tuple]:
  """('inline', code, args) or ('file', path, args) for a shell call."""
  i = 1
  while i < len(argv) and argv[i].startswith("-") and not _has_c(argv[i]):
    i += 2 if argv[i] in ("-o", "+o") else 1
  if i < len(argv) and _has_c(argv[i]):
    return ("inline", argv[i + 1], argv[i + 3:]) if i + 1 < len(argv) \
        else None
  if i < len(argv):
    path = _resolve(argv[i], cwd)
    return ("file", path, argv[i + 1:]) if path else None
  return None


def interpreter_job(lang: str, argv: List[str],
                    cwd: Optional[str]) -> Optional[Tuple]:
  i = 1
  while i < len(argv):
    arg = argv[i]
    if arg in INLINE_FLAGS[lang]:
      return ("inline", argv[i + 1], argv[i + 2:]) if i + 1 < len(argv) \
          else None
    if arg in ("-m", "run") and lang in ("python", "js", "go"):
      if arg == "-m":
        return None
      i += 1
      continue
    if arg.startswith("-"):
      i += 2 if arg in ("-r", "-M", "-I", "-W", "-X") else 1
      continue
    path = _resolve(arg, cwd)
    return ("file", path, argv[i + 1:]) if path else None
  return None


def _find_package(cwd: Optional[str], stop: Optional[str]) -> Optional[str]:
  d = cwd
  while d:
    if os.path.isfile(posixpath.join(d, "package.json")):
      return d
    if d == stop or d == "/":
      return None
    d = posixpath.dirname(d)
  return None


def package_jobs(argv: List[str], cwd: Optional[str],
                 stop: Optional[str]) -> List[Job]:
  """The package.json scripts an npm/pnpm/yarn/bun call runs, in order."""
  tool = posixpath.basename(argv[0])
  rest = [a for a in argv[1:] if not a.startswith("-")]
  if not rest:
    return []
  name = rest[0]
  if name in ("run", "run-script"):
    name = rest[1] if len(rest) > 1 else ""
  elif tool == "npm" and name not in NPM_DIRECT:
    return []
  pkg_dir = _find_package(cwd, stop)
  if not name or not pkg_dir:
    return []
  try:
    with open(posixpath.join(pkg_dir, "package.json"), encoding="utf-8") as fh:
      scripts = json.load(fh).get("scripts") or {}
  except (OSError, ValueError, AttributeError):
    return []
  if not isinstance(scripts, dict):
    return []
  out = []
  for key in (f"pre{name}", name, f"post{name}"):
    body = scripts.get(key)
    if isinstance(body, str):
      out.append(Job("shell", body, f"{tool} run {key}", cwd=pkg_dir))
  return out


def _make_args(argv: List[str], cwd: Optional[str]):
  directory, makefile, targets, overrides = cwd, None, [], {}
  i = 1
  while i < len(argv):
    arg = argv[i]
    if arg in ("-C", "--directory") and i + 1 < len(argv):
      directory = _resolve(argv[i + 1], directory)
      i += 2
      continue
    if arg in ("-f", "--file", "--makefile") and i + 1 < len(argv):
      makefile = argv[i + 1]
      i += 2
      continue
    if "=" in arg and not arg.startswith("-"):
      key, _, value = arg.partition("=")
      overrides[key] = value
    elif not arg.startswith("-"):
      targets.append(arg)
    i += 1
  return directory, makefile, targets, overrides


def _parse_makefile(text: str):
  """(rules, variables, target order); rules map target -> (deps, recipe)."""
  rules: Dict[str, Tuple[List[str], List[str]]] = {}
  variables: Dict[str, str] = {}
  order: List[str] = []
  current: List[str] = []
  for line in text.replace("\\\n", " ").split("\n"):
    if line.startswith("\t"):
      for t in current:
        rules[t][1].append(line[1:].lstrip("@-+ "))
      continue
    var = MAKE_VAR_RE.match(line)
    rule = None if var else MAKE_RULE_RE.match(line)
    current = rule.group(1).split() if rule else []
    if var:
      variables[var.group(1)] = var.group(2).strip()
    for t in current:
      rules.setdefault(t, ([], []))[0].extend(rule.group(2).split())
    order.extend(t for t in current
                 if not t.startswith(".") and t not in order)
  return rules, variables, order


def make_jobs(argv: List[str], cwd: Optional[str],
              read) -> Tuple[List[Job], Dict[str, str]]:
  """Recipes `make` would run, and the make variables they use."""
  directory, makefile, targets, overrides = _make_args(argv, cwd)
  if not directory:
    return [], {}
  names = [makefile] if makefile else ["GNUmakefile", "makefile", "Makefile"]
  text = None
  for name in names:
    text = read(_resolve(name, directory))
    if text is not None:
      break
  if text is None:
    return [], {}
  rules, variables, order = _parse_makefile(text)
  variables.update(overrides)
  todo, seen, jobs = list(targets or order[:1]), set(), []
  while todo:
    target = todo.pop(0)
    if target in seen or target not in rules:
      continue
    seen.add(target)
    deps, recipe = rules[target]
    todo.extend(deps)
    if recipe:
      jobs.append(Job("shell", "\n".join(recipe), f"make {target}",
                      cwd=directory))
  return jobs, variables


JUST_RECIPE_RE = re.compile(r"^@?([A-Za-z_][\w-]*)(?:\s+[^:=]*)?:(?!=)(.*)$")


def just_jobs(argv: List[str], cwd: Optional[str], read) -> List[Job]:
  """Recipes `just [recipe]` runs, with the recipes they depend on."""
  names = [a for a in argv[1:] if not a.startswith("-") and "=" not in a]
  text = None
  for fname in ("justfile", "Justfile", ".justfile"):
    text = read(_resolve(fname, cwd)) if cwd else None
    if text is not None:
      break
  if text is None:
    return []
  recipes: Dict[str, Tuple[List[str], List[str]]] = {}
  current = None
  for line in text.split("\n"):
    m = JUST_RECIPE_RE.match(line)
    if m and not line[:1].isspace():
      current = m.group(1)
      recipes[current] = (m.group(2).split(), [])
    elif current and line[:1].isspace() and line.strip():
      recipes[current][1].append(line.strip().lstrip("@-"))
  todo, seen, jobs = names[:1] or list(recipes)[:1], set(), []
  while todo:
    name = todo.pop(0)
    if name in seen or name not in recipes:
      continue
    seen.add(name)
    deps, body = recipes[name]
    todo.extend(deps)
    if body:
      jobs.append(Job("shell", "\n".join(body), f"just {name}", cwd=cwd))
  return jobs


def make_to_shell(code: str, variables: Dict[str, str]) -> str:
  """`$(VAR)` / `${VAR}` from make variables (recursively) or the
  environment; `$$` is a literal `$`."""
  def sub(m: "re.Match[str]") -> str:
    name = m.group(1) or m.group(2)
    return variables.get(name, "\x03{" + name + "}")
  code = code.replace("$$", "\x02")
  for _ in range(5):
    code, n = MAKE_REF_RE.subn(sub, code)
    if not n:
      break
  return code.replace("\x03", "$").replace("\x02", "$")
