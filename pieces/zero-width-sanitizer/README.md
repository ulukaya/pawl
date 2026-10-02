# Zero-width sanitizer

One piece of the CoS blueprint, published on its own so you can run it in ten
minutes. Three files, standard library only, no dependency on the rest of the
system.

## What it does

Strips zero width space (U+200B), non-joiner (U+200C), joiner (U+200D outside
emoji sequences), byte order mark (U+FEFF), word joiner (U+2060) and soft
hyphen (U+00AD) from content fields of `write_to_file` (`CodeContent`),
`replace_file_content` (`TargetContent`, `ReplacementContent`) and
`multi_replace_file_content` (`ReplacementChunks`). When something was
stripped the hook answers with an overwrite block holding the full argument
object, cleaned:

```json
{"decision": "allow", "overwrite": {"TargetFile": "...", "CodeContent": "..."}}
```

Otherwise it answers `{"decision": "allow"}`. It never blocks.

## The problem it exists for

An invisible character in an edit target makes the match fail against a clean
file, and the agent then rewrites a file that was already correct.

## Files

| File | Purpose |
|---|---|
| `zero_width_sanitizer.py` | Pattern, `sanitize()`, overwrite block, watchdog, CLI. |
| `zero_width_sanitizer_hook.py` | PreToolUse hook. |
| `test_zero_width_sanitizer.py` | 27 tests: each character, untouched clean twins, other fields and tools, hook shapes. |

## Run it

```bash
python3 -m pytest -q .
python3 zero_width_sanitizer.py strip < draft.txt > clean.txt
```

## Wire it as a hook

```json
{
  "zero-width-sanitizer": {
    "enabled": true,
    "PreToolUse": [
      {
        "matcher": "write_to_file|replace_file_content",
        "hooks": [
          {"type": "command", "command": "python3 /path/to/zero_width_sanitizer_hook.py", "timeout": 15}
        ]
      }
    ]
  }
}
```

`overwrite_block()` in `zero_width_sanitizer.py` is the one function to adapt
if a host expects a different shape for rewritten arguments.

## Configuration

| Variable | Meaning | Default |
|---|---|---|
| `PAWL_ZERO_WIDTH_WATCHDOG_S` | Seconds before the hook fails open | 14 |

## Design notes

-   Only content fields are cleaned; paths and instructions are never touched.
-   The zero width joiner inside emoji sequences is preserved; isolated
    joiners outside emoji are stripped.
-   The pattern is a raw string so the source file itself carries no
    invisible characters.
