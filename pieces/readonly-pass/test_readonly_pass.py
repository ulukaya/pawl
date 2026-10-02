#!/usr/bin/env python3
"""Tests for readonly_pass.py, readonly_rules.py and readonly_pass_hook.py.

APPROVE must come back read-only, PROMPT must not. Most PROMPT rows sit one
flag, operator or quote away from an APPROVE row; the review findings of CL
987874058 (sort --compress-program, `> 1`, VCS global options, `uniq - out`,
quoted secret paths, jj --config-file) each have rows here.

Run: python3 -m pytest -q test_readonly_pass.py
"""

# pylint: disable=redefined-outer-name

from __future__ import annotations

import json
import os
from pathlib import Path  # pylint: disable=g-importing-member
import subprocess
import sys
from typing import Any, Dict, Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# pylint: disable=g-import-not-at-top,g-bad-import-order
import readonly_pass as rp  # noqa: E402
import pytest  # noqa: E402

# pylint: enable=g-import-not-at-top

HOOK = HERE / "readonly_pass_hook.py"

APPROVE = [
    "ls",
    "ls -la src/",
    "ls -la src/*.py",
    "cat README.md",
    "cat < input.txt",
    "head -n 20 a.py",
    "head -20 a.py",
    "tail -n5 build.log",
    "wc -l a.py b.py",
    "grep -rn TODO src",
    "rg -n foo src",
    "rg -rn foo",
    "rg --hidden -g '*.py' foo",
    "find . -name '*.py' -type f",
    "find src -newer setup.py",
    "sed -n '1,20p' a.py",
    "sed -n 5p a.py",
    "sed -n '1,$p' a.py",
    "sort a.txt",
    "sort -u -k 2 a.txt",
    "uniq -c",
    "uniq -f 1 in.txt",
    "uniq -",
    "tree -L 2",
    "file a.bin",
    "echo 'costs $5'",
    "echo '{a,b}'",
    "pwd",
    "cd src && ls",
    "git status",
    "git log --oneline -5",
    "git log --format=%H",
    "git --no-pager log",
    "git -C repo status",
    "git -P diff HEAD~1",
    "git show HEAD:a.py",
    "git diff --stat",
    "git reflog",
    "git reflog show",
    "git blame a.py",
    "git ls-files",
    "git stash list",
    "hg -R x log",
    "hg --cwd x status",
    "hg log -l 5",
    "jj -R x log",
    "jj st",
    "jj diff",
    "g4 opened",
    "g4 changes -m 5",
    "ls 2>&1 | head",
    "ls >&2",
    "ls &>/dev/null",
    "ls 2>/dev/null",
    "cat a.txt | head -5 | wc -l",
    "git status; git log -1",
    "git status && git diff",
    "ls;",
]

PROMPT = [
    "",
    "rm -rf build",
    "touch x",
    "ls; rm x",
    "ls && touch x",
    "ls | xargs rm",
    "ls | tee out.txt",
    "cat $(which x)",
    "cat `which x`",
    "diff <(ls a) <(ls b)",
    "cat <<EOF\nx\nEOF",
    "cat <<<x",
    "(ls)",
    "{ ls; }",
    "ls &",
    "FOO=1 ls",
    "/bin/ls",
    "./ls",
    "ls\nrm x",
    "ls # rm x",
    "echo 'unterminated",
    # redirects
    "ls > out.txt",
    "ls >> out.txt",
    "ls > 1",
    "ls > 2",
    "ls 2> 1",
    "ls >| out",
    "cat <> f",
    "echo payload > 1",
    # find
    r"find . -exec rm {} \;",
    "find . -delete",
    "find . -execdir x",
    "find . -fprint out",
    "find . -ok rm",
    # sed
    "sed -i 's/a/b/' f",
    "sed -n 's/a/b/p' f",
    "sed 's/a/b/' f",
    "sed -n '1,20p;w out' f",
    "sed -n '1,20p' -i f",
    # sort, rg, tree, file
    "sort -o out in",
    "sort --output=out in",
    "sort --compress-program=sh f",
    "sort --compress-prog sh f",
    "sort -uo out in",
    "sort -ofile in",
    "rg -z foo",
    "rg -nz foo",
    "rg --search-zip foo",
    "rg --hostname-bin=x foo",
    "rg --pre cat foo",
    "tree -ao out",
    "tree -R",
    "file -C -m magic",
    "file --compile",
    # uniq
    "uniq - out.txt",
    "uniq -- -in out.txt",
    "uniq -f 1 in.txt out.txt",
    "uniq in.txt out.txt",
    # VCS
    "git push",
    "git commit -m x",
    "git checkout .",
    "git stash",
    "git stash drop",
    "git --work-tree=/x status",
    "git --work-tree /x status",
    "git --git-dir=/x log",
    "git -c core.pager=sh log",
    "g4 -u diff",
    "g4 revert //...",
    "g4 print -o out //depot/x",
    "git reflog expire --all",
    "git reflog delete x",
    "git diff --output=x",
    "git log --output x",
    "git diff --ext-diff",
    "git grep -O foo",
    "git grep --open-files-in-pager foo",
    "hg cat -o out f",
    "hg log --config ui.x=y",
    "hg log --con ui.x=y",
    "jj diff --config-file=evil.toml",
    "jj diff --config-file evil.toml",
    "jj log --config-toml=x",
    "jj log --config-toml x",
    "jj diff --tool x",
    # secrets and conversation stores
    "cat ~/.ssh/id_rsa",
    'cat ~/.net"rc"',
    'cat ~/."ssh"/id_"rsa"',
    'cat transcript".jsonl"',
    "cat ~/.netrc",
    "cat ~/.aws/credentials",
    "head ~/.config/gcloud/credentials.db",
    "cat < ~/.ssh/id_rsa",
    "rg foo ~/.gnupg",
    "cat .env",
    "ls ~/.gemini/jetski/brain/abc",
    "ls brain/",
    "cat conversations/abc.db",
    "rg -n x --file=~/.netrc",
    "cat /proc/self/environ",
    "cat /proc/1234/environ",
    # globs and recursive walks reaching secrets or conversation stores
    "cat .env.local",
    "cat .env*",
    "cat ~/.s*",
    "cat ~/*",
    "cat ~/.*",
    "cat /*",
    "grep -r password ~",
    "grep -rn password /",
    "grep -r password ~/.gemini",
    "find ~",
    "find /",
    "find ~/.gemini",
    "tree ~",
    "tree /",
    "rg password ~",
    "rg password /",
    "du ~",
    "du /",
    # $ and braces
    "cat $GOOGLE_APPLICATION_CREDENTIALS",
    'cat "$HOME/x"',
    "cat ~/.{ssh,gnupg}/x",
    "cat x{1..3}",
    "echo $'x'",
    # programs that are not provable readers
    "python3 x.py",
    "make",
    "awk '{print}' f",
    "env ls",
    "sudo ls",
    "bash -c ls",
    "xargs cat",
]


