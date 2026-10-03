"""code_scan.py: deletions written in Python, JavaScript, Ruby or Perl.

Finds calls to each language's recursive-delete and unlink APIs and judges
their first argument when it names a place: the home directory
(`Path.home()`, `os.homedir()`, `Dir.home`, `$ENV{HOME}`, ...), a string
literal path, or the root. An argument pawl cannot read (a variable) is left
alone: code is the agent's own business until it names a dangerous place.

Standard library only.
"""

from __future__ import annotations

import re
import shlex
from typing import List, Optional, Tuple

DELETE_CALLS = {
    "python": re.compile(
        r"\b(?:shutil\.rmtree|os\.(?:remove|unlink|rmdir|removedirs)"
        r"|rmtree)\s*\("),
    "js": re.compile(
        r"\b(?:fs(?:\.promises)?\.(?:rmSync|rm|rmdirSync|rmdir|unlinkSync"
        r"|unlink)|fse?\.(?:remove|removeSync|emptyDir|emptyDirSync)"
        r"|rimraf(?:\.sync)?|rmSync|rmdirSync)\s*\("),
    "ruby": re.compile(
        r"\bFileUtils\.(?:rm_rf|rm_r|rm|remove_dir|remove_entry"
        r"|remove_entry_secure)\s*\(?"),
    "perl": re.compile(r"\b(?:rmtree|remove_tree)\s*\(?"),
    "go": re.compile(r"\bos\.(?:RemoveAll|Remove)\s*\("),
}
SHELL_OUTS = {
    "python": re.compile(
        r"\b(?:os\.(?:system|popen)|subprocess\.(?:run|call|check_call"
        r"|check_output|Popen|getoutput|getstatusoutput))\s*\("),
    "js": re.compile(
        r"\b(?:exec|execSync|spawn|spawnSync|execFile|execFileSync)\s*\("),
    "ruby": re.compile(r"(?:\bsystem|\bexec|%x)\s*[\(\{]?"),
    "perl": re.compile(r"\b(?:system|exec|qx)\s*[\(\{]?"),
    "go": re.compile(r"\bexec\.Command(?:Context)?\s*\("),
}
LIST_ITEM_RE = re.compile(r"['\"]([^'\"]*)['\"]")
PERL_ENV_RE = re.compile(r"\$ENV\{\s*(\w+)\s*\}")
METHOD_DELETES = re.compile(r"\)\s*\.(?:unlink|rmdir)\s*\(")
HOME_EXPRS = re.compile(
    r"Path\.home\(\)|expanduser\(\s*['\"]~/?['\"]\s*\)"
    r"|os\.environ\[\s*['\"]HOME['\"]\s*\]"
    r"|os\.environ\.get\(\s*['\"]HOME['\"]\s*\)"
    r"|os\.getenv\(\s*['\"]HOME['\"]\s*\)"
    r"|os\.homedir\(\)|homedir\(\)|process\.env\.HOME"
    r"|process\.env\[\s*['\"]HOME['\"]\s*\]|Dir\.home"
    r"|ENV\[\s*['\"]HOME['\"]\s*\]|ENV\.fetch\(\s*['\"]HOME['\"]\s*\)"
    r"|\$ENV\{\s*HOME\s*\}|File\.expand_path\(\s*['\"]~['\"]\s*\)"
    r"|os\.Getenv\(\s*\"HOME\"\s*\)|glob\(\s*['\"]~/?['\"]\s*\)")
# What may sit around a home expression and leave it the home itself:
# `os.path.`, `require('os').`, `str(...)`.
RECEIVER_RE = re.compile(r"[\w.()'\"\s]*")
# `Path.home() / "x"`, `.joinpath("x")`, `filepath.Join(home, "x")`
JOIN_RE = re.compile(r"^\s*(?:/|\.joinpath\(|,)\s*['\"]([^'\"]+)['\"]")
JOIN_CALL_RE = re.compile(
    r"^\s*(?:os\.path\.join|path\.join|path\.resolve|filepath\.Join"
    r"|File\.join)\(\s*[^,]+,\s*['\"]([^'\"]+)['\"]\s*\)\s*$")
STRING_RE = re.compile(r"^\s*(?:[rbuf]?['\"]([^'\"]*)['\"]|Path\(\s*['\"]"
                       r"([^'\"]*)['\"]\s*\))\s*$")
WRAPPERS = re.compile(r"^\s*(?:str|Path|path\.resolve|os\.path\.abspath"
                      r"|os\.path\.realpath)\(\s*(.*?)\s*\)\s*$", re.S)


COMMENT_STARTS = {"python": ("#",), "ruby": ("#",), "perl": ("#",),
                  "js": ("//", "/*"), "go": ("//", "/*")}


def quiet_spans(code: str, lang: str) -> List[Tuple[int, int]]:
  """Spans of string literals and comments: text there is data, not calls."""
  spans, i, n = [], 0, len(code)
  comments = COMMENT_STARTS.get(lang, ())
  while i < n:
    start = i
    if code.startswith(('"""', "\'\'\'"), i) and lang == "python":
      end = code.find(code[i:i + 3], i + 3)
      i = n if end < 0 else end + 3
    elif code[i] in "'\"`":
      i = _string_end(code, i)
    elif any(code.startswith(c, i) for c in comments):
      closer = "*/" if code.startswith("/*", i) else "\n"
      end = code.find(closer, i + 2)
      i = n if end < 0 else end + len(closer)
    else:
      i += 1
      continue
    spans.append((start, i))
  return spans


