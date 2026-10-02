# pawl-zero-width-sanitizer

CLI:

```
python3 -B <root>/pieces/zero-width-sanitizer/zero_width_sanitizer.py
```

Gate `zero-width` of `hooks/pawl.py`, on writes and edits. Strips zero width
space, non-joiner, joiner (outside emoji), byte order mark, word joiner and
soft hyphen from written and replaced text (Claude Code `Write`/`Edit`
content, Codex `apply_patch` body lines) and rewrites the call's input so
the write still lands: `overwrite` on Antigravity, `updatedInput` on Claude
Code and Codex. Paths and other fields are untouched. Never blocks; fails
open.

## Commands

| Invocation | Effect |
|---|---|
| `strip` | copy stdin to stdout with the characters removed |

## Environment

-   `PAWL_ZERO_WIDTH_WATCHDOG_S`: watchdog seconds before fail-open
    (default 14)

## Test

```bash
cd <root>/pieces/zero-width-sanitizer && python3 -B -m pytest -q .
```

Expected: every test passes.
