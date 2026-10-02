"""Tests for destructive_git_guard.py and destructive_git_guard_hook.py.

Runs against real temp dirs, no mocks.

Run:  python3 -m pytest -q test_destructive_git_guard.py
"""

# pylint: disable=redefined-outer-name,unused-argument


from __future__ import annotations

import json
import os
from pathlib import Path  # pylint: disable=g-importing-member
import subprocess
import sys
from typing import Any, Dict, Optional

HERE = Path(__file__).resolve().parent
HOOK = HERE / "destructive_git_guard_hook.py"
CLI = HERE / "destructive_git_guard.py"
if str(HERE) not in sys.path:
  sys.path.insert(0, str(HERE))

# pylint: disable=g-import-not-at-top,g-bad-import-order
import destructive_git_guard as dgg  # noqa: E402
import pytest  # noqa: E402

# pylint: enable=g-import-not-at-top


@pytest.fixture()
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
  root = tmp_path / "shared"
  (root / "sub").mkdir(parents=True)
  (tmp_path / "other").mkdir()
  (tmp_path / "data").mkdir()
  monkeypatch.setenv("PAWL_GIT_PROTECTED_ROOTS", str(root))
  monkeypatch.setenv("PAWL_DATA", str(tmp_path / "data"))
  return root


def _payload(cmd: str, cwd: str, name: str = "run_command") -> str:
  return json.dumps(
      {"toolCall": {"name": name, "args": {"CommandLine": cmd, "Cwd": cwd}}}
  )


def run_hook(
    raw: str, env_extra: Optional[Dict[str, str]] = None
) -> Dict[str, Any]:
  """Runs the hook shim on raw stdin and parses its JSON."""
  env = dict(os.environ)
  env.update(env_extra or {})
  proc = subprocess.run(
      [sys.executable, "-B", str(HOOK)],
      input=raw,
      text=True,
      capture_output=True,
      env=env,
      timeout=20,
      check=False,
  )
  assert proc.returncode == 0, proc.stderr
  return json.loads(proc.stdout)


# --- destructive subcommands inside the protected root ------------------------


@pytest.mark.parametrize(
    "cmd",
    [
        "git reset --hard",
        "git reset --hard HEAD~1",
        "git reset",
        "git reset --mixed HEAD",
        "git checkout -- file.py",
        "git checkout .",
        "git checkout -f main",
        "git checkout --force main",
        "git restore file.py",
        "git restore --staged --worktree file.py",
        "git restore -S -W file.py",
        "git stash",
        "git stash push",
        "git stash pop",
        "git stash drop",
        "git clean -fd",
        "git clean -fdx",
        "git clean --force",
        "git rm file.py",
        "git rm -r --cached dir/",
    ],
)
def test_destructive_forms_denied_in_protected_root(
    repo: Path, cmd: str
) -> None:
  """Every destructive form is denied when cwd is the protected root."""
  reason = dgg.scan(cmd, str(repo))
  assert reason.startswith("[DESTRUCTIVE GIT]"), cmd
  assert str(repo) in reason


@pytest.mark.parametrize(
    "cmd",
    [
        "git -C {root} reset --hard",
        "git -C {root}/sub checkout -- x.py",
        "git --git-dir={root}/.git reset --hard",
        "git --work-tree {root} clean -fd",
        "cd {root} && git reset --hard",
        "cd {root}/sub; git stash",
        "git checkout -- {root}/sub/x.py",
        "git rm {root}/x.py",
        "sudo git -C {root} reset --hard",
        "timeout 30 git -C {root} clean -fdx",
        "GIT_TRACE=1 env FOO=bar git -C {root} restore x.py",
        "/usr/bin/git -C {root} stash",
        "echo start && git -C {root} reset --hard && echo done",
    ],
)
def test_targeting_the_root_from_elsewhere_is_denied(
    repo: Path, tmp_path: Path, cmd: str
) -> None:
  """Naming the root via -C, --git-dir, cd or a path is still denied."""
  other = str(tmp_path / "other")
  assert dgg.scan(cmd.format(root=repo), other).startswith(
      "[DESTRUCTIVE GIT]"
  ), cmd


def test_relative_cd_chain_resolves_into_root(
    repo: Path, tmp_path: Path
) -> None:
  assert dgg.scan(
      "cd shared && cd sub && git reset --hard", str(tmp_path)
  ).startswith("[DESTRUCTIVE GIT]")
  assert not dgg.scan("cd other && git reset --hard", str(tmp_path))


# --- allowed forms ------------------------------------------------------------


