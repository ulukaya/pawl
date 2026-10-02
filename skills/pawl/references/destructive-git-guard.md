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

`git worktree add` onto `/tmp`, `/dev/shm` or `/run` (literal or realpath)
denies in every repo, even with zero protected roots; move the worktree to a
durable path such as `~/worktrees/<name>`.

## Environment

-   `PAWL_GIT_PROTECTED_ROOTS`: colon-separated repo roots; unset means the git
    toplevel of the call's cwd
-   `PAWL_DATA`: denials.jsonl dir (default ~/.pawl)
-   `PAWL_GIT_GUARD_WATCHDOG_S`: watchdog seconds before fail-open (default 14)

## Test

```bash
cd ${PLUGIN_ROOT}/pieces/destructive-git-guard && python3 -B -m pytest -q .
```

Expected: `123 passed`.
