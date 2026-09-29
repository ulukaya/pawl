---
name: pawl-ratchet
description: "A defect baseline that can only go down: failing tests, bare excepts, lint counts. Use in pre-commit or CI so today's count is the ceiling and every drop is locked with a written reason."
---

# pawl-ratchet

CLI:

```
python3 -B ${PLUGIN_ROOT}/pieces/ratchet-baseline/ratchet.py
```

## Commands

| Invocation | Effect |
|---|---|
| `check --baseline F --metric NAME=N [...]` | exit 1 when any metric rose; unseen metrics pass |
| `update --baseline F --metric NAME=N --reason TEXT` | lower the floor; reason must be 40+ chars; refuses to raise |
| `show --baseline F` | print the baseline table |

## Environment

-   (none): baseline path is explicit

## Test

```bash
cd ${PLUGIN_ROOT}/pieces/ratchet-baseline && python3 -B -m pytest -q test_ratchet.py
```

Expected: `14 passed`.
