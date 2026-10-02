# pawl-conversation-fence

Hook: `hooks/pawl_fence_hook.py` on every tool. Returns `force_ask` before a
call whose arguments read, under a store root, another conversation's
`brain/<id>/`, its `conversations/<id>.*` transcript, a listing or sweep of
`brain/` or `conversations/`, or `conversation_summaries.db`. This
conversation and its direct parent or child pass; lineage comes from
`conversation_summaries.db` opened read-only, and a failed lookup counts as
not lineage. Fails open.

On `[PAWL fence]`: read another conversation's files only when the user asked
for it. The fence reads tool arguments; it is not a sandbox.

## Commands

| Invocation | Effect |
|---|---|
| (hook only) | `conversation_fence_hook.py` reads one payload on stdin |

## Environment

-   `PAWL_DATA`: denials.jsonl dir (default ~/.pawl); rows carry the argument
    sha1, never the path
-   `PAWL_CONVERSATION_ROOTS`: colon-separated store roots (default
    `~/.gemini/antigravity`, `~/.gemini/jetski`, `~/.antigravity`,
    `~/.jetski`)
-   `PAWL_CONVERSATION_FENCE_STRICT`: `1` turns the prompt into a deny
-   `PAWL_CONVERSATION_FENCE_OFF`: `1` turns the piece off
-   `PAWL_CONVERSATION_FENCE_WATCHDOG_S`: watchdog seconds before fail-open
    (default 14)

## Test

```bash
cd ${PLUGIN_ROOT}/pieces/conversation-fence && python3 -B -m pytest -q .
```

Expected: `25 passed`.
