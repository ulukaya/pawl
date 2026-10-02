# Pawl Safety Rules for Codex Agents

Pawl enforces deterministic safety gates on agent actions:

1. **Git safety**: Do not run commands that discard uncommitted work
   (`git reset --hard`, `git clean -f`, `git checkout -- .`) without user
   approval. Use `git stash` or create a branch first.
2. **Poll loops**: Avoid unbounded sleep loops (`while :; do sleep 1; done`).
   Run bounded checks instead.
3. **No-op edits**: Do not submit file replacements where replacement text
   equals target text. Read the target section first to confirm diffs.
4. **Clean writes**: Never introduce zero-width invisible Unicode characters
   into code or data files.
5. **Tool oscillations**: Avoid repeating the exact same failing tool call
   consecutively. Adapt arguments or report the blocker.
6. **Isolation**: Never inspect or read other conversations' transcripts or
   memory stores.
7. **Read-only commands**: Provably read-only commands (`ls`, `cat`, `rg`,
   `git status`) are safe and auto-approved.
