# Privacy

pawl runs entirely on your machine. It makes no network connections, sends
no telemetry, and calls no model. Nothing it records leaves your computer
unless you copy it somewhere yourself.

## What it reads

*   The tool call each hook receives from your agent harness: the tool name
    and its arguments (a shell command, a file path, the text of an edit).
*   The harness's transcript path, to tell this conversation's transcript
    from another's and to count turns.
*   On Antigravity, `conversation_summaries.db`, opened read-only for two
    columns (a conversation id and its parent), and `/proc`, to find this
    conversation's background tasks.
*   The output of `git rev-parse --show-toplevel`, to learn which repo a git
    command targets.

## What it writes

Everything goes under `PAWL_DATA` (default `~/.pawl`):

| File | Holds |
| --- | --- |
| `denials.jsonl` | one row per gate hit: time, conversation id, gate, outcome, and a SHA-1 of the command or path, never the command or path itself |
| `gate_events.jsonl` | one row per send-gate evaluation: time, gate, decision |
| `send_budget.json` | sends per channel per day, and the tool path of the last send (no recipient, no message text) |
| `egress_rules.json` | the egress firewall rules, copied from the bundled default |
| `oscillation/`, `reread/`, `idle_task_gate.json` | per-conversation counters and SHA-1 digests of recent calls |
| `pycache/` | compiled Python bytecode, so hooks start fast |

Delete `~/.pawl` (or your `PAWL_DATA`) to remove all of it.

## What it changes

*   **Permission prompts (Claude Code, Antigravity).** pawl approves shell
    commands it can prove only read (`ls`, `git log`, `rg`, ...) without
    showing you the prompt. Turn this off with the plugin's "Approve
    read-only shell commands" setting in `/config`, or
    `PAWL_READONLY_PASS_OFF=1`. On Codex pawl never skips a prompt. Every
    other gate can only make a call stricter: it asks, refuses, or strips
    invisible characters from a write.
*   **Background tasks (Antigravity).** When a turn ends twice with the same
    unbounded waits still running (`tail -f`, `while true; sleep`), pawl
    sends them SIGTERM. On Claude Code and Codex it only names them.
*   **Installer.** `install.sh` changes harness configuration only through
    each harness's own plugin commands, plus a symlink and one
    `plugins.json` entry for Antigravity; `--uninstall` reverses it.

Questions: https://github.com/ulukaya/pawl/issues
