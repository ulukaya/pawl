# pawl-egress-firewall

CLI:

```
python3 -B <root>/pieces/egress-firewall/egress_firewall.py
```

## Commands

| Invocation | Effect |
|---|---|
| `check [--rules FILE] < text` | exit 0 clean, 1 hit, 2 internal error |
| `hook [--rules FILE] [--field a.b.c]` | PreToolUse adapter; reads tool-call JSON on stdin, prints a decision |
| `init-rules > rules.json` | write the bundled example rules to start from |

## Environment

-   (none): rules path comes from --rules; the pawl hook uses
    PAWL_DATA/egress_rules.json

## Test

```bash
cd <root>/pieces/egress-firewall && python3 -B -m pytest -q test_egress_firewall.py
```

Expected: every test passes.
