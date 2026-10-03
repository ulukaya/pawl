# pawl-blast-radius-guard

CLI:

```
python3 -B <root>/pieces/blast-radius-guard/blast_radius.py
```

Gate `blast` of `hooks/pawl.py`, on shell commands. Follows what a command
runs (scripts, `-c` strings, `trap`, `eval`, functions, `npm`/`pnpm`/`yarn`
scripts, Makefile and justfile recipes, Python/JS/Ruby/Perl/Go code and
their shell-outs, `docker run -v` mounts, `cmd /c`, `powershell`) and judges
every deletion by where it lands: home, root, system folders, `~/.ssh`,
`~/Documents`, `~/code`, a drive or a disk deny; the whole workspace,
anything else outside it, or a path starting with an unresolvable value
asks; inside the workspace or a temp dir passes.

On `[PAWL blast]`: delete inside the workspace, by a path you can name.
Never rewrite the command to reach the same place another way; if the user
wants a deny-level deletion, they run it by hand. On Codex an ask arrives as
a deny: tell the user what would be deleted.

## Commands

| Invocation | Effect |
|---|---|
| `check [--cwd DIR] -- <command...>` | prints allow, exit 0; or `ask: <reason>` / `deny: <reason>`, exit 1 |
| `hook` | reads a tool-call payload on stdin, prints the decision |

## Environment

-   `HOME`, `TMPDIR`: the home and temp roots the targets are judged against

## Test

```bash
cd <root>/pieces/blast-radius-guard && python3 -B -m pytest -q .
python3 -B score.py --corpus holdout4.json --verbose
```
