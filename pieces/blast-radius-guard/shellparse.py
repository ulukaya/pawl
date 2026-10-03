"""shellparse.py: shell text -> pipelines of commands, as bash would see them.

Not a shell: enough of one to find every simple command a line runs, with
the spellings that hide a command from a text matcher undone first.

  normalize()   `$IFS` / `${IFS}` become spaces; ANSI-C `$'\\x72\\x6d'`
                strings are decoded into ordinary quoted text.
  pipelines()   splits on `;`, `&&`, `||`, `&`, newlines and `|` outside
                quotes, joins `\\`-continued lines, and attaches each
                heredoc body to the command that opened it.
  words()       shlex words with redirects removed; each `$( )` or backtick
                group becomes one placeholder word whose text is kept, so
                expand.py can evaluate it.

Standard library only.
"""

from __future__ import annotations

import re
import shlex
from typing import Dict, List, NamedTuple, Optional, Tuple

SUBST = "\x00S{}\x00"
PROC_MARK = "\x04"  # a substitution body that came from `<( )`
SUBST_RE = re.compile(r"\x00S(\d+)\x00")
_IFS_RE = re.compile(r"\$\{IFS\}|\$IFS\b")
_ANSI_RE = re.compile(r"\$'((?:\\.|[^'\\])*)'")
_HEREDOC_RE = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][\w.-]*)\1")
_REDIRECT_RE = re.compile(r"^(?:\d*|&)(?:>>?|<<?<?|>&|<&|&>>?)(?:&?\d+|-)?$")
_REDIRECT_JOINED_RE = re.compile(r"^(?:\d*|&)(?:>>?|<|&>>?)(?!&)(.+)$")
_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "e": "\x1b", "a": "\a",
            "b": "\b", "f": "\f", "v": "\v", "\\": "\\", "'": "'", '"': '"'}


class Command(NamedTuple):
  text: str
  heredoc: Optional[str] = None  # body of a `<<WORD` opened by this command
  heredoc_expands: bool = False  # the `<<WORD` was unquoted: $( ) run in it


class Words(NamedTuple):
  argv: List[str]
  substs: List[str]  # bodies of `$( )` / backtick groups, by placeholder
  writes: List[str]  # targets of `>` and `>>` redirects
  herestring: Optional[str] = None  # the word after `<<<`
  stdin: Optional[str] = None  # the file after `<`


Pipeline = List[Command]


def _decode_ansi(body: str) -> str:
  out, i = [], 0
  while i < len(body):
    ch = body[i]
    if ch != "\\" or i + 1 >= len(body):
      out.append(ch)
      i += 1
      continue
    nxt = body[i + 1]
    hexm = re.match(r"x([0-9a-fA-F]{1,2})", body[i + 1:])
    octm = re.match(r"([0-7]{1,3})", body[i + 1:])
    if hexm:
      out.append(chr(int(hexm.group(1), 16)))
      i += 1 + len(hexm.group(0))
    elif octm:
      out.append(chr(int(octm.group(1), 8)))
      i += 1 + len(octm.group(0))
    else:
      out.append(_ESCAPES.get(nxt, "\\" + nxt))
      i += 2
  return "".join(out)


def normalize(text: str) -> str:
  """Undoes `$IFS` word splitting and ANSI-C quoting."""
  text = _IFS_RE.sub(" ", text)
  return _ANSI_RE.sub(lambda m: shlex.quote(_decode_ansi(m.group(1))), text)


def _scan_quoted(text: str, i: int) -> int:
  """Index just past the quoted span starting at text[i]."""
  quote = text[i]
  j = i + 1
  while j < len(text):
    if quote == '"' and text[j] == "\\":
      j += 2
      continue
    if text[j] == quote:
      return j + 1
    j += 1
  return len(text)


def _scan_subst(text: str, i: int) -> int:
  """Index just past the `$(...)` starting at text[i] (balanced)."""
  depth, j = 0, i + 1
  while j < len(text):
    ch = text[j]
    if ch in "'\"":
      j = _scan_quoted(text, j)
      continue
    depth += {"(": 1, ")": -1}.get(ch, 0)
    j += 1
    if depth == 0:
      return j
  return len(text)


