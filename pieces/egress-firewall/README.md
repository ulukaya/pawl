# Egress firewall

One piece of the CoS blueprint, published on its own so you can run it in ten
minutes. Three files, standard library only, no dependency on the rest of the
system.

## What it does

Scans outbound text before it leaves the machine. The text is normalized first
(HTML entities decoded, Unicode NFKC, zero-width characters stripped, common
Cyrillic and Greek look-alikes folded to ASCII), then matched against a JSON
rules file with four rule classes:

- `literals`: plain deny strings, case-insensitive.
- `regexes`: named deny patterns.
-   `paths`: named patterns for local filesystem paths (home directories, hidden
    config dirs like `~/.something/`, `/tmp/` state).
-   `email`: one address pattern plus an allowlist of addresses or domains. Any
    match not on the allowlist is a hit.

`check` exits 0 when clean, 1 with one line per hit, 2 on any internal error.
`hook` wraps the same scan as a PreToolUse hook and prints an allow or deny
decision. Both fail closed: a missing rules file, a bad regex, or an unreadable
stdin blocks the send.

## The problem it exists for

An agent that drafts your messages has read things you would never paste into
one: local paths, config directories, hostnames, tokens, other people's
addresses, its own reasoning tags. Most of the time it knows not to send them.
Sometimes it sends them inside an HTML entity, behind a zero-width space, or
with one Cyrillic letter swapped in, and a naive substring check waves it
through. The fix is a deterministic scan that normalizes before it matches,
reads its rules from a file you own, and refuses to send when it cannot run.

## Files

| File | Purpose |
|---|---|
| `egress_firewall.py` | Normalizer, rules loader, scanner, and the `check`, `hook`, `init-rules` subcommands. |
| `test_egress_firewall.py` | 16 `unittest` cases against a real temp rules file: each rule class, three obfuscation styles, both exit-2 paths, hook allow and deny, hook deny on internal error, example rules parse and load. |
| `README.md` | This file, including the example rules. |

## Run it

```bash
python3 -m unittest test_egress_firewall -v
python3 egress_firewall.py init-rules > rules.json
printf 'see db01.internal.example for the dump' | python3 egress_firewall.py check --rules rules.json
echo "exit $?"
```

Expected on the third line: `DENY literal .internal.example at offset 8:
.internal.example fo` and `exit 1`.

Hit lines follow one shape: `DENY <class> <rule> at offset N: <20 chars
context>`. Offsets and context refer to the normalized text, so an
entity-encoded hit is reported as what it decodes to.

`init-rules` prints this. Replace the placeholders with your own hostnames,
paths, and domains.

```json
{
  "literals": [
    ".internal.example",
    ".corp.example",
    "DO NOT FORWARD"
  ],
  "regexes": [
    {"name": "long-token", "pattern": "[A-Za-z0-9+/_-]{40,}={0,2}"},
    {"name": "reasoning-tag", "pattern": "<\\s*/?\\s*(thought|reasoning|scratchpad)\\s*>"}
  ],
  "paths": [
    {"name": "hidden-config-dir", "pattern": "~/\\.[A-Za-z0-9_.-]+/"},
    {"name": "home-dir", "pattern": "/(home|Users)/[A-Za-z0-9_.-]+/"},
    {"name": "tmp-state", "pattern": "/tmp/[A-Za-z0-9_.-]+"}
  ],
  "email": {
    "pattern": "[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}",
    "allow": ["example.com", "support@example.org"]
  }
}
```

Regex and path entries may also be bare strings; the pattern then doubles as the
rule name. All patterns compile with `re.IGNORECASE`.

## In the pawl plugin

You do not wire this piece yourself there: gate `send` of `hooks/pawl.py`
(`hooks/send_gates.py`) scans the outbound payload of every send, before the
prose gate and the budget, in Antigravity, Claude Code and Codex.
`PAWL_DISABLE=egress` skips it for a session, `PAWL_DISABLE=send` all three. The
rest of this page is for running the piece on its own.

## Wire it

Add a PreToolUse group to your harness hook config (adjust both paths):

```json
{
  "matcher": "run_command",
  "hooks": [
    {"type": "command", "command": "python3 /path/egress_firewall.py hook --rules /path/rules.json", "timeout": 15}
  ]
}
```

The hook reads the tool call JSON on stdin and looks for the outbound text in
this order: the dotted path given by `--field` (for example `--field
toolCall.args.message`), then the `message`, `body`, or `text` field of the call
args, then the whole command line. If none is present it allows; there is
nothing outbound to scan. It prints `{"decision": "allow"}` or `{"decision":
"deny", "reason": "[EGRESS FIREWALL] ..."}` with up to five hit lines in the
reason.

This is the one hook that must fail closed. On any internal error (missing
rules, bad regex, stdin that is not JSON) it prints a deny with the error text
instead of an allow. A rate limiter or a linter can fail open without harm; a
leak filter that fails open is not a filter.

## Design notes

-   Fail closed. `check` exits 2 and `hook` denies on every error path,
    including exceptions the code did not anticipate. The cost is a blocked send
    when the rules file is missing. The cost of the alternative is a leak you
    find out about later.
-   Normalize before match. HTML unescape, then NFKC, then drop category `Cf`
    code points, then fold a fixed homoglyph table. Rules are normalized the
    same way, so a literal you typed with a fullwidth character still matches.
-   Rules live in data, not code. The script ships no hostnames or paths of its
    own. Everything it denies comes from the JSON file, which you can version,
    diff, and review like any other config.
-   Offsets and context come from the normalized text. That is the string that
    was matched, and the 20 characters after the hit tell you what tripped it
    without printing the whole payload.
-   What it does not catch: content described rather than quoted (a paraphrased
    secret), values split across two messages, images, and look-alikes outside
    the fixed homoglyph table. It is a deterministic last line, not a
    classifier. Pair it with a send budget and a human read for anything that
    matters.
