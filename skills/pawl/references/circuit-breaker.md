# pawl-circuit-breaker

CLI:

```
python3 -B ${PLUGIN_ROOT}/pieces/circuit-breaker/breaker.py
```

## Commands

| Invocation | Effect |
|---|---|
| `run <name> -- <cmd...>` | should-run, run, record in one call; exit 3 when skipped |
| `should-run <name>` | exit 0 run it, 3 breaker open |
| `record <name> ok\|fail [--exit-code N]` | update the breaker after a manual run |
| `status` | table of breakers, states, next retry |
| `reset <name>` | close a breaker by hand |
| `--state FILE` | state JSON (default BREAKER_STATE or ~/.local/state/breaker/breakers.json) |

## Environment

-   `BREAKER_STATE`: state file path

## Test

```bash
cd ${PLUGIN_ROOT}/pieces/circuit-breaker && python3 -B -m pytest -q test_breaker.py
```

Expected: `16 passed`.
