# Poll loop guard

One piece of the CoS blueprint, published on its own so you can run it in ten
minutes. Three files, standard library only, no dependency on the rest of the
system.

## What it does

Denies a `run_command` whose command line is an unbounded wait: a `while True` /
`until` loop beside a sleep, `tail -f` / `watch`, or a single `sleep` longer
than 600 seconds. A leading `timeout N` (N at most 600, optional `cd X &&`
prefix) makes any of them bounded and allowed. Matching is literal on the
command text, so a heredoc that mentions `while True` still denies. Wrap it in
`timeout`.

## The problem it exists for

A worker ran `python3 -c "while True: ... time.sleep(2)"` to tail a sibling task
log for an exit phrase. The phrase never appeared in that log, so the loop sat
idle for nine hours. Nothing denied it before it started and nothing swept it
after the turn ended. The host already wakes the agent when a background task
finishes, so a hand-rolled watcher is never needed. A regex that returns `LOOP`
at PreToolUse removes the class of bug.

## Files

| File                      | Purpose                                          |
| ------------------------- | ------------------------------------------------ |
| `poll_loop_guard.py`      | `classify()` returns `LOOP`, `TAIL`, `SLEEP` or  |
:                           : `None`; `deny_reason()` writes the message;      :
:                           : inlined payload reader (fails closed on bad      :
:                           : JSON), watchdog (fails open on time), denial log :
:                           : (`PAWL_DATA/denials.jsonl`), and a CLI.          :
| `poll_loop_guard_hook.py` | PreToolUse hook. Reads the tool call JSON on     |
:                           : stdin and prints `{"decision"\: "allow"}` or     :
:                           : `{"decision"\: "deny", "reason"\: ...}`.         :
| `test_poll_loop_guard.py` | 35 tests: the classification matrix, `timeout`   |
:                           : bounding, payload shapes, fail-closed stdin,     :
:                           : denial log rows, watchdog handler, CLI exit      :
:                           : codes.                                           :

## Run it

```bash
python3 -m pytest -q test_poll_loop_guard.py
python3 poll_loop_guard.py classify tail -f build.log
python3 poll_loop_guard.py check sleep 9999
```

`classify` prints the label or `none` and exits 0. `check` exits 0 when bounded
and 1 with the deny reason when not.

## In the pawl plugin

You do not wire this piece yourself there: `hooks/pawl.py` runs it as gate
`poll` in Antigravity, Claude Code and Codex, and `hooks/harness.py`
translates each harness's payload and answer, so the piece only ever sees
the Antigravity shape below. `PAWL_DISABLE=poll` turns it off for a
session. The rest of this page is for running the piece on its own.

## Wire it as a hook

Jetski and Antigravity read `hooks.json`. Add a group like this (adjust the
path):

```json
{
  "poll-loop-guard": {
    "enabled": true,
    "PreToolUse": [
      {
        "matcher": "run_command",
        "hooks": [
          {"type": "command", "command": "python3 /path/to/poll_loop_guard_hook.py", "timeout": 15}
        ]
      }
    ]
  }
}
```

Other harnesses: the hook only needs a command string. `command_of()` in
`poll_loop_guard.py` is the one function to adapt to a different payload shape.

## Configuration

| Variable | Meaning | Default |
|---|---|---|
| `PAWL_DATA` | Where `denials.jsonl` is appended | `~/.pawl` |
| `PAWL_POLL_LOOP_GUARD_WATCHDOG_S` | Seconds before the watchdog prints allow and exits | 14 (host timeout 15 minus 1) |

## Design notes

-   Fail closed on the payload, fail open on time. Stdin that is not a JSON
    object is a deny; a hook that outruns its budget must not stall the host, so
    the watchdog prints allow.
-   The deny reason names the fix: end the turn (the host notifies on task
    completion), use the schedule tool with a task-id condition, or prefix
    `timeout 600`.
-   `sleep 600` is allowed and `sleep 601` is not. One number, no heuristics on
    intent.
-   The regex block between the `CLASSIFY BEGIN` and `CLASSIFY END` markers is
    copied verbatim into the idle-task-gate piece, which uses it at Stop time to
    decide which stale tasks to terminate. A test there asserts the two copies
    are identical.

## What the full system adds on top

In the blueprint the denial rows roll into a Sunday retro and the idle-task-gate
piece sweeps whatever slipped through at Stop. You need neither for this gate to
do its job.
