#!/usr/bin/env python3
"""Tests for reread_guard.py, reread_shapes.py and reread_guard_hook.py.

Every limit is tested at the last allowed read and the first denied one, and
every bounded shape is tested next to its unbounded twin.

Run: python3 -m pytest -q test_reread_guard.py
"""

# pylint: disable=redefined-outer-name

from __future__ import annotations

import json
import os
from pathlib import Path  # pylint: disable=g-importing-member
import subprocess
import sys
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# pylint: disable=g-import-not-at-top,g-bad-import-order
import reread_guard as rg  # noqa: E402
import pytest  # noqa: E402

# pylint: enable=g-import-not-at-top

HOOK = HERE / "reread_guard_hook.py"
CONV = "conv-aaa"


class Session:
  """One conversation's files plus a helper that sends tool calls."""

  def __init__(self, root: Path) -> None:
    self.dir = root / "brain" / CONV
    self.dir.mkdir(parents=True)
    self.transcript = self.dir / "transcript.jsonl"
    self.transcript.write_text('{"type": "USER_INPUT"}\n' + "{}\n" * 50)
    self.skill = root / "skills" / "x" / "SKILL.md"
    self.skill.parent.mkdir(parents=True)
    self.skill.write_text("# skill\n" * 40)
    self.memory = root / "GEMINI.md"
    self.memory.write_text("# memory\n" * 40)
    self.turn: Optional[str] = "t1"

  def payload(self, tool: str, args: Dict[str, Any]) -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "conversationId": CONV,
        "transcriptPath": str(self.transcript),
        "toolCall": {"name": tool, "args": args},
    }
    if self.turn is not None:
        body["turnId"] = self.turn
    return body

  def view(self, path: Path, start: Optional[int] = None,
           end: Optional[int] = None) -> Dict[str, str]:
    args: Dict[str, Any] = {"AbsolutePath": str(path)}
    if start is not None:
      args["StartLine"] = start
    if end is not None:
      args["EndLine"] = end
    return rg.decide(self.payload("view_file", args))

  def run(self, cmd: str) -> Dict[str, str]:
    return rg.decide(self.payload("run_command", {"CommandLine": cmd}))

  def decisions(self, calls: List[Any]) -> List[str]:
    return [c()["decision"] for c in calls]


