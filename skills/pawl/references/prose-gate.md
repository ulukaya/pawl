# pawl-prose-gate

CLI:

```
python3 -B ${PLUGIN_ROOT}/pieces/prose-gate/prose_gate.py
```

## Commands

| Invocation | Effect |
|---|---|
| `--plane chat <file>` | short replies; threshold 3.0, 2 families free |
| `--plane deliverable <file>` | docs and posts; threshold 6.0, 4 families free |
| `- (stdin)` | score text piped on stdin |
| `--why` | one stderr line per hit with the matched span |
| `--explain` | per-pattern table on stdout |
| `--json` | machine-readable result: score, threshold, is_fail, details |

## Environment

-   `PROSE_GATE_PATTERNS`: catalog path (default prose_patterns.json beside the
    script)

## Test

```bash
cd ${PLUGIN_ROOT}/pieces/prose-gate && python3 -B -m pytest -q test_prose_gate.py
```

Expected: `10 passed`.
