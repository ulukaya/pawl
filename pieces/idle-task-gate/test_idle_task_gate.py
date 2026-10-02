"""Tests for idle_task_gate.py and idle_task_gate_hook.py.

Runs against a fake procfs in a temp dir, no mocks.

Run:  python3 -m pytest -q test_idle_task_gate.py
"""

# pylint: disable=redefined-outer-name,unused-argument


from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path  # pylint: disable=g-importing-member
import signal
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent
HOOK = HERE / "idle_task_gate_hook.py"
CLI = HERE / "idle_task_gate.py"
POLL_PIECE = HERE.parent / "poll-loop-guard" / "poll_loop_guard.py"
if str(HERE) not in sys.path:
  sys.path.insert(0, str(HERE))

# pylint: disable=g-import-not-at-top,g-bad-import-order
import idle_task_gate as itg  # noqa: E402
import pytest  # noqa: E402

# pylint: enable=g-import-not-at-top

CONV = "conv-abc"
UPTIME_S = 5000.0


def _hz() -> float:
  return float(os.sysconf("SC_CLK_TCK"))


class FakeProc:
  """Writes a minimal /proc tree: uptime, per-pid environ, stat, cmdline."""

  def __init__(self, root: Path) -> None:
    self.root = root
    root.mkdir(parents=True, exist_ok=True)
    (root / "uptime").write_text(f"{UPTIME_S} 1000.0\n")

  def add(
      self,
      pid: int,
      *,
      ppid: int = 1,
      pgid: Optional[int] = None,
      age_s: float = 0.0,
      cmd: str = "sleep 1",
      conv: Optional[str] = CONV,
  ) -> None:
    """Writes one fake /proc/<pid> entry with the given attributes."""
    d = self.root / str(pid)
    d.mkdir(exist_ok=True)
    env = "HOME=/x\0PATH=/bin\0" + (
        f"ANTIGRAVITY_CONVERSATION_ID={conv}\0" if conv else ""
    )
    (d / "environ").write_bytes(env.encode())
    start = int((UPTIME_S - age_s) * _hz())
    tail = " ".join(["0"] * 16)  # fields 3..18; starttime lands at index 19
    (d / "stat").write_text(
        f"{pid} (bash) S {ppid} {pgid or pid} {tail} {start} 0 0 0\n"
    )
    (d / "cmdline").write_bytes(cmd.replace(" ", "\0").encode() + b"\0")


@pytest.fixture()
def proc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeProc:
  """A fake procfs under tmp_path, wired in via PAWL_IDLE_TASK_PROC_ROOT."""
  fake = FakeProc(tmp_path / "proc")
  monkeypatch.setenv("PAWL_IDLE_TASK_PROC_ROOT", str(fake.root))
  monkeypatch.setenv("PAWL_DATA", str(tmp_path / "data"))
  for var in (
      "PAWL_IDLE_TASK_MINUTES",
      "PAWL_IDLE_TASK_ALLOW_RE",
      "PAWL_IDLE_TASK_STATE",
      "CONVERSATION_ID",
      "ANTIGRAVITY_CONVERSATION_ID",
      "JETSKI_CONVERSATION_ID",
  ):
    monkeypatch.delenv(var, raising=False)
  return fake


def run_hook(
    raw: str, env_extra: Optional[Dict[str, str]] = None
) -> Dict[str, Any]:
  """Runs the hook shim on raw stdin and parses its JSON."""
  env = dict(os.environ)
  env.update(env_extra or {})
  p = subprocess.run(
      [sys.executable, "-B", str(HOOK)],
      input=raw,
      text=True,
      capture_output=True,
      env=env,
      timeout=20,
      check=False,
  )
  assert p.returncode == 0, p.stderr
  return json.loads(p.stdout)


def _payload(conv: str = CONV) -> str:
  return json.dumps({"conversationId": conv})


# --- vendored classify identity -----------------------------------------------


def _block(path: Path) -> List[str]:
  lines = path.read_text().splitlines()
  b = next(i for i, l in enumerate(lines) if l.startswith("# CLASSIFY BEGIN"))
  e = next(i for i, l in enumerate(lines) if l.startswith("# CLASSIFY END"))
  return lines[b : e + 1]


