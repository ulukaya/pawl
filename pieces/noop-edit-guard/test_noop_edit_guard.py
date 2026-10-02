#!/usr/bin/env python3
"""Tests for noop_edit_guard.py and noop_edit_guard_hook.py.

Every deny case has an allow twin one character away.

Run: python3 -m pytest -q test_noop_edit_guard.py
"""

# pylint: disable=redefined-outer-name

from __future__ import annotations

import json
import os
from pathlib import Path  # pylint: disable=g-importing-member
import subprocess
import sys
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# pylint: disable=g-import-not-at-top,g-bad-import-order
import noop_edit_guard as neg  # noqa: E402
import pytest  # noqa: E402

# pylint: enable=g-import-not-at-top

HOOK = HERE / "noop_edit_guard_hook.py"
CLI = HERE / "noop_edit_guard.py"
FILE = "/work/src/app.py"


@pytest.fixture(autouse=True)
def data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
  monkeypatch.setenv("PAWL_DATA", str(tmp_path))
  return tmp_path


def replace(target: str, repl: str, **extra: Any) -> Dict[str, Any]:
  return dict(
      TargetFile=FILE, TargetContent=target, ReplacementContent=repl, **extra
  )


def multi(pairs: List[tuple]) -> Dict[str, Any]:
  return {
      "TargetFile": FILE,
      "ReplacementChunks": [
          {"TargetContent": t, "ReplacementContent": r, "StartLine": 1}
          for t, r in pairs
      ],
  }


def payload(tool: str, args: Any, conv: Optional[str] = "c1") -> str:
  body: Dict[str, Any] = {"toolCall": {"name": tool, "args": args}}
  if conv:
    body["conversationId"] = conv
  return json.dumps(body)


def run_hook(raw: str) -> Dict[str, Any]:
  proc = subprocess.run(
      [sys.executable, "-B", str(HOOK)], input=raw, text=True,
      capture_output=True, env=dict(os.environ), timeout=20, check=False,
  )
  assert proc.returncode == 0, proc.stderr
  return json.loads(proc.stdout)


# --- replace_file_content -----------------------------------------------------


def test_identical_replace_denies_and_one_char_change_allows() -> None:
  reason = neg.noop_reason("replace_file_content", replace("x = 1", "x = 1"))
  assert reason.startswith("[PAWL no-op]")
  assert "app.py" in reason
  assert not neg.noop_reason("replace_file_content", replace("x = 1", "x = 2"))


def test_whitespace_only_change_is_a_real_edit() -> None:
  assert not neg.noop_reason("replace_file_content", replace("a\n", "a"))
  assert not neg.noop_reason("replace_file_content", replace("a", "a "))
  assert neg.noop_reason("replace_file_content", replace("a ", "a "))


def test_identical_empty_strings_deny() -> None:
  assert neg.noop_reason("replace_file_content", replace("", ""))


def test_extra_args_do_not_matter() -> None:
  args = replace("y", "y", Instruction="tidy", AllowMultiple=True)
  assert neg.noop_reason("replace_file_content", args)
  args = replace("y", "z", Instruction="tidy", AllowMultiple=True)
  assert not neg.noop_reason("replace_file_content", args)


def test_reason_sends_the_agent_back_to_view_file() -> None:
  reason = neg.noop_reason("replace_file_content", replace("q", "q"))
  assert "view_file" in reason


# --- multi_replace_file_content -----------------------------------------------


def test_multi_denies_only_when_every_chunk_is_a_noop() -> None:
  tool = "multi_replace_file_content"
  reason = neg.noop_reason(tool, multi([("a", "a"), ("b", "b")]))
  assert reason.startswith("[PAWL no-op]")
  assert "2" in reason
  assert not neg.noop_reason(tool, multi([("a", "a"), ("b", "c")]))


def test_multi_single_noop_chunk_denies() -> None:
  tool = "multi_replace_file_content"
  assert neg.noop_reason(tool, multi([("a", "a")]))
  assert not neg.noop_reason(tool, multi([("a", "A")]))


def test_multi_without_chunks_allows() -> None:
  tool = "multi_replace_file_content"
  assert not neg.noop_reason(tool, {"TargetFile": FILE})
  assert not neg.noop_reason(tool, {"ReplacementChunks": []})
  assert not neg.noop_reason(tool, {"ReplacementChunks": "nope"})
  assert neg.noop_reason(tool, multi([("x", "x")]))