@pytest.mark.parametrize(
    "cmd",
    [
        "git status",
        "git log --oneline -5",
        "git diff HEAD~1",
        "git show HEAD:file.py",
        "git rev-parse --show-toplevel",
        "git blame file.py",
        "git ls-files",
        "git reflog",
        "git config user.name",
        "git remote -v",
        "git stash list",
        "git stash show -p",
        "git clean -n",
        "git clean --dry-run -d",
        "git clean -nd",
        "git reset --soft HEAD~1",
        "git restore --staged file.py",
        "git restore -S file.py",
        "git checkout main",
        "git checkout -b feature",
        "git add file.py",
        "git commit --only file.py -m 'msg'",
        "git commit -am 'msg'",
        "git commit -m 'this mentions -n and --no-verify in the message'",
        "git push origin main",
        "git fetch --all",
        "git pull --rebase",
        "git switch main",
    ],
)
def test_read_only_and_safe_writes_are_allowed(repo: Path, cmd: str) -> None:
  """Read-only and receipt-keeping git commands are allowed."""
  assert not dgg.scan(cmd, str(repo)), cmd


@pytest.mark.parametrize(
    "cmd",
    [
        "ls -la",
        "echo git reset --hard",
        "gitk",
        "digit reset",
        "python3 -m pytest -q",
    ],
)
def test_non_git_commands_pass_through(repo: Path, cmd: str) -> None:
  """Commands that only look like git are allowed."""
  assert not dgg.scan(cmd, str(repo)), cmd


def test_destructive_in_another_repo_is_allowed(
    repo: Path, tmp_path: Path
) -> None:
  other = str(tmp_path / "other")
  assert not dgg.scan("git reset --hard", other)
  assert not dgg.scan(f"git -C {other} clean -fdx", str(repo))
  assert not dgg.scan("git -C ../other stash", str(repo))


