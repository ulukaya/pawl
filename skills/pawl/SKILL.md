---
name: pawl
description: "Deterministic gates for agent harnesses. Use when a call is denied, prompted or blocked with a [PAWL egress|prose|budget|git|poll|loop|no-op|reread|fence] or [IDLE TASK] reason, or before sending a message, discarding git work, waiting on a background task, fixing a bug with a reproducer, or growing an always-loaded prompt file."
---

# pawl

`<root>` is the plugin root, two directories above this file. Pieces live
under `<root>/pieces/<name>/`; every hook runs `<root>/hooks/pawl.py`. State
lives under `PAWL_DATA` (default `~/.pawl`).

## Denial prefixes

-   `[PAWL egress]`, `[PAWL prose]`, `[PAWL budget]` -> `send-gates.md`
-   `[PAWL git]` -> `destructive-git-guard.md`
-   `[PAWL poll]` -> `poll-loop-guard.md`
-   `[PAWL loop]` -> `oscillation-breaker.md`
-   `[PAWL no-op]` -> `noop-edit-guard.md`
-   `[PAWL reread]` -> `reread-guard.md`
-   `[PAWL fence]` -> `conversation-fence.md`
-   `[IDLE TASK]` -> `idle-task-gate.md`

## Routing

Each arrow names the page to read under `references/` beside this file.

-   Send (chat, mail, social post): gate `send`, egress then prose then
    budget -> `send-gates.md`
-   Destructive `git` in a protected repo, `git commit -n`, a worktree on
    tmpfs, a force push, `git branch -D`: gate `git` asks; by hand
    `destructive_git_guard.py check` -> `destructive-git-guard.md`
-   Poll loop, `tail -f`, `watch`, `sleep` over 600 s: gate `poll` asks; by
    hand `poll_loop_guard.py classify` -> `poll-loop-guard.md`
-   Turn end with background waits still running: gate `idle` blocks once ->
    `idle-task-gate.md`
-   Same tool call 3 times, or 2-3 calls alternating: gate `loop` asks ->
    `oscillation-breaker.md`
-   Edit whose replacement equals its target, or a write of the bytes
    already on disk: gate `noop` denies -> `noop-edit-guard.md`
-   Invisible characters in a write: gate `zero-width` strips them ->
    `zero-width-sanitizer.md`
-   Read-only shell command: gate `readonly` approves; by hand
    `readonly_pass.py check` -> `readonly-pass.md`
-   Re-reading the own transcript, a `SKILL.md` or a memory file: gate
    `reread` denies past the limit -> `reread-guard.md`
-   Reading another conversation's files: gate `fence` asks ->
    `conversation-fence.md`
-   Which gates fired this week: `report.py --days 7` -> `report.md`
-   Text a human will read: `prose_gate.py --plane chat|deliverable` ->
    `prose-gate.md`
-   Outbound text with paths, tokens, hostnames: `egress_firewall.py check`
    -> `egress-firewall.md`
-   Sends per channel per day: `send_budget.py status|check|spend` ->
    `send-budget.md`
-   A count that may only go down: `ratchet.py check|update|show` ->
    `ratchet.md`
-   Scheduled job failing repeatedly: `breaker.py run <name> -- <cmd>` ->
    `circuit-breaker.md`
-   Bug fix needing a failing reproducer: `repro_fence.py red|fence|both` ->
    `repro-fence.md`
-   Always-loaded files growing: `prompt_budget.py check|report --config
    budget.json` -> `prompt-budget.md`

## Contracts

-   CLIs exit 0 pass, 1 fail, 2 internal error or missing input (piece pages
    list extras: breaker 3 = skipped).
-   A gate that asks: Antigravity `force_ask`, Claude Code `ask`. Codex
    hooks cannot ask, so there it denies with the same reason; tell the user.
-   The egress firewall, git and poll guards fail closed; every other gate
    fails open.
-   Skip gates for a session: `PAWL_DISABLE=git,poll,send,...` (any gate
    name, or `egress`, `prose`, `budget`); `<root>/hooks/pawl.py gates` lists
    them.
-   Never edit a piece to make a gate pass. Fix the draft, command, count, or
    reason.

## Verify

```bash
python3 -B <root>/run_tests.py
python3 -B <root>/check_portable.py
```

Expected: one `OK` line per suite, then `portable: clean`, exit 0.
