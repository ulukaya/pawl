#!/usr/bin/env python3
"""Tests for breaker.py. Run: python3 -m unittest test_breaker -v

Every test uses a real temp state file and a fake clock. The clock is BREAKER_NOW (epoch
seconds), read by breaker.now() in-process and by every subprocess the tests spawn.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import breaker  # noqa: E402

SCRIPT = HERE / "breaker.py"
T0 = 1_700_000_000.0


class BreakerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name) / "breakers.json"
        self.env = dict(os.environ)
        self.env.update({
            "BREAKER_STATE": str(self.state),
            "BREAKER_COOLDOWN": "100",
            "BREAKER_MAX_COOLDOWN": "350",
            "BREAKER_THRESHOLD": "3",
        })
        self._saved = {k: os.environ.get(k) for k in
                       ("BREAKER_STATE", "BREAKER_COOLDOWN", "BREAKER_MAX_COOLDOWN",
                        "BREAKER_THRESHOLD", "BREAKER_NOW")}
        for k, v in self.env.items():
            if k.startswith("BREAKER_"):
                os.environ[k] = v
        breaker.set_state_path(None)
        self.clock(T0)

    def tearDown(self) -> None:
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    # helpers
    def clock(self, t: float) -> None:
        os.environ["BREAKER_NOW"] = str(t)
        self.env["BREAKER_NOW"] = str(t)

    def cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(SCRIPT), *args], env=self.env,
                              capture_output=True, text=True)

    def entry(self, name: str) -> dict:
        return json.loads(self.state.read_text())["breakers"][name]

    def trip(self, name: str = "job") -> None:
        for _ in range(3):
            breaker.record(name, ok=False, exit_code=1)

    # tests
    def test_closed_allows(self) -> None:
        self.assertEqual(breaker.should_run("job"), (True, "CLOSED"))
        breaker.record("job", ok=False)
        self.assertEqual(breaker.should_run("job"), (True, "CLOSED"))

    def test_two_fails_stay_closed(self) -> None:
        breaker.record("job", ok=False)
        entry = breaker.record("job", ok=False)
        self.assertEqual(entry["state"], "CLOSED")
        self.assertEqual(entry["failures"], 2)

    def test_three_fails_open(self) -> None:
        self.trip()
        e = self.entry("job")
        self.assertEqual(e["state"], "OPEN")
        self.assertEqual(e["failures"], 3)
        self.assertEqual(e["opened_at"], T0)
        self.assertEqual(e["next_probe_at"], T0 + 100)
        self.assertEqual(breaker.should_run("job"), (False, "OPEN"))

    def test_open_skips_with_exit_3(self) -> None:
        self.trip()
        self.clock(T0 + 50)
        proc = self.cli("should-run", "job")
        self.assertEqual(proc.returncode, 3)
        self.assertEqual(proc.stdout.strip(), "OPEN")

    def test_half_open_after_cooldown_allows_one_probe(self) -> None:
        self.trip()
        self.clock(T0 + 100)
        proc = self.cli("should-run", "job")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "HALF_OPEN")
        self.assertEqual(self.entry("job")["state"], "HALF_OPEN")
        # second caller in the same window is refused: only one probe is out
        self.assertEqual(breaker.should_run("job"), (False, "HALF_OPEN"))

    def test_probe_fail_reopens_and_doubles_cooldown(self) -> None:
        self.trip()
        self.clock(T0 + 100)
        breaker.should_run("job")
        entry = breaker.record("job", ok=False, exit_code=2)
        self.assertEqual(entry["state"], "OPEN")
        self.assertEqual(entry["cooldown"], 200)
        self.assertEqual(entry["opened_at"], T0 + 100)
        self.assertEqual(entry["next_probe_at"], T0 + 300)
        self.clock(T0 + 250)
        self.assertEqual(breaker.should_run("job"), (False, "OPEN"))
        self.clock(T0 + 300)
        self.assertEqual(breaker.should_run("job"), (True, "HALF_OPEN"))

    def test_probe_ok_closes_and_resets(self) -> None:
        self.trip()
        self.clock(T0 + 100)
        breaker.should_run("job")
        entry = breaker.record("job", ok=True)
        self.assertEqual(entry["state"], "CLOSED")
        self.assertEqual(entry["failures"], 0)
        self.assertEqual(entry["cooldown"], 100)
        self.assertIsNone(entry["opened_at"])
        self.assertIsNone(entry["next_probe_at"])
        self.assertEqual(breaker.should_run("job"), (True, "CLOSED"))

    def test_cooldown_cap(self) -> None:
        self.trip()
        t = T0
        for expected in (200, 350, 350):
            t = self.entry("job")["next_probe_at"]
            self.clock(t)
            self.assertEqual(breaker.should_run("job"), (True, "HALF_OPEN"))
            entry = breaker.record("job", ok=False)
            self.assertEqual(entry["cooldown"], expected)
            self.assertEqual(entry["next_probe_at"], t + expected)

    def test_success_in_closed_resets_failure_count(self) -> None:
        breaker.record("job", ok=False)
        breaker.record("job", ok=False)
        entry = breaker.record("job", ok=True)
        self.assertEqual(entry["failures"], 0)
        breaker.record("job", ok=False)
        breaker.record("job", ok=False)
        self.assertEqual(self.entry("job")["state"], "CLOSED")

    def test_reset(self) -> None:
        self.trip()
        proc = self.cli("reset", "job")
        self.assertEqual(proc.returncode, 0)
        self.assertNotIn("job", json.loads(self.state.read_text())["breakers"])
        self.assertEqual(breaker.should_run("job"), (True, "CLOSED"))

    def test_status_compact_output(self) -> None:
        breaker.record("healthy", ok=True)
        self.trip("broken")
        proc = self.cli("status", "--compact")
        self.assertEqual(proc.returncode, 0)
        lines = proc.stdout.strip().splitlines()
        self.assertEqual(lines, [
            "broken=OPEN failures=3 opened_at=2023-11-14T22:13:20Z next_probe_at=2023-11-14T22:15:00Z",
            "healthy=CLOSED failures=0 opened_at=- next_probe_at=-",
        ])
        full = self.cli("status")
        self.assertEqual(full.returncode, 0)
        self.assertIn("next_probe_at", full.stdout)
        self.assertIn("broken", full.stdout)

    def test_state_flag_overrides_env(self) -> None:
        other = Path(self.tmp.name) / "other.json"
        proc = self.cli("record", "job", "fail", "--state", str(other))
        self.assertEqual(proc.returncode, 0)
        self.assertTrue(other.exists())
        self.assertFalse(self.state.exists())

    def test_run_wrapper_propagates_exit_code(self) -> None:
        ok = self.cli("run", "job", "--", sys.executable, "-c", "raise SystemExit(0)")
        self.assertEqual(ok.returncode, 0)
        self.assertEqual(self.entry("job")["state"], "CLOSED")
        bad = self.cli("run", "job", "--", sys.executable, "-c", "raise SystemExit(7)")
        self.assertEqual(bad.returncode, 7)
        e = self.entry("job")
        self.assertEqual(e["failures"], 1)
        self.assertEqual(e["last_exit_code"], 7)

    def test_run_wrapper_skips_with_3_when_open(self) -> None:
        self.trip()
        marker = Path(self.tmp.name) / "ran"
        proc = self.cli("run", "job", "--", sys.executable, "-c",
                        f"open({str(marker)!r}, 'w').close()")
        self.assertEqual(proc.returncode, 3)
        self.assertFalse(marker.exists())
        self.assertIn("SKIP", proc.stderr)
        self.assertEqual(self.entry("job")["failures"], 3)

    def test_run_wrapper_accepts_state_flag_before_separator(self) -> None:
        other = Path(self.tmp.name) / "other.json"
        proc = self.cli("run", "job", "--state", str(other), "--",
                        sys.executable, "-c", "raise SystemExit(4)")
        self.assertEqual(proc.returncode, 4)
        self.assertEqual(json.loads(other.read_text())["breakers"]["job"]["failures"], 1)
        self.assertFalse(self.state.exists())

    def test_concurrent_record_from_8_processes(self) -> None:
        os.environ["BREAKER_THRESHOLD"] = self.env["BREAKER_THRESHOLD"] = "100"
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.cli("record", "job", "fail"), range(8)))
        self.assertTrue(all(r.returncode == 0 for r in results), [r.stderr for r in results])
        self.assertEqual(self.entry("job")["failures"], 8)
        leftovers = [p for p in Path(self.tmp.name).iterdir() if p.suffix == ".tmp"]
        self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
