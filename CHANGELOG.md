# Changelog

## 0.4.0

One dispatcher and one adapter for every harness, native plugin packaging
for Claude Code and Codex, and the gates taught each harness's shapes.

### Fixed

*   Claude Code: every "no objection" was sent as `permissionDecision:
    allow`, which skips the user's permission prompt, so installing pawl
    auto-approved every tool call. A gate with no objection is now silent.
*   Claude Code and Codex: hook commands used relative paths, but both
    harnesses run hooks from the session's directory. Python then exits 2,
    which both read as a block, so every tool call was refused. Commands now
    run through a quoted `${CLAUDE_PLUGIN_ROOT}` / `${PLUGIN_ROOT}`.
*   Claude Code: a gate that should ask the human (`force_ask`) became a
    deny nobody could approve. It is now `permissionDecision: ask`.
*   Claude Code: Stop answers used the PreToolUse schema; they are now
    `{"decision": "block", "reason": ...}` or nothing.
*   Codex: `ask` and a bare `allow` are invalid PreToolUse answers there. An
    ask is now a deny that carries the reason and says how to proceed; an
    approval is silent.
*   Claude Code: translating `Bash` input must keep
    `dangerouslyDisableSandbox`, or readonly-pass would approve an
    unsandboxed retry.
*   The installer linked into `~/.claude/plugins/pawl` and
    `~/.agent-skills/pawl`, neither of which any harness loads.
*   `check_portable.py` scanned `.venv/`, so the gate failed in the
    virtualenv CLAUDE.md asks for.
*   Claude Code: the no-op edit reason named Antigravity's
    `ReplacementContent` and `TargetContent`; it now says `new_string`
    equals `old_string`.
*   `install.sh --source .` handed `.` to the plugin CLIs, which refuse it;
    a local directory is now passed as an absolute path.

### Added

*   `hooks/pawl.py`: one entry point, `pre` / `stop`, with `--harness` and
    `--only`. Gates run in one process, stop at the first deny, and merge
    deny > ask > approve > allow. `PAWL_DISABLE` takes any gate name.
*   `hooks/harness.py`: translates Claude Code and Codex payloads to the
    canonical call the pieces speak and renders each harness's contract;
    reasons use the harness's own tool names.
*   `.claude-plugin/marketplace.json` and `.codex-plugin/plugin.json`:
    `claude plugin install pawl@pawl` and `codex plugin add pawl@pawl` after
    adding the `ulukaya/pawl` marketplace.
*   noop-edit-guard and zero-width-sanitizer read Codex `apply_patch`.
*   conversation-fence fences Claude Code (`~/.claude/projects/`,
    `file-history/`, `history.jsonl`) and Codex (`~/.codex/sessions/`,
    `history.jsonl`) stores; a glob is a sweep, not a conversation named `*`.
*   idle-task-gate reads Claude Code's Stop `background_tasks`.
*   reread-guard counts the harness's own transcript path whatever its name,
    and clears a turn's counts at Stop.
*   oscillation-breaker treats `.output` reads (Claude Code task output) as
    polls.
*   The oscillation ring and reread counts are kept per subagent
    (`agent_id`), so parallel subagents running one command are not a loop;
    Claude Code's `prompt_id` keys the turn.
*   `install.py`: per-harness install and uninstall through each harness's
    plugin CLI, idempotent, with `--dry-run` and `--source`.
*   `check_contract.py`: checks every hook config, the manifests, a 500-line
    file limit and a nesting depth of 3.
*   End-to-end tests that run the shipped Claude Code and Codex configs from
    an unrelated project directory.
*   Claude Code plugin settings in `/config`: approve read-only commands,
    protected repos, strict fence, gates to turn off. `check_contract.py`
    fails when a setting and the dispatcher's option map disagree.
*   Directory listing fields: icon and documentation, support, privacy and
    terms links; Codex gets the same links and two starter prompts.
*   `PRIVACY.md`: every file pawl reads and writes, and the one place it
    loosens a harness default.
*   `.githooks/pre-push` runs `check_portable.py` and the battery.
*   `pawl.py demo`: ten canned calls through the real dispatcher in all
    three harnesses' shapes, printed as a decision matrix (`--verbose`,
    `--json`); runs in a scratch tree, ignores the user's settings.
*   CI: Python 3.14 and a ruff (pyflakes, bugbear) job.

### Changed

*   The ten per-gate hook scripts and `pawl_harness.py` are replaced by the
    dispatcher; `pawl_hook.py` is now `send_gates.py` and `pawl_hook.py
    stats` is `pawl.py stats`.
*   The skill description fits the 150-token budget (141 tokens).
*   A hook call costs about a third less (Bash 92 to 63 ms, Read 76 to
    48 ms): modules compile once into `PAWL_DATA/pycache`, and the hot path
    no longer imports `dataclasses` or `argparse`.
*   README tables render on GitHub.
*   Licensed under Apache-2.0.
*   GitHub CI runs by hand only (`workflow_dispatch`) until hosted runners
    are available on the account; the pre-push hook enforces the same
    checks.

### Removed

*   `harnesses/codex/`: its config pointed at deleted scripts and its prompt
    text promised approvals Codex hooks cannot give.
*   An internal review archive and a reconstruction spec that did not
    belong in a public repo, from the tree and from git history.

## 0.3.0

Force-ask kill switches, `pawl report`, the oscillation breaker, the five
guard pieces (noop-edit, zero-width, readonly, reread, fence), and the
gates-on vs gates-off eval suite.
