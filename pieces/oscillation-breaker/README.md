# Oscillation breaker

One piece of the CoS blueprint, published on its own so you can run it in ten
minutes. Four files, standard library only, no dependency on the rest of the
system.

## What it does

Keeps a short ring of recent tool calls per conversation, each stored as `(tool,
sha1 of canonical JSON args)`, and asks a human to confirm the next call when
the ring shows a repeat:

-   the same `(tool, args)` three times in a row;
-   a 2-gram cycle: the last two calls equal the two before them;
-   a 3-gram cycle: the last three calls equal the three before them.

The decision is `force_ask`, not deny: the host shows the reason and one click
lets the call run. Every prompt lands as one row in `PAWL_DATA/denials.jsonl`
with gate `OSCILLATION` and outcome `force_ask`.

Idea from Alex Lementuev's session-retro loop_breaker.

## The problem it exists for

An agent that reads the same file, gets the same answer, and reads it again is
not making progress, and nothing in the harness notices; each call is valid on
its own. The same goes for two-step dances: edit, fail, edit back, fail. A short
memory of what was just called turns a silent burn into one prompt.

## Files

| File                          | Purpose                                      |
| ----------------------------- | -------------------------------------------- |
| `oscillation_breaker.py`      | Ring, detector, hook decision and CLI. Ring  |
:                               : saved with a temp file and `os.replace`.     :
| `oscillation_breaker_hook.py` | PreToolUse hook. Reads the tool call JSON on |
:                               : stdin, prints `{"decision"\: "allow"}` or    :
:                               : `{"decision"\: "force_ask", "reason"\:       :
:                               : "[PAWL loop] ..."}`. Fails open on           :
:                               : everything.                                  :
| `test_oscillation_breaker.py` | 19 tests: ring persistence, window cap,      |
:                               : triple repeat, 2-gram and 3-gram cycles,     :
:                               : no-fire cases, exempt tools, hook fail-open  :
:                               : paths, CLI exit codes.                       :
| `README.md`                   | This file.                                   |

## Run it

```bash
python3 -m pytest -q test_oscillation_breaker.py
python3 oscillation_breaker.py check my-conv view_file '{"path": "a.py"}'
python3 oscillation_breaker.py show my-conv
python3 oscillation_breaker.py reset my-conv
```

`check` records one call and exits 0 when clear, 1 on a repeat with the reason
on stdout.

## Wire it as a hook

Jetski and Antigravity read `hooks.json`. Match every tool, not only
`run_command`:

```json
{
  "oscillation-breaker": {
    "enabled": true,
    "PreToolUse": [
      {
        "matcher": ".*",
        "hooks": [
          {"type": "command", "command": "python3 /path/to/oscillation_breaker_hook.py", "timeout": 15}
        ]
      }
    ]
  }
}
```

Other harnesses: `tool_and_args()` and `resolve_conversation_id()` in
`oscillation_breaker.py` are the two functions to adapt to a different payload
shape.

## Configuration

| Variable                      | Meaning                 | Default   |
| ----------------------------- | ----------------------- | --------- |
| `PAWL_DATA`                   | State dir; rings under  | `~/.pawl` |
:                               : `oscillation/<id>.json` :           :
| `PAWL_OSCILLATION_WINDOW`     | Ring size (min 6)       | 16        |
| `PAWL_OSCILLATION_WATCHDOG_S` | Seconds before the hook | 14        |
:                               : fails open on time      :           :

## Design notes

-   Prompt, do not block. A repeat is sometimes right (re-reading after an
    edit); the human is the one who knows.
-   Hash the arguments. The ring never stores a file path, a command line, or a
    message body.
-   Exempt the tools that repeat by design: `manage_task`, `schedule`,
    `send_message`, `ask_question`.
-   Fail open everywhere. No conversation id, bad stdin, an unwritable state
    dir, a watchdog timeout: the call runs and nothing is written.

## What the full system adds on top

In the blueprint the same ring feeds a session retro that names the loop after
the fact. You need none of that for the breaker to do its job.
