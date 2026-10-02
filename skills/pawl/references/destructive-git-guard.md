# pawl-destructive-git-guard

CLI:

```
python3 -B ${PLUGIN_ROOT}/pieces/destructive-git-guard/destructive_git_guard.py
```

## Commands

| Invocation | Effect |
|---|---|
| `check [--cwd DIR] <git command...>` | prints allow, exit 0; or the deny reason, exit 1 |
| `roots [--cwd DIR]` | print the protected roots in effect for DIR |

## Environment

-   `PAWL_GIT_PROTECTED_ROOTS`: colon-separated repo roots; unset means the git
    toplevel of the call's cwd
-   `PAWL_DATA`: denials.jsonl dir (default ~/.pawl)
-   `PAWL_GIT_GUARD_WATCHDOG_S`: watchdog seconds before fail-open (default 14)

## Test

```bash
cd ${PLUGIN_ROOT}/pieces/destructive-git-guard && python3 -B -m pytest -q test_destructive_git_guard.py
```

Expected: `100 passed`.