def test_classify_block_is_identical_to_poll_loop_guard() -> None:
  ours, theirs = _block(CLI), _block(POLL_PIECE)
  assert len(ours) > 20
  assert ours == theirs


def test_max_sleep_constant_matches_poll_loop_guard() -> None:
  spec = importlib.util.spec_from_file_location("plg_for_test", POLL_PIECE)
  mod = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(mod)  # type: ignore[union-attr]
  assert itg.MAX_SLEEP_S == mod.MAX_SLEEP_S
  assert itg.classify("sleep 9999") == mod.classify("sleep 9999") == "SLEEP"


# --- root discovery -----------------------------------------------------------


def test_no_stale_tasks_allows(proc: FakeProc) -> None:
  proc.add(100, age_s=30, cmd="sleep 1")
  assert itg.decide({"conversationId": CONV}) == {"decision": "allow"}
  assert not itg.idle_task_roots(CONV)


def test_stale_root_is_listed_with_age_and_cmd(proc: FakeProc) -> None:
  proc.add(200, ppid=1, pgid=200, age_s=1500, cmd="tail -f build.log")
  rows = itg.idle_task_roots(CONV)
  assert len(rows) == 1
  assert (
      rows[0]["pid"] == 200
      and rows[0]["pgid"] == 200
      and rows[0]["cmd"] == "tail -f build.log"
  )
  assert 1495 <= rows[0]["age"] <= 1500


def test_children_of_a_tagged_parent_are_not_roots(proc: FakeProc) -> None:
  proc.add(300, ppid=1, age_s=1500, cmd="bash -c wrapper")
  proc.add(301, ppid=300, age_s=1500, cmd="sleep 99999")
  assert [r["pid"] for r in itg.idle_task_roots(CONV)] == [300]


def test_other_conversations_and_self_are_skipped(proc: FakeProc) -> None:
  proc.add(400, age_s=1500, cmd="sleep 99999", conv="someone-else")
  proc.add(401, age_s=1500, cmd="sleep 99999", conv=None)
  proc.add(402, age_s=1500, cmd="python3 idle_task_gate_hook.py")
  proc.add(os.getpid(), age_s=1500, cmd="sleep 99999")
  assert not itg.idle_task_roots(CONV)


def test_minutes_env_sets_the_threshold(
    proc: FakeProc, monkeypatch: pytest.MonkeyPatch
) -> None:
  proc.add(500, age_s=200, cmd="sleep 99999")
  assert not itg.idle_task_roots(CONV)  # default 10m
  monkeypatch.setenv("PAWL_IDLE_TASK_MINUTES", "2")
  assert itg.max_age_s() == 120.0
  assert [r["pid"] for r in itg.idle_task_roots(CONV)] == [500]
  monkeypatch.setenv("PAWL_IDLE_TASK_MINUTES", "not-a-number")
  assert itg.max_age_s() == 600.0


def test_allow_regex_exempts_matching_commands(
    proc: FakeProc, monkeypatch: pytest.MonkeyPatch
) -> None:
  proc.add(600, age_s=1500, cmd="node dev-server.js")
  proc.add(601, age_s=1500, cmd="tail -f x.log")
  monkeypatch.setenv("PAWL_IDLE_TASK_ALLOW_RE", r"dev-server")
  assert [r["pid"] for r in itg.idle_task_roots(CONV)] == [601]


def test_missing_proc_root_allows(
    proc: FakeProc, monkeypatch: pytest.MonkeyPatch
) -> None:
  monkeypatch.setenv("PAWL_IDLE_TASK_PROC_ROOT", str(proc.root / "nope"))
  assert not itg.idle_task_roots(CONV)
  assert itg.decide({"conversationId": CONV}) == {"decision": "allow"}


# --- decision and breaker -----------------------------------------------------


