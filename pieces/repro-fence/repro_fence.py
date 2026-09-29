#!/usr/bin/env python3
"""Red-first reproducer gate (R1) and public-signature fence (R2).

Standard library only. Read-only against the repository: the only git call is
`git show <rev>:<path>`, which reads an object and writes nothing.

R1  Reproducer falsifiability.
    `red --cmd "<reproducer>" [--timeout N]` runs the reproducer on the current
    tree. It PASSES only when the reproducer exits non-zero in the failing
    range. These exit codes REJECT, each with a one-line reason:
        0    already passing, so the command proves nothing
        2    usage error, the command itself is wrong
        5    import or collection error, the test never ran
        124  timeout, a hang is not a reproduction
        127  binary missing or not executable

R2  Public-signature fence.
    `fence --rev <rev> --file a.py [--file b.py ...]` reads every file at
    `--rev` (default HEAD) and compares public functions, classes, and
    `Class.method` members against the working copy. A public symbol that was
    removed, or whose normalized signature `(args)dN` changed, rejects.
    Leading-underscore symbols are free. Adding a new public symbol is fine.
    Files that do not end in `.py` print `R2-skip` and are ignored.

    Gotcha 1: adding a keyword default to an existing public function is a
    signature change. `def f(a)` -> `def f(a, b=None)` normalizes from
    `(a)d0` to `(a,b)d1` and is rejected. Add a new public function instead.
    Gotcha 2: `def test_*` names in test modules are public symbols too.
    Renaming a test is rejected as a removed symbol. Keep the name and change
    the body.

`both --cmd ... --rev ... --file ...` runs R1 then R2 and reports every
violation from both.

Exit codes: 0 pass, 1 reject, 2 usage.
"""

from __future__ import annotations

import argparse
import ast
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

DEFAULT_TIMEOUT: int = 60
GIT_TIMEOUT_S: int = 30
DEFAULT_REV: str = "HEAD"

# Exit codes that mean "this reproducer did not demonstrate the defect".
REJECTED_EXIT_REASONS: dict[int, str] = {
    0: "exited 0 on the current tree; a passing reproducer proves nothing",
    2: "exited 2 (usage error); the reproducer command itself is wrong",
    5: "exited 5 (import or collection error); the test never ran",
    124: "timed out; a hang is not a reproduction",
    127: "binary not found or not executable",
}


@dataclass(frozen=True)
class Violation:
    """One rule failure, printed as `[rule] detail`."""

    rule: str
    detail: str


# ----------------------------------------------------------------- subprocess


