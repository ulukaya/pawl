"""Tests for poll_loop_guard.py and poll_loop_guard_hook.py.

Runs against a real temp data dir, no mocks.

Run:  python3 -m pytest -q test_poll_loop_guard.py
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path  # pylint: disable=g-importing-member
import signal
import subprocess
import sys
from typing import Any, Dict, Optional

HERE = Path(__file__).resolve().parent
HOOK = HERE / "poll_loop_guard_hook.py"
CLI = HERE / "poll_loop_guard.py"
if str(HERE) not in sys.path:
  sys.path.insert(0, str(HERE))

# pylint: disable=g-import-not-at-top,g-bad-import-order
import poll_loop_guard as plg  # noqa: E402
import pytest  # noqa: E402

# pylint: enable=g-import-not-at-top


def _payload(cmd: str, name: str = "run_command") -> str:
  return json.dumps({"toolCall": {"name": name, "args": {"CommandLine": cmd}}})


def run_hook(
    raw: str, tmp: Path, env_extra: Optional[Dict[str, str]] = None
) -> Dict[str, Any]:
  """Runs the hook shim on raw stdin and parses its JSON."""
  env = dict(os.environ, PAWL_DATA=str(tmp))
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


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
  return subprocess.run(
      [sys.executable, "-B", str(CLI), *args],
      text=True,
      capture_output=True,
      timeout=20,
      check=False,
  )


@pytest.mark.parametrize(
    "cmd",
    [
        'python3 -c "import time\nwhile True:\n    time.sleep(2)"',
        "while true; do sleep 5; done",
        "while :; do sleep 1; done",
        "until test -f done.txt; do sleep 3; done",
        'node -e "while (true) { await sleep(1000) }"',
    ],
)
def test_loop_shapes_classify_as_loop(cmd: str) -> None:
  assert plg.classify(cmd) == "LOOP"


@pytest.mark.parametrize(
    "cmd",
    [
        "tail -f build.log",
        "tail -n 20 -F out.log",
        "cd x && watch ls",
        "tail --follow app.log",
    ],
)
def test_tail_and_watch_classify_as_tail(cmd: str) -> None:
  assert plg.classify(cmd) == "TAIL"


@pytest.mark.parametrize(
    "cmd", ["sleep 601", "sleep 3600 && echo done", "sleep 11m", "sleep 1h"]
)
def test_long_sleep_classifies_as_sleep(cmd: str) -> None:
  assert plg.classify(cmd) == "SLEEP"


@pytest.mark.parametrize(
    "cmd",
    [
        "sleep 600",
        "sleep 5; echo ok",
        "ls -la",
        'python3 -c "for i in range(10): time.sleep(1)"',
        "tail -n 50 build.log",
        "",
        "   ",
    ],
)
def test_bounded_and_neutral_commands_are_none(cmd: str) -> None:
  """Bounded waits and unrelated commands classify as None."""
  assert plg.classify(cmd) is None


@pytest.mark.parametrize(
    "cmd",
    [
        "timeout 600 tail -f build.log",
        "timeout 5m bash -c 'while true; do sleep 1; done'",
        "cd /tmp && timeout 120 sleep 9999",
        "FOO=1 timeout -k 5 30 tail -F x.log",
    ],
)
def test_leading_timeout_bounds_the_command(cmd: str) -> None:
  assert plg.classify(cmd) is None


def test_timeout_over_cap_does_not_bound() -> None:
  assert plg.classify("timeout 601 tail -f x.log") == "TAIL"
  assert plg.classify("timeout 2h sleep 1") is None  # sleep itself is short
  assert plg.classify("timeout 2h tail -f x.log") == "TAIL"


def test_deny_reason_names_label_and_remedies() -> None:
  reason = plg.deny_reason("TAIL", "tail   -f   x.log")
  assert reason.startswith(
      "[POLL LOOP] TAIL: unbounded wait in `tail -f x.log`"
  )
  assert "TimerCondition=<task-id>" in reason
  assert f"timeout {plg.MAX_SLEEP_S} <cmd>" in reason


def test_command_of_accepts_both_payload_shapes() -> None:
  """command_of reads both the toolCall and tool_input payload shapes."""
  assert (
      plg.command_of(
          {"toolCall": {"name": "run_command", "args": {"CommandLine": "ls"}}}
      )
      == "ls"
  )
  assert (
      plg.command_of(
          {"tool_name": "run_command", "tool_input": {"command": "pwd"}}
      )
      == "pwd"
  )
  assert not plg.command_of({
      "toolCall": {
          "name": "write_to_file",
          "args": {"CommandLine": "tail -f x"},
      }
  })
  assert not plg.command_of(
      {"toolCall": {"name": "run_command", "args": "tail -f x"}}
  )


def test_decide_ignores_non_run_command_tools() -> None:
  assert (
      plg.decide({
          "toolCall": {
              "name": "view_file",
              "args": {"CommandLine": "tail -f x.log"},
          }
      })
      is None
  )
  assert plg.decide({
      "toolCall": {
          "name": "run_command",
          "args": {"CommandLine": "tail -f x.log"},
      }
  }).startswith("[POLL LOOP] TAIL")


def test_hook_allows_neutral_command_without_writing_state(
    tmp_path: Path,
) -> None:
  assert run_hook(_payload("ls -la"), tmp_path) == {"decision": "allow"}
  assert not (tmp_path / "denials.jsonl").exists()


def test_hook_denies_loop_and_logs_denial(tmp_path: Path) -> None:
  """The hook denies a sleep loop and writes one denials.jsonl row."""
  out = run_hook(_payload("while true; do sleep 2; done"), tmp_path)
  assert out["decision"] == "deny"
  assert out["reason"].startswith("[POLL LOOP] LOOP")
  rows = [
      json.loads(l)
      for l in (tmp_path / "denials.jsonl").read_text().splitlines()
  ]
  assert len(rows) == 1
  assert rows[0]["gate"] == "POLL_LOOP"
  assert rows[0]["hook"] == "poll_loop_guard"
  assert set(rows[0]) == {"ts", "conv", "hook", "gate", "cmd_sha1", "outcome"}
  assert rows[0]["outcome"] == "deny"


def test_hook_unparsable_stdin_fails_closed(tmp_path: Path) -> None:
  out = run_hook("not json", tmp_path)
  assert out["decision"] == "deny"
  assert out["reason"].startswith("[HOOK PAYLOAD]")
  out = run_hook("[1, 2]", tmp_path)
  assert out["decision"] == "deny"


def test_hook_empty_stdin_allows(tmp_path: Path) -> None:
  assert run_hook("", tmp_path) == {"decision": "allow"}


def test_hook_watchdog_fails_open_when_over_budget() -> None:
  """The SIGALRM handler prints allow and exits 0."""
  # A tiny budget with a normal payload still finishes under it, so the
  # watchdog handler itself is exercised directly.
  saved_stdout = sys.stdout
  fired = {}
  real_exit = os._exit
  try:
    sys.stdout = io.StringIO()

    def fake_exit(code: int) -> None:
      fired.setdefault("code", code)

    os._exit = fake_exit  # type: ignore[assignment]
    plg.arm_watchdog(0.05)
    signal.alarm(0)  # cancel the real timer; call the handler by hand
    handler = signal.getsignal(signal.SIGALRM)
    handler(signal.SIGALRM, None)  # type: ignore[misc]
    assert json.loads(sys.stdout.getvalue()) == {"decision": "allow"}
    assert fired == {"code": 0}
  finally:
    os._exit = real_exit  # type: ignore[assignment]
    sys.stdout = saved_stdout
    plg.disarm_watchdog()


def test_cli_classify_and_check_exit_codes() -> None:
  assert run_cli("classify", "tail", "-f", "x.log").stdout.strip() == "TAIL"
  assert run_cli("classify", "ls").stdout.strip() == "none"
  ok = run_cli("check", "sleep", "5")
  assert ok.returncode == 0 and ok.stdout.strip() == "bounded"
  bad = run_cli("check", "sleep", "9999")
  assert bad.returncode == 1 and bad.stdout.startswith("[POLL LOOP] SLEEP")
  assert run_cli().returncode == 2


def test_denial_log_never_raises_on_unwritable_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  blocker = tmp_path / "file"
  blocker.write_text("x")
  monkeypatch.setenv(
      "PAWL_DATA", str(blocker / "sub")
  )  # parent is a file: mkdir fails
  plg.record_denial("POLL_LOOP", "tail -f x", {})  # must not raise
