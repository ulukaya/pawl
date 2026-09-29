---
name: pawl-idle-task-gate
description: "Stop hook that blocks ending a turn while this conversation still has background tasks older than the age limit, names them, and on a second stop with the same set SIGTERMs the unbounded-wait ones. Use to list stale roots by hand. Fails open."
---

# pawl-idle-task-gate

CLI:

```
python3 -B ${PLUGIN_ROOT}/pieces/idle-task-gate/idle_task_gate.py
```

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
cd ${PLUGIN_ROOT}/pieces/idle-task-gate && python3 -B -m pytest -q test_idle_task_gate.py
```

Expected: `20 passed`.
