# pawl-destructive-git-guard

CLI:

```
python3 -B <root>/pieces/destructive-git-guard/destructive_git_guard.py
```

Gate `git` of `hooks/pawl.py`, on shell commands. A hit asks the user
(Antigravity `force_ask`, Claude Code `ask`); Codex hooks cannot ask, so
there it is a deny with the reason.

In every repo it also asks before a force push (`--force`, `-f`,
`--force-with-lease`, a `+` refspec), a remote delete (`--delete`, `:dst`),
`push --mirror` or `--prune`, and `git branch -D`, `-f`, `-M` or `-C`. Plain
`git push`, dry runs and `git branch -d` pass. On a hit, push to a new branch
or ask the user; never retry with another force spelling.

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
cd <root>/pieces/destructive-git-guard && python3 -B -m pytest -q .
```

Expected: every test passes.
