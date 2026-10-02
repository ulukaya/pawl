#!/usr/bin/env python3
"""Tests for oscillation_breaker.py.

Run: python3 -m pytest -q test_oscillation_breaker.py
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
sys.path.insert(0, str(HERE))

# pylint: disable=g-import-not-at-top,g-bad-import-order
import oscillation_breaker as ob  # noqa: E402
import pytest  # noqa: E402

# pylint: enable=g-import-not-at-top

HOOK = HERE / "oscillation_breaker_hook.py"
CONV = "osc-test-conv"


@pytest.fixture(autouse=True)
def data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
  monkeypatch.setenv("PAWL_DATA", str(tmp_path))
  monkeypatch.delenv("PAWL_OSCILLATION_WINDOW", raising=False)
  return tmp_path


def _payload(
    tool: str, args: Dict[str, Any], conv: Optional[str] = CONV
) -> str:
  body: Dict[str, Any] = {"toolCall": {"name": tool, "args": args}}
  if conv:
    body["conversationId"] = conv
  return json.dumps(body)


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


# --- ring ---------------------------------------------------------------------


def test_ring_persists_between_calls(data: Path) -> None:
  assert ob.observe(CONV, "view_file", {"p": 1}) is None
  assert ob.observe(CONV, "grep_search", {"q": "x"}) is None
  ring = ob.load_ring(CONV)
  assert [t for t, _ in ring] == ["view_file", "grep_search"]
  assert ring[0][1] == ob.args_digest({"p": 1})
  assert (data / "oscillation" / f"{CONV}.json").is_file()
  assert not list((data / "oscillation").glob("*.tmp"))


def test_args_digest_is_order_independent() -> None:
  assert ob.args_digest({"a": 1, "b": 2}) == ob.args_digest({"b": 2, "a": 1})
  assert ob.args_digest({"a": 1}) != ob.args_digest({"a": 2})


def test_window_caps_ring(monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setenv("PAWL_OSCILLATION_WINDOW", "6")
  for i in range(20):
    ob.observe(CONV, "view_file", {"i": i})
  assert len(ob.load_ring(CONV)) == 6
  assert ob.load_ring(CONV)[-1][1] == ob.args_digest({"i": 19})


def test_window_floor_and_bad_value(monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setenv("PAWL_OSCILLATION_WINDOW", "2")
  assert ob.window() == ob.MIN_WINDOW
  monkeypatch.setenv("PAWL_OSCILLATION_WINDOW", "lots")
  assert ob.window() == ob.DEFAULT_WINDOW


def test_reset_ring() -> None:
  ob.observe(CONV, "view_file", {})
  ob.reset_ring(CONV)
  assert not ob.load_ring(CONV)
  ob.reset_ring(CONV)  # second reset is a no-op


# --- detection ----------------------------------------------------------------


def test_triple_repeat_fires_on_third_call() -> None:
  assert ob.observe(CONV, "view_file", {"p": "a"}) is None
  assert ob.observe(CONV, "view_file", {"p": "a"}) is None
  reason = ob.observe(CONV, "view_file", {"p": "a"})
  assert reason is not None
  assert reason.startswith(
      "[PAWL loop] view_file repeated with identical args 3 times in a row"
      " (last 3 calls). Approve to continue."
  )
  reason = ob.observe(CONV, "view_file", {"p": "a"})
  assert "4 times in a row (last 4 calls)" in reason


def test_two_gram_cycle_fires() -> None:
  assert ob.observe(CONV, "view_file", {"p": "a"}) is None
  assert ob.observe(CONV, "run_command", {"c": "ls"}) is None
  assert ob.observe(CONV, "view_file", {"p": "a"}) is None
  reason = ob.observe(CONV, "run_command", {"c": "ls"})
  assert reason is not None
  assert reason.startswith(
      "[PAWL loop] run_command/view_file repeated with identical args 2 times"
      " in a row (last 4 calls)."
  )


def test_three_gram_cycle_fires() -> None:
  seq = [("a", {"x": 1}), ("b", {"x": 2}), ("c", {"x": 3})]
  for tool, args in seq:
    assert ob.observe(CONV, tool, args) is None
  assert ob.observe(CONV, "a", {"x": 1}) is None
  assert ob.observe(CONV, "b", {"x": 2}) is None
  reason = ob.observe(CONV, "c", {"x": 3})
  assert reason is not None
  assert (
      "a/b/c repeated with identical args 2 times in a row (last 6 calls)"
      in reason
  )


def test_different_args_do_not_fire() -> None:
  for i in range(12):
    assert ob.observe(CONV, "view_file", {"line": i}) is None


def test_two_repeats_do_not_fire() -> None:
  assert ob.observe(CONV, "view_file", {"p": "a"}) is None
  assert ob.observe(CONV, "view_file", {"p": "a"}) is None
  assert ob.observe(CONV, "grep_search", {"q": "b"}) is None


def test_exempt_tools_are_not_recorded() -> None:
  for _ in range(5):
    assert ob.observe(CONV, "manage_task", {"Action": "status"}) is None
  assert not ob.load_ring(CONV)


def test_detect_on_empty_and_short_rings() -> None:
  assert ob.detect([]) is None
  assert ob.detect([("a", "1"), ("a", "1")]) is None


# --- hook process -------------------------------------------------------------


def test_hook_allows_then_force_asks_and_logs(data: Path) -> None:
  """Two identical calls allow; the third force_asks and logs a row."""
  for _ in range(2):
    assert run_hook(_payload("view_file", {"p": "a"})) == {"decision": "allow"}
  assert not (data / "denials.jsonl").exists()
  out = run_hook(_payload("view_file", {"p": "a"}))
  assert out["decision"] == "force_ask"
  assert out["reason"].startswith("[PAWL loop] view_file repeated")
  rows = [
      json.loads(l) for l in (data / "denials.jsonl").read_text().splitlines()
  ]
  assert len(rows) == 1
  assert rows[0]["gate"] == "OSCILLATION"
  assert rows[0]["outcome"] == "force_ask"
  assert rows[0]["conv"] == CONV
  assert set(rows[0]) == {"ts", "conv", "hook", "gate", "cmd_sha1", "outcome"}


def test_hook_missing_conversation_fails_open(data: Path) -> None:
  env = {
      k: "" for k in ("CONVERSATION_ID", ob.CONV_ENV, "JETSKI_CONVERSATION_ID")
  }
  for _ in range(4):
    assert run_hook(_payload("view_file", {"p": "a"}, conv=None), env) == {
        "decision": "allow"
    }
  assert not (data / "oscillation").exists()


def test_hook_env_conversation_id_is_used(data: Path) -> None:
  env = {
      "CONVERSATION_ID": "",
      ob.CONV_ENV: "env-conv",
      "JETSKI_CONVERSATION_ID": "",
  }
  for _ in range(3):
    out = run_hook(_payload("view_file", {"p": "a"}, conv=None), env)
  assert out["decision"] == "force_ask"
  assert (data / "oscillation" / "env-conv.json").is_file()


def test_hook_bad_stdin_fails_open(data: Path) -> None:
  assert run_hook("{not json") == {"decision": "allow"}
  assert run_hook("42") == {"decision": "allow"}
  assert run_hook("") == {"decision": "allow"}
  assert not (data / "oscillation").exists()


def test_hook_unwritable_state_fails_open(data: Path) -> None:
  (data / "oscillation").write_text("a file where a dir should be")
  assert run_hook(_payload("view_file", {"p": "a"})) == {"decision": "allow"}


# --- CLI ----------------------------------------------------------------------


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
  return subprocess.run(
      [sys.executable, "-B", str(HERE / "oscillation_breaker.py"), *args],
      text=True,
      capture_output=True,
      timeout=20,
      check=False,
  )


def test_cli_check_show_reset() -> None:
  """check exits 0 then 1 at the loop; show prints the ring; reset clears."""
  args = json.dumps({"p": "a"})
  assert run_cli("check", CONV, "view_file", args).returncode == 0
  assert run_cli("check", CONV, "view_file", args).returncode == 0
  proc = run_cli("check", CONV, "view_file", args)
  assert proc.returncode == 1
  assert proc.stdout.startswith("[PAWL loop] view_file repeated")
  show = run_cli("show", CONV)
  assert show.returncode == 0
  assert len(show.stdout.splitlines()) == 3
  assert show.stdout.splitlines()[0].startswith("view_file ")
  assert run_cli("reset", CONV).returncode == 0
  assert not run_cli("show", CONV).stdout


def test_cli_usage_errors() -> None:
  assert run_cli().returncode == 2
  assert run_cli("check", CONV).returncode == 2
  assert run_cli("check", CONV, "view_file", "{bad").returncode == 2
  assert run_cli("bogus", CONV).returncode == 2


def test_claude_session_oscillation(monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.delenv("CONVERSATION_ID", raising=False)
  monkeypatch.delenv(ob.CONV_ENV, raising=False)
  monkeypatch.delenv("JETSKI_CONVERSATION_ID", raising=False)
  session = "claude-session-123"
  p = json.dumps({
      "session_id": session,
      "tool_name": "Bash",
      "tool_input": {"command": "cargo test"},
  })
  assert ob.decide(ob.read_payload(p)) == {"decision": "allow"}
  assert ob.decide(ob.read_payload(p)) == {"decision": "allow"}
  out = ob.decide(ob.read_payload(p))
  assert out["decision"] == "force_ask"
  assert "Bash repeated" in out["reason"]



