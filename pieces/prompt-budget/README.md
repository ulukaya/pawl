# Prompt budget

One piece of the CoS blueprint, published on its own so you can run it in ten
minutes. Three files, standard library only, no dependency on the rest of the
system.

## What it does

Reads a `budget.json` that lists the instruction files an agent loads on every
prompt. Estimates the token cost of each file, prints one line per file and a
total line, and exits 1 when any file or the total is over its ceiling. An entry
can also require a frontmatter pin such as `visibility: PINNED`, so a file that
is supposed to be always-loaded cannot silently lose that marker.

The estimate is an approximation: characters times 0.25, rounded up (about one
token per four characters). It is deterministic and needs no tokenizer. Change
the multiplier with `--tokens-per-char`.

## The problem it exists for

Every always-loaded instruction file is paid on every prompt. Those files grow
by appends: one more rule, one more preference, one more "always do X". Each
append looks small. Nobody sees the total, because no single edit shows it. A
ceiling in a commit hook makes the growth visible and forces a trade: to add a
line, cut a line, or move the rule to a file that loads only when triggered.

## Files

| File | Purpose |
|---|---|
| `prompt_budget.py` | The gate and CLI. `check` fails on any offender, `report` prints the table sorted by cost. Frontmatter parsed with plain string ops, no YAML. |
| `test_prompt_budget.py` | 13 unittest cases against a real temp tree: ceilings, glob expansion, missing and optional files, frontmatter pin, report mode, multiplier override, offender ordering. |
| `README.md` | This file. |

## Run it

Write a `budget.json` next to your instruction files:

```json
{
  "total_tokens": 10000,
  "files": [
    {"path": "AGENT.md", "max_tokens": 1800},
    {"glob": "rules/*.md", "max_tokens_each": 900},
    {"path": "memory/preferences.md", "max_tokens": 1200,
     "require_frontmatter": {"visibility": "PINNED"}},
    {"path": "local-overrides.md", "max_tokens": 300, "optional": true}
  ]
}
```

Then:

```bash
python3 -m unittest test_prompt_budget -v
python3 prompt_budget.py check --config budget.json
python3 prompt_budget.py report --config budget.json
python3 prompt_budget.py check --config budget.json --root /path/to/agent/config --tokens-per-char 0.3
```

Output is one line per file, `OK|OVER <tokens>/<ceiling> <path>`, then a total
line. In `check` mode offenders print first. Paths resolve against `--root`, or
the config file's directory when `--root` is not given.

Exit codes: 0 all under ceiling, 1 a file or the total is over or a required
frontmatter pair is missing, 2 a required file does not exist, 3 bad config.
`report` exits 0 whatever it finds.

## Wire it

Add one line to `.git/hooks/pre-commit` (or your hook runner of choice):

```bash
python3 /path/to/prompt_budget.py check --config /path/to/budget.json || exit 1
```

A commit that pushes a file or the total over its ceiling is refused with the
offenders listed.

## Design notes

-   The estimate is an approximation on purpose. A real tokenizer varies by
    model and adds a dependency. A stable number that moves in the same
    direction as the real cost is enough to spot growth in a diff.
-   Per-file and total ceilings are both needed. Per-file catches one file
    bloating; total catches ten files each staying under their own line while
    the sum doubles.
-   The frontmatter check exists because "always-loaded" is often a marker
    inside the file. If the marker goes away, the file stops being paid for, and
    the budget would report a false saving. Requiring the pair keeps the budget
    honest about what it measures.
-   When a file is over, move content to a triggered file (a skill, a topic
    note, a rule loaded on a matcher). Do not raise the ceiling. The ceiling is
    the point.
-   `report` never fails so it can run in a dashboard or a weekly summary
    without gating anything.
