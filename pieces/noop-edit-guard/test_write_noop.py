#!/usr/bin/env python3
"""Tests for the whole-file rule of noop_edit_guard.py.

A write whose bytes equal the file already on disk changes nothing; every
deny case has an allow twin that differs by one byte, one path or one shape.

Run: python3 -m pytest -q test_write_noop.py
"""

from __future__ import annotations

from pathlib import Path  # pylint: disable=g-importing-member
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# pylint: disable=g-import-not-at-top,g-bad-import-order
import noop_edit_guard as neg  # noqa: E402
import pytest  # noqa: E402

TEXT = "def main():\n    return 0\n"


@pytest.fixture
def target(tmp_path: Path) -> Path:
  path = tmp_path / "app.py"
  path.write_text(TEXT, encoding="utf-8")
  return path


def write(path: Path, content: str) -> dict:
  return {"TargetFile": str(path), "CodeContent": content}


def test_rewriting_the_same_bytes_denies(target: Path) -> None:
  reason = neg.noop_reason("write_to_file", write(target, TEXT))
  assert reason.startswith("[PAWL no-op] write_to_file on app.py:")
  assert "already holds exactly this content" in reason
  assert "view_file" in reason


def test_one_byte_different_allows(target: Path) -> None:
  assert neg.noop_reason("write_to_file", write(target, TEXT + "\n")) == ""
  assert neg.noop_reason("write_to_file", write(target, TEXT[:-1])) == ""
  assert neg.noop_reason("write_to_file", write(target, TEXT.upper())) == ""


def test_claude_write_tool_denies_in_its_own_words(target: Path) -> None:
  args = {"file_path": str(target), "content": TEXT}
  reason = neg.noop_reason("Write", args)
  assert reason.startswith("[PAWL no-op] Write on app.py:")
  assert "Read" in reason and "view_file" not in reason


def test_new_file_relative_path_and_directory_allow(
    target: Path, tmp_path: Path) -> None:
  assert neg.noop_reason("write_to_file",
                         write(tmp_path / "new.py", TEXT)) == ""
  assert neg.noop_reason("write_to_file",
                         {"TargetFile": "app.py", "CodeContent": TEXT}) == ""
  assert neg.noop_reason("write_to_file", write(tmp_path, "")) == ""


def test_malformed_args_allow(target: Path) -> None:
  assert neg.noop_reason("write_to_file", {"TargetFile": str(target)}) == ""
  assert neg.noop_reason("write_to_file", {"CodeContent": TEXT}) == ""
  assert neg.noop_reason("write_to_file", "not a dict") == ""


def test_large_files_are_not_read(target: Path) -> None:
  big = "x" * (neg.MAX_COMPARE_BYTES + 1)
  target.write_text(big, encoding="utf-8")
  assert neg.noop_reason("write_to_file", write(target, big)) == ""
