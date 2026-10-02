# pawl-prompt-budget

CLI:

```
python3 -B ${PLUGIN_ROOT}/pieces/prompt-budget/prompt_budget.py
```

## Commands

| Invocation | Effect |
|---|---|
| `check --config budget.json [--root DIR] [--tokens-per-char F]` | exit 1 over ceiling or pin missing, 2 file missing, 3 bad config |
| `report --config budget.json` | print the table, never fail |

## Environment

-   (none): config path is explicit

## Test

```bash
cd ${PLUGIN_ROOT}/pieces/prompt-budget && python3 -B -m pytest -q test_prompt_budget.py
```

Expected: `13 passed`.
