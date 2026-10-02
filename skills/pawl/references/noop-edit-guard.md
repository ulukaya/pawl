# pawl-noop-edit-guard

CLI:

```
python3 -B ${PLUGIN_ROOT}/pieces/noop-edit-guard/noop_edit_guard.py
```

Hook: `hooks/pawl_noop_edit_hook.py` on
`replace_file_content|multi_replace_file_content`. Denies a
`replace_file_content` whose `ReplacementContent` equals its `TargetContent`,
and a `multi_replace_file_content` where every chunk is such a no-op. Equality
is exact; a whitespace change is a real edit. Fails open.

On `[PAWL no-op]`: `view_file` the region you meant to change, then send the
edit with the new text. Do not resend the same pair with another anchor.

## Commands

| Invocation | Effect |
|---|---|
| `check <tool> <json-args>` | print allow, exit 0; or the reason, exit 1 |

## Environment

-   `PAWL_DATA`: denials.jsonl dir (default ~/.pawl)
-   `PAWL_NOOP_EDIT_WATCHDOG_S`: watchdog seconds before fail-open
    (default 14)

## Test

```bash
cd ${PLUGIN_ROOT}/pieces/noop-edit-guard && python3 -B -m pytest -q .
```

Expected: `24 passed`.