def test_first_stop_blocks_second_stop_allows_and_kills_wait_shape(
    proc: FakeProc, tmp_path: Path
) -> None:
  """First stop blocks; second stop allows and SIGTERMs the wait shape."""
  victim = subprocess.Popen(
      [sys.executable, "-c", "import time; time.sleep(300)"],
      start_new_session=True,
  )
  try:
    proc.add(
        victim.pid, ppid=1, pgid=victim.pid, age_s=1500, cmd="tail -f build.log"
    )
    first = itg.decide({"conversationId": CONV})
    assert first["decision"] == "block"
    assert first["reason"].startswith("[IDLE TASK] 1 background task(s)")
    assert f"pid {victim.pid} (25m): tail -f build.log" in first["reason"]
    assert "have run past 10m" in first["reason"]
    assert victim.poll() is None
    second = itg.decide({"conversationId": CONV})
    assert second == {"decision": "allow"}
    deadline = time.time() + 5
    while victim.poll() is None and time.time() < deadline:
      time.sleep(0.05)
    assert victim.returncode == -signal.SIGTERM
  finally:
    if victim.poll() is None:
      victim.kill()
      victim.wait()
  state = json.loads((tmp_path / "data" / "idle_task_gate.json").read_text())
  assert state[CONV] == {"fingerprint": str(victim.pid), "blocks": 2}
  rows = [
      json.loads(l)
      for l in (tmp_path / "data" / "denials.jsonl").read_text().splitlines()
  ]
  assert (
      len(rows) == 1
      and rows[0]["gate"] == "IDLE_TASK"
      and rows[0]["conv"] == CONV
  )


def test_second_stop_leaves_non_wait_shapes_running(proc: FakeProc) -> None:
  """Second stop never signals a process that is not a wait shape."""
  victim = subprocess.Popen(
      [sys.executable, "-c", "import time; time.sleep(300)"],
      start_new_session=True,
  )
  try:
    proc.add(
        victim.pid,
        ppid=1,
        pgid=victim.pid,
        age_s=1500,
        cmd="node dev-server.js",
    )
    assert itg.decide({"conversationId": CONV})["decision"] == "block"
    assert itg.decide({"conversationId": CONV}) == {"decision": "allow"}
    time.sleep(0.2)
    assert victim.poll() is None
  finally:
    victim.kill()
    victim.wait()


def test_new_pid_set_resets_the_breaker(proc: FakeProc) -> None:
  proc.add(700, age_s=1500, cmd="sleep 99999")
  assert itg.breaker_permits_block(CONV, "700")
  assert not itg.breaker_permits_block(CONV, "700")
  assert itg.breaker_permits_block(CONV, "700,701")
  assert itg.breaker_permits_block("other-conv", "700")