def _split_top(text: str) -> List[Tuple[str, str]]:
  """(chunk, separator-after) pairs, split outside quotes and `$( )`."""
  out, buf, i = [], [], 0
  while i < len(text):
    ch = text[i]
    if ch in "'\"`":
      end = _scan_quoted(text, i)
      buf.append(text[i:end])
      i = end
      continue
    if text.startswith("$(", i):
      end = _scan_subst(text, i)
      buf.append(text[i:end])
      i = end
      continue
    sep = next((s for s in ("&&", "||", ";", "\n", "|", "&")
                if text.startswith(s, i) and not _is_redirect_amp(text, i, s)),
               None)
    if sep is None:
      buf.append(ch)
      i += 1
      continue
    out.append(("".join(buf), sep))
    buf = []
    i += len(sep)
  out.append(("".join(buf), ""))
  return out


def _is_redirect_amp(text: str, i: int, sep: str) -> bool:
  """`2>&1`, `&>` and `>&` are redirects, not separators."""
  if sep not in ("&", "|"):
    return False
  before = text[i - 1] if i else ""
  after = text[i + 1] if i + 1 < len(text) else ""
  return before in "<>" or (sep == "&" and after == ">")


def _take_heredocs(
    lines: List[str]) -> Tuple[List[str], Dict[int, Tuple[str, bool]]]:
  """Removes heredoc bodies; maps the opening line's index to (body, expands).

  `expands` is True when the delimiter was unquoted (`<<EOF`): a bash heredoc
  then runs `$( )` and `${ }` in its body. A quoted delimiter (`<<'EOF'`,
  `<<"EOF"`) is literal.
  """
  kept, bodies, i = [], {}, 0
  while i < len(lines):
    line = lines[i]
    kept.append(line)
    i += 1
    m = _HEREDOC_RE.search(line)
    if not m:
      continue
    body = []
    while i < len(lines) and lines[i].strip() != m.group(2):
      body.append(lines[i])
      i += 1
    i += 1  # the terminator line
    bodies[len(kept) - 1] = ("\n".join(body) + "\n", not m.group(1))
  return kept, bodies


def pipelines(text: str) -> List[Pipeline]:
  """Every pipeline in `text`, each a list of commands, in order."""
  text = normalize(text.replace("\\\n", " "))
  lines, bodies = _take_heredocs(text.split("\n"))
  out: List[Pipeline] = []
  for idx, line in enumerate(lines):
    current: Pipeline = []
    for chunk, sep in _split_top(line):
      pair = bodies.get(idx) if _HEREDOC_RE.search(chunk) else None
      body, expands = pair if pair else (None, False)
      if chunk.strip():
        current.append(Command(chunk.strip(), body, expands))
      if sep != "|" and current:
        out.append(current)
        current = []
    if current:
      out.append(current)
  return out


def _placeholders(text: str) -> Tuple[str, List[str]]:
  """Replaces `$( )` and backtick groups with placeholder words."""
  inner: List[str] = []
  out, i = [], 0
  while i < len(text):
    if text.startswith(("$(", "<("), i) and not text.startswith("$((", i):
      end = _scan_subst(text, i)
      mark = PROC_MARK if text[i] == "<" else ""
      inner.append(mark + text[i + 2:end - 1])
      out.append(SUBST.format(len(inner) - 1))
      i = end
    elif text[i] == "`":
      end = _scan_quoted(text, i)
      inner.append(text[i + 1:end - 1])
      out.append(SUBST.format(len(inner) - 1))
      i = end
    elif text[i] == "'":
      end = _scan_quoted(text, i)
      out.append(text[i:end])
      i = end
    else:
      out.append(text[i])
      i += 1
  return "".join(out), inner


_ARGV_BRACE_RE = re.compile(r"\{([^{}]*,[^{}]*)\}")


def _brace_words(token: str) -> List[str]:
  """`{rm,-rf,/}` -> [rm, -rf, /]; `a{b,c}` -> [ab, ac]; one comma group."""
  m = _ARGV_BRACE_RE.search(token)
  if not m:
    return [token]
  out: List[str] = []
  for option in m.group(1).split(","):
    out.extend(_brace_words(token[:m.start()] + option + token[m.end():]))
  return out


