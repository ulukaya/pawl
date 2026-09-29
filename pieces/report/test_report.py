#!/usr/bin/env python3
"""Tests for report.py.

Run: python3 -m pytest -q test_report.py
"""

from __future__ import annotations

import collections
import datetime
import json
import os
from pathlib import Path  # pylint: disable=g-importing-member
import subprocess
import sys
from typing import Dict

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import report  # noqa: E402  # pylint: disable=g-import-not-at-top


def _row(gate: str, ts: datetime.datetime, **extra: str) -> str:
  row: Dict[str, str] = {
      "ts": ts.strftime(report.TS_FORMAT),
      "conv": "c1",
      "hook": "x",
      "gate": gate,
      "cmd_sha1": "0" * 40,
  }
  row.update(extra)
  return json.dumps(row, sort_keys=True)


def _now() -> datetime.datetime:
  return datetime.datetime.now(datetime.timezone.utc)


def run_cli(*args: str, data: Path) -> subprocess.CompletedProcess[str]:
  env = dict(os.environ, PAWL_DATA=str(data))
  return subprocess.run(
      [sys.executable, "-B", str(HERE / "report.py"), *args],
      text=True,
      capture_output=True,
      env=env,
      timeout=20,
      check=False,
  )


def test_summarize_counts_and_sorts() -> None:
  """Counts per gate sort descending and the window drops old rows."""
  now = _now()
  lines = [
      _row("POLL_LOOP", now),
      _row("DESTRUCTIVE_GIT", now),
      _row("POLL_LOOP", now - datetime.timedelta(days=1)),
  ]
  counts, skipped, first, last = report.summarize(lines, None)
  assert counts == {"POLL_LOOP": 2, "DESTRUCTIVE_GIT": 1}
  assert skipped == 0
  assert first is not None and last is not None and first < last
  text = report.render(counts, skipped, first, last)
  assert text.splitlines()[0] == "POLL_LOOP 2"
  assert text.splitlines()[1] == "DESTRUCTIVE_GIT 1"
  assert "total 3" in text


def test_summarize_filters_by_since() -> None:
  now = _now()
  lines = [_row("A", now), _row("A", now - datetime.timedelta(days=30))]
  counts, _, first, last = report.summarize(
      lines, now - datetime.timedelta(days=7)
  )
  assert counts == {"A": 1}
  assert first == last


def test_malformed_rows_are_counted_not_raised() -> None:
  """Bad JSON and missing fields are skipped and counted, never raised."""
  now = _now()
  lines = [
      "not json",
      "[1, 2]",
      json.dumps({"ts": "nope", "gate": "A"}),
      json.dumps({"ts": now.strftime(report.TS_FORMAT)}),
      "",
      _row("A", now),
  ]
  counts, skipped, _, _ = report.summarize(lines, None)
  assert counts == {"A": 1}
  assert skipped == 4


def test_render_omits_command_text_and_ids() -> None:
  now = _now()
  lines = [_row("A", now, conv="secret-conv", cmd_sha1="f" * 40)]
  counts, skipped, first, last = report.summarize(lines, None)
  text = report.render(counts, skipped, first, last)
  assert "secret-conv" not in text
  assert "f" * 40 not in text
  assert text.splitlines() == [
      "A 1",
      "total 1",
      f"range {now.date().isoformat()} {now.date().isoformat()}",
      "skipped 0",
  ]


def test_render_empty() -> None:
  assert report.render(collections.Counter(), 0, None, None) == (
      "total 0\nrange none\nskipped 0\n"
  )


def test_cli_missing_file_exits_2(tmp_path: Path) -> None:
  proc = run_cli(data=tmp_path)
  assert proc.returncode == 2
  assert proc.stdout.strip() == f"no denials file at {tmp_path}/denials.jsonl"


def test_cli_reports_and_honours_days(tmp_path: Path) -> None:
  """The CLI prints the report and --days narrows the window."""
  now = _now()
  (tmp_path / "denials.jsonl").write_text(
      "\n".join([
          _row("POLL_LOOP", now),
          _row("IDLE_TASK", now - datetime.timedelta(days=20)),
          "garbage",
      ])
      + "\n"
  )
  proc = run_cli(data=tmp_path)
  assert proc.returncode == 0
  assert proc.stdout.splitlines() == [
      "POLL_LOOP 1",
      "total 1",
      f"range {now.date().isoformat()} {now.date().isoformat()}",
      "skipped 1",
  ]
  proc = run_cli("--days", "30", data=tmp_path)
  assert proc.stdout.splitlines()[:3] == [
      "IDLE_TASK 1",
      "POLL_LOOP 1",
      "total 2",
  ]
  proc = run_cli("--days", "0", data=tmp_path)
  assert "total 2" in proc.stdout


def test_cli_data_flag_beats_env(tmp_path: Path) -> None:
  other = tmp_path / "other"
  other.mkdir()
  (other / "denials.jsonl").write_text(_row("A", _now()) + "\n")
  proc = run_cli("--data", str(other), data=tmp_path)
  assert proc.returncode == 0
  assert proc.stdout.splitlines()[0] == "A 1"
