# Deletion guards, side by side

`compare.py` runs any Claude Code PreToolUse hook on the 329 labelled cases
of `pieces/blast-radius-guard` (the dev corpus and four held-out sets), each
in the same sandbox, and counts what it caught and what it flagged by
mistake.

## Results

Run on 2026-10-03, Linux, each guard at its default level unless named:

| Guard | Caught (191 dangerous) | False alarms (138 everyday) |
| --- | --- | --- |
| pawl `blast` gate | 190 (99.5%) | 0 |
| cc-safety-net 2.5.1 | 100 (52.4%) | 22 (15.9%) |
| cc-safety-net 2.5.1, `paranoid` | 144 (75.4%) | 62 (44.9%) |

## Read this before quoting it

*   **Home field.** The same author wrote pawl's guard and these cases. The
    four held-out sets were written before pawl was tuned on them, and
    pawl's first-seen scores on them were lower (68% to 91% caught; see
    `pieces/blast-radius-guard/HILLCLIMB.md`). Expect a gap on someone
    else's cases.
*   **Labels are pawl's policy.** Deleting inside a temp directory or a
    sibling folder of the same repo is labelled fine; a guard that blocks
    every `rm -rf` outside the current directory counts those as false
    alarms. Some of cc-safety-net's flags are that policy, and it also
    refuses `.env` access by design; others are plain misreadings (`cd
    frontend && rm -rf node_modules` read as "outside cwd", a folder
    literally named `~` read as home).
*   **Scope.** cc-safety-net reads inline code (`bash -c`, `python -c`) but
    not script files, package scripts or Makefiles, which is where most of
    its misses sit; it also covers secrets and other guards pawl leaves to
    other gates.

## Run it

```bash
npm install cc-safety-net
python3 eval/blast-compare/compare.py \
  --guard "pawl=python3 -B hooks/pawl.py pre --only blast --harness claude" \
  --guard "ccsn=node node_modules/cc-safety-net/dist/bin/cc-safety-net.js hook --claude-code" \
  --verbose
```

`--verbose` lists the ids each guard got wrong, so any row can be checked by
hand against the corpus files.
