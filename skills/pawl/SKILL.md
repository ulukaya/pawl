---
name: pawl
description: "Deterministic gates for agent harnesses. Use when a task involves sending a message from a tool call, running git commands that discard work, waiting on a background task, ending a turn with tasks still running, committing or pushing code, running a scheduled job, fixing a bug with a reproducer, or keeping always-loaded prompt files under a token ceiling. Routes to one of 12 stdlib Python pieces: 11 checks and a report command that tallies what they blocked."
---

# pawl

Plugin root: `${PLUGIN_ROOT}` (local install `~/.gemini/config/plugins/pawl`).
Pieces under `pieces/<name>/`. State under `PAWL_DATA` (default `~/.pawl`; send
budget, egress rules and gate events share it).

## Routing

Piece CLIs live under `pieces/<piece>/`; hooks under the plugin root.

-   Send: chat, mail, social post: `hooks/pawl_hook.py`: egress, prose, budget
    in order -> `pawl-send-gates`
-   Destructive `git` in a protected repo, or `git commit -n`:
    `hooks/pawl_git_hook.py` prompts; hand: `destructive_git_guard.py check` ->
    `pawl-destructive-git-guard`
-   Poll loop, `tail -f`, `watch`, `sleep` over 600 s: `hooks/pawl_poll_hook.py`
    prompts; hand: `poll_loop_guard.py classify` -> `pawl-poll-loop-guard`
-   Turn end with tasks older than 10 min: `hooks/pawl_stop_hook.py` blocks
    once, then kills wait shapes -> `pawl-idle-task-gate`
-   Same tool call 3 times, or 2-3 calls alternating:
    `hooks/pawl_oscillation_hook.py` prompts -> `pawl-oscillation-breaker`
-   Which gates fired this week: `report.py --days 7` -> `pawl-report`
-   Text a human will read: `prose_gate.py --plane chat|deliverable` ->
    `pawl-prose-gate`
-   Outbound text with paths, tokens, hostnames: `egress_firewall.py check` ->
    `pawl-egress-firewall`
-   Sends per channel per day: `send_budget.py status|check|spend` ->
    `pawl-send-budget`
-   A count that may only go down: `ratchet.py check|update|show` ->
    `pawl-ratchet`
-   Scheduled job failing repeatedly: `breaker.py run <name> -- <cmd>` ->
    `pawl-circuit-breaker`
-   Bug fix needing a failing reproducer: `repro_fence.py red|fence|both` ->
    `pawl-repro-fence`
-   Always-loaded files growing: `prompt_budget.py check|report --config
    budget.json` -> `pawl-prompt-budget`

## Contracts

-   Exit 0 pass, 1 fail, 2 internal error (piece READMEs list extras: breaker
    3 = skipped).
-   Hook decision JSON on stdout only: `{"decision":"allow"}`,
    `{"decision":"deny","reason":"..."}` or
    `{"decision":"force_ask","reason":"..."}`; the Stop hook uses `"block"`.
-   Egress firewall fails closed. Prose gate, send budget, idle task gate and
    oscillation breaker fail open.
-   Disable a send gate for one session: `PAWL_DISABLE=egress,prose,budget` (any
    subset).
-   Git guard, poll guard and oscillation breaker return force_ask at a hit;
    only a human click passes. `SEND_BUDGET_OVERRIDE=1` stays for hand runs of
    the send-budget CLI.
-   Never edit a piece to make a gate pass. Fix the draft, command, count, or
    reason.

## Verify

```bash
python3 -B ${PLUGIN_ROOT}/run_tests.py
python3 -B ${PLUGIN_ROOT}/check_portable.py
```

Expected: 14 lines starting `OK`, then `portable: clean`, exit 0.
