---
name: pawl-oscillation-breaker
description: "Prompt (force_ask) when a conversation repeats the same tool call with identical args 3 times in a row, or alternates the same 2 or 3 calls twice. Use to inspect or reset the per-conversation ring by hand. Fails open."
---

# pawl-oscillation-breaker

CLI:

```
python3 -B ${PLUGIN_ROOT}/pieces/oscillation-breaker/oscillation_breaker.py
```

## Commands

| Invocation                        | Effect                                  |
| --------------------------------- | --------------------------------------- |
| `check <conv> <tool> [json-args]` | append one call to the ring and print   |
:                                   : the decision; exit 0 allow, 1 force_ask :
| `show <conv>`                     | print the ring for a conversation       |
| `reset <conv>`                    | delete the ring file                    |

## Environment

-   `PAWL_DATA`: ring files under PAWL_DATA/oscillation/<conv>.json (default
    ~/.pawl)
-   `PAWL_OSCILLATION_WINDOW`: ring length (default 16, floor 6)
-   `PAWL_OSCILLATION_WATCHDOG_S`: watchdog seconds before fail-open
    (default 14)

## Test

```bash
cd ${PLUGIN_ROOT}/pieces/oscillation-breaker && python3 -B -m pytest -q test_oscillation_breaker.py
```

Expected: `19 passed`.
