#!/usr/bin/env python3
"""Tests for demo.py: the decision matrix, its scratch tree, its failures.

Run: python3 -m pytest -q demo_test.py (from hooks/).
"""

from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path  # pylint: disable=g-importing-member
import shutil
import subprocess
import sys
import tempfile
from typing import Dict, List, Tuple
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
  sys.path.insert(0, str(HERE))

import demo  # noqa: E402  pylint: disable=g-import-not-at-top

# What each harness is told, scenario by scenario: the demo's whole claim.
EXPECTED: Dict[str, Tuple[str, str, str, str]] = {
    # label: (gate, antigravity, claude, codex)
    "make build": ("-", "allow", "silent", "silent"),
    "git log --oneline -5": ("readonly", "auto_approve", "allow", "silent"),
    "git reset --hard": ("git", "force_ask", "ask", "deny"),
    "script that runs rm -rf ~/": ("blast", "deny", "deny", "deny"),
    "tail -f server.log": ("poll", "force_ask", "ask", "deny"),
    "same pytest run, 3rd time": ("loop", "force_ask", "ask", "deny"),
    "edit that changes nothing": ("noop", "deny", "deny", "deny"),
    "write with a U+200B": ("zero-width", "allow +rewrite", "+rewrite",
                            "allow +rewrite"),
    "send naming ~/.deploy/": ("egress", "deny", "deny", "deny"),
    "read another session": ("fence", "force_ask", "ask", "deny"),
    "stop with tail -f running": ("idle", "n/a", "block", "n/a"),
}


def matrix(answers: List[demo.Answer]) -> Dict[str, Tuple[str, ...]]:
  """label -> (gate, cell per harness), the way table() derives them."""
  by_key = {(a.scenario, a.harness): a for a in answers}
  out = {}
  for sc in demo.SCENARIOS:
    cells, reasons = [], []
    for h in demo.HARNESSES:
      if (sc.kind, h) in demo.NOT_SHOWN:
        cells.append("n/a")
        reasons.append("")
        continue
      cell, reason = demo.summarize(by_key[sc.label, h])
      cells.append(cell)
      reasons.append(reason)
    out[sc.label] = (demo.gate_of(reasons, cells),) + tuple(cells)
  return out


class DemoMatrixTest(unittest.TestCase):

  @classmethod
  def setUpClass(cls) -> None:
    super().setUpClass()
    # The user's own settings must not change the demo.
    with mock.patch.dict(os.environ, {"PAWL_DISABLE": "git,poll,loop",
                                      "CLAUDE_PLUGIN_OPTION_DISABLE": "fence",
                                      "PAWL_READONLY_PASS_OFF": "1"}):
      cls.answers = demo.collect()

  def test_every_answer_matches_the_expected_matrix(self) -> None:
    self.assertEqual([a.error for a in self.answers if a.error], [])
    self.assertEqual(matrix(self.answers), EXPECTED)

  def test_reasons_use_each_harness_own_words(self) -> None:
    reasons = {(a.scenario, a.harness): demo.summarize(a)[1]
               for a in self.answers}
    edit = "edit that changes nothing"
    self.assertIn("view_file", reasons[edit, "antigravity"])
    self.assertIn("new_string equals old_string", reasons[edit, "claude"])
    self.assertIn("apply_patch", reasons[edit, "codex"])
    self.assertIn("Codex hooks cannot ask",
                  reasons["git reset --hard", "codex"])

  def test_table_has_a_row_per_scenario_and_the_legend(self) -> None:
    text = demo.table(self.answers)
    lines = text.splitlines()
    for label, (gate, *cells) in EXPECTED.items():
      row = f"{label:<27} {gate:<12}" + "".join(f"{c:<17}" for c in cells)
      self.assertIn(row.rstrip(), lines)
    self.assertIn("Codex hooks can neither ask nor approve", text)
    self.assertNotIn("reasons:", text)
    self.assertIn("reasons:", demo.table(self.answers, verbose=True))


class DemoScratchTest(unittest.TestCase):

  def test_scratch_tree_is_removed_and_home_untouched(self) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="pawl-demo-test-"))
    self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
    scratch = tmp / "scratch"
    scratch.mkdir()
    home = tmp / "real-home"
    home.mkdir()
    with mock.patch.object(demo.tempfile, "mkdtemp",
                           return_value=str(scratch)), \
        mock.patch.dict(os.environ, {"HOME": str(home),
                                     "PAWL_DATA": str(home / ".pawl")}):
      demo.collect()
    self.assertFalse(scratch.exists())
    self.assertEqual(list(home.iterdir()), [])

  def test_a_failing_dispatcher_is_reported_not_raised(self) -> None:
    err = io.StringIO()
    with mock.patch.object(demo, "PAWL", HERE / "no-such-pawl.py"), \
        contextlib.redirect_stdout(io.StringIO()), \
        contextlib.redirect_stderr(err):
      code = demo.main([])
    self.assertEqual(code, 1)
    runs = len(demo.SCENARIOS) * len(demo.HARNESSES) - len(demo.NOT_SHOWN)
    failures = err.getvalue().splitlines()
    self.assertEqual(len(failures), runs)
    self.assertIn("[pawl demo] make build / antigravity: pawl.py exited 2",
                  failures[0])
    self.assertNotIn("Traceback", err.getvalue())


class DemoCommandTest(unittest.TestCase):

  def run_pawl(self, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-B", str(HERE / "pawl.py")]
                          + list(args), capture_output=True, text=True,
                          timeout=120, check=False)

  def test_pawl_demo_json_lists_every_call(self) -> None:
    proc = self.run_pawl("demo", "--json")
    self.assertEqual(proc.returncode, 0, proc.stderr)
    rows = json.loads(proc.stdout)
    self.assertEqual(len(rows), len(demo.SCENARIOS) * len(demo.HARNESSES))
    self.assertEqual({r["harness"] for r in rows}, set(demo.HARNESSES))

  def test_demo_writes_nothing_under_home(self) -> None:
    home = Path(tempfile.mkdtemp(prefix="pawl-demo-home-"))
    self.addCleanup(shutil.rmtree, home, ignore_errors=True)
    env = {k: v for k, v in os.environ.items() if k != "PAWL_DATA"}
    env["HOME"] = str(home)
    proc = subprocess.run([sys.executable, str(HERE / "pawl.py"), "demo"],
                          capture_output=True, text=True, env=env,
                          timeout=120, check=False)
    self.assertEqual(proc.returncode, 0, proc.stderr)
    self.assertEqual(sorted(p.name for p in home.rglob("*")), [])

  def test_bad_flag_exits_2_without_a_traceback(self) -> None:
    proc = self.run_pawl("demo", "--bogus")
    self.assertEqual(proc.returncode, 2)
    self.assertIn("usage: pawl.py demo", proc.stderr)
    self.assertNotIn("Traceback", proc.stderr)


if __name__ == "__main__":
  unittest.main()
