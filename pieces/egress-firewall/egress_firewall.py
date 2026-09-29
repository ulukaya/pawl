#!/usr/bin/env python3
"""egress_firewall.py: fail-closed content scan for outbound text.

Three subcommands:

    egress_firewall.py check [--rules rules.json] < payload.txt
    egress_firewall.py hook  [--rules rules.json] [--field a.b.c] < toolcall.json
    egress_firewall.py init-rules > rules.json

`check` normalizes the payload (HTML unescape, NFKC, zero-width strip, homoglyph fold),
then applies the rules file: literal deny strings (case-insensitive), regex deny patterns,
path patterns, and an optional email pattern behind an allowlist.

Exit codes for `check`:
    0  clean
    1  one or more hits, each printed as
       DENY <class> <rule> at offset N: <20 chars context>
    2  internal error (missing rules file, bad regex, unreadable stdin)

`hook` prints {"decision": "allow"} or {"decision": "deny", "reason": "..."}. Any
internal error is a deny. This is the one hook that must fail closed: a broken firewall
blocks the send, it never lets it through.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import unicodedata
from typing import Any, Dict, List, Tuple

DEFAULT_RULES = "rules.json"
CONTEXT_CHARS = 20

# Common confusables folded to their ASCII look-alike. Fullwidth and most compatibility
# forms are already handled by NFKC; this table covers Cyrillic and Greek letters that
# NFKC leaves alone because they are distinct code points, not compatibility variants.
HOMOGLYPHS = {
    # Cyrillic lowercase
    "\u0430": "a", "\u0435": "e", "\u043e": "o", "\u0440": "p", "\u0441": "c",
    "\u0445": "x", "\u0443": "y", "\u0456": "i", "\u0458": "j", "\u0455": "s",
    "\u0501": "d", "\u051b": "q", "\u051d": "w", "\u04bb": "h", "\u0433": "r",
    "\u043a": "k", "\u043c": "m", "\u043d": "h", "\u0442": "t", "\u0432": "b",
    # Cyrillic uppercase
    "\u0410": "A", "\u0412": "B", "\u0415": "E", "\u041a": "K", "\u041c": "M",
    "\u041d": "H", "\u041e": "O", "\u0420": "P", "\u0421": "C", "\u0422": "T",
    "\u0425": "X", "\u0406": "I", "\u0408": "J", "\u0405": "S",
    # Greek
    "\u03bf": "o", "\u03b1": "a", "\u03b5": "e", "\u03b9": "i", "\u03ba": "k",
    "\u03bd": "v", "\u03c1": "p", "\u03c4": "t", "\u03c5": "u", "\u03c7": "x",
    "\u0391": "A", "\u0392": "B", "\u0395": "E", "\u0397": "H", "\u0399": "I",
    "\u039a": "K", "\u039c": "M", "\u039d": "N", "\u039f": "O", "\u03a1": "P",
    "\u03a4": "T", "\u03a5": "Y", "\u03a7": "X", "\u0396": "Z",
}
_HOMOGLYPH_TABLE = str.maketrans(HOMOGLYPHS)

EXAMPLE_RULES: Dict[str, Any] = {
    "literals": [
        ".internal.example",
        ".corp.example",
        "DO NOT FORWARD",
    ],
    "regexes": [
        {"name": "long-token", "pattern": "[A-Za-z0-9+/_-]{40,}={0,2}"},
        {"name": "reasoning-tag", "pattern": "<\\s*/?\\s*(thought|reasoning|scratchpad)\\s*>"},
    ],
    "paths": [
        {"name": "hidden-config-dir", "pattern": "~/\\.[A-Za-z0-9_.-]+/"},
        {"name": "home-dir", "pattern": "/(home|Users)/[A-Za-z0-9_.-]+/"},
        {"name": "tmp-state", "pattern": "/tmp/[A-Za-z0-9_.-]+"},
    ],
    "email": {
        "pattern": "[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}",
        "allow": ["example.com", "support@example.org"],
    },
}


class FirewallError(Exception):
    """Any internal failure. Callers turn this into exit 2 or a hook deny."""


# ---------------------------------------------------------------- normalization

def normalize(text: str) -> str:
    text = html.unescape(text)
    text = unicodedata.normalize("NFKC", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Cf")
    text = text.translate(_HOMOGLYPH_TABLE)
    return text


# ---------------------------------------------------------------- rules

def _named(entries: Any, cls: str) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    if entries is None:
        return out
    if not isinstance(entries, list):
        raise FirewallError(f"rules.{cls} must be a list")
    for e in entries:
        if isinstance(e, str):
            if e:
                out.append((e, e))
        elif isinstance(e, dict) and isinstance(e.get("pattern"), str):
            out.append((str(e.get("name") or e["pattern"]), e["pattern"]))
        else:
            raise FirewallError(f"rules.{cls} entry is not a string or {{name, pattern}}: {e!r}")
    return out


def _compile(name: str, pattern: str, cls: str) -> Tuple[str, "re.Pattern[str]"]:
    try:
        return name, re.compile(pattern, re.IGNORECASE)
    except re.error as exc:
        raise FirewallError(f"bad regex in rules.{cls} '{name}': {exc}") from exc


def load_rules(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except OSError as exc:
        raise FirewallError(f"cannot read rules file {path}: {exc}") from exc
    except ValueError as exc:
        raise FirewallError(f"rules file {path} is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise FirewallError(f"rules file {path} must contain a JSON object")

    rules: Dict[str, Any] = {
        "literals": [normalize(s).lower() for s in (raw.get("literals") or []) if isinstance(s, str) and s],
        "regexes": [_compile(n, p, "regexes") for n, p in _named(raw.get("regexes"), "regexes")],
        "paths": [_compile(n, p, "paths") for n, p in _named(raw.get("paths"), "paths")],
        "email": None,
    }
    email = raw.get("email")
    if email:
        if not isinstance(email, dict) or not isinstance(email.get("pattern"), str):
            raise FirewallError("rules.email must be {pattern, allow}")
        _, compiled = _compile("email", email["pattern"], "email")
        allow = [a.lower() for a in (email.get("allow") or []) if isinstance(a, str)]
        rules["email"] = (compiled, allow)
    return rules


# ---------------------------------------------------------------- scan

def _context(text: str, offset: int) -> str:
    snippet = text[offset:offset + CONTEXT_CHARS]
    return snippet.replace("\n", " ").replace("\r", " ")


def _email_allowed(addr: str, allow: List[str]) -> bool:
    addr = addr.lower()
    domain = addr.rsplit("@", 1)[-1]
    for a in allow:
        if a == addr or a == domain or domain.endswith("." + a.lstrip(".")) or (a.startswith(".") and domain.endswith(a)):
            return True
    return False


def scan(text: str, rules: Dict[str, Any]) -> List[str]:
    """Return one DENY line per hit against the normalized text. Empty list means clean."""
    norm = normalize(text)
    lowered = norm.lower()
    hits: List[str] = []

    for lit in rules["literals"]:
        idx = lowered.find(lit)
        if idx >= 0:
            hits.append(f"DENY literal {lit} at offset {idx}: {_context(norm, idx)}")

    for cls in ("regexes", "paths"):
        label = "regex" if cls == "regexes" else "path"
        for name, pat in rules[cls]:
            m = pat.search(norm)
            if m:
                hits.append(f"DENY {label} {name} at offset {m.start()}: {_context(norm, m.start())}")

    if rules["email"]:
        pat, allow = rules["email"]
        for m in pat.finditer(norm):
            if not _email_allowed(m.group(0), allow):
                hits.append(f"DENY email {m.group(0)} at offset {m.start()}: {_context(norm, m.start())}")

    return hits


# ---------------------------------------------------------------- hook helpers

def _walk(obj: Any, dotted: str) -> Any:
    cur = obj
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def extract_text(payload: Any, field: str | None = None) -> str:
    """Pick the outbound text out of a tool call payload.

    Order: the configured --field, then message/body/text in the call args, then the
    whole command line. Returns "" when nothing outbound is present.
    """
    if not isinstance(payload, dict):
        return ""
    if field:
        val = _walk(payload, field)
        if isinstance(val, str) and val:
            return val
    call = payload.get("toolCall") or payload.get("tool_call") or payload
    args = call.get("args") or call.get("arguments") or call.get("input") or {}
    if not isinstance(args, dict):
        args = {}
    for key in ("message", "body", "text"):
        val = args.get(key)
        if isinstance(val, str) and val:
            return val
    for key in ("CommandLine", "command_line", "command", "cmd"):
        val = args.get(key)
        if isinstance(val, str) and val:
            return val
    return ""


# ---------------------------------------------------------------- commands

def _read_stdin() -> str:
    try:
        return sys.stdin.read()
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        raise FirewallError(f"cannot read stdin: {exc}") from exc


def cmd_check(args: argparse.Namespace) -> int:
    try:
        rules = load_rules(args.rules)
        text = _read_stdin()
        hits = scan(text, rules)
    except FirewallError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # any surprise is still a block, never a pass
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    for line in hits:
        print(line)
    return 1 if hits else 0


def _emit(decision: str, reason: str = "") -> int:
    out: Dict[str, str] = {"decision": decision}
    if reason:
        out["reason"] = reason
    sys.stdout.write(json.dumps(out))
    sys.stdout.flush()
    return 0


def cmd_hook(args: argparse.Namespace) -> int:
    try:
        rules = load_rules(args.rules)
        raw = _read_stdin()
        payload = json.loads(raw or "{}")
        text = extract_text(payload, args.field)
        if not text:
            return _emit("allow")
        hits = scan(text, rules)
    except Exception as exc:
        return _emit("deny", f"[EGRESS FIREWALL] internal error, failing closed: {exc}")
    if not hits:
        return _emit("allow")
    shown = hits[:5]
    more = f" (+{len(hits) - len(shown)} more)" if len(hits) > len(shown) else ""
    return _emit("deny", "[EGRESS FIREWALL] " + "; ".join(shown) + more)


def cmd_init_rules(_: argparse.Namespace) -> int:
    print(json.dumps(EXAMPLE_RULES, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Fail-closed egress firewall for outbound text.")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="scan stdin, exit 0 clean / 1 hit / 2 error")
    c.add_argument("--rules", default=DEFAULT_RULES)
    c.set_defaults(func=cmd_check)

    h = sub.add_parser("hook", help="PreToolUse hook: tool call JSON on stdin, decision on stdout")
    h.add_argument("--rules", default=DEFAULT_RULES)
    h.add_argument("--field", default=None,
                   help="dotted JSON path to the outbound text, e.g. toolCall.args.message")
    h.set_defaults(func=cmd_hook)

    i = sub.add_parser("init-rules", help="print an example rules.json")
    i.set_defaults(func=cmd_init_rules)
    return p


def main(argv: List[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
