# pawl-reread-guard

Hook: `hooks/pawl_reread_hook.py` on
`view_file|run_command|run_shell_command`. Counts per conversation and user
turn, and denies the 11th unbounded read of this conversation's own
`transcript*.jsonl`, or the 6th read of the same unchanged `SKILL.md` or
memory file (`MEMORY.md`, `GEMINI.md`, `AGENTS.md`, `CLAUDE.md`, `*.md` under
`memory/`) read from the top. After three denials in one turn it allows
everything until the next turn. Fails open.

Bounded reads never count: `view_file` under 10 lines, `head`/`tail` at most
10 lines or 20000 bytes, `sed -n` over at most 10 lines, `wc`, `rg -c`,
`grep -c`, or any read piped into one of those.

On `[PAWL reread]`: keep recovery notes in one scratch file, read it once,
answer from what you already have, and name any fact that is still missing.

## Commands

| Invocation | Effect |
|---|---|
| (hook only) | `reread_guard_hook.py` reads one payload on stdin |

## Environment

-   `PAWL_DATA`: state under PAWL_DATA/reread/ and denials.jsonl (default
    ~/.pawl)
-   `PAWL_REREAD_GUARD_OFF`: `1` turns the piece off
-   `PAWL_REREAD_WATCHDOG_S`: watchdog seconds before fail-open (default 14)

## Test

```bash
cd ${PLUGIN_ROOT}/pieces/reread-guard && python3 -B -m pytest -q .
```

Expected: `19 passed`.