@pytest.fixture()
def s(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
  monkeypatch.setenv("PAWL_DATA", str(tmp_path / "data"))
  monkeypatch.delenv("PAWL_REREAD_GUARD_OFF", raising=False)
  return Session(tmp_path)


# --- transcript ---------------------------------------------------------------


def test_eleventh_unbounded_transcript_read_denies(s: Session) -> None:
  for _ in range(10):
    assert s.view(s.transcript) == {"decision": "allow"}
  out = s.view(s.transcript)
  assert out["decision"] == "deny"
  assert out["reason"].startswith("[PAWL reread]")
  assert "scratch file" in out["reason"]


def test_shell_dumps_of_the_transcript_count(s: Session) -> None:
  t = s.transcript
  cmds = [f"cat {t}", f"tail -n 200 {t}", f"head -c 100000 {t}",
          f"sed -n '1,400p' {t}", f"grep USER {t}", f"tail -n +5 {t}",
          f"head -n -3 {t}", f"less {t}", f"cat {t} | grep x", f"rg x {t}"]
  assert [s.run(c)["decision"] for c in cmds] == ["allow"] * 10
  assert s.run(f"cat {t}")["decision"] == "deny"


def test_bounded_reads_never_count(s: Session) -> None:
  t = s.transcript
  bounded = [
      lambda: s.view(t, 1, 9), lambda: s.view(t, 40, 45),
      lambda: s.run(f"head {t}"), lambda: s.run(f"head -n 10 {t}"),
      lambda: s.run(f"tail -5 {t}"), lambda: s.run(f"tail -n3 {t}"),
      lambda: s.run(f"head --lines=8 {t}"), lambda: s.run(f"head -c 20000 {t}"),
      lambda: s.run(f"wc -l {t}"), lambda: s.run(f"rg -c x {t}"),
      lambda: s.run(f"grep --count x {t}"),
      lambda: s.run(f"sed -n '1,10p' {t}"),
      lambda: s.run(f"sed -n 7p {t}"), lambda: s.run(f"cat {t} | tail -5"),
      lambda: s.run(f"cat {t} | wc -l"),
  ]
  assert s.decisions(bounded * 2) == ["allow"] * 30
  assert s.decisions([lambda: s.view(t)] * 10) == ["allow"] * 10
  assert s.view(t, 1, 10)["decision"] == "deny"  # 10 lines is not bounded


def test_another_conversations_transcript_never_counts(
    s: Session, tmp_path: Path
) -> None:
  other = tmp_path / "brain" / "conv-bbb" / "transcript.jsonl"
  other.parent.mkdir(parents=True)
  other.write_text("{}\n")
  assert s.decisions([lambda: s.view(other)] * 20) == ["allow"] * 20
  assert s.decisions([lambda: s.view(s.transcript)] * 11)[-1] == "deny"


def test_rotated_own_transcripts_share_one_count(s: Session) -> None:
  second = s.dir / "transcript_2.jsonl"
  second.write_text("{}\n")
  calls = [lambda: s.view(s.transcript), lambda: s.view(second)] * 5
  assert s.decisions(calls) == ["allow"] * 10
  assert s.view(second)["decision"] == "deny"


# --- SKILL.md and memory files ------------------------------------------------


def test_sixth_read_of_an_unchanged_skill_denies(s: Session) -> None:
  assert s.decisions([lambda: s.view(s.skill)] * 5) == ["allow"] * 5
  assert s.view(s.skill)["decision"] == "deny"


def test_editing_the_skill_starts_a_fresh_count(s: Session) -> None:
  s.decisions([lambda: s.view(s.skill)] * 5)
  st = s.skill.stat()
  os.utime(s.skill, (st.st_atime, st.st_mtime + 5))
  assert s.decisions([lambda: s.view(s.skill)] * 5) == ["allow"] * 5
  assert s.run(f"cat {s.skill}")["decision"] == "deny"


def test_each_skill_has_its_own_count(s: Session, tmp_path: Path) -> None:
  other = tmp_path / "skills" / "y" / "SKILL.md"
  other.parent.mkdir(parents=True)
  other.write_text("# y\n" * 20)
  calls = [lambda: s.view(s.skill), lambda: s.view(other)] * 5
  assert s.decisions(calls) == ["allow"] * 10
  assert s.view(other)["decision"] == "deny"


def test_memory_file_counts_only_reads_from_the_top(s: Session) -> None:
  m = s.memory
  middle = [lambda: s.view(m, 20, 40), lambda: s.run(f"tail -n 30 {m}"),
            lambda: s.run(f"sed -n '20,40p' {m}")]
  assert s.decisions(middle * 4) == ["allow"] * 12
  top = [lambda: s.view(m), lambda: s.view(m, 1, 40), lambda: s.run(f"cat {m}"),
         lambda: s.run(f"sed -n '1,40p' {m}"), lambda: s.view(m, 1)]
  assert s.decisions(top) == ["allow"] * 5
  assert s.view(m)["decision"] == "deny"


def test_memory_dir_markdown_counts(s: Session, tmp_path: Path) -> None:
  note = tmp_path / "memory" / "user_role.md"
  note.parent.mkdir()
  note.write_text("x\n" * 30)
  assert s.decisions([lambda: s.view(note)] * 5) == ["allow"] * 5
  assert s.view(note)["decision"] == "deny"
  plain = tmp_path / "notes.md"
  plain.write_text("x\n" * 30)
  assert s.decisions([lambda: s.view(plain)] * 12) == ["allow"] * 12


# --- turns and the three-denial stand-down ------------------------------------


def test_new_turn_id_resets_counts(s: Session) -> None:
  s.decisions([lambda: s.view(s.skill)] * 5)
  s.turn = "t2"
  assert s.decisions([lambda: s.view(s.skill)] * 5) == ["allow"] * 5
  assert s.view(s.skill)["decision"] == "deny"


def test_user_input_lines_key_the_turn_without_turn_id(s: Session) -> None:
  s.turn = None
  s.decisions([lambda: s.view(s.skill)] * 5)
  assert s.view(s.skill)["decision"] == "deny"
  with s.transcript.open("a") as fh:
    fh.write('{"type": "USER_INPUT"}\n')
  assert s.view(s.skill)["decision"] == "allow"


def test_three_denials_stand_down_until_next_turn(s: Session) -> None:
  s.decisions([lambda: s.view(s.skill)] * 5)
  assert s.decisions([lambda: s.view(s.skill)] * 3) == ["deny"] * 3
  assert s.decisions([lambda: s.view(s.skill)] * 5) == ["allow"] * 5
  s.turn = "t2"
  s.decisions([lambda: s.view(s.skill)] * 5)
  assert s.view(s.skill)["decision"] == "deny"


# --- non-reads, switches, failure modes ---------------------------------------


def test_writes_and_other_tools_never_count(s: Session) -> None:
  calls = [lambda: s.run(f"echo hi > {s.skill}"),
           lambda: rg.decide(s.payload("write_to_file",
                                       {"TargetFile": str(s.skill)})),
           lambda: s.run(f"ls {s.skill.parent}")]
  assert s.decisions(calls * 6) == ["allow"] * 18
  assert s.decisions([lambda: s.view(s.skill)] * 6)[-1] == "deny"


def test_off_switch(s: Session, monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setenv("PAWL_REREAD_GUARD_OFF", "1")
  assert s.decisions([lambda: s.view(s.skill)] * 8) == ["allow"] * 8
  monkeypatch.delenv("PAWL_REREAD_GUARD_OFF")
  assert s.decisions([lambda: s.view(s.skill)] * 6)[-1] == "deny"


def test_missing_conversation_fails_open(s: Session) -> None:
  body = s.payload("view_file", {"AbsolutePath": str(s.skill)})
  del body["conversationId"]
  assert [rg.decide(body)["decision"] for _ in range(8)] == ["allow"] * 8
  assert s.decisions([lambda: s.view(s.skill)] * 6)[-1] == "deny"


def test_denial_row_carries_path_sha_only(s: Session, tmp_path: Path) -> None:
  s.decisions([lambda: s.view(s.skill)] * 6)
  rows = (tmp_path / "data" / "denials.jsonl").read_text().splitlines()
  assert len(rows) == 1
  row = json.loads(rows[0])
  assert row["gate"] == "REREAD" and row["outcome"] == "deny"
  assert str(s.skill) not in rows[0]


def run_hook(raw: str, env: Dict[str, str]) -> Dict[str, Any]:
  proc = subprocess.run(
      [sys.executable, "-B", str(HOOK)], input=raw, text=True,
      capture_output=True, env=dict(os.environ, **env), timeout=20,
      check=False,
  )
  assert proc.returncode == 0, proc.stderr
  return json.loads(proc.stdout)


def test_hook_process_denies_and_fails_open(s: Session, tmp_path: Path) -> None:
  env = {"PAWL_DATA": str(tmp_path / "hookdata")}
  raw = json.dumps(s.payload("view_file", {"AbsolutePath": str(s.skill)}))
  outs = [run_hook(raw, env)["decision"] for _ in range(6)]
  assert outs == ["allow"] * 5 + ["deny"]
  for bad in ("{nope", "42", "", "[]"):
    assert run_hook(bad, env) == {"decision": "allow"}


def test_unwritable_state_fails_open(s: Session, tmp_path: Path) -> None:
  (tmp_path / "data").mkdir()
  (tmp_path / "data" / "reread").write_text("a file, not a dir")
  assert s.decisions([lambda: s.view(s.skill)] * 8) == ["allow"] * 8
  assert rg.run_hook("{nope") == {"decision": "allow"}


def test_claude_read_tool(s: Session) -> None:
  payload = {
      "session_id": CONV,
      "tool_name": "Read",
      "tool_input": {"file_path": str(s.skill)},
      "transcript_path": str(s.transcript),
  }
  decisions = [rg.decide(payload)["decision"] for _ in range(6)]
  assert decisions == ["allow"] * 5 + ["deny"]

