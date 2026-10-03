# Hillclimb log

How the guard got its numbers, including the bad ones. Each held-out set
was written before the version under test saw it, scored once, and only
then used to improve the guard; after that it counts as development data.

`caught` is dangerous cases asked or denied; `false alarms` is everyday
cases asked or denied; `weak` is a deny that came back as an ask.

| Version | Set (cases) | Caught | False alarms | Weak | First seen? |
| --- | --- | --- | --- | --- | --- |
| v0 | dev (135) | 85/86 (98.8%) | 7/49 | 2 | written alongside |
| v1 | dev (135) | 86/86 | 0/49 | 0 | no |
| v1 | holdout-1 (60) | 32/35 (91.4%) | 1/25 | 4 | yes |
| v2 | holdout-2 (50) | 19/28 (67.9%) | 2/22 | 2 | yes |
| v3 | holdout-3 (43) | 18/25 (72.0%) | 0/18 | 0 | yes, plus 1 crash |
| v4 | holdout-4 (39) | 14/17 (82.4%) | 0/22 | 3 | yes |
| v5 | all five sets (329) | 190/191 | 0/138 | 0 | no |
| v5 | real session replay (472 commands) | none dangerous | 0/472 | | yes |

## What each round taught

*   **v0 -> v1.** A pattern glob (`rm -f *.pyc`) and a filtered `find -delete`
    were judged as "the whole workspace"; a home expression behind a
    receiver chain (`os.path.expanduser('~')`) was judged as somewhere in
    home; a script that reassigns `HOME` erased the real home from the
    possible values (the #88462 trap). Fixed: only `*`, `.*` and friends
    mean everything; a filtered find deletes entries below its root; a
    variable keeps its starting value among the possible ones.
*   **holdout-1.** `bash -lc` (the `c` inside a cluster), `rsync --delete`,
    `npx rimraf`, `ENV["HOME"]` with its closing bracket, make variables
    that name other make variables, Windows user folders, and function
    arguments (`clean build` makes `$1` "build", not unknown).
*   **holdout-2.** The worst round. Shell-outs from code (`os.system`,
    `subprocess.run([...])`, `execSync`, Perl `system`), containers
    (`docker run -v ~:/data ... rm -rf /data` deletes the host home),
    `cmd /c` and `powershell -Command`, brace expansion, `${HOME%/*}`,
    `//`, here-strings, and two false alarms: a `read` loop fed by
    `git ls-files`, and `/tmp/$(whoami)-cache` judged as maybe `/tmp`.
*   **Real sessions.** Replaying this repo's own Claude Code session (461
    real commands) found 3 false alarms: generator scripts that hold
    dangerous commands as quoted test data. Calls inside string literals
    and comments no longer count.
*   **holdout-3.** A crash (re-parsing `eval "$(...)"` lost its
    substitutions; an internal error now asks instead of raising), then
    `~root`, `cat x.sh | bash`, `source <(...)`, `bash < x.sh`, Go's
    `os.RemoveAll`, justfiles and `diskutil`. One label was wrong against
    the stated policy (`rmtree(Path.home().joinpath('Documents'))` is the
    same deletion as `rm -rf ~/Documents`) and was relabelled ask -> deny
    after scoring; the case records why.
*   **holdout-4.** `\$` inside double quotes, `/*/`, `~/**/*`,
    `$(cd ~ && pwd)` and Perl `glob("~")`.

## Known misses

*   Deletions computed at run time from walking a tree
    (`os.remove(os.path.join(r, f)) for r, _, fs in os.walk(home)`): the
    target is never written down. holdout-4 keeps this case as a miss.
*   Anything a command downloads and runs (`curl ... | sh`), and any program
    pawl cannot read (a compiled binary, an unparsed language).
*   The scorer's sandbox puts HOME under `/tmp`; cases are written so that
    this layout does not change their answer.

## Reproduce

```bash
python3 score.py                       # dev set
python3 score.py --corpus holdout4.json --verbose
```
