# pawl-repro-fence

CLI:

```
python3 -B <root>/pieces/repro-fence/repro_fence.py
```

## Commands

| Invocation | Effect |
|---|---|
| `red --cmd CMD [--repo DIR] [--timeout S]` | R1: CMD must exit non-zero now |
| `fence --file F [--rev REV] [--repo DIR]` | R2: compare public symbols of F at REV vs working tree |
| `both --cmd CMD --file F [--rev REV]` | R1 then R2 |

## Environment

-   (none): all inputs are flags

## Test

```bash
cd <root>/pieces/repro-fence && python3 -B -m pytest -q test_repro_fence.py
```

Expected: every test passes.