def _expand_argv(argv: List[str]) -> List[str]:
  """Brace expansion over words, with the empty words bash would drop.

  `{,rm} -rf /` becomes `rm -rf /`: brace expansion runs before word
  splitting, an unquoted empty expansion leaves no word behind. A group that
  held `$IFS` (already a space here) splits again into words, as bash does
  after it expands `rm{,$IFS-rf$IFS/}`.
  """
  out: List[str] = []
  for tok in argv:
    expanded = _brace_words(tok)
    if expanded == [tok]:
      out.append(tok)  # no brace: a quoted space in the word is kept
    else:
      out.extend(p for w in expanded for p in w.split() if p)
  return out


def substitutions(text: str) -> List[str]:
  """Bodies of every `$( )`, `<( )` and backtick group in `text`.

  Used to scan an unquoted heredoc body, where these run. A `<( )` body keeps
  its PROC_MARK so callers can tell process substitution apart.
  """
  _, inner = _placeholders(text)
  return inner


def _split_redirects(tokens: List[str]):
  """(argv, write targets): `>`, `2>&1`, `>out`, `2>/dev/null` removed."""
  argv: List[str] = []
  writes: List[str] = []
  here: List[str] = []
  stdin: List[str] = []
  pending = ""
  for tok in tokens:
    if pending:
      if ">" in pending:
        writes.append(tok)
      elif pending == "<<<":
        here.append(tok)
      elif pending.endswith("<") and not pending.startswith("<<"):
        stdin.append(tok)
      pending = ""
      continue
    if len(tok) > 3 and tok.startswith("<<<"):  # `<<<'word'` joined by shlex
      here.append(tok[3:])
      continue
    if _REDIRECT_RE.match(tok):
      if not tok.endswith(("&1", "&2", "-")):
        pending = tok
      continue
    joined = _REDIRECT_JOINED_RE.match(tok)
    if joined and not tok.startswith("-"):
      if ">" in tok.split(joined.group(1))[0]:
        writes.append(joined.group(1))
      continue
    argv.append(tok)
  return argv, writes, (here[0] if here else None), \
      (stdin[0] if stdin else None)


def restore(text: str, substs: List[str]) -> str:
  """Puts `$( )` back where words() left placeholders, for re-parsing."""
  def back(m: "re.Match[str]") -> str:
    i = int(m.group(1))
    if i >= len(substs):
      return ""
    body = substs[i]
    if body.startswith(PROC_MARK):
      return f"<({body[len(PROC_MARK):]})"
    return f"$({body})"
  return SUBST_RE.sub(back, text)


def plain(argv: List[str], substs: List[str]) -> str:
  """Words joined the way eval and `cmd /c` join them."""
  return restore(" ".join(argv), substs)


def quoted(argv: List[str], substs: List[str]) -> str:
  """Words as an argv a shell would rebuild (`docker run img sh -c ...`)."""
  out = []
  for word in argv:
    if SUBST_RE.search(word):
      out.append('"' + restore(word, substs) + '"')
    else:
      out.append(shlex.quote(word))
  return " ".join(out)


def _unescape_dquoted(text: str) -> str:
  """Inside "...", bash turns \\$ and \\` into $ and `; shlex does not."""
  out, i = [], 0
  while i < len(text):
    if text[i] == '"':
      end = _scan_quoted(text, i)
      out.append(text[i:end].replace("\\$", "$").replace("\\`", "`"))
      i = end
    elif text[i] == "'":
      end = _scan_quoted(text, i)
      out.append(text[i:end])
      i = end
    else:
      out.append(text[i])
      i += 1
  return "".join(out)


def words(command: str) -> Words:
  """argv, substitution bodies and write targets of one simple command."""
  text, inner = _placeholders(_unescape_dquoted(command))
  text = _HEREDOC_RE.sub(" ", text)
  try:
    lexer = shlex.shlex(text, posix=True)
    lexer.whitespace_split = True
    lexer.commenters = "#"
    tokens = list(lexer)
  except ValueError:
    tokens = text.split()
  argv, writes, here, stdin = _split_redirects(tokens)
  return Words(_expand_argv(argv), inner, writes, here, stdin)
