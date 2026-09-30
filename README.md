# pawl

<img src="assets/logo.svg" width="96" alt="pawl mark: a hook holding a gear">

Plugin for Antigravity.

Questions or bugs: open an issue at https://github.com/ulukaya/pawl/issues
or email ulukaya@gmail.com.

Agents make the same mistakes over and over, and telling them not to in the
prompt stops working after a page. `pawl` is eleven small checks that run as
code, not as instructions, and a report command that tallies what they blocked.
Each check watches for one mistake and refuses it. Plain Python, no model calls,
nothing added to the prompt.

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

A pawl is the small part in a ratchet that lets the wheel move forward and stops
it from slipping back. Every check here works the same way: the current state is
the floor.

[TOC]

## What it is not

*   Not a model, model router, or model picker. `pawl` never names, selects,
    or calls a model. Use whatever model is available in your Jetski client.
*   Not a replacement for gpowers. gpowers gives an agent skills and a bash
    guard; `pawl` adds gates on what the agent sends and commits. They run
    side by side; both register `PreToolUse` hooks on `run_command` and Jetski
    runs both.
*   Not a workflow engine. Nothing here spawns subagents, reads your chat, or
    schedules anything.

## Install

Add the plugin entry and restart Jetski.

Local checkout:

```bash
mkdir -p ~/.gemini/config/plugins
cp -r /path/to/pawl ~/.gemini/config/plugins/pawl
python3 -B ~/.gemini/config/plugins/pawl/run_tests.py
```

Expected: 14 lines starting `OK`.

In `~/.gemini/config/plugins.json`:

```json
{"entries": [{"path": "plugins/pawl"}]}
```

Confirm after restart with the plugin inventory in your client; the
plugin name is `pawl`.

## What the hooks do

Five hook groups in `hooks.json`: three `PreToolUse` hooks on `run_command`, one
`PreToolUse` hook on every tool, and one `Stop` hook. Each is its own group, so
you can disable one without the others.

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
`tail`/`head`/`/dev/null`. Only a human click runs the command; the row in
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
a row or the last two or three calls repeat the ones before them. Fails open.
Reason prefix `[PAWL loop]`.

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
| One send past the ceiling           | approve the prompt Jetski shows; only |
:                                     : a human click passes the ceiling      :
| One git, poll or repeated call past | approve the prompt Jetski shows; the  |
: a gate                              : row is logged as a human override     :
| Change ceilings                     | `SEND_BUDGET_CEILINGS='{"chat_space": |
:                                     : 4}'`                                  :
| Change egress rules                 | edit `$PAWL_DATA/egress_rules.json`   |
:                                     : (default `~/.pawl/`; the send budget  :
:                                     : counter lands in the same dir)        :

## Pieces

Twelve pieces: eleven checks and `report`, a viewer that tallies what the checks
blocked.

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
report                | CLI, retro                 | `pieces/report/report.py --days 7`

Each piece directory has its own `README.md` and tests. Skills under `skills/`
give an agent the flag table for each piece.

## Verify a checkout

```bash
python3 -B run_tests.py
python3 -B check_portable.py
```

`run_tests.py` runs the 14 suites. `check_portable.py` exits 1 when the tree
carries an absolute home path, ships an `agents/` or `mcp_config.json`, uses an
absolute path in a hook command, imports anything outside the standard library,
carries a CR byte (CRLF line ending) in any text file, or has a markdown prose
line over 80 columns (fenced code, tables, HTML, URLs and lone code spans are
exempt).
