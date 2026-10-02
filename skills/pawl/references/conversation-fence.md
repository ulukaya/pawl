# pawl-conversation-fence

Gate `fence` of `hooks/pawl.py`, on every tool. Asks before a call whose
arguments read another conversation's files, a sweep of every
conversation, or an index of them all:

| Harness | Fenced |
|---|---|
| Antigravity | `brain/<id>/`, `conversations/<id>.*`, `conversation_summaries.db` |
| Claude Code | `projects/<project>/<id>.jsonl` and `<id>/`, `file-history/<id>/`, `history.jsonl` |
| Codex | `sessions/**/rollout-*-<id>.jsonl`, `archived_sessions/`, `history.jsonl` |

This conversation passes; on Antigravity so do its direct parent and child
(from `conversation_summaries.db`, opened read-only). Claude Code's shared
`projects/<project>/memory/` is never fenced. A glob in place of an id is a
sweep. Fails open.

On `[PAWL fence]`: read another conversation's files only when the user asked
for it. The fence reads tool arguments; it is not a sandbox.

## Commands

| Invocation | Effect |
|---|---|
| (hook only) | `conversation_fence_hook.py` reads one Antigravity payload |

## Environment

-   `PAWL_DATA`: denials.jsonl dir (default ~/.pawl); rows carry the argument
    sha1, never the path
-   `PAWL_CONVERSATION_ROOTS`: colon-separated Antigravity store roots
    (default `~/.gemini/antigravity`, `~/.gemini/jetski`, `~/.antigravity`,
    `~/.jetski`)
-   `CLAUDE_CONFIG_DIR`: Claude Code's store (default `~/.claude`)
-   `CODEX_HOME`: Codex's store (default `~/.codex`)
-   `PAWL_CONVERSATION_FENCE_STRICT`: `1` turns the prompt into a deny
-   `PAWL_CONVERSATION_FENCE_OFF`: `1` turns the piece off
-   `PAWL_CONVERSATION_FENCE_WATCHDOG_S`: watchdog seconds before fail-open
    (default 14)

## Test

```bash
cd <root>/pieces/conversation-fence && python3 -B -m pytest -q .
```

Expected: every test passes.
