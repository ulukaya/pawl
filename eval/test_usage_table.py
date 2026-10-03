#!/usr/bin/env python3
"""Tests for claude_agent.sh and usage_table.py, with known-bad twins.

The agent script runs against a stub claude that records its arguments and
stdin; the table reads planted stream.jsonl files. Every flag has a twin
that must raise it: an off arm that loaded pawl, an on arm that did not, a
capped run, a session with no result.

Run: python3 -m pytest -q eval/test_usage_table.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path  # pylint: disable=g-importing-member
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Optional
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import usage_table  # noqa: E402  # pylint: disable=g-import-not-at-top

AGENT = HERE / "claude_agent.sh"
STUB = """#!/usr/bin/env python3
import json, os, sys
rec = {"argv": sys.argv[1:], "stdin": sys.stdin.read(), "cwd": os.getcwd()}
with open(os.environ["STUB_RECORD"], "w") as fh:
    json.dump(rec, fh)
print(json.dumps({"type": "system", "subtype": "init", "plugins": []}))
print(json.dumps({"type": "result", "subtype": "success", "num_turns": 1}))
"""


def init(plugins: Optional[List[Any]]) -> Dict[str, Any]:
  event: Dict[str, Any] = {"type": "system", "subtype": "init",
                           "model": "claude-test-1"}
  if plugins is not None:
    event["plugins"] = plugins
  return event


def result(cost: float = 0.1, turns: int = 5,
           subtype: str = "success") -> Dict[str, Any]:
  return {
      "type": "result", "subtype": subtype, "is_error": subtype != "success",
      "num_turns": turns, "total_cost_usd": cost,
      "usage": {"input_tokens": 10, "cache_creation_input_tokens": 200,
                "cache_read_input_tokens": 3000, "output_tokens": 40},
  }


PAWL = [{"name": "pawl", "path": "/stage/pawl"}]


class Base(unittest.TestCase):

  def setUp(self) -> None:
    super().setUp()
    self.tmp = Path(tempfile.mkdtemp(prefix="pawl_usage_test_"))
    self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

  def plant(self, label: str, *stream: Any) -> Path:
    path = self.tmp / "runs" / label / "stream.jsonl"
    path.parent.mkdir(parents=True)
    lines = [e if isinstance(e, str) else json.dumps(e) for e in stream]
    path.write_text("\n".join(lines) + "\n")
    return path

  def flags(self) -> Dict[str, tuple]:
    return {r.label: r.flags for r in usage_table.collect(self.tmp)}


class TableTest(Base):

  def test_clean_pair_has_no_flags_and_sums(self) -> None:
    self.plant("1/on/case_a", init(PAWL), result(cost=0.25, turns=7))
    self.plant("1/off/case_a", init([]), result(cost=0.75, turns=9))
    runs = usage_table.collect(self.tmp)
    self.assertEqual([r.flags for r in runs], [(), ()])
    text = usage_table.render(runs, cases=24, passes=3)
    self.assertIn("| total | 2 | 16 | 20 | 400 | 6,000 | 80 | $1.00 |", text)
    self.assertIn("models: claude-test-1\n", text)
    self.assertIn("on $0.250, off $0.750", text)
    self.assertIn("= 144 runs, about $72.00", text)
    self.assertNotIn("flagged", text)

  def test_pawl_on_the_off_arm_is_flagged(self) -> None:
    self.plant("1/off/case_a", init(PAWL), result())
    self.assertEqual(self.flags()["1/off/case_a"],
                     ("pawl loaded on the off arm",))

  def test_marketplace_name_counts_as_pawl(self) -> None:
    self.plant("1/off/case_a", init(["pawl@pawl"]), result())
    self.assertIn("pawl loaded on the off arm", self.flags()["1/off/case_a"])

  def test_pawl_missing_on_the_on_arm_is_flagged(self) -> None:
    self.plant("1/on/case_a", init([{"name": "other"}]), result())
    self.assertEqual(self.flags()["1/on/case_a"],
                     ("pawl not loaded on the on arm",))

  def test_unlisted_plugins_cannot_pass_as_checked(self) -> None:
    self.plant("1/on/case_a", init(None), result())
    self.plant("1/off/case_b", result())
    flags = self.flags()
    self.assertIn("cannot check", flags["1/on/case_a"][0])
    self.assertIn("cannot check", flags["1/off/case_b"][0])

  def test_capped_run_is_flagged(self) -> None:
    self.plant("1/on/case_a", init(PAWL), result(subtype="error_max_turns"))
    self.assertEqual(self.flags()["1/on/case_a"],
                     ("stopped: error_max_turns",))

  def test_api_refusal_reported_as_success_is_flagged(self) -> None:
    refused = {**result(), "is_error": True, "terminal_reason": "api_error",
               "stop_reason": "refusal"}
    self.plant("1/on/case_a", init(PAWL), refused)
    self.assertEqual(self.flags()["1/on/case_a"],
                     ("stopped: api_error (refusal)",))

  def test_session_with_no_result_is_flagged_and_costs_nothing(self) -> None:
    self.plant("1/off/case_a", "claude: unknown option", init([]))
    run = usage_table.collect(self.tmp)[0]
    self.assertEqual((run.cost, run.model), (0.0, "claude-test-1"))
    self.assertIn("no result event", run.flags[0])

  def test_last_result_wins_and_garbage_lines_are_skipped(self) -> None:
    self.plant("1/on/case_a", "not json", init(PAWL), "[1, 2]",
               result(cost=0.1), result(cost=0.3))
    run = usage_table.collect(self.tmp)[0]
    self.assertEqual((run.cost, run.flags), (0.3, ()))

  def test_one_arm_only_projects_with_the_overall_mean(self) -> None:
    self.plant("1/off/case_a", init([]), result(cost=0.5))
    text = usage_table.render(usage_table.collect(self.tmp), 24, 1)
    self.assertIn("= 48 runs, about $24.00", text)

  def test_main_exit_codes(self) -> None:
    self.assertEqual(usage_table.main([str(self.tmp)]), 2)
    self.plant("1/on/case_a", init(PAWL), result())
    self.assertEqual(usage_table.main([str(self.tmp)]), 0)
    self.plant("1/off/case_a", init(PAWL), result())
    self.assertEqual(usage_table.main([str(self.tmp)]), 1)


class AgentTest(Base):

  def setUp(self) -> None:
    super().setUp()
    self.stub = self.tmp / "claude"
    self.stub.write_text(STUB)
    self.stub.chmod(self.stub.stat().st_mode | stat.S_IEXEC)
    self.run_dir = self.tmp / "run"
    (self.run_dir / "work").mkdir(parents=True)
    self.prompt = self.run_dir / "prompt.txt"
    self.prompt.write_text("fix the bug\n")
    self.record = self.tmp / "record.json"

  def agent(self, plugin: str, **env: str) -> subprocess.CompletedProcess:
    full = {**os.environ, "PAWL_EVAL_CLAUDE": str(self.stub),
            "STUB_RECORD": str(self.record), **env}
    for key in ("PAWL_EVAL_MODEL", "PAWL_EVAL_MAX_USD"):
      if key not in env:
        full.pop(key, None)
    return subprocess.run(
        ["bash", str(AGENT), str(self.run_dir / "work"), str(self.prompt),
         plugin], capture_output=True, text=True, env=full, check=False)

  def argv(self) -> List[str]:
    return json.loads(self.record.read_text())["argv"]

  def test_on_arm_loads_the_plugin_and_saves_the_stream(self) -> None:
    proc = self.agent("/stage/pawl")
    self.assertEqual(proc.returncode, 0, proc.stderr)
    argv = self.argv()
    self.assertEqual(argv[argv.index("--plugin-dir") + 1], "/stage/pawl")
    rec = json.loads(self.record.read_text())
    self.assertEqual(rec["stdin"], "fix the bug\n")
    self.assertEqual(Path(rec["cwd"]).resolve(),
                     (self.run_dir / "work").resolve())
    stream = usage_table.events(self.run_dir / "stream.jsonl")
    self.assertEqual(stream[-1]["type"], "result")

  def test_off_arm_loads_no_plugin(self) -> None:
    self.assertEqual(self.agent("").returncode, 0)
    self.assertNotIn("--plugin-dir", self.argv())

  def test_both_arms_skip_user_settings_and_refuse_prompts(self) -> None:
    for plugin in ("/stage/pawl", ""):
      self.agent(plugin)
      argv = self.argv()
      self.assertEqual(argv[argv.index("--setting-sources") + 1],
                       "project,local")
      self.assertEqual(argv[argv.index("--permission-prompts") + 1], "none")
      self.assertIn("--no-session-persistence", argv)

  def test_caps_and_model_come_from_the_environment(self) -> None:
    self.agent("", PAWL_EVAL_MAX_USD="0.25", PAWL_EVAL_MODEL="sonnet")
    argv = self.argv()
    self.assertEqual(argv[argv.index("--max-budget-usd") + 1], "0.25")
    self.assertEqual(argv[argv.index("--model") + 1], "sonnet")
    self.agent("")
    argv = self.argv()
    self.assertEqual(argv[argv.index("--max-budget-usd") + 1], "1")
    self.assertNotIn("--model", argv)

  def test_bad_arguments_exit_2(self) -> None:
    proc = subprocess.run(["bash", str(AGENT), "only-one"],
                          capture_output=True, text=True, check=False)
    self.assertEqual(proc.returncode, 2)
    self.prompt.unlink()
    proc = self.agent("")
    self.assertEqual(proc.returncode, 2)
    self.assertIn("missing workdir", proc.stderr)


if __name__ == "__main__":
  unittest.main()
