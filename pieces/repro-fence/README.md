# Repro fence

One piece of the CoS blueprint, published on its own so you can run it in ten
minutes. Three files, standard library only, no dependency on the rest of the
system.

## What it does

Two gates in one script, both read-only against your git repository.

R1, the failing-test-first reproducer gate. `repro_fence.py red --cmd
"<reproducer>"` runs the reproducer on the current tree and passes only when it
fails. A reproducer that exits 0 is rejected, because a test that already passes
proves nothing about the bug. Exit 2 (usage), 5 (import or collection error),
124 (timeout), and 127 (missing binary) are also rejected, each with a one-line
reason, because none of them demonstrate the defect either. Before anything
runs, the command line itself is checked: a reproducer that carries `||`, `&&`,
`;`, `exit`, `true`, or `false` (directly or inside `sh -c "..."`) is rejected
as R1-shape, because a command that picks its own exit code proves nothing about
the bug either.

R2, the public-signature fence. `repro_fence.py fence --rev HEAD --file a.py`
reads each file at the given revision with `git show rev:path`, collects every
public function, class, and `Class.method`, and compares them to the working
copy. A public symbol that is gone, or whose normalized signature `(args)dN`
changed, is rejected. Leading-underscore symbols are free to change. New public
symbols are fine. Files that do not end in `.py` print `R2-skip` and are
ignored.

`repro_fence.py both` runs R1 then R2 and reports every violation from both.

## The problem it exists for

An agent handed a bug report will often start editing before it has seen the
bug. It writes a test, the test passes on the first run, the agent declares
victory, and the bug is still there. R1 makes the agent produce a red test
before it is allowed to touch the fix.

The second failure is the fix that works by subtraction. The agent cannot make
the call site happy, so it deletes the public helper, renames the method, or
bolts a new keyword onto the signature. Every caller outside the diff breaks. R2
holds the public surface of each touched file fixed at the revision you name and
rejects the commit when a symbol is missing or reshaped.

## Files

| File | Purpose |
|---|---|
| `repro_fence.py` | The gate and CLI. `red`, `fence`, `both`. Only git call is `git show rev:path`. Exit 0 pass, 1 reject, 2 usage. |
| `test_repro_fence.py` | 21 unittest cases. Each builds a real throwaway git repository in a temp dir and drives the CLI as a subprocess. Covers every R1 exit code, every R2 rule, both gotchas, `--rev`, and a zero-git-writes check. |
| `README.md` | This file. |

## Run it

```bash
python3 -m unittest test_repro_fence -v
python3 repro_fence.py red --cmd "python3 -m pytest tests/test_bug.py -x" --timeout 60
python3 repro_fence.py fence --rev HEAD --file src/service.py --file src/api.py
python3 repro_fence.py both --cmd "python3 -m pytest tests/test_bug.py -x" --rev HEAD --file src/service.py
```

Run from the repository root, or pass `--repo /path/to/repo` after the
subcommand. `--file` is repeatable and repo-relative. On success the script
prints `<label> PASSED` to stdout; on reject it prints one `[R1-repro]` or
`[R2-fence]` line per violation to stderr.

## Wire it

As a pre-commit hook that fences every staged Python file against HEAD:

```bash
tee .git/hooks/pre-commit <<'EOF'
#!/bin/sh
files=$(git diff --cached --name-only --diff-filter=AM | grep '\.py$')
[ -z "$files" ] && exit 0
args=""
for f in $files; do args="$args --file $f"; done
exec python3 tools/repro_fence.py fence --rev HEAD $args
EOF
chmod +x .git/hooks/pre-commit
```

Verify with `git commit --allow-empty -m test`; the commit lands and nothing is
printed.

As a stage in an agent loop: record the revision before dispatch, require `red`
to pass before the agent may edit, then run `fence --rev <that revision>` over
the files the agent touched before you accept the diff. Set
`FENCE_OVERRIDE`-style escapes at the harness layer, not in this script.

## Design notes

-   The fence compares shapes, not bodies. Refactor freely inside a function;
    the gate only cares that the name, the parameter names in order, and the
    count of defaults survive.
-   Gotcha 1: adding a keyword default to an existing public function is a
    signature change. `def f(a)` to `def f(a, b=None)` goes from `(a)d0` to
    `(a,b)d1` and is rejected. Add a new public function that carries the new
    behavior and have the old one delegate to it.
-   Parameter kind is part of the shape. The normalized form keeps `/` after
    positional-only parameters and a bare `*` before keyword-only ones, so `def
    f(a, b)` is `(a,b)d0`, `def f(a, /, b)` is `(a,/,b)d0` and `def f(a, *, b)`
    is `(a,*,b)d0`. Moving a parameter across either marker breaks callers that
    pass it by position or by name, and is rejected.
-   Gotcha 2: `def test_*` names in test modules are public symbols too.
    Renaming a test to match new behavior is rejected as a removed symbol. Keep
    the name and change the body.
-   R1 uses `shell=False` and a hard timeout. Timeout maps to 124 and a missing
    binary to 127 so the reasons line up with what a shell would report.
-   Zero git writes. The only subprocess call into git is `git show`, and the
    test suite asserts that `git status --porcelain` is byte-identical before
    and after a full `both` run.
