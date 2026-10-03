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
| v6 | all five sets (329) | 190/191 | 0/138 | 0 | no |
| v6 | real session replay (570 commands) | none dangerous | 0/570 | | yes |

v6 kept every internal number while closing gaps that only another project's
cases revealed (see below). The one dev miss is still `h4-py-walk-home`.

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

## Iteration 6: scored on other guards' own corpora

The first five sets were all written by pawl's author. To find blind spots,
v5 was scored against the labelled cases three other deletion guards ship
for themselves (`../../eval/blast-compare/external.py`): Dicklesworthstone's
`destructive_command_guard` (dcg), `shguard`, and `cc-safety-net`. Only
their deletion-related cases are kept. A case they block counts as caught
when pawl denies or asks; a case they allow counts as agreed when pawl
allows. First seen, then after the v6 fixes:

| Their corpus | Their blocks pawl caught | Their allows pawl agreed |
| --- | --- | --- |
| dcg | 49/78 -> 62/78 | 73/87 -> 82/87 |
| shguard | 80/125 -> 109/125 | 16/21 -> 16/21 |
| cc-safety-net | 11/25 -> 15/25 | 10/12 -> 11/12 |

What v6 fixed (each had a known-bad twin added to `test_blast_radius.py`):

*   **Substitutions run.** `$( )`, backticks and `<( )` bodies, and the
    body of an unquoted heredoc, are scanned as shell: `echo $(rm -rf ~)`,
    `env -C /tmp/$(rm -rf /) ...`. A single-quoted `$( )` is still inert.
*   **Brace expansion at the word level.** `{,rm} -rf /` and `{rm,-rf,/}`
    expand to words as bash does, dropping the empty ones.
*   **Empty leading words.** `X=; $X rm -rf /` and `''${IFS}rm -rf /`.
*   **More runners.** `busybox`, `chrt`, `taskset`, `setsid`, `flock`,
    `watch`, `eval --`, `uv run`/`poetry run`/`conda run`..., and GNU
    `parallel TEMPLATE ::: args`.
*   **fish** `-c`/`-C`/`--command`/`--init-command`, with the value
    attached, clustered, `=`-joined or abbreviated (`--com=`).
*   **Disks and overwrites.** `dd of=PATH` is judged where PATH lands
    (`of=/dev/sda` denied, `//dev/sda` normalized); `wipefs` only erases
    with `-a`/`-o`, so bare `wipefs DEV` is no longer denied.
*   **More language APIs.** Perl `unlink`, Ruby `spawn`/`IO.popen`/`Open3`,
    and `subprocess . run` with spaces around the dot.
*   **Windows paths** (`winpath.py`). `C:\...`, `c:/...` and MSYS
    `/c/Users/...` are judged by place: a drive root, `C:\Windows`, a user
    profile or `Documents`/`.ssh`/`AppData` are denied; files inside
    `AppData\Local\Temp` are allowed; `..` is resolved first.

### Differences left on purpose (not bugs)

*   **Temp is fair game.** dcg and cc-safety-net block deletions inside
    `/tmp`, `$TMPDIR` and `AppData\Local\Temp`; pawl allows them and only
    asks before wiping a whole temp root. Most of pawl's remaining "misses"
    against dcg are this.
*   **The workspace is fair game.** `rm -rf ./build`, `rm -rf node_modules`,
    `find . -delete` inside the repo: dcg and cc-safety-net block every
    recursive delete, pawl allows deletions that stay inside the git
    toplevel and asks before the workspace root itself.
*   **Printing is not deleting.** cc-safety-net blocks a command that only
    prints dangerous text (`print("rm -rf /")`, `cat <<EOF` of `rm -rf ~`);
    pawl allows it, because nothing runs.
*   **git deletions** (`git rm`, `git checkout .`) are the destructive-git
    gate's job, not this one.

### Narrow gaps still open

*   A `{ ...; } | sh` brace group whose body is built with a heredoc:
    the newline split in `pipelines()` separates the heredoc from the pipe.
*   Brace expansion whose group is split by `$IFS` first
    (`rm{,$IFS-rf$IFS/}`), and `$(printf -- -delete)` fed into `find`.
*   A `$( )` inside a single-quoted string that spans newlines is scanned
    anyway, so it can ask when it should allow. The direction is safe, and
    no real session command hit it.

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
