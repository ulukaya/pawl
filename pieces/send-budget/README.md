# Send budget

One piece of the CoS blueprint, published on its own so you can run it in ten
minutes. Three files, standard library only, no dependency on the rest of the
system.

## What it does

Every outbound message the agent sends burns one unit from a per-channel daily
ceiling. At the ceiling the gate denies the next send until the local day rolls
over. The gate also denies the owner-DM channel overnight. The gate counts
denials, so you get a number for how often the agent wanted to talk and was told
no.

Defaults: `dm_owner` 12, `chat_space` 8, `email` 6, `social` 3 per day; quiet
window 22:00 to 07:00 on `dm_owner`.

## The problem it exists for

An agent with a chat tool and a standing instruction to "keep me posted" will
post. Long sessions, sidecars, and subagents each think their update is the
important one. The human ends up with 40 DMs, stops reading them, and misses the
one that mattered. A prompt line ("do not over-message") lowers the rate for a
while. A counter that returns exit 3 lowers it permanently.

## Files

| File | Purpose |
|---|---|
| `send_budget.py` | The counter and CLI. Exclusive `flock` around read-modify-write, atomic write via temp file + `os.replace`, 30-day history on day rollover. |
| `send_budget_hook.py` | PreToolUse hook. Reads the tool call JSON on stdin, classifies the command line, spends, prints `{"decision": "allow"}` or `{"decision": "deny", "reason": ...}`. Fail-open on any error. |
| `test_send_budget.py` | 17 tests against a real temp state dir: classification, ceiling, quiet window, rollover, override, 12-process race, CLI exit codes, hook decisions. |

## Run it

```bash
python3 -m pytest -q test_send_budget.py
python3 send_budget.py status
python3 send_budget.py spend --channel email --reason "weekly summary"
python3 send_budget.py check --channel email
```

`spend` and `check` exit 0 when allowed and 3 when denied.

## Wire it as a hook

Jetski and Antigravity read `hooks.json`. Add a group like this (adjust the
path):

```json
{
  "send-budget": {
    "enabled": true,
    "PreToolUse": [
      {
        "matcher": "run_command",
        "hooks": [
          {"type": "command", "command": "python3 /path/to/send_budget_hook.py", "timeout": 15}
        ]
      }
    ]
  }
}
```

Set `SEND_BUDGET_OWNER_SPACE` to the substring that identifies a DM to you (for
Google Chat, your DM space id), so those sends land on `dm_owner` instead of
`chat_space`.

Other harnesses: the hook only needs a command string. `_command_from()` in
`send_budget_hook.py` is the one function to adapt to a different payload shape.

## Configuration

| Variable                  | Meaning            | Default                      |
| ------------------------- | ------------------ | ---------------------------- |
| `SEND_BUDGET_STATE_DIR`   | Where              | `~/.local/state/send_budget` |
:                           : `send_budget.json` :                              :
:                           : and the lock live  :                              :
| `SEND_BUDGET_TZ`          | IANA zone for the  | system local time            |
:                           : local day          :                              :
| `SEND_BUDGET_OWNER_SPACE` | Substring marking  | unset (all chat sends are    |
:                           : a chat send as a   : `chat_space`)                :
:                           : DM to the owner    :                              :
| `SEND_BUDGET_CEILINGS`    | JSON object        | built-in defaults            |
:                           : overriding         :                              :
:                           : ceilings, e.g.     :                              :
:                           : `{"email"\: 2}`    :                              :
| `SEND_BUDGET_OVERRIDE=1`  | CLI `spend` only:  | unset                        |
:                           : force allow for a  :                              :
:                           : send a human runs  :                              :
:                           : by hand; recorded  :                              :
:                           : as overridden. The :                              :
:                           : full-system hook   :                              :
:                           : ignores this       :                              :
:                           : variable and       :                              :
:                           : returns            :                              :
:                           : `force_ask` at the :                              :
:                           : ceiling instead    :                              :

## Design notes

-   Fail closed on the send, fail open on the tool. A broken counter must never
    block `ls`; a full counter must block the send.
-   Count the denial before you refuse. The denial ledger is the metric; without
    it you only know the ceiling existed, not that it did anything.
-   One JSON file, one lock. Twelve processes hammering it in the test land
    exactly twelve outcomes. Anything fancier than `flock` + `os.replace` is a
    second system to debug.
-   Override is an environment variable, not a flag, so a human has to ask for
    it in the same turn. The gate records it too.

## What the full system adds on top

In the blueprint this counter feeds a wider outbound-text filter (content scan,
audit log, per-turn prose scoring) and its denial counts roll into a Sunday
retro. You need none of that for the budget to do its job.