@pytest.mark.parametrize("cmd", APPROVE)
def test_read_only_commands_approve(cmd: str) -> None:
  assert rp.is_read_only(cmd), cmd


@pytest.mark.parametrize("cmd", PROMPT)
def test_unprovable_commands_prompt(cmd: str) -> None:
  assert not rp.is_read_only(cmd), cmd


# --- hook process -------------------------------------------------------------


@pytest.fixture(autouse=True)
def data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
  monkeypatch.setenv("PAWL_DATA", str(tmp_path))
  monkeypatch.delenv("PAWL_READONLY_PASS_OFF", raising=False)
  return tmp_path


def run_hook(raw: str, env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
  proc = subprocess.run(
      [sys.executable, "-B", str(HOOK)], input=raw, text=True,
      capture_output=True, env=dict(os.environ, **(env or {})), timeout=20,
      check=False,
  )
  assert proc.returncode == 0, proc.stderr
  return json.loads(proc.stdout)


def call(cmd: str, tool: str = "run_command", **extra: Any) -> str:
  args = dict(CommandLine=cmd, **extra)
  return json.dumps({"conversationId": "c1",
                     "toolCall": {"name": tool, "args": args}})


def test_hook_auto_approves_and_logs_sha_only(data: Path) -> None:
  assert run_hook(call("git status")) == {"decision": "auto_approve"}
  rows = [json.loads(x) for x in
          (data / "denials.jsonl").read_text().splitlines()]
  assert len(rows) == 1
  assert rows[0]["gate"] == "READONLY_PASS"
  assert rows[0]["outcome"] == "auto_approve"
  assert "git status" not in json.dumps(rows[0])


def test_hook_allows_unprovable_without_logging(data: Path) -> None:
  assert run_hook(call("git push")) == {"decision": "allow"}
  assert not (data / "denials.jsonl").exists()
  assert run_hook(call("git log"))["decision"] == "auto_approve"


def test_hook_shell_command_and_tool_input_shapes() -> None:
  assert run_hook(call("ls", tool="run_shell_command"))[
      "decision"] == "auto_approve"
  raw = json.dumps({"tool_name": "run_command",
                    "tool_input": {"command": "cat a.txt"}})
  assert run_hook(raw)["decision"] == "auto_approve"


def test_hook_other_tools_allow() -> None:
  assert run_hook(call("ls", tool="view_file")) == {"decision": "allow"}
  assert run_hook(call("ls"))["decision"] == "auto_approve"


def test_hook_sandbox_bypass_request_prompts() -> None:
  assert run_hook(call("ls", BypassSandbox=True)) == {"decision": "allow"}
  assert run_hook(call("ls", BypassSandbox=False))["decision"] == "auto_approve"


def test_hook_off_switch() -> None:
  env = {"PAWL_READONLY_PASS_OFF": "1"}
  assert run_hook(call("ls"), env) == {"decision": "allow"}
  assert run_hook(call("ls"))["decision"] == "auto_approve"


@pytest.mark.parametrize("raw", ["{nope", "42", "", "[]"])
def test_hook_fails_open_on_bad_stdin(raw: str) -> None:
  assert run_hook(raw) == {"decision": "allow"}
  assert run_hook(call("ls"))["decision"] == "auto_approve"


def test_hook_never_denies_or_asks() -> None:
  for cmd in PROMPT[:20] + APPROVE[:20]:
    assert run_hook(call(cmd))["decision"] in {"allow", "auto_approve"}
  assert run_hook(call("ls"))["decision"] == "auto_approve"


def test_cli_check_exit_codes() -> None:
  def cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-B", str(HERE / "readonly_pass.py"), *args],
        capture_output=True, text=True, timeout=20, check=False,
    )

  ok = cli("check", "git", "status")
  assert ok.returncode == 0 and "read-only" in ok.stdout
  no = cli("check", "rm", "x")
  assert no.returncode == 1 and "prompt" in no.stdout
  assert cli().returncode == 2
