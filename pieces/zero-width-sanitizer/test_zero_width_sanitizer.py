#!/usr/bin/env python3
"""Tests for zero_width_sanitizer.py and zero_width_sanitizer_hook.py.

Every strip case has a clean twin that must come back untouched.

Run: python3 -m pytest -q test_zero_width_sanitizer.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path  # pylint: disable=g-importing-member
import subprocess
import sys
from typing import Any, Dict

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# pylint: disable=g-import-not-at-top,g-bad-import-order
import zero_width_sanitizer as zws  # noqa: E402
import pytest  # noqa: E402

# pylint: enable=g-import-not-at-top

HOOK = HERE / "zero_width_sanitizer_hook.py"
CLI = HERE / "zero_width_sanitizer.py"
INVISIBLE = {
    "zero width space": "\u200b",
    "zero width non-joiner": "\u200c",
    "zero width joiner": "\u200d",
    "byte order mark": "\ufeff",
    "word joiner": "\u2060",
    "soft hyphen": "\u00ad",
}


def run_hook(raw: str) -> Dict[str, Any]:
  proc = subprocess.run(
      [sys.executable, "-B", str(HOOK)], input=raw, text=True,
      capture_output=True, env=dict(os.environ), timeout=20, check=False,
  )
  assert proc.returncode == 0, proc.stderr
  return json.loads(proc.stdout)


def call(tool: str, args: Any) -> str:
  return json.dumps({"toolCall": {"name": tool, "args": args}})


# --- strip --------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(INVISIBLE))
def test_each_invisible_character_is_stripped(name: str) -> None:
  ch = INVISIBLE[name]
  assert zws.strip(f"de{ch}f run():{ch}") == "def run():"
  assert zws.strip("def run():") == "def run():"


def test_visible_unicode_survives() -> None:
  text = "café — naïve “quotes” → 日本語  nbsp"
  assert zws.strip(text) == text
  assert zws.strip(text + "\u200b") == text


def test_pattern_is_a_raw_string_class() -> None:
  assert zws.ZERO_WIDTH_PATTERN.pattern.startswith("[\\u200b")


# --- sanitize args ------------------------------------------------------------


def test_write_to_file_code_content_is_cleaned() -> None:
  args = {"TargetFile": "/w/a.py", "CodeContent": "\ufeffimport os\u200b\n"}
  out = zws.sanitize("write_to_file", args)
  assert out == {"TargetFile": "/w/a.py", "CodeContent": "import os\n"}
  assert args["CodeContent"].startswith("\ufeff")  # input not mutated
  assert zws.sanitize("write_to_file", {"CodeContent": "import os\n"}) is None


def test_replace_target_and_replacement_are_cleaned() -> None:
  args = {
      "TargetFile": "/w/a.py",
      "TargetContent": "x\u200d = 1",
      "ReplacementContent": "x = 2\u00ad",
      "AllowMultiple": False,
  }
  out = zws.sanitize("replace_file_content", args)
  assert out == dict(args, TargetContent="x = 1", ReplacementContent="x = 2")
  clean = dict(args, TargetContent="x = 1", ReplacementContent="x = 2")
  assert zws.sanitize("replace_file_content", clean) is None


def test_only_content_fields_are_touched() -> None:
  args = {
      "TargetFile": "/w/a\u200b.py",
      "Instruction": "fix\u200b it",
      "CodeContent": "a\u200b",
  }
  out = zws.sanitize("write_to_file", args)
  assert out["TargetFile"] == "/w/a\u200b.py"
  assert out["Instruction"] == "fix\u200b it"
  assert out["CodeContent"] == "a"


@pytest.mark.parametrize(
    "tool", ["view_file", "run_command", "multi_replace_file_content", ""]
)
def test_other_tools_are_left_alone(tool: str) -> None:
  assert zws.sanitize(tool, {"CodeContent": "a\u200b"}) is None
  assert zws.sanitize("write_to_file", {"CodeContent": "a\u200b"}) is not None


def test_non_dict_and_non_string_fields_are_left_alone() -> None:
  assert zws.sanitize("write_to_file", None) is None
  assert zws.sanitize("write_to_file", ["a\u200b"]) is None
  assert zws.sanitize("write_to_file", {"CodeContent": 5}) is None
  assert zws.sanitize("write_to_file", {"CodeContent": "\u200b"}) == {
      "CodeContent": ""
  }


# --- hook process -------------------------------------------------------------


def test_hook_returns_overwrite_with_full_cleaned_args() -> None:
  args = {"TargetFile": "/w/a.py", "CodeContent": "a\u200bb", "Overwrite": True}
  out = run_hook(call("write_to_file", args))
  assert out == {
      "decision": "allow",
      "overwrite": {"TargetFile": "/w/a.py", "CodeContent": "ab",
                    "Overwrite": True},
  }


def test_hook_plain_allow_when_clean() -> None:
  assert run_hook(call("write_to_file", {"CodeContent": "ab"})) == {
      "decision": "allow"
  }
  dirty = call("write_to_file", {"CodeContent": "a\u00ad"})
  assert "overwrite" in run_hook(dirty)


def test_hook_tool_input_shape() -> None:
  raw = json.dumps({
      "tool_name": "replace_file_content",
      "tool_input": {"TargetContent": "\u2060a", "ReplacementContent": "b"},
  })
  out = run_hook(raw)
  assert out["overwrite"] == {"TargetContent": "a", "ReplacementContent": "b"}


@pytest.mark.parametrize("raw", ["{nope", "42", "", "[]"])
def test_hook_never_blocks_on_bad_stdin(raw: str) -> None:
  assert run_hook(raw) == {"decision": "allow"}
  dirty = call("write_to_file", {"CodeContent": "\u200b"})
  assert "overwrite" in run_hook(dirty)


def test_hook_never_denies(tmp_path: Path) -> None:
  for tool in ("write_to_file", "replace_file_content"):
    out = run_hook(call(tool, {"CodeContent": "\u200b" * 1000,
                               "TargetContent": "\ufeff"}))
    assert out["decision"] == "allow"
  assert "overwrite" in out


# --- CLI ----------------------------------------------------------------------


def test_cli_strip_filters_stdin() -> None:
  proc = subprocess.run(
      [sys.executable, "-B", str(CLI), "strip"], input="a\u200bb\ufeff\n",
      capture_output=True, text=True, timeout=20, check=False,
  )
  assert proc.returncode == 0
  assert proc.stdout == "ab\n"
  bad = subprocess.run(
      [sys.executable, "-B", str(CLI), "nope"], capture_output=True,
      text=True, timeout=20, check=False,
  )
  assert bad.returncode == 2
