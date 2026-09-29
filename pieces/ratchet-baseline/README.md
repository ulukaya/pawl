# Ratchet baseline

One piece of the CoS blueprint, published on its own so you can run it in ten
minutes. Three files, standard library only, no dependency on the rest of the
system.

## What it does

A JSON file holds named integer counters: failing tests, bare `except: pass`
blocks, lint warnings, whatever you can count. `check` takes fresh counts and
fails when any counter is above the file. `update` lowers the stored numbers,
and refuses two things: raising any number, and lowering one without a written
reason of at least 40 characters. Metrics the file has never seen pass on
`check` (first observation) and are added on `update`.

The file only ever gets better. That is the whole tool.

## The problem it exists for

Nobody decides to let a codebase decay. It happens one exception at a time. A
test is skipped "for now". A `try/except: pass` is added to get a demo out. The
lint count goes from 40 to 41 and nobody notices, because nobody remembers it
was 40. A goal like "zero failing tests" is too far away to gate on, so it gates
on nothing.

A ratchet gates on the number you have today. Today's count is the ceiling. It
can drop, and when it drops you lock the new floor with a sentence saying why.
It cannot rise, not by one, not with a good excuse in the commit message. The
excuse has to go into the baseline file, and it has to be about lowering, never
raising.

## Files

| File | Purpose |
|---|---|
| `ratchet.py` | The CLI: `check`, `update`, `show`. Atomic write via temp file + `os.replace`. Exit codes 0, 1, 2. |
| `test_ratchet.py` | 14 `unittest` cases against a real temp dir: equal, lower, higher, unknown metric, short reason, raised value, seeding, no leftover temp file, show output, bad input. |
| `README.md` | This file. |

## Run it

```bash
timeout 120 python3 -m unittest test_ratchet -v
python3 ratchet.py update --baseline .ratchet.json --metric failing_tests=5 --metric bare_except=3 --reason "Seeding the baseline with the counts we have today, before any cleanup."
python3 ratchet.py check --baseline .ratchet.json --metric failing_tests=5 --metric bare_except=4
python3 ratchet.py check --baseline .ratchet.json --metric failing_tests=2 --metric bare_except=3
python3 ratchet.py update --baseline .ratchet.json --metric failing_tests=2 --reason "Fixed the three parser tests that broke when the date format changed."
python3 ratchet.py show --baseline .ratchet.json
```

The second command exits 1 and prints `bare_except rose: 3 -> 4`. The third
exits 0 and prints `failing_tests dropped: 5 -> 2`. The fourth locks 2 as the
new floor.

## Wire it

`ratchet.py` does not know how to count anything. You supply the collectors. A
pre-commit hook with two of them looks like this (put it in
`.git/hooks/pre-commit`, make it executable):

```bash
set -e
FAILING=$(python3 -m pytest -q 2>/dev/null | grep -oE '[0-9]+ failed' | grep -oE '[0-9]+' || echo 0)
SWALLOWED=$(grep -rEc --include='*.py' 'except\s*:\s*pass' . | awk -F: '{s+=$2} END {print s+0}')
python3 tools/ratchet.py check --baseline .ratchet.json \
  --metric failing_tests="$FAILING" --metric bare_except="$SWALLOWED"
```

Commit `.ratchet.json` to the repo. When someone fixes tests, they run `update`
with a reason and commit the lower file in the same change. When someone adds a
failing test, the hook exits 1 and names the metric, the old number, and the new
one.

Swap the collectors for whatever your stack counts: `eslint -f json | jq`, `mypy
| wc -l`, `git grep -c TODO`. Any command that prints an integer works.

## Design notes

-   Monotonic by construction. There is no flag to raise a value. If you need a
    higher ceiling, you delete the file and reseed, and that shows up in the
    diff as a deleted file, not as a quiet +1.
-   The reason is data, not a commit message. It lives in the baseline file next
    to the number it justifies, and 40 characters is long enough that "fix" and
    "wip" do not qualify.
-   Atomic write. The new file lands via `tempfile.mkstemp` in the same
    directory and `os.replace`. A crash mid-write leaves the old baseline intact
    and no temp file behind.
-   Three exit codes, no more. 0 means nothing rose or the file was written. 1
    means a metric rose (check only). 2 means a rule was broken on update, or
    the input was bad.
-   What it does not do: run tests, parse output, know about git, or lock the
    file. One process at a time writes the baseline, and that process is the
    hook. Adding a collector or a lock is your job, in your shell, where you can
    see it.
