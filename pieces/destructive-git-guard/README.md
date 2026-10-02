# Destructive git guard

One piece of the CoS blueprint, published on its own so you can run it in ten
minutes. Five files, standard library only, no dependency on the rest of the
system.

## What it does

Denies a `run_command` whose command line runs a git subcommand that discards
work in a protected repo: `reset` (anything but `--soft`), `checkout` with a
pathspec (`--`, `.`, `-f`), `restore` that touches the working tree, `stash`
(anything but `list`/`show`), `clean` (anything but a dry run, `-fdx` clusters
included) and `rm`. In every repo it also denies a `git commit` that hides a
rejected commit: `--no-verify` or a short cluster carrying `n` (`-n`, `-nm`,
`-anm`), output piped into `tail`/`head`, output sent to `/dev/null`. And in
every repo, with zero protected roots, it denies `git worktree add` onto tmpfs
(`/tmp`, `/dev/shm`, `/run`): that working tree disappears on reboot with every
uncommitted change in it. The typed destination is matched against the literal
prefixes and its realpath against theirs (a symlinked `/tmp` still denies);
`~` and `$VAR` are expanded first, and a path still starting with an unknown
variable is allowed. Values of `-b`, `-B` and `--reason` are never taken for
the destination. The reason names a durable spot such as `~/worktrees/<name>`.

Each shell segment is tokenized with `shlex`, the `git` token is located past
`env`, `sudo`, `timeout N` and `VAR=val` prefixes, `-C`, `--git-dir`,
`--work-tree` and an earlier `cd X` decide which repo is targeted, and quoted
text is masked so `-m "drop -n"` cannot trip a rule.

## The problem it exists for

Git has no pre-reset, pre-checkout, pre-clean or pre-stash hook. In a repo
several agents write to at once, one `git reset --hard` from a worker that
wanted a clean tree erased a sibling's uncommitted edits, and a `git commit -n`
in another turn skipped the pre-commit gate that would have rejected the change.
The only place to stop both is the tool call.

## Files

| File                            | Purpose                                    |
| ------------------------------- | ------------------------------------------ |
| `destructive_git_guard.py`      | `scan(command, cwd)` returns a deny reason |
:                                 : or `''`; `protected_roots(cwd)`;           :
:                                 : `unsafe_commit_reason(cmd)`; inlined       :
:                                 : payload reader (fails closed on bad JSON), :
:                                 : watchdog (fails open on time), denial log  :
:                                 : (`PAWL_DATA/denials.jsonl`), and a CLI.    :
| `worktree_tmpfs.py`             | `reason(args, base)`: the tmpfs rule for   |
:                                 : `git worktree add`.                        :
| `destructive_git_guard_hook.py` | PreToolUse hook. Reads the tool call JSON  |
:                                 : on stdin and prints `{"decision"\:         :
:                                 : "allow"}` or `{"decision"\: "deny",        :
:                                 : "reason"\: ...}`.                          :
| `test_destructive_git_guard.py` | 100 tests: every destructive form,         |
:                                 : `-C`/`cd`/pathspec targeting, read-only    :
:                                 : and safe writes, other repos, multiple     :
:                                 : roots, the git-toplevel default (real `git :
:                                 : init`), the commit rules, hook decisions   :
:                                 : and denial rows, CLI exit codes.           :
| `test_worktree_tmpfs.py`        | 23 tests: each tmpfs prefix, option        |
:                                 : skipping, relative/`cd`/`-C` bases, `~`    :
:                                 : and `$VAR` expansion, symlinks, zero       :
:                                 : roots, CLI and hook decisions.             :

## Run it

```bash
python3 -m pytest -q .
python3 destructive_git_guard.py check --cwd /path/to/repo git reset --hard
python3 destructive_git_guard.py roots --cwd /path/to/repo
```

`check` exits 0 and prints `allow`, or exits 1 with the deny reason. `roots`
prints the protected roots in effect for that directory.

## In the pawl plugin

You do not wire this piece yourself there: `hooks/pawl.py` runs it as gate
`git` in Antigravity, Claude Code and Codex, and `hooks/harness.py`
translates each harness's payload and answer, so the piece only ever sees
the Antigravity shape below. `PAWL_DISABLE=git` turns it off for a
session. The rest of this page is for running the piece on its own.

## Wire it as a hook

Jetski and Antigravity read `hooks.json`. Add a group like this (adjust the
path):

```json
{
  "destructive-git-guard": {
    "enabled": true,
    "PreToolUse": [
      {
        "matcher": "run_command",
        "hooks": [
          {"type": "command", "command": "python3 /path/to/destructive_git_guard_hook.py", "timeout": 15}
        ]
      }
    ]
  }
}
```

Other harnesses: the hook needs a command string and a working directory.
`command_and_cwd()` in `destructive_git_guard.py` is the one function to adapt.

## Configuration

| Variable | Meaning | Default |
|---|---|---|
| `PAWL_GIT_PROTECTED_ROOTS` | Colon-separated repo roots to protect | unset: the git toplevel of the call's working directory; outside a repo nothing is protected |
| `PAWL_DATA` | Where `denials.jsonl` is appended | `~/.pawl` |
| `PAWL_GIT_GUARD_WATCHDOG_S` | Seconds before the watchdog prints allow and exits | 14 (host timeout 15 minus 1) |

## Design notes

-   Fail closed on the payload and on an internal error while scanning a git
    command. A guard on destructive commands that fails open on a crash is not a
    guard. Fail open on time only.
-   Allow-list, not deny-list, inside a guarded subcommand: `reset --soft`,
    `restore --staged`, `stash list`, `clean -n` are the named safe forms;
    everything else under those six verbs denies.
-   The commit rules apply in every repo because a skipped gate is a problem
    wherever it happens.
-   The deny reason names the fix: `write_to_file` to rewrite a file, `git
    restore --staged` to unstage, `git commit --only <paths>` to commit without
    racing on `.git/index`.
-   The default root is resolved with `git rev-parse --show-toplevel` under a 3
    second cap; set `PAWL_GIT_PROTECTED_ROOTS` explicitly to skip the
    subprocess.

## What the full system adds on top

In the blueprint the denial rows roll into a Sunday retro and the same protected
roots feed the pre-commit gate. You need neither for this guard to do its job.
