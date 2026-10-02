# No-op edit guard

One piece of the CoS blueprint, published on its own so you can run it in ten
minutes. Three files, standard library only, no dependency on the rest of the
system.

## What it does

Denies a `replace_file_content` whose `ReplacementContent` equals its
`TargetContent`, and a `multi_replace_file_content` where every chunk is such
a no-op. Equality is exact, so a whitespace change is a real edit. The reason
starts with `[PAWL no-op]` and sends the agent back to `view_file`. Each deny
lands as one row in `PAWL_DATA/denials.jsonl` with gate `NOOP_EDIT` and the
sha1 of the target path.

## The problem it exists for

A zero-diff edit costs a tool call and a round of context and leaves the file
as it was; the agent then resends it with a different anchor. Refusing the
first one sends it back to the step it skipped: reading the file.

## Files

| File | Purpose |
|---|---|
| `noop_edit_guard.py` | Rule, payload reader, denial log, watchdog, CLI. |
| `noop_edit_guard_hook.py` | PreToolUse hook; prints allow or deny. |
| `test_noop_edit_guard.py` | 24 tests: each deny next to its allow twin, hook fail-open paths, CLI exit codes. |

## Run it

```bash
python3 -m pytest -q .
python3 noop_edit_guard.py check replace_file_content '{"TargetContent": "a", "ReplacementContent": "a"}'
```

## Wire it as a hook

```json
{
  "noop-edit-guard": {
    "enabled": true,
    "PreToolUse": [
      {
        "matcher": "replace_file_content|multi_replace_file_content",
        "hooks": [
          {"type": "command", "command": "python3 /path/to/noop_edit_guard_hook.py", "timeout": 15}
        ]
      }
    ]
  }
}
```

## Configuration

| Variable | Meaning | Default |
|---|---|---|
| `PAWL_DATA` | Where `denials.jsonl` is appended | `~/.pawl` |
| `PAWL_NOOP_EDIT_WATCHDOG_S` | Seconds before the hook fails open | 14 |

## Design notes

-   Fail open everywhere: bad stdin, an unexpected argument shape, an
    unwritable log, a watchdog timeout.
-   A multi-chunk edit with one real change goes through untouched.
