---
name: pawl-send-budget
description: "Cap how many messages go out per channel per local day (chat space, owner DM, email, social). Use to inspect or spend the budget by hand; the pawl hook spends it automatically."
---

# pawl-send-budget

CLI:

```
python3 -B ${PLUGIN_ROOT}/pieces/send-budget/send_budget.py
```

## Commands

| Invocation | Effect |
|---|---|
| `status [--json]` | counts, ceilings, denials for today |
| `check --channel C [--json] [--compact]` | exit 0 room left, 3 ceiling reached; no state change |
| `spend --channel C [--reason TEXT]` | consume one unit; exit 0 or 3 |
| `classify <command line>` | print the channel a shell command maps to, or none |
| `reset` | clear today's counters (keeps 30-day history) |

## Environment

-   `SEND_BUDGET_STATE_DIR`: state dir (default ~/.local/state/send_budget; the
    pawl hook seeds it from PAWL_DATA)
-   `SEND_BUDGET_CEILINGS`: JSON overrides, default
    {"dm_owner":12,"chat_space":8,"email":6,"social":3}
-   `SEND_BUDGET_OWNER_SPACE`: space id that counts as dm_owner
-   `SEND_BUDGET_TZ`: IANA zone for the day boundary
-   `SEND_BUDGET_OVERRIDE=1`: allow one send past the ceiling; logged as
    overridden

## Test

```bash
cd ${PLUGIN_ROOT}/pieces/send-budget && python3 -B -m pytest -q test_send_budget.py
```

Expected: `21 passed`.
