# Idle task gate

One piece of the CoS blueprint, published on its own so you can run it in ten
minutes. Three files, standard library only, no dependency on the rest of the
system.

## What it does

A Stop hook. When the agent tries to end its turn, the gate walks procfs for
task-root processes tagged with this conversation's id that have run longer than
the age limit (default 10 minutes). First stop with a given set of stale pids:
block the stop and name each pid, its age and its command line. Second stop with
the same set: SIGTERM the process groups whose command line is an unbounded wait
shape (a poll loop, `tail -f`, a long `sleep`) and allow the stop. Anything old
that is not a wait shape is left running and reported on stderr, so a dev server
survives and a forgotten poll loop does not.

## The problem it exists for

A background watcher that never found its exit phrase sat idle for nine hours
after the turn it belonged to had ended. Nothing looked back at the process
table when the agent stopped. Pairing this gate with the poll-loop-guard piece
covers both ends: deny the wait shape before it starts, and sweep whatever got
through when the turn ends.

## Files

| File                     | Purpose                                           |
| ------------------------ | ------------------------------------------------- |
| `idle_task_gate.py`      | `idle_task_roots(conv)` walks procfs;             |
:                          : `breaker_permits_block()` keeps the               :
:                          : one-block-per-pid-set state;                      :
:                          : `terminate_idle_waits()` signals wait shapes;     :
:                          : `decide()` builds the Stop decision; inlined      :
:                          : watchdog, denial log and a CLI. Carries a         :
:                          : verbatim copy of `classify()` from the            :
:                          : poll-loop-guard piece between `CLASSIFY BEGIN`    :
:                          : and `CLASSIFY END` markers.                       :
| `idle_task_gate_hook.py` | Stop hook. Reads the Stop payload JSON on stdin   |
:                          : and prints `{"decision"\: "allow"}` or            :
:                          : `{"decision"\: "block", "reason"\: ...}`.         :
| `test_idle_task_gate.py` | 20 tests on a fake procfs tree in a temp dir:     |
:                          : root discovery, parent/child collapse, other      :
:                          : conversations, the minutes threshold, the allow   :
:                          : regex, a real child process SIGTERMed on the      :
:                          : second stop, breaker reset on a new pid set,      :
:                          : fail-open paths, the hook process, the CLI, and a :
:                          : byte-identity check of the copied `classify`      :
:                          : block.                                            :

## Run it

```bash
python3 -m pytest -q test_idle_task_gate.py
python3 idle_task_gate.py list <conversation-id>
python3 idle_task_gate.py classify tail -f build.log
```

`list` prints one JSON row per stale root (`pid`, `pgid`, `age`, `cmd`).
`classify` prints `LOOP`, `TAIL`, `SLEEP` or `none`.

## Wire it as a hook

Jetski and Antigravity read `hooks.json`. The Stop event takes a flat list
(adjust the path):

```json
{
  "idle-task-gate": {
    "enabled": true,
    "Stop": [
      {"type": "command", "command": "python3 /path/to/idle_task_gate_hook.py", "timeout": 15}
    ]
  }
}
```

The host passes `{"conversationId": "..."}` on stdin. Background tasks the host
starts carry `ANTIGRAVITY_CONVERSATION_ID` in their environment; that is how a
process is matched to the conversation. Another harness needs the same two
facts: a conversation id on stdin and a matching variable in each task's environ
(`CONV_ENV` in `idle_task_gate.py`).

## Configuration

| Variable | Meaning | Default |
|---|---|---|
| `PAWL_IDLE_TASK_MINUTES` | Age limit in minutes | `10` |
| `PAWL_IDLE_TASK_ALLOW_RE` | Regex; matching command lines are never reported or signalled | unset |
| `PAWL_IDLE_TASK_PROC_ROOT` | procfs root; tests point it at a fake tree | `/proc` |
| `PAWL_IDLE_TASK_STATE` | Breaker state file | `PAWL_DATA/idle_task_gate.json` |
| `PAWL_DATA` | State dir and `denials.jsonl` | `~/.pawl` |
| `PAWL_IDLE_TASK_WATCHDOG_S` | Seconds before the watchdog prints allow and exits | 14 (host timeout 15 minus 1) |

## Design notes

-   A Stop hook fails open everywhere: unparsable stdin, a missing procfs, an
    internal error, or the watchdog firing all print allow. Blocking a stop
    forever is worse than one leaked task.
-   Block once, then act. The breaker keys on the sorted pid set, so a new stale
    task resets the strike and gets its own warning before anything is
    signalled.
-   Only wait shapes are terminated, and the decision reuses the exact
    `classify()` the PreToolUse guard uses. The two copies are asserted
    identical by a test that reads both files.
-   A task root is a pid whose environ carries the conversation id and whose
    parent's does not, so a shell wrapper and its children count once, and the
    whole process group is signalled.
-   State writes go through a temp file and `os.replace`, so a crash mid-write
    cannot corrupt the breaker.

## What the full system adds on top

In the blueprint the block reasons and denial rows feed a Sunday retro that
counts how often each conversation leaked a task. You do not need that for this
gate to do its job.
