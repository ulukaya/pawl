---
name: pawl
description: "Deterministic gates for agent harnesses. Use when a tool call is denied, prompted or blocked with [PAWL egress], [PAWL prose], [PAWL budget], [PAWL git], [PAWL poll], [PAWL loop], [PAWL no-op], [PAWL reread], [PAWL fence] or [IDLE TASK], or before sending a message, discarding work with git, waiting on a background task, fixing a bug with a reproducer, running a scheduled job, or growing an always-loaded prompt file. Routes to 17 stdlib Python pieces; read references/<piece>.md for flags, knobs and how to recover from a deny."
---

# pawl

Plugin root: `${PLUGIN_ROOT}` (local install `~/.gemini/config/plugins/pawl`).
Pieces under `pieces/<name>/`. State under `PAWL_DATA` (default `~/.pawl`; send
budget, egress rules and gate events share it).

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

Piece CLIs live under `pieces/<piece>/`; hooks under the plugin root. Each
arrow names the page to read under `references/` in this skill directory.

-   Send: chat, mail, social post: `hooks/pawl_hook.py`: egress, prose, budget
    in order -> `send-gates.md`
-   Destructive `git` in a protected repo, or `git commit -n`:
    `hooks/pawl_git_hook.py` prompts; hand: `destructive_git_guard.py check` ->
    `destructive-git-guard.md`
-   Poll loop, `tail -f`, `watch`, `sleep` over 600 s: `hooks/pawl_poll_hook.py`
    prompts; hand: `poll_loop_guard.py classify` -> `poll-loop-guard.md`
-   Turn end with tasks older than 10 min: `hooks/pawl_stop_hook.py` blocks
    once, then kills wait shapes -> `idle-task-gate.md`
-   Same tool call 3 times (task status polls too), or 2-3 calls alternating:
    `hooks/pawl_oscillation_hook.py` prompts -> `oscillation-breaker.md`
-   Edit whose replacement equals its target: `hooks/pawl_noop_edit_hook.py`
    denies -> `noop-edit-guard.md`
-   Invisible characters in a write: `hooks/pawl_zero_width_hook.py` strips
    them, never blocks -> `zero-width-sanitizer.md`
-   Read-only shell command: `hooks/pawl_readonly_hook.py` auto-approves; hand:
    `readonly_pass.py check` -> `readonly-pass.md`
-   Re-reading own transcript, a `SKILL.md` or a memory file:
    `hooks/pawl_reread_hook.py` denies past the limit -> `reread-guard.md`
-   Reading another conversation's `brain/` or transcript:
    `hooks/pawl_fence_hook.py` prompts -> `conversation-fence.md`
-   Which gates fired this week: `report.py --days 7` -> `report.md`
-   Text a human will read: `prose_gate.py --plane chat|deliverable` ->
    `prose-gate.md`
-   Outbound text with paths, tokens, hostnames: `egress_firewall.py check` ->
    `egress-firewall.md`
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

-   Exit 0 pass, 1 fail, 2 internal error (piece READMEs list extras: breaker
    3 = skipped).
-   Hook decision JSON on stdout only: `{"decision":"allow"}`,
    `{"decision":"deny","reason":"..."}` or
    `{"decision":"force_ask","reason":"..."}`; the Stop hook uses `"block"`.
-   Egress firewall fails closed. Prose gate, send budget, idle task gate,
    oscillation breaker, no-op edit guard, zero-width sanitizer, read-only
    pass, reread guard and conversation fence fail open.
-   Disable a send gate for one session: `PAWL_DISABLE=egress,prose,budget` (any
    subset).
-   Read-only pass answers `auto_approve` or `allow`, never deny; the
    zero-width sanitizer answers `allow` with an `overwrite` block.
-   Git guard, poll guard, oscillation breaker and conversation fence return
    force_ask at a hit;
    only a human click passes. `SEND_BUDGET_OVERRIDE=1` stays for hand runs of
    the send-budget CLI.
-   Never edit a piece to make a gate pass. Fix the draft, command, count, or
    reason.

## Verify

```bash
python3 -B ${PLUGIN_ROOT}/run_tests.py
python3 -B ${PLUGIN_ROOT}/check_portable.py
```

Expected: 19 lines starting `OK`, then `portable: clean`, exit 0.
