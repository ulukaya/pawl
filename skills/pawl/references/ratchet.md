# pawl-ratchet

CLI:

```
python3 -B <root>/pieces/ratchet-baseline/ratchet.py
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
cd <root>/pieces/ratchet-baseline && python3 -B -m pytest -q test_ratchet.py
```

Expected: every test passes.
