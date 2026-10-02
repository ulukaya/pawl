"""Tests for the tmpfs rule on `git worktree add` in destructive_git_guard.

Every test pairs a deny with the allow it must not swallow. Destinations are
never created; the rule reads the typed path, not the filesystem.

Run:  python3 -m pytest -q test_worktree_tmpfs.py
"""

# pylint: disable=redefined-outer-name

from __future__ import annotations

import json
import os
from pathlib import Path  # pylint: disable=g-importing-member
import subprocess
import sys

HERE = Path(__file__).resolve().parent
HOOK = HERE / "destructive_git_guard_hook.py"
CLI = HERE / "destructive_git_guard.py"
if str(HERE) not in sys.path:
  sys.path.insert(0, str(HERE))

# pylint: disable=g-import-not-at-top,g-bad-import-order
import destructive_git_guard as dgg  # noqa: E402
import pytest  # noqa: E402

# pylint: enable=g-import-not-at-top

DURABLE = "/opt/worktrees"
CWD = "/opt/repo"


@pytest.fixture(autouse=True)
def no_roots(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
  """Zero protected roots: the tmpfs rule must not depend on them."""
  monkeypatch.setenv("PAWL_GIT_PROTECTED_ROOTS", "")
  monkeypatch.setenv("PAWL_DATA", str(tmp_path / "data"))


def denied(cmd: str, cwd: str = CWD) -> bool:
  reason = dgg.scan(cmd, cwd)
  return reason.startswith("[DESTRUCTIVE GIT]") and "tmpfs" in reason


@pytest.mark.parametrize("dest", ["/tmp/wt", "/dev/shm/wt", "/run/user/1/wt"])
def test_each_tmpfs_prefix_denies(dest: str) -> None:
  assert denied(f"git worktree add {dest}")
  assert not denied(f"git worktree add {DURABLE}/wt")


def test_prefix_root_itself_denies_and_lookalike_allows() -> None:
  assert denied("git worktree add /tmp")
  assert not denied("git worktree add /tmpfoo/wt")
  assert not denied("git worktree add /runner/wt")


def test_denies_with_zero_protected_roots() -> None:
  assert dgg.protected_roots(CWD) == []
  assert dgg.scan("git worktree add /tmp/wt", CWD, roots=[])
  assert not dgg.scan("git worktree add /opt/wt", CWD, roots=[])


@pytest.mark.parametrize(
    "opts",
    [
        "-b feat",
        "-B feat",
        "--detach",
        "-f",
        "--lock --reason keep",
        "--reason=keep --lock",
        "--",
    ],
)
def test_options_before_the_destination_are_skipped(opts: str) -> None:
  assert denied(f"git worktree add {opts} /tmp/wt main")
  assert not denied(f"git worktree add {opts} {DURABLE}/wt main")


def test_option_values_are_not_the_destination() -> None:
  assert not denied(f"git worktree add -b /tmp/x {DURABLE}/wt")
  assert not denied(f"git worktree add --lock --reason /tmp/x {DURABLE}/wt")
  assert denied(f"git worktree add -B {DURABLE} /tmp/wt")


def test_commit_ish_after_destination_is_not_the_destination() -> None:
  assert not denied(f"git worktree add {DURABLE}/wt /tmp")
  assert denied("git worktree add /tmp/wt origin/main")


def test_relative_destination_resolves_against_cwd() -> None:
  assert denied("git worktree add wt", cwd="/tmp")
  assert denied("git worktree add ../../tmp/wt", cwd="/opt/repo")
  assert not denied("git worktree add ../wt", cwd="/opt/repo")


def test_cd_and_dash_c_set_the_base_for_a_relative_path() -> None:
  assert denied("cd /tmp && git worktree add wt")
  assert denied("git -C /dev/shm worktree add wt")
  assert not denied("cd /opt && git worktree add wt", cwd="/tmp")


def test_home_style_paths_are_expanded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
  monkeypatch.setenv("HOME", "/tmp/fakehome")
  monkeypatch.setenv("WT_BASE", "/dev/shm")
  assert denied("git worktree add ~/wt")
  assert denied("git worktree add $HOME/wt")
  assert denied("git worktree add ${WT_BASE}/wt")
  monkeypatch.setenv("HOME", "/opt/home")
  assert not denied("git worktree add ~/wt")


def test_unknown_leading_variable_allows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
  monkeypatch.delenv("PAWL_NO_SUCH_VAR", raising=False)
  assert not denied("git worktree add $PAWL_NO_SUCH_VAR/wt")
  assert not denied("git worktree add ${PAWL_NO_SUCH_VAR}/wt")
  assert denied("git worktree add /tmp/$PAWL_NO_SUCH_VAR/wt")


def test_symlink_into_tmpfs_denies_by_realpath(tmp_path: Path) -> None:
  link = tmp_path / "scratch"
  link.symlink_to("/tmp")
  durable = tmp_path / "durable"
  durable.mkdir()
  assert denied(f"git worktree add {link}/wt", cwd=str(durable))
  assert denied(f"git worktree add {os.path.realpath('/tmp')}/wt")


def test_other_worktree_subcommands_and_mentions_allow() -> None:
  assert not denied("git worktree list")
  assert not denied("git worktree remove /tmp/wt")
  assert not denied("git worktree prune")
  assert not denied("echo 'git worktree add /tmp/wt'")
  assert denied("git status && git worktree add /tmp/wt")


def test_reason_names_a_durable_location() -> None:
  reason = dgg.scan("git worktree add /dev/shm/wt", CWD)
  assert "/dev/shm/wt" in reason
  assert "reboot" in reason
  assert "~/worktrees/" in reason


def test_cli_check_exits_one_on_tmpfs_destination() -> None:
  env = {**os.environ, "PAWL_GIT_PROTECTED_ROOTS": ""}
  bad = subprocess.run(
      [sys.executable, "-B", str(CLI), "check", "--cwd", CWD]
      + ["git", "worktree", "add", "/tmp/wt"],
      capture_output=True, text=True, env=env, timeout=20, check=False,
  )
  good = subprocess.run(
      [sys.executable, "-B", str(CLI), "check", "--cwd", CWD]
      + ["git", "worktree", "add", f"{DURABLE}/wt"],
      capture_output=True, text=True, env=env, timeout=20, check=False,
  )
  assert bad.returncode == 1, bad.stdout + bad.stderr
  assert "tmpfs" in bad.stdout
  assert good.returncode == 0, good.stdout + good.stderr


def test_hook_denies_tmpfs_worktree(tmp_path: Path) -> None:
  env = {
      **os.environ,
      "PAWL_GIT_PROTECTED_ROOTS": "",
      "PAWL_DATA": str(tmp_path / "data"),
  }

  def run(cmd: str) -> dict:
    payload = {
        "toolCall": {
            "name": "run_command",
            "args": {"CommandLine": cmd, "Cwd": CWD},
        }
    }
    proc = subprocess.run(
        [sys.executable, "-B", str(HOOK)],
        input=json.dumps(payload), capture_output=True, text=True, env=env,
        timeout=20, check=False,
    )
    return json.loads(proc.stdout)

  out = run("git worktree add -b fix /tmp/wt")
  assert out["decision"] == "deny"
  assert "tmpfs" in out["reason"]
  assert run(f"git worktree add -b fix {DURABLE}/wt") == {"decision": "allow"}
