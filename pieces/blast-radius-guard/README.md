# Blast-radius guard

Judges a shell command by what it would delete, after following everything
the command runs. Standard library only; it runs on its own or as gate
`blast` of the pawl plugin.

## The problem it exists for

Agents have deleted home directories, a whole drive and production data, and
the guards meant to stop them read the command line. The deletion is often
one step away from it:

*   in a script the agent wrote a minute earlier (`bash cleanup.sh`), where a
    `trap 'rm -rf "$D"' EXIT` runs after `D="$HOME"`
    ([claude-code#88462](https://github.com/anthropics/claude-code/issues/88462),
    the fifth report of its class; closed reports
    [#29082](https://github.com/anthropics/claude-code/issues/29082),
    [#32938](https://github.com/anthropics/claude-code/issues/32938),
    [#49129](https://github.com/anthropics/claude-code/issues/49129));
*   in `npm run clean`, a Makefile recipe, `python -c`, `node -e`,
    `os.system(...)`, `docker run -v ~:/data ... rm -rf /data`
    ([claude-code#85274](https://github.com/anthropics/claude-code/issues/85274));
*   behind spellings that bash undoes after a text matcher has looked:
    `r''m`, `$'\x72\x6d'`, `$IFS`, `X=rm; $X`, base64 piped to `sh`
    ([GuardFall](https://github.com/tsukasaI/shguard)).

## What it does

It parses the command the way bash would (quotes, `$IFS`, ANSI-C strings,
heredocs, here-strings, substitutions, redirects), follows what it runs, and
expands each word to every value it can take. A variable holds every value
it is ever assigned, so a trap sees a later reassignment. Then each deletion
target is judged by where it lands:

| Lands on | Answer |
| --- | --- |
| `/`, a system directory, home or above it, `~/.ssh`, `~/Documents`, `~/code` and similar, a drive root, a disk device, or all the contents of any of these | deny |
| the whole workspace, anything else outside it and the temp directories, or a path that starts with a value pawl cannot resolve | ask |
| inside the workspace (the git toplevel of the command's directory), or inside a temp directory | allow |

Followed: script files (`bash x.sh`, `./x`, `source`, `.`, `cat x | sh`,
`bash < x`, `bash <(...)`), `-c` strings (bash, and `fish -c`/`-C`/
`--command` in every spelling), `eval`, `trap`, functions (with `$1` from
each call site), `read` loops (values from what feeds them), substitutions
(`$( )`, backticks and unquoted heredoc bodies run as shell),
`npm`/`pnpm`/`yarn`/`bun` scripts with pre and post hooks, `npx` and `dlx`,
`uv run`/`poetry run`/`conda run` and friends, GNU `parallel`, Makefile and
justfile recipes, Python, JavaScript, Ruby, Perl and Go files and inline
code (their delete calls and their shell-outs), containers (a bind mount
maps the container path back to the host), `cmd /c` and `powershell`
`-Command` / `-EncodedCommand`, and a file this same command writes before
running it. Prefixes it sees through: `sudo`, `env`, `timeout`, `nice`,
`busybox`, `chrt`, `taskset`, `setsid`, `flock`, `watch` and the like, plus
argv-level brace expansion (`{,rm} -rf /`).

Deleters: `rm`, `rmdir`, `unlink`, `shred`, `rimraf`, `find -delete` and
`-exec rm`, `xargs rm`, `mv` to `/dev/null` or of a protected folder,
`rsync --delete`, `dd of=<path>` (judged where it lands), `mkfs`,
`wipefs -a`, `diskutil erase*`, the cmd and PowerShell forms, and the delete
APIs of each language above. Windows paths (`C:\...`, MSYS `/c/...`) are
judged by place too: drive roots, `C:\Windows`, user profiles and
`Documents`/`.ssh`/`AppData` are denied, files inside `AppData\Local\Temp`
allowed.

## Measured

Five labelled corpora, 329 cases, with the score of each held-out set taken
before the guard was tuned on it; `HILLCLIMB.md` has every round:

| Set | First-seen score (caught, false alarms) | Now |
| --- | --- | --- |
| holdout-1 | 32/35, 1/25 | 35/35, 0/25 |
| holdout-2 | 19/28, 2/22 | 28/28, 0/22 |
| holdout-3 | 18/25, 0/18 | 25/25, 0/18 |
| holdout-4 | 14/17, 0/22 | 16/17, 0/22 |
| 472 real commands from a Claude Code session | 3 false alarms | 0 |

Judging a command takes about a millisecond; following scripts reads at
most 256 KiB per file and 1 MiB per command.

Scored against the cases three other deletion guards ship for themselves
(their deletion cases only; `../../eval/blast-compare/external.py`): of the
commands they block, pawl catches dcg 62/78, shguard 109/125, cc-safety-net
15/25; of the commands they allow, pawl agrees dcg 82/87, shguard 16/21,
cc-safety-net 11/12. Most of the remaining gaps are policy, not defects
(pawl allows temp and workspace deletions, and treats printing as not
deleting); `HILLCLIMB.md` reads them one by one.

## Not covered

A deletion whose target is computed at run time (walking a tree), anything
downloaded and run (`curl | sh`), compiled programs, and remote hosts. It
reads files to follow them and never runs anything.

## Files

| File | Purpose |
| --- | --- |
| `blast_radius.py` | `assess(command, cwd, env)`, the hook decision, CLI |
| `scanner.py` | walks shell text; three passes per text |
| `deletes.py`, `follows.py` | per-command handlers: deleters, and commands that run other code |
| `shellparse.py`, `expand.py` | bash-like parsing and expansion |
| `targets.py` | where a path lands, and how bad that is |
| `runs.py`, `code_scan.py`, `argv_util.py` | scripts, package jobs, make and just, other languages, prefixes |
| `winpath.py` | judges Windows and MSYS paths by place |
| `blast_radius_hook.py` | PreToolUse hook |
| `score.py`, `corpus.json`, `holdout*.json` | the scorer and its labelled cases |
| `test_blast_radius.py`, `test_corpus.py` | rules with twins; the corpus ratchet |

## Run it

```bash
python3 blast_radius.py check --cwd . -- rm -rf ~/   # exit 1, deny: ...
python3 score.py --corpus holdout4.json --verbose
python3 -m pytest -q
```

## In the pawl plugin

`hooks/pawl.py` runs it as gate `blast` on shell commands in Antigravity,
Claude Code and Codex. A deny is a deny everywhere; an ask is `force_ask`
on Antigravity, `ask` on Claude Code, and a deny with the reason on Codex,
whose hooks cannot ask. `PAWL_DISABLE=blast` turns it off for a session.
