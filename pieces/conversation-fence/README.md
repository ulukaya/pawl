# Conversation fence

One piece of the CoS blueprint, published on its own so you can run it in ten
minutes. Three files, standard library only, no dependency on the rest of the
system.

## What it does

Asks the user (`force_ask`) before a tool call reads another conversation's
local data. Under each store root it fences:

-   `brain/<id>/...`: another conversation's brain directory;
-   `conversations/<id>.*`: another conversation's transcript;
-   `brain/` or `conversations/` itself: a listing or sweep of all of them;
-   `conversation_summaries.db`: every conversation's title and summary.

This conversation and its direct parent or child pass without a prompt. The
lineage lookup opens `conversation_summaries.db` read-only (`mode=ro`, 0.5 s
timeout) and reads only `conversation_id` and `parent_conversation_id`; a
failed lookup counts as not lineage.

Paths come from every string argument, and from every shell word with quotes
removed, `~` expanded, `--flag=` values split and relative paths joined to the
call's `Cwd`. Symlinks are followed.

## The problem it exists for

Jetski keeps every conversation's transcript, artifacts and scratch files on
local disk. An agent in one conversation can read a sibling's private context
with a plain `view_file`. The fence keeps each conversation's context its own
unless the user says otherwise.

## Files

| File | Purpose |
|---|---|
| `conversation_fence.py` | Path candidates, store match, lineage lookup, decision, log. |
| `conversation_fence_hook.py` | PreToolUse hook. |
| `test_conversation_fence.py` | 25 tests: lineage, sweeps, path shapes, read-only lookup, switches. |

## In the pawl plugin

You do not wire this piece yourself there: `hooks/pawl.py` runs it as gate
`fence` in Antigravity, Claude Code and Codex, and `hooks/harness.py`
translates each harness's payload and answer, so the piece only ever sees
the Antigravity shape below. `PAWL_DISABLE=fence` turns it off for a
session. The rest of this page is for running the piece on its own.

## Configuration

| Variable | Meaning | Default |
|---|---|---|
| `PAWL_DATA` | Where hit rows are appended | `~/.pawl` |
| `PAWL_CONVERSATION_ROOTS` | Colon-separated store roots | `~/.gemini/antigravity:~/.gemini/jetski:~/.antigravity:~/.jetski` |
| `PAWL_CONVERSATION_FENCE_STRICT` | `1` turns the prompt into a deny | unset |
| `PAWL_CONVERSATION_FENCE_OFF` | `1` turns the piece off | unset |
| `PAWL_CONVERSATION_FENCE_WATCHDOG_S` | Seconds before the hook fails open | 14 |

## Limits

The fence reads tool arguments, so a script that builds the path at run time
is not fenced. It stops casual reads and broad sweeps; it is not a sandbox.
