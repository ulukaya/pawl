# Prose gate

One piece of the CoS blueprint, published on its own. Two files, standard
library only.

## What it does

Scores a draft for machine-sounding prose and exits 1 above a threshold. Run it
on anything an agent wrote before it leaves: a chat reply, an email, a doc, a
post.

```bash
printf 'Ran the migration on staging. 4,120 rows moved, 0 rejected, 11 seconds.\n' \
  | python3 prose_gate.py --plane chat
PASS score=0.0 threshold=3.0 words=12

printf "It's not just a migration, it's a fundamental shift. Let's dive in. Here's the kicker: studies show this seamlessly delivers a game-changing result.\n" \
  | python3 prose_gate.py --plane chat --why
[why] promotional_superlative @105: 'seamlessly'
[why] vague_attribution @87: 'studies show'
[why] reasoning_leak @53: "Let's dive in"
[why] suspense_hook @68: "Here's the kicker"
FAIL score=40.5 threshold=3.0 words=23 hard:vague_attribution
```

## The problem it exists for

Telling a model "do not sound like an AI" in the prompt works for about a page.
The tells come back under load: the negated parallelism, the suspense hook, the
restatement, the superlative, the signposted conclusion. You can read every
draft yourself, or you can put a regex battery with a threshold between the
model and the send button and only read what passes.

## How the score works

`prose_patterns.json` holds 36 tells. Each has a regex, a `plane` (chat,
deliverable, both), a `threshold_per_1000w` tolerance, a `weight`, and a
`family`.

1.  Blank what the author did not write in their own voice: front matter, code
    fences, URLs, inline code, double-quoted spans, italics, blockquotes, HTML
    comments. Offsets are kept so `--why` points at the real position.
2.  For each tell, density = hits per 1000 words (drafts under 250 words are
    scored as 250 so one hit in one sentence cannot dominate). Excess = `max(0,
    density - tolerance) / max(tolerance, 0.5)`, contribution = `weight *
    excess`.
3.  Add `0.5` for every family over tolerance beyond the plane's free count (2
    chat, 4 deliverable). One tell is a habit; six families at once is a voice.
4. Tolerance 0 tells fail on a single hit, whatever the total.
5. Fail above 3.0 on chat, 6.0 on deliverable. Both numbers live in the JSON.

## Files

| File | Purpose |
|---|---|
| `prose_gate.py` | Scorer and CLI. `--plane chat|deliverable`, `--explain` table, `--why` per-hit spans on stderr, `--json`. |
| `prose_patterns.json` | The catalog. Edit tolerances here, never in code. |
| `test_prose_gate.py` | 10 tests: human sample passes, slop sample fails, hard-fail tell, quote and fence masking, word floor, family penalty, plane thresholds, catalog shape, CLI exit codes. |

## Run it

```bash
python3 -m pytest -q test_prose_gate.py
python3 prose_gate.py --plane deliverable post.md --explain
```

## Wire it

Pre-commit on a blog repo:

```bash
git diff --cached --name-only --diff-filter=AM -- '*.md' '*.mdx' \
  | xargs -r -n1 python3 /path/to/prose_gate.py --plane deliverable
```

As an agent egress hook: run the gate on the message body before the send
command and refuse on exit 1. The blueprint version does this inside a
PreToolUse hook that parses the send command's arguments; the standalone gate is
the part that scores.

## Calibration

Tolerances shipped here are the ones running in the blueprint system as of this
publish. They were set by hand and are being recalibrated from live telemetry
(per-pattern Poisson upper bound on two weeks of real traffic). Expect them to
loosen slightly; nothing here should tighten. If a pattern fires on your normal
writing more than one time in twenty, raise its tolerance in the JSON and send
the sample back.

## Design notes

-   Data, not code. The catalog is JSON so it can be swapped, diffed, and
    calibrated without a code review.
-   Additive score. Every term is summable so `--explain` accounts for the whole
    number.
-   Blank, do not delete. Masked spans keep their width so offsets in `--why`
    are real.
-   A hard-fail tier exists because some tells are never acceptable in your own
    voice ("studies show" with no citation) and no density argument should
    rescue them.

## What the full system adds on top

In the blueprint the gate runs at three points: a Stop hook that logs a score
for every turn (telemetry only), a pre-commit on two repos, and an egress hook
that blocks chat, mail, and doc writes. Scores feed a weekly recalibration and
an opt-out marker with an expiry date. None of that is needed for the gate to do
its job on one file.
