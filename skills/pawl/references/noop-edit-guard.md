# pawl-noop-edit-guard

CLI:

```
python3 -B <root>/pieces/noop-edit-guard/noop_edit_guard.py
```

Gate `noop` of `hooks/pawl.py`, on edits. Denies a `replace_file_content`
(Claude Code: `Edit`) whose replacement equals its target, a
`multi_replace_file_content` where every chunk is such a no-op, and a Codex
`apply_patch` that only updates files and whose every hunk puts back the
lines it takes out. Equality is exact; a whitespace change is a real edit.
Fails open.

On `[PAWL no-op]`: re-read the region you meant to change, then send the
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
cd <root>/pieces/noop-edit-guard && python3 -B -m pytest -q .
```

Expected: every test passes.
