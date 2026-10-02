# pawl

<img src="assets/logo.svg" width="96" alt="pawl mark: a hook holding a gear">

Plugin for Antigravity, Claude Code, and OpenAI Codex.

Questions or bugs: open an issue at https://github.com/ulukaya/pawl/issues
or email ulukaya@gmail.com.

Agents make the same mistakes over and over, and telling them not to in the
prompt stops working after a page. `pawl` is sixteen small checks that run as
code, not as instructions, and a report command that tallies what they blocked.
Each check watches for one mistake and refuses it, cleans it up, or (for
provably read-only commands) waves it through without a prompt. Plain Python, no
model calls, nothing added to the prompt.

| The mistake             | What pawl does             | Piece                 |
| ----------------------- | -------------------------- | --------------------- |
| Floods a chat room or   | Caps how many messages go  | send-budget           |
: inbox                   : out per channel per day    :                       :
| Pastes internal paths,  | Scans outgoing text and    | egress-firewall       |
: tokens, or hostnames    : blocks it                  :                       :
: into a message          :                            :                       :
| Writes text that reads  | Scores the draft and       | prose-gate            |
: like a bot              : blocks it above a          :                       :
:                         : threshold                  :                       :
| Runs `git reset --hard` | Blocks git commands that   | destructive-git-guard |
: or a `--no-verify`      : discard work or skip hooks :                       :
: commit and loses work   :                            :                       :
| Runs `while true; do    | Blocks commands that never | poll-loop-guard       |
: sleep` or `tail -f` and : return                     :                       :
: hangs                   :                            :                       :
| Ends its turn with      | Refuses to stop until the  | idle-task-gate        |
: background jobs still   : jobs are named or killed   :                       :
: running                 :                            :                       :
| Lets failing tests or   | Keeps a baseline that can  | ratchet-baseline      |
: lint counts creep up    : only go down               :                       :
| Keeps retrying a cron   | Pauses the job after       | circuit-breaker       |
: job that fails every    : repeated failures, with    :                       :
: night                   : backoff                    :                       :
| Claims a bug is fixed   | Requires the test to fail  | repro-fence           |
: without proving it      : before the fix and pass    :                       :
:                         : after                      :                       :
| Grows always-on prompt  | Caps the token size of     | prompt-budget         |
: files until they cost   : those files                :                       :
: more than they help     :                            :                       :
| Calls the same tool     | Asks a human before the    | oscillation-breaker   |
: with the same arguments : third identical call       :                       :
: in a loop               :                            :                       :
| Sends an edit whose     | Refuses the zero-diff edit | noop-edit-guard       |
: replacement equals its  : and points back to         :                       :
: target                  : view_file                  :                       :
| Writes invisible        | Strips them so the write   | zero-width-sanitizer  |
: zero-width characters   : lands clean                :                       :
: into a file             :                            :                       :
| Stalls on a permission  | Auto-approves commands     | readonly-pass         |
: prompt for `ls` or      : that provably only read    :                       :
: `git log`               :                            :                       :
| Re-reads its own        | Refuses past a per-turn    | reread-guard          |
: transcript after every  : limit                      :                       :
: context truncation      :                            :                       :
| Reads another           | Asks the user first        | conversation-fence    |
: conversation's private  :                            :                       :
: files                   :                            :                       :

A pawl is the small part in a ratchet that lets the wheel move forward and stops
it from slipping back. Every check here works the same way: the current state is
the floor.

[TOC]

## What it is not

*   Not a model, model router, or model picker. `pawl` never names, selects,
    or calls a model. Use whatever model is available in your Antigravity
    client.
*   Not a replacement for other plugins in your agent harness. Skill packs and
    shell guards keep doing their jobs; `pawl` adds gates on what the agent
    sends, commits and reads. Each plugin registers its own `PreToolUse` hooks
    and the agent harness runs them all.
*   Not a workflow engine. Nothing here spawns subagents, reads your chat, or
    schedules anything.

## Install

From a checkout:

```bash
./install.sh
```

`install.sh` symlinks the checkout to `~/.gemini/config/plugins/pawl`, adds
`{"path": "plugins/pawl"}` to `~/.gemini/config/plugins.json` next to any
plugins already listed, and runs the test battery. Running it again changes
nothing. It refuses to replace a directory or a link that points somewhere
else unless you pass `--force`. The tests need `pytest`; the plugin itself
needs only the Python standard library. Set `PAWL_PYTHON` to pick the
interpreter.

By hand:

```bash
mkdir -p ~/.gemini/config/plugins
ln -s "$PWD" ~/.gemini/config/plugins/pawl
python3 -B ~/.gemini/config/plugins/pawl/run_tests.py
```

Expected: 20 lines starting `OK`. Then add the entry to
`~/.gemini/config/plugins.json`:

```json
{"entries": [{"path": "plugins/pawl"}]}
```

Restart Antigravity after either path.