def test_state_path_env_and_atomic_write(
    proc: FakeProc, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  custom = tmp_path / "deep" / "state.json"
  monkeypatch.setenv("PAWL_IDLE_TASK_STATE", str(custom))
  custom.parent.mkdir(parents=True)
  custom.write_text("{not json")
  # corrupt state reads as empty
  assert itg.breaker_permits_block(CONV, "1")
  assert json.loads(custom.read_text())[CONV]["blocks"] == 1
  assert not custom.with_name("state.json.tmp").exists()


def test_missing_conversation_allows(proc: FakeProc) -> None:
  proc.add(800, age_s=1500, cmd="tail -f x")
  assert itg.decide({}) == {"decision": "allow"}


def test_conversation_id_sources(
    proc: FakeProc, monkeypatch: pytest.MonkeyPatch
) -> None:
  assert itg.resolve_conversation_id({"conversation_id": " c2 "}) == "c2"
  assert itg.resolve_conversation_id({"notify_conversation": "c3"}) == "c3"
  monkeypatch.setenv("JETSKI_CONVERSATION_ID", "c4")
  assert itg.resolve_conversation_id({}) == "c4"
  monkeypatch.setenv("CONVERSATION_ID", "c5")
  assert itg.resolve_conversation_id({"conversationId": ""}) == "c5"


def test_internal_error_fails_open(
    proc: FakeProc, monkeypatch: pytest.MonkeyPatch
) -> None:

  def boom(unused_conv):
    raise RuntimeError("proc walk exploded")

  monkeypatch.setattr(itg, "idle_task_roots", boom)
  assert itg.run_hook(_payload()) == {"decision": "allow"}


# --- hook process -------------------------------------------------------------


def test_hook_allows_on_neutral_tree(proc: FakeProc) -> None:
  proc.add(900, age_s=10, cmd="sleep 1")
  assert run_hook(_payload()) == {"decision": "allow"}


def test_hook_blocks_on_stale_task(proc: FakeProc) -> None:
  proc.add(901, age_s=1500, cmd="sleep 99999")
  out = run_hook(_payload())
  assert (
      out["decision"] == "block"
      and "pid 901 (25m): sleep 99999" in out["reason"]
  )


def test_hook_unparsable_and_empty_stdin_allow(proc: FakeProc) -> None:
  proc.add(902, age_s=1500, cmd="sleep 99999")
  assert run_hook("{not json") == {"decision": "allow"}
  assert run_hook("[1,2]") == {"decision": "allow"}
  assert run_hook("") == {"decision": "allow"}


def test_cli_list_and_classify(proc: FakeProc) -> None:
  """The CLI lists idle roots as JSON and classifies a command."""
  proc.add(904, age_s=1500, cmd="tail -f y.log")
  env = dict(os.environ)
  out = subprocess.run(
      [sys.executable, "-B", str(CLI), "list", CONV],
      capture_output=True,
      text=True,
      env=env,
      timeout=20,
      check=False,
  )
  assert out.returncode == 0
  assert json.loads(out.stdout.strip())["pid"] == 904
  cl = subprocess.run(
      [sys.executable, "-B", str(CLI), "classify", "watch", "ls"],
      capture_output=True,
      text=True,
      timeout=20,
      check=False,
  )
  assert cl.stdout.strip() == "TAIL"
  assert (
      subprocess.run(
          [sys.executable, "-B", str(CLI)],
          capture_output=True,
          timeout=20,
          check=False,
      ).returncode
      == 2
  )


# --- harness-reported background tasks (Claude Code Stop payload) -------------


def _reported(*tasks: Dict[str, Any]) -> Dict[str, Any]:
  return {"conversationId": CONV, "backgroundTasks": list(tasks)}


def _shell(task_id: str, cmd: str, **extra: Any) -> Dict[str, Any]:
  return dict(id=task_id, type="shell", status="running", command=cmd,
              description="d", **extra)


def test_reported_wait_shape_blocks_once_then_allows(proc: FakeProc) -> None:
  payload = _reported(_shell("b1", "tail -f /var/log/app.log"),
                      _shell("b2", "npm run build"))
  out = itg.decide(payload)
  assert out["decision"] == "block"
  assert out["reason"].startswith("[IDLE TASK] 1 background task(s)")
  assert "b1" in out["reason"] and "tail -f" in out["reason"]
  assert "b2" not in out["reason"]
  assert itg.decide(payload) == itg.ALLOW


def test_reported_tasks_without_wait_shapes_allow(proc: FakeProc) -> None:
  payload = _reported(_shell("b1", "pytest -q"),
                      {"id": "a1", "type": "subagent", "status": "running"},
                      {"id": "m1", "type": "monitor", "command": "tail -f x"})
  assert itg.decide(payload) == itg.ALLOW
  assert itg.decide(_reported()) == itg.ALLOW


def test_reported_list_replaces_the_procfs_scan(proc: FakeProc) -> None:
  proc.add(4242, age_s=3600, cmd="sleep 9999")
  assert itg.decide(_reported()) == itg.ALLOW
  assert itg.decide({"conversationId": CONV})["decision"] == "block"


def test_reported_allow_regex_and_new_set(
    proc: FakeProc, monkeypatch: pytest.MonkeyPatch
) -> None:
  monkeypatch.setenv("PAWL_IDLE_TASK_ALLOW_RE", r"dev\.log")
  assert itg.decide(_reported(_shell("b1", "tail -f dev.log"))) == itg.ALLOW
  first = _reported(_shell("b2", "while true; do sleep 5; done"))
  assert itg.decide(first)["decision"] == "block"
  second = _reported(_shell("b2", "while true; do sleep 5; done"),
                     _shell("b3", "sleep 9000"))
  assert itg.decide(second)["decision"] == "block"


def test_reported_malformed_entries_are_ignored(proc: FakeProc) -> None:
  payload = {"conversationId": CONV,
             "backgroundTasks": ["x", None, {"type": "shell"}, 3]}
  assert itg.decide(payload) == itg.ALLOW
