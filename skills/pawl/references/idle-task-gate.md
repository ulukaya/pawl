# pawl-idle-task-gate

CLI:

```
python3 -B <root>/pieces/idle-task-gate/idle_task_gate.py
```

Gate `idle` of `hooks/pawl.py stop`. Antigravity: walks `/proc` for this
conversation's task roots older than the age limit; first stop with a set
blocks and names them, the second terminates the wait shapes and allows.
Claude Code: reads the Stop payload's background tasks instead; a shell task
whose command is a wait shape (it never finishes, so it never reports back)
blocks once per set, then the stop passes. Codex reports no tasks, so the
gate stays quiet there.

## Commands

| Invocation | Effect |
|---|---|
| `list <conversation-id>` | one JSON row per stale task root: pid, pgid, age, cmd |
| `classify <command...>` | LOOP, TAIL, SLEEP or none (same classifier as poll-loop-guard) |

## Environment

-   `PAWL_IDLE_TASK_MINUTES`: age limit in minutes (default 10)
-   `PAWL_IDLE_TASK_ALLOW_RE`: regex; matching command lines are never reported
    or signalled
-   `PAWL_IDLE_TASK_PROC_ROOT`: procfs root (default /proc)
-   `PAWL_IDLE_TASK_STATE`: breaker state file (default
    PAWL_DATA/idle_task_gate.json)

## Test

```bash
cd <root>/pieces/idle-task-gate && python3 -B -m pytest -q test_idle_task_gate.py
```

Expected: every test passes.
