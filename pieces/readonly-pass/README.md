# Read-only pass

One piece of the CoS blueprint, published on its own so you can run it in ten
minutes. Four files, standard library only, no dependency on the rest of the
system.

## What it does

Answers a `run_command` with the host's `auto_approve` decision when every
clause of the command provably only reads, and `allow` otherwise, so the host
prompts as it does today. It never denies or asks. `auto_approve` sits below
deny, force_ask and ask, so every other hook still wins, and the host honors
it only over its own default prompt, never over a rule the user configured.

Trusted: `ls`, `cat`, `head`, `tail`, `wc`, `grep`, `rg`, `find` without
`-exec`/`-delete`/`-fprint`, `sed -n '<N>,<M>p'`, `sort`, `uniq` with at most
one file, `tree`, `file`, `echo`, `cd`, `pwd`, and the read-only subcommands
of `git`, `hg`, `jj` and `g4`. Flags that write or run another program are
caught in long form, as abbreviations (`--compress-prog`) and inside short
clusters (`sort -uo out`).

Left to the prompt: `$` outside single quotes, backticks, process
substitution, heredocs, subshells, braces, `#`, newlines, background `&`,
leading `VAR=value`, a program named by path, writing redirects other than to
`/dev/null` or between stdout and stderr, VCS global options other than a
repo path or no-pager switch, secret paths, agent conversation stores, and
calls that ask to bypass the sandbox. Secret paths are checked per shell word
after quote removal and `~` expansion.

## The problem it exists for

Permission prompts on read-only commands were the most common request in the
Jetski discussion space for a week; on one workstation 31.7% of 21,326
run_command calls passed this kind of strict read-only check.

## Files

| File | Purpose |
|---|---|
| `readonly_pass.py` | Shell parsing, path fence, hook decision, approval log, CLI. |
| `readonly_rules.py` | Trusted programs, writing flags, VCS allowlists. |
| `readonly_pass_hook.py` | PreToolUse hook. |
| `test_readonly_pass.py` | 201 tests: approve and prompt rows, hook shapes, off switch. |

## Run it

```bash
python3 -m pytest -q .
python3 readonly_pass.py check git log --oneline -5
```

## In the pawl plugin

You do not wire this piece yourself there: `hooks/pawl.py` runs it as gate
`readonly` in Antigravity, Claude Code and Codex, and `hooks/harness.py`
translates each harness's payload and answer, so the piece only ever sees
the Antigravity shape below. `PAWL_DISABLE=readonly` turns it off for a
session. The rest of this page is for running the piece on its own.

On Claude Code an approval is `permissionDecision: allow`, which skips the
prompt. On Codex the gate stays silent: a PreToolUse hook there cannot
approve, and pawl does not answer Codex's PermissionRequest hook either:
that payload leaves out whether the command asked to leave the sandbox, and
pawl will not approve an escalation it cannot see.

## Configuration

| Variable | Meaning | Default |
|---|---|---|
| `PAWL_DATA` | Where approval rows are appended | `~/.pawl` |
| `PAWL_READONLY_PASS_OFF` | `1` turns the piece off | unset |
| `PAWL_READONLY_PASS_WATCHDOG_S` | Seconds before the hook fails open | 14 |

## Limits

Relative paths are not resolved against the call's working directory. Globs
and recursive walks targeting sensitive roots (~, /, conversation stores)
are stopped and left to the prompt.
