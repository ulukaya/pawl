# pawl-readonly-pass

CLI:

```
python3 -B ${PLUGIN_ROOT}/pieces/readonly-pass/readonly_pass.py
```

Hook: `hooks/pawl_readonly_hook.py` on `run_command|run_shell_command`.
Answers `auto_approve` when every clause only reads: `ls`, `cat`, `head`,
`tail`, `wc`, `grep`, `rg`, `find` without `-exec`/`-delete`,
`sed -n '<N>,<M>p'`, `sort`, `uniq`, `tree`, `file`, `echo`, `cd`, and the
read-only subcommands of `git`, `hg`, `jj` and `g4`. Anything unprovable gets
`allow`, so the host prompts as usual. Never denies or asks; any other hook's
deny, force_ask or ask still wins.

Left to the prompt: `$` outside single quotes, backticks, `<(...)`, heredocs,
subshells, braces, `#`, newlines, background `&`, leading `VAR=value`, a
program named by path, writing redirects other than to `/dev/null` or between
stdout and stderr, VCS global options other than a repo path or no-pager
switch, secret paths, `brain/`, `conversations/`, `transcript*.jsonl`, and
calls that ask to bypass the sandbox.

## Commands

| Invocation | Effect |
|---|---|
| `check <command...>` | print read-only, exit 0; or prompt, exit 1 |

## Environment

-   `PAWL_DATA`: denials.jsonl dir (default ~/.pawl); each approval logs gate
    READONLY_PASS, outcome auto_approve, command sha1 only
-   `PAWL_READONLY_PASS_OFF`: `1` turns the piece off
-   `PAWL_READONLY_PASS_WATCHDOG_S`: watchdog seconds before fail-open
    (default 14)

## Test

```bash
cd ${PLUGIN_ROOT}/pieces/readonly-pass && python3 -B -m pytest -q .
```

Expected: `201 passed`.