Confirm after restart with the plugin inventory in your client; the
plugin name is `pawl`.

## What the hooks do

Ten hook groups in `hooks.json`: three `PreToolUse` gates on `run_command`, the
oscillation breaker on every tool with a `Stop` entry that clears its ring, five
guard pieces on edits and reads, and one `Stop` hook for idle tasks. Each is
its own group, so you can disable one without the others.

### `pawl-send-gates`: `hooks/pawl_hook.py`

Acts only on commands that send something (chat, mail, social post); every
other command returns `allow` with no state written.

| Order | Gate | Fails | Deny prefix |
| ----- | ---- | ----- | ----------- |
| 1 | egress firewall (paths, long tokens, hostnames, reasoning tags, email domains) | closed | `[PAWL egress]` |
| 2 | prose gate on the quoted message text, 40+ words only | open | `[PAWL prose]` |
| 3 | send budget, one unit per channel per local day | open | `[PAWL budget]` |

Defaults: chat space 8, owner DM 12, email 6, social 3 per day.

### `pawl-destructive-git-guard`: `hooks/pawl_git_hook.py`

Prompts (`force_ask`) on `git reset|checkout|restore|stash|clean|rm` forms that
discard work in a protected repo (`PAWL_GIT_PROTECTED_ROOTS`, default the git
toplevel of the call's cwd), and in any repo on a `git commit` with
`--no-verify`, a short cluster carrying `n`, or output piped to
`tail`/`head`/`/dev/null`, or a `git worktree add` onto tmpfs (`/tmp`,
`/dev/shm`, `/run`). Only a human click runs the command; the row in
`denials.jsonl` carries outcome `force_ask`. Unparsable stdin is a deny. Reason
prefix `[PAWL git]`.

### `pawl-poll-loop-guard`: `hooks/pawl_poll_hook.py`

Prompts (`force_ask`) on a command that is an unbounded wait: a loop with
`sleep`, `tail -f`, `watch`, or a bare `sleep` over 600 s. A leading `timeout N`
with N <= 600 allows it. Only a human click runs the command. Unparsable stdin
is a deny. Reason prefix `[PAWL poll]`.

### `pawl-oscillation-breaker`: `hooks/pawl_oscillation_hook.py`

On every tool call, records `(tool, sha1 of args)` in a 16-slot ring per
conversation and prompts (`force_ask`) when the same call repeats three times in
a row or the last two or three calls repeat the ones before them. Intent fields
and a growing log read's `EndLine` are dropped before hashing, so three
`manage_task` status checks on one task prompt too. The ring clears on
`schedule` and at turn end, so cron wakeups never add up. Fails open. Reason
prefix `[PAWL loop]`.

### `pawl-noop-edit-guard`: `hooks/pawl_noop_edit_hook.py`

Denies a `replace_file_content` whose replacement equals its target, and a
`multi_replace_file_content` where every chunk is a no-op, sending the agent
back to `view_file`. Fails open. Reason prefix `[PAWL no-op]`.

### `pawl-zero-width-sanitizer`: `hooks/pawl_zero_width_hook.py`

Strips zero-width space, joiners, BOM, word joiner and soft hyphen from the
content of `write_to_file` and `replace_file_content` through an `overwrite`
block, so the write still lands. Never blocks.

### `pawl-conversation-fence`: `hooks/pawl_fence_hook.py`

On every tool, prompts (`force_ask`) before a read of another conversation's
`brain/<id>/`, its transcript, a sweep of `brain/` or `conversations/`, or
`conversation_summaries.db`. This conversation and its direct parent or child
pass. `PAWL_CONVERSATION_FENCE_STRICT=1` denies instead. Fails open. Reason
prefix `[PAWL fence]`.

### `pawl-reread-guard`: `hooks/pawl_reread_hook.py`

Denies the 11th unbounded read of this conversation's own transcript, or the
6th read of the same unchanged `SKILL.md` or memory file, in one user turn.
Bounded reads never count; after three denials in a turn it stands down until
the next. Fails open. Reason prefix `[PAWL reread]`.

### `pawl-readonly-pass`: `hooks/pawl_readonly_hook.py`

Answers `auto_approve` when every clause of a shell command provably only
reads, `allow` otherwise. Never denies or asks; every other hook's deny,
force_ask or ask still wins. `PAWL_READONLY_PASS_OFF=1` turns it off.

### `pawl-idle-task-gate`: `hooks/pawl_stop_hook.py`

On Stop, lists background tasks from this conversation older than
`PAWL_IDLE_TASK_MINUTES` (default 10). First stop with a given set: `block` and
name them. Second stop with the same set: SIGTERM the ones that are wait shapes,
allow. Fails open. Reason prefix `[IDLE TASK]`.

### Overrides

| Need                                | Do                                    |
| ----------------------------------- | ------------------------------------- |
| Skip one gate this session          | `PAWL_DISABLE=egress,prose,budget`    |
:                                     : (any subset)                          :
| See how often each gate fires       | `python3 hooks/pawl_hook.py stats`    |
:                                     : (reads                                :
:                                     : `$PAWL_DATA/gate_events.jsonl`)       :
| One send past the ceiling           | approve the prompt Antigravity shows; |
:                                     : only a human click passes the ceiling :
| One git, poll or repeated call past | approve the prompt Antigravity shows; |
: a gate                              : the row is logged as a human override :
| Change ceilings                     | `SEND_BUDGET_CEILINGS='{"chat_space": |
:                                     : 4}'`                                  :
| Change egress rules                 | edit `$PAWL_DATA/egress_rules.json`   |
:                                     : (default `~/.pawl/`; the send budget  :
:                                     : counter lands in the same dir)        :

## Pieces

Seventeen pieces: sixteen deterministic checks (the zero-width sanitizer and
the read-only pass among them) and `report`, a viewer that tallies what the
checks blocked.

Piece                 | Wire point                 | Command
--------------------- | -------------------------- | -------
prose-gate            | hook, pre-send, CI on docs | `pieces/prose-gate/prose_gate.py --plane chat draft.md`
egress-firewall       | hook, pre-send             | `pieces/egress-firewall/egress_firewall.py check < text`
send-budget           | hook                       | `pieces/send-budget/send_budget.py status`
ratchet-baseline      | pre-commit, CI             | `pieces/ratchet-baseline/ratchet.py check --baseline .ratchet.json --metric failing_tests=N`
circuit-breaker       | cron, sidecars             | `pieces/circuit-breaker/breaker.py run nightly -- ./job.sh`
repro-fence           | pre-commit on bug fixes    | `pieces/repro-fence/repro_fence.py both --cmd "pytest tests/test_x.py" --file src/x.py`
prompt-budget         | pre-commit on prompt files | `pieces/prompt-budget/prompt_budget.py check --config budget.json`
destructive-git-guard | hook                       | `pieces/destructive-git-guard/destructive_git_guard.py check --cwd . git reset --hard`
poll-loop-guard       | hook                       | `pieces/poll-loop-guard/poll_loop_guard.py classify "while true; do sleep 5; done"`
idle-task-gate        | Stop hook                  | `pieces/idle-task-gate/idle_task_gate.py list <conversation-id>`
oscillation-breaker   | hook, every tool           | `pieces/oscillation-breaker/oscillation_breaker.py check <conversation-id> view_file '{"path": "a"}'`
noop-edit-guard       | hook, edits                | `pieces/noop-edit-guard/noop_edit_guard.py check replace_file_content '{"TargetContent": "a", "ReplacementContent": "a"}'`
zero-width-sanitizer  | hook, writes               | `pieces/zero-width-sanitizer/zero_width_sanitizer.py strip < draft.txt`
readonly-pass         | hook                       | `pieces/readonly-pass/readonly_pass.py check git log -5`
reread-guard          | hook, reads                | `pieces/reread-guard/reread_guard_hook.py < payload.json`
conversation-fence    | hook, every tool           | `pieces/conversation-fence/conversation_fence_hook.py < payload.json`
report                | CLI, retro                 | `pieces/report/report.py --days 7`

Each piece directory has its own `README.md` and tests. One skill,
`skills/pawl/SKILL.md`, routes an agent by denial prefix or task to
`skills/pawl/references/<piece>.md`, which carries that piece's flag table.

## Installation

Install into all detected agent harnesses
(`~/.gemini`, `~/.claude`, `~/.codex`):

```bash
./install.sh
```

Or target a specific harness:

```bash
./install.sh --antigravity  # Google Antigravity / Jetski (~/.gemini/config)
./install.sh --claude       # Claude Code (~/.claude/plugins/pawl)
./install.sh --codex        # OpenAI Codex / Agent Skills (~/.codex, ~/.agent-skills)
```

## Eval

`eval/` holds a gates-on vs gates-off ablation suite: 24 tasks with a
temptation in each (10 code-change, 6 repo-hygiene, 8 outbound), throwaway
git fixtures, stub senders and script graders, with no LLM judge.
`eval/run_arms.sh` runs both arms; see `eval/README.md`. It is not part of
the installed plugin.

## Verify a checkout

```bash
python3 -B run_tests.py
python3 -B check_portable.py
```

`run_tests.py` runs the 20 suites, the last being the eval grader twins; the
suites use `pytest`. CI runs both commands on Linux and macOS with Python 3.11,
3.12 and 3.13 (`.github/workflows/ci.yml`).
`check_portable.py` exits 1 when the tree carries an absolute home path, ships
an `agents/` or `mcp_config.json`, uses an absolute path in a hook command,
imports anything outside the standard library, carries a CR byte (CRLF line
ending) in any text file, or has a markdown prose line over 80 columns (fenced
code, tables, HTML, URLs and lone code spans are exempt).
