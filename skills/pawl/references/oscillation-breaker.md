# pawl-oscillation-breaker

CLI:

```
python3 -B <root>/pieces/oscillation-breaker/oscillation_breaker.py
```

## Commands

| Invocation                        | Effect                                  |
| --------------------------------- | --------------------------------------- |
| `check <conv> <tool> [json-args]` | append one call to the ring and print   |
:                                   : the decision; exit 0 allow, 1 force_ask :
| `show <conv>`                     | print the ring for a conversation       |
| `reset <conv>`                    | delete the ring file                    |

Hashed args drop `toolSummary`/`toolAction` and, on a `view_file` with a
`StartLine`, the `EndLine`. `manage_task` counts only as `Action=status` with a
`TaskId` (Claude Code's `TaskOutput` maps to it). A read of a `.log` or
`.output` file is a poll. The ring clears on `schedule` (Claude Code:
`CronCreate`, `ScheduleWakeup`) and at turn end (`hooks/pawl.py stop`), so
cron wakeups never add up. Codex hooks cannot ask, so there a repeat is a
deny.

## Environment

-   `PAWL_DATA`: ring files under PAWL_DATA/oscillation/<conv>.json (default
    ~/.pawl)
-   `PAWL_OSCILLATION_WINDOW`: ring length (default 16, floor 6)
-   `PAWL_OSCILLATION_IDLE_S`: expire a ring idle this many seconds (default 0,
    off; measured from the last call's start, so slow retries can expire it)
-   `PAWL_OSCILLATION_WATCHDOG_S`: watchdog seconds before fail-open
    (default 14)

## Test

```bash
cd <root>/pieces/oscillation-breaker && python3 -B -m pytest -q .
```

Expected: every test passes.
