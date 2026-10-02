# Contributing

pawl is small on purpose: plain Python standard library, every rule backed
by a test, one dispatcher for every harness. Contributions that keep it that
way are welcome.

## Set up

```bash
git clone https://github.com/ulukaya/pawl && cd pawl
python3 -m venv .venv && .venv/bin/pip install pytest
git config core.hooksPath .githooks     # checks run before every push
.venv/bin/python3 -B run_tests.py       # one OK line per suite
.venv/bin/python3 -B check_portable.py  # portable: clean
python3 hooks/pawl.py demo              # every gate in every harness
```

## The rules that decide most reviews

`CLAUDE.md` holds the full list. In short:

*   Shipped code imports the standard library only.
*   No file over 500 lines, no function nested more than 3 blocks deep.
    `check_portable.py` enforces both.
*   Errors are friendly and actionable, never a traceback. Exit codes: 0
    pass, 1 deny or violation, 2 missing config or input.
*   Markdown prose wraps at 80 columns; code, URLs and table rows are exempt.
*   Every new test fails before your change and passes after it. Say in the
    pull request how you checked ("reverted `harness.py`; `test_x` failed").
*   A grader gets a known-bad twin: a planted bad solution it must fail.
*   Commit messages end with the battery counts, for example
    `Battery: 22 suites OK, 883 tests passed; check_portable clean.`

## Where things go

*   A rule about one kind of mistake is a piece under `pieces/<name>/`, with
    its own tests and README, runnable without the rest of pawl.
*   Harness differences live only in `hooks/harness.py`. Pieces see the
    canonical Antigravity call; a change to what Claude Code or Codex receive
    needs a case in `hooks/e2e_test.py`, which runs the shipped configs.
*   A new gate: write the piece, add a `Gate` to `hooks/gates.py`, its group
    to `hooks.json`, and `skills/pawl/references/<name>.md`;
    `check_portable.py` names whatever is still missing.
*   A Claude Code plugin setting: add it to `userConfig` in
    `.claude-plugin/plugin.json` and to `PLUGIN_OPTIONS` in `hooks/pawl.py`;
    `check_contract.py` fails when the two disagree.

## False positives

A gate that fires on a harmless call is a bug, and the most useful report
there is. Open a "False positive" issue with the harness, the call and the
reason text. A fix starts with a failing test holding that exact call.

## License

pawl is licensed under Apache-2.0. Under section 5 of that license, a
contribution you submit is licensed under the same terms.
