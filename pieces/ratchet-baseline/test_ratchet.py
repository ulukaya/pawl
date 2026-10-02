"""Tests for ratchet.py. Run: python3 -m unittest test_ratchet -v"""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ratchet  # noqa: E402

GOOD_REASON = "Fixed the three flaky parser tests and removed the dead retry loop."
SHORT_REASON = "fixed stuff"


def run(argv):
    """Runs ratchet.main and returns (exit_code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = ratchet.main(argv)
    return code, out.getvalue(), err.getvalue()


class RatchetTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.baseline = self.dir / "baseline.json"
        ratchet.write_baseline(self.baseline, {"failing_tests": 5, "bare_except": 3}, GOOD_REASON)

    def tearDown(self):
        self._tmp.cleanup()

    def stored(self):
        return json.loads(self.baseline.read_text())["metrics"]

    def leftovers(self):
        return [p.name for p in self.dir.iterdir() if p.name != "baseline.json"]

    # check

    def test_check_passes_on_equal(self):
        code, out, _ = run(["check", "--baseline", str(self.baseline),
                            "--metric", "failing_tests=5", "--metric", "bare_except=3"])
        self.assertEqual(code, 0)
        self.assertIn("OK", out)
        self.assertNotIn("rose", out)

    def test_check_passes_on_lower_and_names_the_drop(self):
        code, out, _ = run(["check", "--baseline", str(self.baseline),
                            "--metric", "failing_tests=2", "--metric", "bare_except=3"])
        self.assertEqual(code, 0)
        self.assertIn("failing_tests dropped: 5 -> 2", out)

    def test_check_fails_on_higher_with_exit_1_and_message(self):
        code, out, _ = run(["check", "--baseline", str(self.baseline),
                            "--metric", "failing_tests=7", "--metric", "bare_except=3"])
        self.assertEqual(code, 1)
        self.assertIn("failing_tests rose: 5 -> 7", out)
        self.assertIn("FAIL", out)

    def test_check_unknown_metric_passes_as_first_observation(self):
        code, out, _ = run(["check", "--baseline", str(self.baseline),
                            "--metric", "todo_comments=42"])
        self.assertEqual(code, 0)
        self.assertIn("todo_comments", out)
        self.assertIn("first observation", out)

    def test_check_does_not_modify_the_file(self):
        before = self.baseline.read_bytes()
        run(["check", "--baseline", str(self.baseline), "--metric", "failing_tests=1"])
        self.assertEqual(before, self.baseline.read_bytes())

    def test_check_with_missing_baseline_passes(self):
        missing = self.dir / "nope.json"
        code, out, _ = run(["check", "--baseline", str(missing), "--metric", "x=9"])
        self.assertEqual(code, 0)
        self.assertFalse(missing.exists())

    # update

    def test_update_rejects_short_reason_with_exit_2_and_rule(self):
        code, out, _ = run(["update", "--baseline", str(self.baseline),
                            "--metric", "failing_tests=1", "--reason", SHORT_REASON])
        self.assertEqual(code, 2)
        self.assertIn("Rule:", out)
        self.assertEqual(self.stored()["failing_tests"], 5)

    def test_update_rejects_higher_value_with_exit_2_and_rule(self):
        code, out, _ = run(["update", "--baseline", str(self.baseline),
                            "--metric", "bare_except=4", "--reason", GOOD_REASON])
        self.assertEqual(code, 2)
        self.assertIn("bare_except rose: 3 -> 4", out)
        self.assertIn("Rule:", out)
        self.assertEqual(self.stored()["bare_except"], 3)

    def test_update_writes_lower_value_and_keeps_others(self):
        code, out, _ = run(["update", "--baseline", str(self.baseline),
                            "--metric", "failing_tests=2", "--reason", GOOD_REASON])
        self.assertEqual(code, 0)
        self.assertEqual(self.stored(), {"failing_tests": 2, "bare_except": 3})
        data = json.loads(self.baseline.read_text())
        self.assertEqual(data["reason"], GOOD_REASON)
        self.assertIn("updated", data)

    def test_update_adds_unknown_metric(self):
        code, _, _ = run(["update", "--baseline", str(self.baseline),
                          "--metric", "todo_comments=42", "--reason", GOOD_REASON])
        self.assertEqual(code, 0)
        self.assertEqual(self.stored()["todo_comments"], 42)
        self.assertEqual(self.stored()["failing_tests"], 5)

    def test_update_seeds_a_missing_baseline(self):
        fresh = self.dir / "fresh.json"
        code, _, _ = run(["update", "--baseline", str(fresh),
                          "--metric", "a=1", "--metric", "b=2", "--reason", GOOD_REASON])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(fresh.read_text())["metrics"], {"a": 1, "b": 2})

    def test_atomic_write_leaves_no_temp_file(self):
        run(["update", "--baseline", str(self.baseline),
             "--metric", "failing_tests=0", "--reason", GOOD_REASON])
        self.assertEqual(self.leftovers(), [])
        self.assertEqual(self.stored()["failing_tests"], 0)

    # show

    def test_show_prints_all_metrics(self):
        code, out, _ = run(["show", "--baseline", str(self.baseline)])
        self.assertEqual(code, 0)
        self.assertIn("failing_tests", out)
        self.assertIn("bare_except", out)
        self.assertIn("5", out)
        self.assertIn("3", out)

    # input validation

    def test_bad_metric_syntax_exits_2(self):
        code, _, err = run(["check", "--baseline", str(self.baseline), "--metric", "oops"])
        self.assertEqual(code, 2)
        self.assertIn("name=value", err)


if __name__ == "__main__":
    unittest.main()
