---
name: pawl-poll-loop-guard
description: "Prompt (force_ask) before a run_command that is an unbounded wait: a poll loop with sleep, tail -f or watch, or a bare sleep over 600 s. Use to classify a command by hand. Background tasks notify on completion, so waiting in a shell is never needed."
---

# pawl-poll-loop-guard

CLI:

```
python3 -B ${PLUGIN_ROOT}/pieces/poll-loop-guard/poll_loop_guard.py
```

## Commands

| Invocation | Effect |
|---|---|
| `classify <command...>` | print LOOP, TAIL, SLEEP or none; exit 0 |
| `check <command...>` | print bounded, exit 0; or the deny reason, exit 1 |

## Environment

-   `PAWL_DATA`: denials.jsonl dir (default ~/.pawl)
-   `PAWL_POLL_LOOP_GUARD_WATCHDOG_S`: watchdog seconds before fail-open
    (default 14)

## Test

```bash
cd ${PLUGIN_ROOT}/pieces/poll-loop-guard && python3 -B -m pytest -q test_poll_loop_guard.py
```

Expected: `35 passed`.
