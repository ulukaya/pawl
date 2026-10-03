#!/usr/bin/env python3
"""history_guard.py: pushes and branch moves that drop commits.

These rules apply in every repo, like the commit rules: a remote branch is
shared by definition, and a branch deleted unmerged takes its commits with
it.

  push    --force / -f (short clusters too), --force-with-lease, --mirror,
          --prune, --delete / -d, a `+src:dst` refspec (force) and a `:dst`
          refspec (delete). A dry run (-n / --dry-run) passes.
  branch  -D, -d or --delete with -f / --force, -f / --force (reset a branch
          to another commit), -M and -C (rename or copy over a branch).
"""

from __future__ import annotations

from typing import List

from git_parse import _short_flags

PUSH_VALUE_SHORTS = "o"  # `-o <option>`: its value is not a refspec
FORCE_LONGS = ("--force", "--force-with-lease", "--mirror")
PUSH_REMEDY = (
    "Push to a new branch name instead, or ask the user first; even"
    " `--force-with-lease` overwrites commits someone else pushed since your"
    " last fetch."
)
BRANCH_REMEDY = (
    "`git branch -d` deletes only a branch whose commits are merged; ask the"
    " user before dropping one that is not."
)


def _long(flags: List[str], name: str) -> bool:
  return any(f == name or f.startswith(name + "=") for f in flags)


def _push_operands(args: List[str]) -> List[str]:
  """Remote and refspecs, skipping the value of `-o` / `--push-option`."""
  out, skip = [], False
  for arg in args:
    if skip:
      skip = False
      continue
    if arg in ("-o", "--push-option"):
      skip = True
      continue
    if not arg.startswith("-"):
      out.append(arg)
  return out


def push_reason(args: List[str]) -> str:
  flags = [a for a in args if a.startswith("-") and a != "--"]
  letters = _short_flags(flags)
  if _long(flags, "--dry-run") or "n" in letters:
    return ""
  refspecs = _push_operands(args)[1:]
  if any(_long(flags, name) for name in FORCE_LONGS) or "f" in letters:
    what = "force-pushes, replacing commits on the remote"
  elif any(spec.startswith("+") for spec in refspecs):
    what = "with a `+` refspec force-pushes, replacing commits on the remote"
  elif _long(flags, "--delete") or "d" in letters or any(
      spec.startswith(":") for spec in refspecs):
    what = "deletes a branch or tag on the remote"
  elif _long(flags, "--prune"):
    what = "with --prune deletes every remote branch missing locally"
  else:
    return ""
  return f"`git push` {what}, where others pull from. {PUSH_REMEDY}"


def branch_reason(args: List[str]) -> str:
  flags = [a for a in args if a.startswith("-") and a != "--"]
  letters = _short_flags(flags)
  force = _long(flags, "--force") or "f" in letters
  delete = _long(flags, "--delete") or "d" in letters
  if "D" in letters or (delete and force):
    what = "deletes a branch whether or not its commits are merged"
  elif "M" in letters or "C" in letters:
    what = "renames or copies over an existing branch, dropping its commits"
  elif force:
    what = "moves an existing branch, dropping the commits it pointed to"
  else:
    return ""
  return f"`git branch` {what}. {BRANCH_REMEDY}"


def reason(subcmd: str, args: List[str]) -> str:
  """Deny reason for `git <subcmd> <args>`, or '' to allow."""
  if subcmd == "push":
    return push_reason(args)
  if subcmd == "branch":
    return branch_reason(args)
  return ""
