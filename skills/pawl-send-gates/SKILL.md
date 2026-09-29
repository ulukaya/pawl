---
name: pawl-send-gates
description: "What the pawl PreToolUse hook does to a send-shaped run_command (chat, mail, social post), how to read a deny, and how to recover without weakening the gate."
---

# pawl-send-gates

Hook: `${PLUGIN_ROOT}/hooks/pawl_hook.py`, registered in `hooks.json` on matcher
`run_command|run_shell_command`, timeout 10 s.

## What counts as a send

`send_budget.classify_command`, per shell clause (split at `|`, `&&`, `;`,
newline; heredoc bodies dropped): the executable basename must match the tool
column AND one of the first 3 bare arguments must match the verb column.
`--help` or `-h` anywhere in the clause is never a send.

| Channel | Tool (basename) | Verb |
|---|---|---|
| `chat_space` / `dm_owner` | `gchat*\|slack\|chat` | `send\|send-message\|send-direct-message\|post` |
| `email` | `gmail*\|mail\|sendmail` | `send\|send-message\|reply\|reply-all` |
| `social` | `bsky\|bluesky\|linkedin\|x_post\|post_social\|tweet` | `post\|publish\|send` |

Anything else: `{"decision":"allow"}` with no state touched. A command whose
executable is `send_budget.py` itself (any interpreter or env prefix) is never a
send, whatever its arguments say; neither is a `grep`, `diff`, or heredoc that
merely mentions a send tool.

## Order and failure mode

| # | Gate | Input | Fails |
|---|---|---|---|
| 1 | egress firewall | outbound text only: values of `--text --message --body --subject --title --to --cc --bcc --space --user -m`, heredoc bodies, and the contents of files read via `$(cat F)` or `$(< F)`; any other `$(...)` or backtick in a value is a deny; no extractable payload falls back to the whole command line | closed |
| 2 | prose gate | longest quoted string, only when 40+ words, plane `chat` | open |
| 3 | send budget | one unit on the channel for the local day | open |

## Reading a deny

-   `[PAWL egress] DENY <class> <name> at offset N`: rule hit or rules file
    unreadable. Remove the path, token, address or hostname; fix
    `egress_rules.json` if broken.
-   `[PAWL prose] draft scored S over T; tells: ...`: machine-sounding draft.
    Rewrite in plain words; `prose_gate.py --plane chat --why -` shows each
    span.
-   `[PAWL budget] <channel>: <why> (N/M today). Approve to send anyway; the
    send is logged as a human override.`: ceiling burned or owner-DM quiet
    window; decision is `force_ask`, not deny. Approve the prompt; only a human
    click passes. No command text or env var changes this.

## Knobs (environment)

-   `PAWL_DATA`: state and rules dir (default `~/.pawl`); seeds
    `SEND_BUDGET_STATE_DIR` when unset.
-   `PAWL_DISABLE`: comma list of `egress,prose,budget` to skip.
-   `PAWL_DATA/gate_events.jsonl`: one row per evaluated gate (`ts, gate,
    decision, override`); budget at the ceiling logs `ask`, override true.
-   `PAWL_DATA/send_budget.json`: counters; `last_spend.reason` holds the tool
    path only, never a recipient or message text.
-   `python3 hooks/pawl_hook.py stats`: per-gate allow/deny/override counts; a
    gate at 0% or 100% deny is broken.
-   `SEND_BUDGET_CEILINGS`: JSON, e.g. `{"chat_space": 8, "email": 6}`.
-   `SEND_BUDGET_OWNER_SPACE`: space id that maps to `dm_owner`.
-   `SEND_BUDGET_TZ`: IANA zone for the day boundary.

## Test

```bash
cd ${PLUGIN_ROOT}/hooks && python3 -B -m pytest -q hooks_test.py
```

Expected: `43 passed`.
