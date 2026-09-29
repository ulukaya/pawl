# Report

One piece of the CoS blueprint, published on its own so you can run it in ten
minutes. Three files, standard library only, no dependency on the rest of the
system.

## What it does

Counts the rows in the pawl denial log per gate and prints the counts, a total,
the date range counted, and how many malformed rows were skipped. Nothing else
leaves the log: no command text, no hashes, no conversation ids.

```
POLL_LOOP 4
DESTRUCTIVE_GIT 1
total 5
range 2026-09-11 2026-09-17
skipped 0
```

## The problem it exists for

A gate that fires and is never counted is a gate you cannot argue about. The
denial log grows one JSON line per hit; this piece turns it into five lines you
can paste into a retro or a review without leaking what the agent was doing.

## Files

| File             | Purpose                                                   |
| ---------------- | --------------------------------------------------------- |
| `report.py`      | Reader and CLI. Parses `ts` with `%Y-%m-%dT%H:%M:%S%z`,   |
:                  : groups by `gate`, filters by `--days`.                    :
| `test_report.py` | 8 tests: counting and sort order, day window, malformed   |
:                  : rows, output privacy, empty log, CLI exit codes, `--data` :
:                  : over `PAWL_DATA`.                                         :
| `README.md`      | This file.                                                |

## Run it

```bash
python3 -m pytest -q test_report.py
python3 report.py
python3 report.py --days 30
python3 report.py --data /path/to/pawl-data
```

Exit 0 after a report. Exit 2 with `no denials file at <path>` when the log does
not exist yet. `--days 0` counts every row.

## Configuration

Variable    | Meaning                           | Default
----------- | --------------------------------- | ---------
`PAWL_DATA` | Directory holding `denials.jsonl` | `~/.pawl`

`--data DIR` on the command line wins over the variable.

## Design notes

-   Read only. The piece never writes to the log and never rotates it.
-   Skip, count, continue. A malformed row is a number in the last line, not a
    crash and not a silent drop.
-   Counts only. The row schema carries a command hash and a conversation id so
    a human can trace one hit by hand; the report is for sharing, so it prints
    neither.

## What the full system adds on top

In the blueprint these counts roll into a weekly retro next to the send-budget
`stats` output. You need none of that for the report to do its job.
