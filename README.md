# pawl

![pawl mark: a hook holding a gear](assets/logo.svg)

[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](#install)
[![Dependencies: none](https://img.shields.io/badge/dependencies-none-brightgreen)](CLAUDE.md)
[![Claude Code, Codex, Antigravity](https://img.shields.io/badge/harnesses-Claude%20Code%20%C2%B7%20Codex%20%C2%B7%20Antigravity-6f42c1)](#install)

Deterministic gates for coding agents, as one plugin for **Claude Code**,
**OpenAI Codex** and **Antigravity**.

Agents make the same mistakes over and over, and telling them not to in the
prompt stops working after a page. `pawl` is sixteen small checks that run as
code, not as instructions, and a report command that tallies what they blocked.
Each check watches for one mistake and refuses it, cleans it up, or (for
provably read-only commands) waves it through without a prompt. Plain Python
standard library, no model calls, nothing added to the prompt but a 141-token
skill description.

A pawl is the small part in a ratchet that lets the wheel move forward and stops
it from slipping back. Every check here works the same way: the current state is
the floor.

## Try it

No install, no harness, nothing written outside a scratch directory:

```bash
git clone https://github.com/ulukaya/pawl && python3 pawl/hooks/pawl.py demo
```

It sends ten calls through the real dispatcher, each written the way
Antigravity, Claude Code and Codex send it, and prints what each harness
is told:

```text
call                        gate        antigravity      claude code      codex
-------------------------------------------------------------------------------
make build                  -           allow            silent           silent
git log --oneline -5        readonly    auto_approve     allow            silent
git reset --hard            git         force_ask        ask              deny
tail -f server.log          poll        force_ask        ask              deny
same pytest run, 3rd time   loop        force_ask        ask              deny
edit that changes nothing   noop        deny             deny             deny
write with a U+200B         zero-width  allow +rewrite   +rewrite         allow +rewrite
send naming ~/.deploy/      egress      deny             deny             deny
read another session        fence       force_ask        ask              deny
stop with tail -f running   idle        n/a              block            n/a
```

`silent` leaves the harness's own prompt in place; `allow` and
`auto_approve` skip it. Codex hooks can neither ask nor approve, so there
an ask is a deny that tells the agent how to proceed. `--verbose` adds every
reason, `--json` every raw answer.

## What it catches

| The mistake | What pawl does | Gate |
| --- | --- | --- |
| Runs `git reset --hard`, `git clean -fdx`, `git commit --no-verify`, `git push --force` or `git branch -D` and loses work | Asks the human first | `git` |
| Runs `while true; do sleep`, `tail -f` or `sleep 3600` and hangs | Asks the human first | `poll` |
| Calls the same tool with the same arguments in a loop | Asks before the third identical call | `loop` |
| Sends an edit whose replacement equals its target, or rewrites a file with the bytes it already holds | Refuses it and sends the agent back to read | `noop` |
| Writes invisible zero-width characters into a file | Strips them so the write lands clean | `zero-width` |
| Stalls on a permission prompt for `ls` or `git log` | Approves commands that provably only read | `readonly` |
| Re-reads its own transcript after every context truncation | Refuses past a per-turn limit | `reread` |
| Reads another conversation's private files | Asks the human first | `fence` |
| Ends its turn with a `tail -f` still running in the background | Blocks the stop once and names the task | `idle` |
| Pastes internal paths, tokens or hostnames into a message | Blocks the send | `send` (egress) |
| Writes a message that reads like a bot | Blocks it above a score threshold | `send` (prose) |
| Floods a chat room or inbox | Caps sends per channel per day | `send` (budget) |
| Lets failing-test or lint counts creep up | Keeps a baseline that can only go down | `ratchet.py` |
| Keeps retrying a cron job that fails every night | Pauses it after repeated failures | `breaker.py` |
| Claims a bug is fixed without proving it | Requires the test to fail before the fix | `repro_fence.py` |
| Grows always-on prompt files until they cost more than they help | Caps their token size | `prompt_budget.py` |

The first twelve rows are hook gates that fire on their own; the last four
are CLIs for pre-commit, CI and cron. Every piece also runs on its own: see
`pieces/<name>/README.md`.

## What it costs

| | |
| --- | --- |
| Prompt | 141 tokens: the skill's description, the only always-on text |
| Latency | about 45 ms per Read and 65 ms per Bash call (median, Linux, Python 3.11), all gates in one process |
| Network | none: no telemetry, no model calls ([PRIVACY.md](PRIVACY.md)) |
| Dependencies | the Python 3.11+ standard library |

## Install

### Claude Code

```bash
claude plugin marketplace add ulukaya/pawl
claude plugin install pawl@pawl
```

Or inside a session: `/plugin marketplace add ulukaya/pawl`, then
`/plugin install pawl@pawl`. Start a new session (or `/reload-plugins`), and
`claude plugin details pawl` lists `Hooks (2) PreToolUse, Stop`.

### Codex

```bash
codex plugin marketplace add ulukaya/pawl
codex plugin add pawl@pawl
```

Codex reads `.codex-plugin/plugin.json`, which points it at
`hooks/codex.json` and the skill. Hooks need a Codex release with lifecycle
hooks.

### Antigravity

```bash
git clone https://github.com/ulukaya/pawl && cd pawl
./install.sh --antigravity
```

This links the checkout to `~/.gemini/config/plugins/pawl` and adds
`{"path": "plugins/pawl"}` to `~/.gemini/config/plugins.json` next to any
plugins already listed. Restart Antigravity; the plugin inventory lists
`pawl`.

### From a checkout, any harness

```bash
./install.sh                 # every harness found on this machine
./install.sh --claude        # or --antigravity, --codex
./install.sh --uninstall     # reverse every step
./install.sh --dry-run       # print what would change
```

For Claude Code and Codex the installer runs the commands above with this
checkout as the marketplace, so the plugin loads in place; `--source
ulukaya/pawl` tracks GitHub instead. Every step is idempotent. It then runs
the test battery, which needs `pytest` (`PAWL_PYTHON` picks the
interpreter); the plugin itself needs only Python 3.11+.

## How it works

```
 harness ──stdin──▶ hooks/pawl.py pre|stop --harness H
                      │
                      ├─ harness.parse()    native payload ─▶ one canonical call
                      ├─ gates.plan()       which gates apply to this tool
                      ├─ pieces/<name>/     each gate asks its piece
                      ├─ merge              deny > ask > approve > allow
                      └─ harness.render()   answer in H's own contract ──stdout──▶
```

Each harness has its own config, all running the same dispatcher:

| Harness | Config | Command |
| --- | --- | --- |
| Antigravity | `hooks.json` | `python3 -B hooks/pawl.py pre --only <gate> --harness antigravity`, one group per gate |
| Claude Code | `hooks/hooks.json` | `python3 -B "${CLAUDE_PLUGIN_ROOT}/hooks/pawl.py" pre --harness claude`, plus `stop` |
| Codex | `hooks/codex.json` | `python3 -B "${PLUGIN_ROOT}/hooks/pawl.py" pre --harness codex`, plus `stop` |

`hooks/harness.py` maps each harness's tools onto the canonical names the
pieces speak (Claude Code `Bash`, `Read`, `Write`, `Edit`, `TaskOutput`;
Codex `Bash` and `apply_patch`) and writes each answer the way that harness
reads it:

| pawl decides | Antigravity | Claude Code | Codex |
| --- | --- | --- | --- |
| no objection | `allow` | no output: the normal permission prompt still applies | no output |
| ask the human | `force_ask` | `permissionDecision: ask` | `deny` with the reason: Codex hooks cannot ask |
| refuse | `deny` | `permissionDecision: deny` | `permissionDecision: deny` |
| provably read-only | `auto_approve` | `permissionDecision: allow` | no output: Codex hooks cannot approve |
| rewrite the input | `overwrite` | `updatedInput`, permission unchanged | `updatedInput` |
| keep working (Stop) | `block` | `decision: block` | `decision: block` |

Reasons are reworded in the harness's own tool names (`Read`, not
`view_file`). The first deny ends a run, so a refused call never spends a
send-budget unit. Every answer exits 0; no path prints a traceback.

### Gates

| Gate | Fires on | Fails | Antigravity | Claude Code | Codex |
| --- | --- | --- | --- | --- | --- |
| `fence` | every tool | open | `brain/`, `conversations/` | `~/.claude/projects/` | `~/.codex/sessions/` |
| `git` | shell | closed | yes | yes | yes (deny) |
| `poll` | shell | closed | yes | yes | yes (deny) |
| `noop` | edits | open | `replace_file_content` | `Edit` | `apply_patch` |
| `zero-width` | writes, edits | open | yes | `Write`, `Edit` | `apply_patch` |
| `readonly` | shell | open | yes | yes | no (cannot approve) |
| `reread` | reads, shell | open | yes | yes | yes |
| `loop` | every tool | open | yes | yes | yes (deny) |
| `send` | shell | egress closed | yes | yes | yes |
| `idle` | Stop | open | `/proc` tasks | Stop payload tasks | no task list |

`hooks/pawl.py gates` lists them. Details: `skills/pawl/references/<piece>.md`
and `pieces/<piece>/README.md`.

## Configuration

| Need | Do |
| --- | --- |
| Skip gates for a session | `PAWL_DISABLE=git,poll` (any gate name, or `egress`, `prose`, `budget`) |
| See how often each send gate fires | `python3 hooks/pawl.py stats` (reads `$PAWL_DATA/gate_events.jsonl`) |
| Tally every gate's denials | `python3 pieces/report/report.py --days 7` |
| One send, git or repeated call past a gate | approve the prompt; the row is logged as a human override |
| Change send ceilings | `SEND_BUDGET_CEILINGS='{"chat_space": 4}'` |
| Change egress rules | edit `$PAWL_DATA/egress_rules.json` (default `~/.pawl/`) |
| Protect specific repos | `PAWL_GIT_PROTECTED_ROOTS=/repo/a:/repo/b` (default: the call's git toplevel) |
| Keep the prompt for read-only commands | `PAWL_READONLY_PASS_OFF=1` |
| Refuse, not ask, on another session's files | `PAWL_CONVERSATION_FENCE_STRICT=1` |
| Force a harness format | `--harness` in the config, or `PAWL_HARNESS` |

On Claude Code four of these are plugin settings, so nobody edits an
environment: `/config` lists pawl's rows (Approve read-only shell commands,
Repos the git guard protects, Refuse reads of other sessions, Gates to turn
off), and `claude plugin install pawl@pawl --config disable=reread` sets one
at install. A `PAWL_*` variable you export wins over the setting.

State and logs live under `PAWL_DATA` (default `~/.pawl`). Each piece's knobs
are listed in its reference page.

## Privacy

pawl runs on your machine and nowhere else: no network, no telemetry, no
model calls. Its logs hold counters and SHA-1 digests, never a command,
path or message. One gate loosens anything: on Claude Code and Antigravity,
`readonly` approves shell commands it can prove only read, without a prompt
(Codex hooks cannot approve, so there it stays silent); turn it off in
`/config` or with `PAWL_READONLY_PASS_OFF=1`. [PRIVACY.md](PRIVACY.md) lists
every file pawl reads and writes.

## Pieces

| Piece | Wire point | Command |
| --- | --- | --- |
| prose-gate | gate `send`, CI on docs | `pieces/prose-gate/prose_gate.py --plane chat draft.md` |
| egress-firewall | gate `send` | `pieces/egress-firewall/egress_firewall.py check < text` |
| send-budget | gate `send` | `pieces/send-budget/send_budget.py status` |
| destructive-git-guard | gate `git` | `pieces/destructive-git-guard/destructive_git_guard.py check --cwd . git reset --hard` |
| poll-loop-guard | gate `poll` | `pieces/poll-loop-guard/poll_loop_guard.py classify "while true; do sleep 5; done"` |
| oscillation-breaker | gate `loop` | `pieces/oscillation-breaker/oscillation_breaker.py check <conversation-id> view_file '{"path": "a"}'` |
| noop-edit-guard | gate `noop` | `pieces/noop-edit-guard/noop_edit_guard.py check replace_file_content '{"TargetContent": "a", "ReplacementContent": "a"}'` |
| zero-width-sanitizer | gate `zero-width` | `pieces/zero-width-sanitizer/zero_width_sanitizer.py strip < draft.txt` |
| readonly-pass | gate `readonly` | `pieces/readonly-pass/readonly_pass.py check git log -5` |
| reread-guard | gate `reread` | `pieces/reread-guard/reread_guard_hook.py < payload.json` |
| conversation-fence | gate `fence` | `pieces/conversation-fence/conversation_fence_hook.py < payload.json` |
| idle-task-gate | gate `idle` (Stop) | `pieces/idle-task-gate/idle_task_gate.py list <conversation-id>` |
| ratchet-baseline | pre-commit, CI | `pieces/ratchet-baseline/ratchet.py check --baseline .ratchet.json --metric failing_tests=N` |
| circuit-breaker | cron, sidecars | `pieces/circuit-breaker/breaker.py run nightly -- ./job.sh` |
| repro-fence | pre-commit on bug fixes | `pieces/repro-fence/repro_fence.py both --cmd "pytest tests/test_x.py" --file src/x.py` |
| prompt-budget | pre-commit on prompt files | `pieces/prompt-budget/prompt_budget.py check --config budget.json` |
| report | CLI, retro | `pieces/report/report.py --days 7` |

One skill, `skills/pawl/SKILL.md`, routes an agent by denial prefix or task to
`skills/pawl/references/<piece>.md`, which carries that piece's flags.

## What it is not

*   Not a model, model router, or model picker. `pawl` never names, selects,
    or calls a model.
*   Not a replacement for other plugins. Skill packs and shell guards keep
    doing their jobs; each plugin registers its own hooks and the harness runs
    them all.
*   Not a sandbox. The gates read tool arguments; a script that builds a path
    at run time is not fenced.
*   Not a workflow engine. Nothing here spawns subagents or schedules
    anything.

## Eval

`eval/` holds a gates-on vs gates-off ablation suite: 24 tasks with a
temptation in each (10 code-change, 6 repo-hygiene, 8 outbound), throwaway
git fixtures, stub senders and script graders, with no LLM judge.
`eval/run_arms.sh` runs both arms; see `eval/README.md`. `eval/claude/` holds
four cases for Claude Code's built-in runner (`claude plugin eval .
--scaffold --allow-tools Bash Edit Write`), also judge-free.

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install pytest
.venv/bin/python3 -B run_tests.py       # one OK line per suite
.venv/bin/python3 -B check_portable.py  # portable: clean
git config core.hooksPath .githooks     # run both before every push
```

`run_tests.py` runs the hooks suite (including end-to-end runs of the shipped
Claude Code and Codex configs), every piece's suite, the root tools and the
eval grader twins. `check_portable.py` fails on an absolute home path, a
non-stdlib import in shipped code, a CR byte, a markdown prose line over 80
columns, a reference page naming an env var its piece never reads, a hook
config that does not run `hooks/pawl.py` the way its harness needs,
manifests that disagree on name or version, a source file over 500 lines, or
a function nested more than 3 blocks deep. The `.githooks/pre-push` hook runs
both and refuses a push that fails. GitHub CI (`.github/workflows/ci.yml`,
Linux and macOS, Python 3.11 to 3.14) is paused and runs only by hand for
now. `CLAUDE.md` holds the engineering rules; `CHANGELOG.md` the history.
[CONTRIBUTING.md](CONTRIBUTING.md) is the short version for a first pull
request, and [SECURITY.md](SECURITY.md) says what to report privately.

To add a gate: write the piece under `pieces/<name>/` with its tests, add a
`Gate` to `hooks/gates.py`, add its Antigravity group to `hooks.json`, and
add `skills/pawl/references/<name>.md`; `check_portable.py` tells you what
is missing.

## Questions

Open an issue at https://github.com/ulukaya/pawl/issues or email
ulukaya@gmail.com. pawl is licensed under [Apache-2.0](LICENSE).
