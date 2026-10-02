# pawl-zero-width-sanitizer

CLI:

```
python3 -B ${PLUGIN_ROOT}/pieces/zero-width-sanitizer/zero_width_sanitizer.py
```

Hook: `hooks/pawl_zero_width_hook.py` on `write_to_file|replace_file_content`.
Strips zero width space, non-joiner, joiner, byte order mark, word joiner and
soft hyphen from `CodeContent`, `TargetContent` and `ReplacementContent`, and
answers `{"decision": "allow", "overwrite": {...}}` with the full cleaned
arguments so the write still lands. Paths and other fields are untouched.
Never blocks; fails open.

## Commands

| Invocation | Effect |
|---|---|
| `strip` | copy stdin to stdout with the characters removed |

## Environment

-   `PAWL_ZERO_WIDTH_WATCHDOG_S`: watchdog seconds before fail-open
    (default 14)

## Test

```bash
cd ${PLUGIN_ROOT}/pieces/zero-width-sanitizer && python3 -B -m pytest -q .
```

Expected: `25 passed`.