def run_argv(argv: list[str], cwd: Path, timeout: int) -> int:
    """Run argv with shell=False and a hard timeout; return the exit code.

    Timeout maps to 124 and a missing or non-executable binary maps to 127 so
    the caller sees the same codes a shell would report.
    """
    try:
        proc = subprocess.run(
            argv,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return 124
    except (FileNotFoundError, PermissionError):
        return 127
    return proc.returncode


def git_show(repo: Path, rev: str, rel_path: str) -> str | None:
    """Content of `rel_path` at `rev`, or None when the path is absent there."""
    try:
        proc = subprocess.run(
            ["git", "show", f"{rev}:{rel_path}"],
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_S,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


# ------------------------------------------------------------------- R2 core


def signature_of(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Normalized parameter shape: `(name,/,name,*va,name,**kw)dN`.

    Plain positional parameters list as bare names, so `def f(a, b)` is `(a,b)d0`.
    A `/` follows positional-only parameters and a bare `*` precedes keyword-only
    parameters when there is no `*args` (with `*args` that token already marks the
    boundary), so `def f(a, b)`, `def f(a, /, b)` and `def f(a, *, b)` are three
    different shapes. N counts positional and keyword-only defaults together, so
    appending `extra=None` to a public function changes both the name list and N.
    """
    spec = node.args
    names = [arg.arg for arg in spec.posonlyargs]
    if spec.posonlyargs:
        names.append("/")
    names.extend(arg.arg for arg in spec.args)
    if spec.vararg is not None:
        names.append("*" + spec.vararg.arg)
    elif spec.kwonlyargs:
        names.append("*")
    names.extend(arg.arg for arg in spec.kwonlyargs)
    if spec.kwarg is not None:
        names.append("**" + spec.kwarg.arg)
    defaults = len(spec.defaults) + sum(1 for d in spec.kw_defaults if d is not None)
    return f"({','.join(names)})d{defaults}"


def public_symbols(source: str) -> dict[str, str]:
    """Map qualified public symbol -> signature for one Python module.

    Top-level functions map to their signature, classes map to the literal
    `class`, and methods are qualified as `Class.method` so a rename inside a
    class is caught. Anything with a leading underscore, at any level, is
    skipped. `def test_*` functions are ordinary public functions here.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}
    out: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith("_"):
                out[node.name] = signature_of(node)
        elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            out[node.name] = "class"
            out.update(_class_members(node))
    return out


def _class_members(cls: ast.ClassDef) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in cls.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not item.name.startswith("_"):
                out[f"{cls.name}.{item.name}"] = signature_of(item)
    return out


def diff_symbols(rel: str, before: dict[str, str], after: dict[str, str]) -> list[Violation]:
    """Public symbols that vanished or changed shape between two sources."""
    out: list[Violation] = []
    for name, sig in sorted(before.items()):
        if name not in after:
            out.append(Violation("R2-fence", f"{rel}: public symbol '{name}' was removed"))
        elif after[name] != sig:
            out.append(
                Violation("R2-fence", f"{rel}: '{name}' signature changed {sig} -> {after[name]}")
            )
    return out


# ------------------------------------------------------------------ the rules


def check_reproducer(repo: Path, argv: list[str], timeout: int) -> list[Violation]:
    """R1: the reproducer must fail in the failing range on the current tree."""
    if not argv:
        return [Violation("R1-repro", "no reproducer command supplied")]
    code = run_argv(argv, repo, timeout)
    reason = REJECTED_EXIT_REASONS.get(code)
    if reason is None:
        return []
    if code == 124:
        reason = f"timed out after {timeout}s; a hang is not a reproduction"
    return [Violation("R1-repro", f"reproducer {shlex.join(argv)} {reason}")]


# Shell tokens that let a command line pick its own exit code. A reproducer that
# carries one of these can be red or green regardless of the bug, so the exit
# code stops meaning anything. Matched on the joined argv and again inside any
# `sh -c` / `bash -c` string.
_SHAPE_TOKENS: tuple[str, ...] = ("||", "&&", ";", "exit", "true", "false")
_SHELL_WRAPPERS: frozenset[str] = frozenset({"sh", "bash", "zsh", "dash"})


def _shape_tokens_in(words: list[str]) -> list[str]:
    hits = [w for w in words if w in _SHAPE_TOKENS]
    if len(words) >= 3 and Path(words[0]).name in _SHELL_WRAPPERS and words[1] == "-c":
        try:
            inner = shlex.split(words[2])
        except ValueError:
            inner = words[2].split()
        hits.extend(w for w in inner if w in _SHAPE_TOKENS)
    return hits


def check_reproducer_shape(argv: list[str]) -> list[Violation]:
    """R1-shape: the reproducer's exit code must come from the test, not the command line.

    Rejects argv containing `||`, `&&`, `;`, `exit`, `true`, or `false`, including
    inside a `sh -c "..."` string. `sh failing.sh` is fine; `sh failing.sh || true`,
    `sh -c 'pytest; exit 1'`, and `false && pytest` are not.
    """
    if not argv:
        return []
    hits = _shape_tokens_in(argv)
    if not hits:
        return []
    return [Violation(
        "R1-shape",
        f"reproducer {shlex.join(argv)} sets its own exit code via {', '.join(sorted(set(hits)))}; "
        "the exit code must come from the test itself",
    )]


def _relative(repo: Path, rel: str) -> str:
    p = Path(rel)
    if p.is_absolute():
        try:
            return str(p.resolve().relative_to(repo))
        except ValueError:
            return rel
    return rel


def check_fence(repo: Path, rel_paths: list[str], rev: str) -> list[Violation]:
    """R2: public signatures present at `rev` must survive in the working tree."""
    out: list[Violation] = []
    for raw in rel_paths:
        rel = _relative(repo, raw)
        if not rel.endswith(".py"):
            sys.stderr.write(f"[R2-skip] {rel}: no parser for this file type\n")
            continue
        before = git_show(repo, rev, rel)
        if before is None:
            # Not present at rev: a brand new file has nothing to fence.
            continue
        target = repo / rel
        if not target.exists():
            out.append(Violation("R2-fence", f"{rel}: tracked file was deleted"))
            continue
        after = target.read_text(encoding="utf-8", errors="replace")
        out.extend(diff_symbols(rel, public_symbols(before), public_symbols(after)))
    return out


# ------------------------------------------------------------------------ CLI


def report(violations: list[Violation], label: str) -> int:
    if violations:
        for item in violations:
            sys.stderr.write(f"[{item.rule}] {item.detail}\n")
        return 1
    print(f"{label} PASSED")
    return 0


def _resolve_repo(args: argparse.Namespace) -> Path:
    return Path(getattr(args, "repo", None) or ".").resolve()


def _run_r1(repo: Path, cmd: str, timeout: int) -> list[Violation]:
    argv = shlex.split(cmd)
    shape = check_reproducer_shape(argv)
    if shape:
        return shape
    return check_reproducer(repo, argv, timeout)


def cmd_red(args: argparse.Namespace) -> int:
    repo = _resolve_repo(args)
    return report(_run_r1(repo, args.cmd, args.timeout), "R1 reproducer gate")


def cmd_fence(args: argparse.Namespace) -> int:
    repo = _resolve_repo(args)
    if not args.file:
        sys.stderr.write("[R2-fence] no --file supplied; nothing to fence\n")
        return 2
    return report(check_fence(repo, args.file, args.rev), "R2 signature fence")


def cmd_both(args: argparse.Namespace) -> int:
    repo = _resolve_repo(args)
    if not args.file:
        sys.stderr.write("[R2-fence] no --file supplied; nothing to fence\n")
        return 2
    violations = _run_r1(repo, args.cmd, args.timeout)
    violations.extend(check_fence(repo, args.file, args.rev))
    return report(violations, "R1 reproducer + R2 signature fence")


def build_parser() -> argparse.ArgumentParser:
    # A parent parser shared with subparsers must use SUPPRESS defaults;
    # otherwise the subparser re-applies its own default after parsing and
    # clobbers a value given before the subcommand.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--repo", default=argparse.SUPPRESS, help="repository root (default: cwd)")

    parser = argparse.ArgumentParser(
        prog="repro_fence.py",
        description="R1 red-first reproducer gate and R2 public-signature fence.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    red = sub.add_parser("red", help="R1: reproducer must fail on the current tree", parents=[common])
    red.add_argument("--cmd", required=True, help="reproducer command line (shlex split)")
    red.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="seconds before 124")
    red.set_defaults(func=cmd_red)

    fence = sub.add_parser("fence", help="R2: public signatures must survive", parents=[common])
    fence.add_argument("--file", action="append", default=[], help="repo-relative path; repeatable")
    fence.add_argument("--rev", default=DEFAULT_REV, help="BEFORE revision (default: HEAD)")
    fence.set_defaults(func=cmd_fence)

    both = sub.add_parser("both", help="R1 then R2", parents=[common])
    both.add_argument("--cmd", required=True)
    both.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    both.add_argument("--file", action="append", default=[])
    both.add_argument("--rev", default=DEFAULT_REV)
    both.set_defaults(func=cmd_both)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (OSError, ValueError) as exc:
        sys.stderr.write(f"[gate error] {exc}\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())
