"""Tests for history_guard.py: pushes and branch moves that drop commits.

These rules apply in every repo, protected or not: a remote branch is shared
by definition, and a branch deleted unmerged takes its commits with it.

Run: python3 -m pytest -q test_history_guard.py
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import destructive_git_guard as guard  # noqa: E402
import history_guard  # noqa: E402

CWD = "/work/app"


def scan(cmd: str) -> str:
  # roots=[]: nothing is protected, so a hit can only come from these rules.
  return guard.scan(cmd, CWD, roots=[])


@pytest.mark.parametrize("cmd", [
    "git push --force",
    "git push -f origin main",
    "git push -uf origin feature",
    "git push --force-with-lease origin feature",
    "git push --force-with-lease=main:abc123 origin main",
    "git push origin +main",
    "git push origin +HEAD:main",
    "git push origin main +feature",
    "git push origin --delete feature",
    "git push -d origin feature",
    "git push origin :feature",
    "git push --mirror backup",
    "git push --prune origin",
    "git -C /elsewhere push --force",
    "cd /tmp/x && git push -f",
    "make test && git push --force origin main",
    "sudo git push --force",
])
def test_pushes_that_drop_remote_commits_are_denied(cmd: str) -> None:
  reason = scan(cmd)
  assert reason.startswith("[DESTRUCTIVE GIT] `git push"), (cmd, reason)
  assert "remote" in reason


@pytest.mark.parametrize("cmd", [
    "git branch -D feature",
    "git branch --delete --force feature",
    "git branch -df feature",
    "git branch -d -f feature",
    "git branch -f main HEAD~3",
    "git branch --force main origin/main",
    "git branch -M old new",
    "git branch -C old new",
])
def test_branch_moves_that_drop_commits_are_denied(cmd: str) -> None:
  reason = scan(cmd)
  assert reason.startswith("[DESTRUCTIVE GIT] `git branch"), (cmd, reason)


@pytest.mark.parametrize("cmd", [
    "git push",
    "git push origin main",
    "git push -u origin feature",
    "git push --set-upstream origin feature",
    "git push --tags",
    "git push origin HEAD:refs/heads/feature",
    "git push --force --dry-run origin main",
    "git push -nf origin main",
    "git push -o ci.skip origin main",
    "git branch",
    "git branch -a",
    "git branch -vv",
    "git branch feature",
    "git branch -d merged-feature",
    "git branch --delete merged-feature",
    "git branch -m old new",
    "git branch --show-current",
    'git commit -m "push --force later"',
    'echo "git push --force"',
    "git log --branches -f",
])
def test_safe_pushes_and_branch_calls_are_allowed(cmd: str) -> None:
  assert scan(cmd) == "", cmd


def test_reasons_name_a_safer_route() -> None:
  push = history_guard.reason("push", ["--force", "origin", "main"])
  assert "new branch" in push
  assert "--force-with-lease" in push
  branch = history_guard.reason("branch", ["-D", "feature"])
  assert "git branch -d" in branch


def test_other_subcommands_are_not_this_module_s_business() -> None:
  assert history_guard.reason("reset", ["--hard"]) == ""
  assert history_guard.reason("tag", ["-d", "v1"]) == ""
