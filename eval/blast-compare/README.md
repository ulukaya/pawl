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
| dcg 0.15.2 | 156 (81.7%) | 61 (44.2%) |

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
    other gates. dcg states that it does not scan script files: 33 of its
    35 misses are a script, package script, Makefile, justfile or code file
    (the other two are `npx rimraf ~` and `cmd /c rmdir`). Its default asks
    a human about every `rm -rf`, even `rm -rf node_modules`, which is most
    of its 61 false alarms; that is a deliberate trade, and its packs cover
    databases, clouds and Kubernetes, which these cases do not test.

## On other projects' own corpora

`compare.py` scores every guard on pawl's cases. `external.py` does the
reverse: it reads the labelled cases three other guards ship for *themselves*
and reports where pawl agrees, so the home-field advantage runs the other
way. It keeps only their deletion-related cases; a case they block counts as
caught when pawl denies or asks, a case they allow counts as agreed when
pawl allows. Nothing runs — commands are judged in a fake home.

| Their corpus | Their blocks pawl catches | Their allows pawl allows |
| --- | --- | --- |
| dcg 0.15.2 | 62/78 (79%) | 82/87 (94%) |
| shguard | 109/125 (87%) | 16/21 (76%) |
| cc-safety-net | 15/25 (60%) | 11/12 (92%) |

Run on 2026-10-03. These are not pawl's cases, so the misses are worth
reading, and most are deliberate policy differences, not defects:

*   **Temp and the workspace are fair game for pawl.** dcg and cc-safety-net
    block every recursive delete and distrust `/tmp`/`$TMPDIR`; pawl allows
    deletions that stay inside the git toplevel or a temp directory and asks
    before a whole temp root or the workspace root. This is most of pawl's
    "they block, pawl allows".
*   **Printing is not deleting.** cc-safety-net blocks a command that only
    prints `rm -rf /`; pawl allows it, because nothing runs.
*   **git deletions** (`git rm`) belong to pawl's destructive-git gate.
*   **shguard's `rm -rf ~/subdir/*` and `../build/*`** are allowed by
    shguard and asked by pawl: outside the workspace, in your home or its
    parent, pawl wants a human to look.

`pieces/blast-radius-guard/HILLCLIMB.md` lists what the first run of this
comparison taught pawl (substitutions, brace expansion, more runners, fish,
`dd`/`wipefs`, more language APIs, Windows paths) and the gaps left open.

```bash
# clone the three guards next to each other, then:
python3 eval/blast-compare/external.py \
  --dcg path/to/destructive_command_guard \
  --shguard path/to/shguard \
  --ccsn path/to/claude-code-safety-net --verbose
```

## Run it

```bash
npm install cc-safety-net
cargo install --git https://github.com/Dicklesworthstone/destructive_command_guard destructive_command_guard
python3 eval/blast-compare/compare.py \
  --guard "pawl=python3 -B hooks/pawl.py pre --only blast --harness claude" \
  --guard "ccsn=node node_modules/cc-safety-net/dist/bin/cc-safety-net.js hook --claude-code" \
  --guard "dcg=$HOME/.cargo/bin/dcg" \
  --verbose
```

`--verbose` lists the ids each guard got wrong, so any row can be checked by
hand against the corpus files.
