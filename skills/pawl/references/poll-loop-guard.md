# pawl-poll-loop-guard

CLI:

```
python3 -B <root>/pieces/poll-loop-guard/poll_loop_guard.py
```

Gate `poll` of `hooks/pawl.py`, on shell commands. A hit asks the user
(Antigravity `force_ask`, Claude Code `ask`); Codex hooks cannot ask, so
there it is a deny with the reason.

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
cd <root>/pieces/poll-loop-guard && python3 -B -m pytest -q test_poll_loop_guard.py
```

Expected: every test passes.
