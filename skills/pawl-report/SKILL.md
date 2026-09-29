---
name: pawl-report
description: "Print per-gate denial counts from PAWL_DATA/denials.jsonl for the last N days: gate, count, total, date range. A viewer, not a check; prints no command text, hashes or conversation ids."
---

# pawl-report

CLI:

```
python3 -B ${PLUGIN_ROOT}/pieces/report/report.py
```

## Commands

Invocation     | Effect
-------------- | ----------------------------------------------
`[--days N]`   | window in days (default 7; 0 means every row)
`[--data DIR]` | denials dir; beats PAWL_DATA (default ~/.pawl)

## Environment

-   `PAWL_DATA`: denials.jsonl dir (default ~/.pawl)

## Test

```bash
cd ${PLUGIN_ROOT}/pieces/report && python3 -B -m pytest -q test_report.py
```

Expected: `8 passed`.