def _string_end(code: str, i: int) -> int:
  quote, j = code[i], i + 1
  while j < len(code) and code[j] != quote:
    if code[j] == "\\":
      j += 1
    elif code[j] == "\n" and quote != "`":
      return j
    j += 1
  return j + 1


def _quiet(pos: int, spans: List[Tuple[int, int]]) -> bool:
  return any(a <= pos < b for a, b in spans)


def language(path: str, first_line: str) -> Optional[str]:
  """The language of a script file, from its extension or shebang."""
  ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
  by_ext = {"py": "python", "js": "js", "mjs": "js", "cjs": "js", "ts": "js",
            "rb": "ruby", "pl": "perl", "go": "go", "sh": "shell",
            "bash": "shell",
            "zsh": "shell"}
  if ext in by_ext:
    return by_ext[ext]
  if first_line.startswith("#!"):
    for word, lang in (("python", "python"), ("node", "js"), ("ruby", "ruby"),
                       ("perl", "perl"), ("sh", "shell")):
      if word in first_line:
        return lang
  return None


def _first_arg(code: str, start: int) -> str:
  """The text of the first argument of the call opened before `start`."""
  depth, i, quote = 0, start, ""
  while i < len(code):
    ch = code[i]
    if quote:
      quote = "" if ch == quote and code[i - 1] != "\\" else quote
    elif ch in "'\"`":
      quote = ch
    elif ch in "([{":
      depth += 1
    elif ch in ")]}":
      if depth == 0:
        return code[start:i]
      depth -= 1
    elif ch in ",;\n" and depth == 0:
      return code[start:i]
    i += 1
  return code[start:]


def _unwrap(expr: str) -> str:
  for _ in range(3):
    m = WRAPPERS.match(expr)
    if not m:
      return expr.strip()
    expr = m.group(1)
  return expr.strip()


def judge_arg(expr: str) -> Tuple[str, Optional[str]]:
  """('home'|'path'|'', value): what a delete call's argument names."""
  expr = _unwrap(expr)
  m = HOME_EXPRS.search(expr)
  if m:
    before, after = expr[:m.start()], expr[m.end():]
    joined = JOIN_RE.match(after) or JOIN_CALL_RE.match(expr)
    if joined:
      return "path", "~/" + joined.group(1)
    if RECEIVER_RE.fullmatch(before) and not after.strip(" )'\""):
      return "home", None
    return "home-child", None
  m = STRING_RE.match(expr)
  if m:
    return "path", m.group(1) if m.group(1) is not None else m.group(2)
  return "", None


def _as_shell(arg: str, rest: str) -> Optional[str]:
  """A shell command from a shell-out's argument text, if it is literal:
  a string, an argv list, or a program string followed by an args list."""
  arg = arg.strip()
  m = STRING_RE.match(arg)
  head = m.group(1) if m and m.group(1) is not None else None
  if head is not None and rest.lstrip().startswith("["):
    items = LIST_ITEM_RE.findall(rest[:rest.find("]") + 1])
    return " ".join([head] + [shlex.quote(i) for i in items])
  if head is not None:
    return head
  if arg.startswith("["):
    items = LIST_ITEM_RE.findall(arg)
    return " ".join(shlex.quote(i) for i in items) if items else None
  return None


def shell_outs(code: str, lang: str) -> List[str]:
  """Shell commands the code hands to a shell or runs as an argv list."""
  pattern = SHELL_OUTS.get(lang)
  if not pattern:
    return []
  out = []
  spans = quiet_spans(code, lang)
  for m in pattern.finditer(code):
    if _quiet(m.start(), spans):
      continue
    if lang == "go":
      close = code.find(")", m.end())
      items = LIST_ITEM_RE.findall(code[m.end():close if close > 0 else None])
      if items:
        out.append(" ".join(shlex.quote(i) for i in items))
      continue
    arg = _first_arg(code, m.end())
    rest = code[m.end() + len(arg) + 1:m.end() + len(arg) + 200]
    text = _as_shell(arg, rest)
    if text:
      out.append(PERL_ENV_RE.sub(r"$\1", text) if lang == "perl" else text)
  for m in re.finditer(r"`([^`]+)`", code) if lang in ("ruby", "perl") \
      else ():
    if not any(a < m.start() < b for a, b in spans if (a, b) != (m.start(),
                                                                  m.end())):
      out.append(PERL_ENV_RE.sub(r"$\1", m.group(1)))
  return out


def deletions(code: str, lang: str) -> List[Tuple[str, Optional[str], str]]:
  """(kind, path, call text) for each delete call whose target is readable."""
  out = []
  pattern = DELETE_CALLS.get(lang)
  calls = list(pattern.finditer(code)) if pattern else []
  if lang == "python":
    calls += list(METHOD_DELETES.finditer(code))
  spans = quiet_spans(code, lang)
  calls = [m for m in calls if not _quiet(m.start(), spans)]
  for m in calls:
    if m.re is METHOD_DELETES:
      opener = code.rfind("(", 0, m.start() + 1)
      receiver_start = code.rfind("Path", 0, opener + 1)
      arg = code[receiver_start:m.start() + 1] if receiver_start >= 0 else ""
    else:
      arg = _first_arg(code, m.end())
    kind, value = judge_arg(arg)
    if kind:
      snippet = code[m.start():m.end() + len(arg) + 1].strip()
      out.append((kind, value, snippet[:80]))
  return out