def test_multiple_protected_roots_colon_separated(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  other = tmp_path / "other"
  monkeypatch.setenv("PAWL_GIT_PROTECTED_ROOTS", f"{repo}:{other}")
  assert dgg.scan("git reset --hard", str(other)).startswith(
      "[DESTRUCTIVE GIT]"
  )
  assert not dgg.scan("git reset --hard", str(tmp_path))
  assert sorted(dgg.protected_roots(str(tmp_path))) == sorted(
      [str(repo.resolve()), str(other.resolve())]
  )


def test_empty_roots_env_protects_nothing(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  monkeypatch.setenv("PAWL_GIT_PROTECTED_ROOTS", "")
  assert not dgg.protected_roots(str(repo))
  assert not dgg.scan("git reset --hard", str(repo))


# --- default root: toplevel of the call's cwd ---------------------------------


def _git_ok() -> bool:
  try:
    return (
        subprocess.run(
            ["git", "--version"], capture_output=True, timeout=5, check=False
        ).returncode
        == 0
    )
  except (OSError, subprocess.SubprocessError):
    return False


@pytest.mark.skipif(not _git_ok(), reason="git binary not available")
def test_unset_env_protects_the_toplevel_of_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """Without PAWL_GIT_PROTECTED_ROOTS the git toplevel of cwd is protected."""
  monkeypatch.delenv("PAWL_GIT_PROTECTED_ROOTS", raising=False)
  live = tmp_path / "live"
  (live / "deep").mkdir(parents=True)
  subprocess.run(["git", "init", "-q", str(live)], check=True, timeout=10)
  assert dgg.protected_roots(str(live / "deep")) == [str(live.resolve())]
  assert dgg.scan("git reset --hard", str(live / "deep")).startswith(
      "[DESTRUCTIVE GIT]"
  )
  assert not dgg.scan("git status", str(live / "deep"))
  plain = tmp_path / "plain"
  plain.mkdir()
  assert not dgg.protected_roots(str(plain))
  assert not dgg.scan("git reset --hard", str(plain))


# --- commit rules (apply in every repo) ---------------------------------------


@pytest.mark.parametrize(
    "cmd",
    [
        "git commit --no-verify -m 'x'",
        "git commit -n -m 'x'",
        "git commit -nm 'x'",
        "git commit -anm 'x'",
        "git commit -qn",
        "git -c core.hooksPath=/dev/null commit --no-verify",
        "git commit -m 'x' | tail -3",
        "git commit -m 'x' 2>&1 | head -5",
        "git commit -m 'x' > /dev/null",
        "git commit -m 'x' 2>/dev/null",
        "git commit -m 'x' &>/dev/null",
        'eval "git commit -n -m x"',
        'bash -c "git commit -m x | tail -1"',
        "cd repo && git commit --no-verify -m 'x'",
    ],
)
def test_commit_gate_bypasses_denied_everywhere(
    tmp_path: Path, cmd: str
) -> None:
  """Hook-skipping and output-hiding commit forms are denied in any dir."""
  assert dgg.scan(cmd, str(tmp_path / "other")).startswith(
      "[DESTRUCTIVE GIT]"
  ), cmd
  assert dgg.unsafe_commit_reason(cmd), cmd


@pytest.mark.parametrize(
    "cmd",
    [
        "git commit -m 'skip -n please'",
        "git commit -m 'see a | tail' ",
        "git commit --no-edit --amend",
        "git commit -am 'x' && git log -1 --oneline",
        "git commit -F /tmp/msg > /tmp/commit.log 2>&1; echo exit=$?",
        "git log | tail -3",
        "git status | head",
    ],
)
def test_commit_forms_that_keep_the_receipt_are_allowed(cmd: str) -> None:
  """Commit forms that keep hooks and output are allowed."""
  assert not dgg.unsafe_commit_reason(cmd), cmd


def test_deny_reason_is_actionable(repo: Path) -> None:
  reason = dgg.scan("git reset --hard", str(repo))
  for needle in ("write_to_file", "git restore --staged", "git commit --only"):
    assert needle in reason


# --- hook process -------------------------------------------------------------


def test_hook_allows_neutral_command_without_state(
    repo: Path, tmp_path: Path
) -> None:
  assert run_hook(_payload("ls -la", str(repo))) == {"decision": "allow"}
  assert not (tmp_path / "data" / "denials.jsonl").exists()


def test_hook_denies_and_logs(repo: Path, tmp_path: Path) -> None:
  """The hook denies a hard reset and writes one denials.jsonl row."""
  out = run_hook(_payload("git reset --hard", str(repo)))
  assert out["decision"] == "deny"
  assert out["reason"].startswith("[DESTRUCTIVE GIT]")
  rows = [
      json.loads(l)
      for l in (tmp_path / "data" / "denials.jsonl").read_text().splitlines()
  ]
  assert len(rows) == 1
  assert (
      rows[0]["gate"] == "DESTRUCTIVE_GIT"
      and rows[0]["hook"] == "destructive_git_guard"
  )
  assert set(rows[0]) == {"ts", "conv", "hook", "gate", "cmd_sha1", "outcome"}
  assert rows[0]["outcome"] == "deny"


def test_hook_ignores_other_tools(repo: Path) -> None:
  assert run_hook(
      _payload("git reset --hard", str(repo), name="write_to_file")
  ) == {"decision": "allow"}


def test_hook_unparsable_stdin_fails_closed(repo: Path) -> None:
  out = run_hook("{not json")
  assert out["decision"] == "deny" and out["reason"].startswith(
      "[HOOK PAYLOAD]"
  )
  assert run_hook("42")["decision"] == "deny"
  assert run_hook("") == {"decision": "allow"}


def test_hook_accepts_tool_input_shape(repo: Path) -> None:
  raw = json.dumps({
      "tool_name": "run_command",
      "tool_input": {"command": "git clean -fdx", "cwd": str(repo)},
  })
  assert run_hook(raw)["decision"] == "deny"


def test_internal_error_fails_closed(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  def boom(*_a, **_k):
    raise RuntimeError("scanner exploded")

  monkeypatch.setattr(dgg, "scan", boom)
  out = dgg.run_hook(_payload("git status", str(repo)))
  assert out["decision"] == "deny" and "failing closed" in out["reason"]


def test_cli_check_and_roots(repo: Path) -> None:
  """The CLI exits 1 on a destructive command and 0 on a safe one."""
  env = dict(os.environ)
  deny = subprocess.run(
      [
          sys.executable,
          "-B",
          str(CLI),
          "check",
          "--cwd",
          str(repo),
          "git",
          "reset",
          "--hard",
      ],
      capture_output=True,
      text=True,
      env=env,
      timeout=20,
      check=False,
  )
  assert deny.returncode == 1 and deny.stdout.startswith("[DESTRUCTIVE GIT]")
  allow = subprocess.run(
      [
          sys.executable,
          "-B",
          str(CLI),
          "check",
          "--cwd",
          str(repo),
          "git",
          "status",
      ],
      capture_output=True,
      text=True,
      env=env,
      timeout=20,
      check=False,
  )
  assert allow.returncode == 0 and allow.stdout.strip() == "allow"
  roots = subprocess.run(
      [sys.executable, "-B", str(CLI), "roots"],
      capture_output=True,
      text=True,
      env=env,
      timeout=20,
      check=False,
  )
  assert roots.stdout.strip() == str(repo.resolve())
  assert (
      subprocess.run(
          [sys.executable, "-B", str(CLI)],
          capture_output=True,
          timeout=20,
          check=False,
      ).returncode
      == 2
  )


def test_evaluate_claude_bash_tool(repo: Path) -> None:
  payload = json.dumps({
      "tool_name": "Bash",
      "tool_input": {"command": "git reset --hard", "cwd": str(repo)},
  })
  decision, reason, cmd, parsed = dgg.evaluate(payload)
  assert decision == "deny"
  assert "git reset" in reason
  assert cmd == "git reset --hard"

