#!/usr/bin/env python3
"""Tests for the Claude Code and Codex conversation stores (stores.py).

Each harness keeps every session's transcript under one directory in the
user's home. Every prompt case has an allow twin that reads this session's
own files instead.

Run: python3 -m pytest -q test_stores.py
"""

# pylint: disable=redefined-outer-name

from __future__ import annotations

from pathlib import Path  # pylint: disable=g-importing-member
import sys
from typing import Any, Dict

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# pylint: disable=g-import-not-at-top,g-bad-import-order
import conversation_fence as cf  # noqa: E402
import pytest  # noqa: E402

# pylint: enable=g-import-not-at-top

OWN = "0f3c9a1e-5b2d-4c8e-9f00-123456789abc"
SIB = "ffffffff-0000-4000-8000-000000000000"
ASK = "force_ask"


@pytest.fixture()
def homes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Dict[str, Path]:
  claude, codex = tmp_path / ".claude", tmp_path / ".codex"
  monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude))
  monkeypatch.setenv("CODEX_HOME", str(codex))
  monkeypatch.setenv("PAWL_CONVERSATION_ROOTS", str(tmp_path / "ag"))
  monkeypatch.setenv("PAWL_DATA", str(tmp_path / "data"))
  monkeypatch.delenv("PAWL_CONVERSATION_FENCE_STRICT", raising=False)
  monkeypatch.delenv("PAWL_CONVERSATION_FENCE_OFF", raising=False)
  return {"claude": claude, "codex": codex, "ag": tmp_path / "ag"}


def answer(tool: str, args: Dict[str, Any]) -> Dict[str, str]:
  return cf.decide({"conversationId": OWN,
                    "toolCall": {"name": tool, "args": args}})


def view(path: Path) -> Dict[str, str]:
  return answer("view_file", {"AbsolutePath": str(path)})


def shell(cmd: str) -> Dict[str, str]:
  return answer("run_command", {"CommandLine": cmd, "Cwd": "/work"})


# --- Claude Code: <config>/projects/<project>/<session>.jsonl ------------------


def test_claude_sibling_transcript_asks_own_passes(homes) -> None:
  project = homes["claude"] / "projects" / "-work-repo"
  out = view(project / f"{SIB}.jsonl")
  assert out["decision"] == ASK
  assert f"conversation {SIB}'s transcript" in out["reason"]
  assert view(project / f"{OWN}.jsonl")["decision"] == "allow"


def test_claude_session_dirs_follow_their_owner(homes) -> None:
  project = homes["claude"] / "projects" / "-work-repo"
  assert view(project / SIB / "tool-results" / "r.txt")["decision"] == ASK
  assert view(project / OWN / "tool-results" / "r.txt")["decision"] == "allow"
  assert view(project / OWN / "subagents" / "agent-1.jsonl")[
      "decision"] == "allow"


def test_claude_project_memory_is_shared_not_fenced(homes) -> None:
  memory = homes["claude"] / "projects" / "-work-repo" / "memory" / "MEMORY.md"
  assert view(memory)["decision"] == "allow"


def test_claude_listing_and_prompt_history_ask(homes) -> None:
  projects = homes["claude"] / "projects"
  out = shell(f"ls {projects / '-work-repo'}")
  assert out["decision"] == ASK
  assert "all of projects/" in out["reason"]
  assert shell(f"ls {projects}")["decision"] == ASK
  hist = view(homes["claude"] / "history.jsonl")
  assert hist["decision"] == ASK
  assert "every conversation's prompts" in hist["reason"]
  assert view(homes["claude"] / "settings.json")["decision"] == "allow"


def test_claude_file_history_of_another_session_asks(homes) -> None:
  fh = homes["claude"] / "file-history"
  assert view(fh / SIB / "a.py@v1")["decision"] == ASK
  assert view(fh / OWN / "a.py@v1")["decision"] == "allow"


def test_a_glob_is_a_sweep_not_a_conversation(homes) -> None:
  out = shell(f"cat {homes['claude']}/projects/*/*.jsonl")
  assert out["decision"] == ASK
  assert "all of projects/" in out["reason"]
  assert "conversation *" not in out["reason"]
  ag = shell(f"cat {homes['ag']}/brain/*/notes.md")
  assert "all of brain/" in ag["reason"]


# --- Codex: <home>/sessions/YYYY/MM/DD/rollout-<ts>-<thread>.jsonl -------------


def rollout(homes, thread: str, store: str = "sessions") -> Path:
  return (homes["codex"] / store / "2026" / "10" / "02"
          / f"rollout-2026-10-02T09-00-00-{thread}.jsonl")


def test_codex_sibling_rollout_asks_own_passes(homes) -> None:
  out = view(rollout(homes, SIB))
  assert out["decision"] == ASK
  assert f"conversation {SIB}'s session log" in out["reason"]
  assert view(rollout(homes, OWN))["decision"] == "allow"
  assert view(rollout(homes, SIB, "archived_sessions"))["decision"] == ASK


def test_codex_sweeps_and_prompt_history_ask(homes) -> None:
  assert shell(f"ls -R {homes['codex'] / 'sessions'}")["decision"] == ASK
  assert shell(f"find {homes['codex']}/sessions/2026 -name '*.jsonl'")[
      "decision"] == ASK
  assert view(homes["codex"] / "history.jsonl")["decision"] == ASK
  assert view(homes["codex"] / "config.toml")["decision"] == "allow"


def test_strict_mode_denies_on_every_store(homes, monkeypatch) -> None:
  monkeypatch.setenv("PAWL_CONVERSATION_FENCE_STRICT", "1")
  assert view(rollout(homes, SIB))["decision"] == "deny"
  assert view(homes["claude"] / "projects" / "p" / f"{SIB}.jsonl")[
      "decision"] == "deny"
