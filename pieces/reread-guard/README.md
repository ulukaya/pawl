# Reread guard

One piece of the CoS blueprint, published on its own so you can run it in ten
minutes. Four files, standard library only, no dependency on the rest of the
system.

## What it does

Counts two read shapes per conversation and user turn and denies past a limit
ordinary turns never reach:

-   the 11th unbounded read of this conversation's own `transcript*.jsonl`;
-   the 6th read of the same unchanged `SKILL.md`, or of the same memory file
    (`MEMORY.md`, `GEMINI.md`, `AGENTS.md`, `CLAUDE.md`, `*.md` under
    `memory/`) read from the top.

Bounded reads never count: a `view_file` spanning under 10 lines, `head` or
`tail` capped at 10 lines or 20000 bytes, `sed -n` over at most 10 lines, and
counts (`wc`, `rg -c`, `grep -c`), including a read piped into one of those.
Other conversations' transcripts never count. A repeat is keyed by path and
mtime, so an edit starts a fresh count. After three denials in one turn the
guard allows everything until the next turn, so it cannot become its own loop.

The turn key is the payload's `turnId`, else the number of `USER_INPUT` lines
in its `transcriptPath`, read only for calls the rules count.

## The problem it exists for

After a context truncation an agent often rebuilds what it lost by dumping its
own transcript and re-reading skill files it already read. Each dump refills
the context and brings the next truncation closer. In one observed user turn
of about 2.5 hours, 123 of 310 tool calls were such recovery reads.

## Files

| File | Purpose |
|---|---|
| `reread_guard.py` | Classification, per-turn state, decision, denial log. |
| `reread_shapes.py` | What a `view_file` or shell command reads, and whether it is bounded. |
| `reread_guard_hook.py` | PreToolUse hook. |
| `test_reread_guard.py` | 19 tests: each limit at its edge, bounded twins, turns, stand-down. |

## In the pawl plugin

You do not wire this piece yourself there: `hooks/pawl.py` runs it as gate
`reread` in Antigravity, Claude Code and Codex, and `hooks/harness.py`
translates each harness's payload and answer, so the piece only ever sees
the Antigravity shape below. `PAWL_DISABLE=reread` turns it off for a
session. The rest of this page is for running the piece on its own.

## Configuration

| Variable | Meaning | Default |
|---|---|---|
| `PAWL_DATA` | State under `reread/`, and `denials.jsonl` | `~/.pawl` |
| `PAWL_REREAD_GUARD_OFF` | `1` turns the piece off | unset |
| `PAWL_REREAD_WATCHDOG_S` | Seconds before the hook fails open | 14 |

## Design notes

-   Behavior only: no model or tier check, so every model and effort level
    sees the same limits.
-   Fails open on bad stdin, a missing conversation id, an unwritable state
    dir, an internal error and the watchdog.
