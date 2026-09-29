# Circuit breaker

One piece of the CoS blueprint, published on its own so you can run it in ten
minutes. Three files, standard library only, no dependency on the rest of the
system.

## What it does

Every scheduled job gets a named breaker with three states. `CLOSED` means the
job runs on every tick. Three consecutive failures open it. `OPEN` means the job
is skipped, with exit code 3, until a cooldown passes. The first tick after the
cooldown flips it to `HALF_OPEN` and lets exactly one probe run through. If the
probe passes, the breaker closes and the failure count resets. If the probe
fails, the breaker re-opens and the cooldown doubles, up to a cap. A success in
`CLOSED` also resets the failure count, so two failures a week apart never add
up to a trip.

Defaults: 3 failures to open, 3600 s first cooldown, 6 h cap. All breakers live
in one JSON file.

## The problem it exists for

A cron line does not know the job is broken. It fires every 15 minutes, the job
fails every 15 minutes, and every failure costs something: a page, an API call
against a quota, a log line nobody reads, a retry that hits a rate limit. The
person who owns the job either gets 96 alerts a day or, worse, none, because the
failure is swallowed and the spend continues.

The one rule this file enforces: a job that keeps failing stops being scheduled
until a single probe passes. A broken job cannot page every tick and cannot burn
budget silently. It gets one try per cooldown, and the cooldown grows while it
stays broken.

## Files

| File | Purpose |
|---|---|
| `breaker.py` | The state machine and CLI. Exclusive `flock` around read-modify-write, atomic write via temp file + `os.replace`, one JSON file for every breaker. |
| `test_breaker.py` | 15 `unittest` cases against a real temp state file and a fake clock: open, skip, half-open probe, re-open with doubling, cap, reset, status, run wrapper exit codes, 8-process race. |
| `README.md` | This file. |

## Run it

```bash
python3 -m unittest test_breaker -v
python3 breaker.py should-run nightly_sync
python3 breaker.py record nightly_sync fail --exit-code 1
python3 breaker.py record nightly_sync ok
python3 breaker.py status --compact
python3 breaker.py reset nightly_sync
```

`should-run` prints the state and exits 0 (run) or 3 (skip). `record` takes `ok`
or `fail`. `status` prints every breaker with state, failures, `opened_at`, and
`next_probe_at`. Every subcommand accepts `--state FILE`; otherwise
`BREAKER_STATE` or `~/.local/state/breaker/breakers.json` is used.

Tuning, all optional: `BREAKER_THRESHOLD` (default 3), `BREAKER_COOLDOWN`
seconds (default 3600), `BREAKER_MAX_COOLDOWN` seconds (default 21600).
`BREAKER_NOW` sets the clock to a fixed epoch second; the tests use it, you
should not.

## Wire it

Put the wrapper form in front of the job. It calls `should-run`, executes the
command, records `ok` or `fail` from the exit code, and exits with the command's
code, or 3 when the breaker skipped it.

```
*/15 * * * * /path/to/breaker.py run nightly_sync -- /path/to/job.sh
```

Anything after `--` is the command, run as given, with no shell in between. Use
a different name per job. A skipped tick writes one line to stderr, `SKIP
nightly_sync: breaker OPEN`, so cron mail still tells you the breaker is doing
its job without repeating the failure.

## Design notes

-   The breaker never decides what "failed" means. Exit code non-zero is a
    failure, zero is a success. Anything more clever belongs in the job.
-   One probe at a time. While a probe is out, a second `should-run` returns 3.
    A probe that crashes without reporting back is given up on after one full
    cooldown, so a dead probe cannot wedge the breaker.
-   Doubling starts at the first failed probe, not at the first failure, and it
    is capped. A job broken for a week gets a probe every 6 hours, not every 15
    minutes and not never.
-   One JSON file, one lock. Eight processes recording at once land exactly
    eight failures. Anything fancier than `flock` + `os.replace` is a second
    system to debug.
-   The state file is readable by hand and safe to delete. Deleting it closes
    every breaker.