def test_malformed_chunks_allow() -> None:
  tool = "multi_replace_file_content"
  bad = {"ReplacementChunks": [{"TargetContent": "a"}, "junk"]}
  assert not neg.noop_reason(tool, bad)
  half = {"ReplacementChunks": [{"TargetContent": 1, "ReplacementContent": 1}]}
  assert not neg.noop_reason(tool, half)
  assert neg.noop_reason(tool, multi([("a", "a")]))


# --- other tools and shapes ---------------------------------------------------


@pytest.mark.parametrize(
    "tool", ["write_to_file", "view_file", "run_command", ""]
)
def test_other_tools_allow(tool: str) -> None:
  assert not neg.noop_reason(tool, replace("a", "a"))
  assert neg.noop_reason("replace_file_content", replace("a", "a"))


def test_non_dict_args_allow() -> None:
  assert not neg.noop_reason("replace_file_content", ["a", "a"])
  assert not neg.noop_reason("replace_file_content", None)
  assert neg.noop_reason("replace_file_content", replace("a", "a"))


def test_missing_replacement_is_not_a_noop() -> None:
  args = {"TargetFile": FILE, "TargetContent": "a"}
  assert not neg.noop_reason("replace_file_content", args)
  assert neg.noop_reason("replace_file_content", replace("a", "a"))


# --- hook process -------------------------------------------------------------


def test_hook_denies_and_logs_one_row(data: Path) -> None:
  out = run_hook(payload("replace_file_content", replace("a", "a")))
  assert out["decision"] == "deny"
  assert out["reason"].startswith("[PAWL no-op]")
  rows = [json.loads(x) for x in (data / "denials.jsonl").read_text().split(
      "\n") if x]
  assert len(rows) == 1
  assert rows[0]["gate"] == "NOOP_EDIT"
  assert rows[0]["outcome"] == "deny"
  assert FILE not in json.dumps(rows[0])


def test_hook_allows_a_real_edit_without_logging(data: Path) -> None:
  out = run_hook(payload("replace_file_content", replace("a", "b")))
  assert out == {"decision": "allow"}
  assert not (data / "denials.jsonl").exists()
  assert run_hook(payload("replace_file_content", replace("a", "a")))[
      "decision"] == "deny"


def test_hook_tool_input_shape() -> None:
  raw = json.dumps({
      "tool_name": "multi_replace_file_content",
      "tool_input": multi([("a", "a")]),
  })
  assert run_hook(raw)["decision"] == "deny"
  raw = json.dumps({
      "tool_name": "multi_replace_file_content",
      "tool_input": multi([("a", "b")]),
  })
  assert run_hook(raw) == {"decision": "allow"}


@pytest.mark.parametrize("raw", ["{nope", "42", "", "[]"])
def test_hook_fails_open_on_bad_stdin(raw: str) -> None:
  assert run_hook(raw) == {"decision": "allow"}
  assert run_hook(payload("replace_file_content", replace("a", "a")))[
      "decision"] == "deny"


def test_hook_unwritable_log_still_denies(data: Path) -> None:
  (data / "denials.jsonl").mkdir()
  out = run_hook(payload("replace_file_content", replace("a", "a")))
  assert out["decision"] == "deny"


# --- CLI ----------------------------------------------------------------------


def test_cli_check_exit_codes() -> None:
  def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-B", str(CLI), *args], capture_output=True,
        text=True, timeout=20, check=False,
    )

  bad = run("check", "replace_file_content", json.dumps(replace("a", "a")))
  assert bad.returncode == 1 and "[PAWL no-op]" in bad.stdout
  good = run("check", "replace_file_content", json.dumps(replace("a", "b")))
  assert good.returncode == 0 and "allow" in good.stdout
  assert run("check", "replace_file_content", "{bad").returncode == 2
  assert run().returncode == 2


def test_claude_edit_tool_noop_denies() -> None:
  args = {
      "file_path": FILE,
      "old_string": "hello world",
      "new_string": "hello world",
  }
  reason = neg.noop_reason("Edit", args)
  assert reason.startswith("[PAWL no-op]")
  assert "new_string equals old_string" in reason


def test_claude_edit_tool_different_allows() -> None:
  args = {
      "file_path": FILE,
      "old_string": "hello world",
      "new_string": "hello there",
  }
  assert neg.noop_reason("Edit", args) == ""

