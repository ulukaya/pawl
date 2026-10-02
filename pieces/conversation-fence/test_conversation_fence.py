#!/usr/bin/env python3
"""Tests for conversation_fence.py and conversation_fence_hook.py.

A store root holds four conversations: OWN, its parent PAR, its child KID and
a sibling SIB (same parent, not lineage). Every prompt case has an allow twin
that differs only in whose files are read.

Run: python3 -m pytest -q test_conversation_fence.py
"""

# pylint: disable=redefined-outer-name

from __future__ import annotations

import json
import os
from pathlib import Path  # pylint: disable=g-importing-member
import sqlite3
import subprocess
import sys
from typing import Any, Dict

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# pylint: disable=g-import-not-at-top,g-bad-import-order
import conversation_fence as cf  # noqa: E402
import pytest  # noqa: E402

# pylint: enable=g-import-not-at-top

HOOK = HERE / "conversation_fence_hook.py"
OWN, PAR, KID, SIB, FAR = "own-1", "par-1", "kid-1", "sib-1", "far-1"
ASK = "force_ask"


@pytest.fixture()
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
  store = tmp_path / ".gemini" / "antigravity"
  for conv in (OWN, PAR, KID, SIB, FAR):
    (store / "brain" / conv).mkdir(parents=True)
    (store / "brain" / conv / "notes.md").write_text("private\n")
    (store / "conversations").mkdir(exist_ok=True)
    (store / "conversations" / f"{conv}.db").write_text("x")
  db = sqlite3.connect(store / "conversation_summaries.db")
  db.execute("CREATE TABLE conversation_summaries (conversation_id TEXT,"
             " parent_conversation_id TEXT, title TEXT, summary TEXT)")
  db.executemany(
      "INSERT INTO conversation_summaries VALUES (?, ?, 't', 's')",
      [(OWN, PAR), (KID, OWN), (SIB, PAR), (PAR, ""), (FAR, "")],
  )
  db.commit()
  db.close()
  monkeypatch.setenv("PAWL_CONVERSATION_ROOTS", str(store))
  monkeypatch.setenv("PAWL_DATA", str(tmp_path / "data"))
  monkeypatch.delenv("PAWL_CONVERSATION_FENCE_STRICT", raising=False)
  monkeypatch.delenv("PAWL_CONVERSATION_FENCE_OFF", raising=False)
  return store


def ask(tool: str, args: Dict[str, Any], conv: str = OWN) -> str:
  body: Dict[str, Any] = {"toolCall": {"name": tool, "args": args}}
  if conv:
    body["conversationId"] = conv
  return cf.decide(body)["decision"]


def view(path: Path, conv: str = OWN) -> str:
  return ask("view_file", {"AbsolutePath": str(path)}, conv)


def shell(cmd: str, cwd: str = "", conv: str = OWN) -> str:
  args = {"CommandLine": cmd}
  if cwd:
    args["Cwd"] = cwd
  return ask("run_command", args, conv)


# --- whose brain --------------------------------------------------------------


@pytest.mark.parametrize("conv,expected", [
    (OWN, "allow"), (PAR, "allow"), (KID, "allow"), (SIB, ASK), (FAR, ASK),
])
def test_brain_reads_by_lineage(root: Path, conv: str, expected: str) -> None:
  assert view(root / "brain" / conv / "notes.md") == expected
  assert view(root / "brain" / SIB / "notes.md") == ASK


@pytest.mark.parametrize("conv,expected", [
    (OWN, "allow"), (KID, "allow"), (SIB, ASK), (FAR, ASK),
])
def test_conversation_transcripts_by_lineage(
    root: Path, conv: str, expected: str
) -> None:
  assert view(root / "conversations" / f"{conv}.db") == expected
  assert view(root / "conversations" / f"{FAR}.pb") == ASK


def test_reason_names_the_fence_and_the_way_out(root: Path) -> None:
  out = cf.decide({"conversationId": OWN, "toolCall": {
      "name": "view_file",
      "args": {"AbsolutePath": str(root / "brain" / SIB / "notes.md")}}})
  assert out["decision"] == ASK
  assert out["reason"].startswith("[PAWL fence]")
  assert SIB in out["reason"]
  assert "user" in out["reason"]


# --- sweeps and the summaries db ----------------------------------------------


def test_listing_or_sweeping_the_stores_asks(root: Path) -> None:
  assert ask("list_dir", {"DirectoryPath": str(root / "brain")}) == ASK
  assert shell(f"ls {root}/conversations") == ASK
  assert shell(f"rg -n secret {root}/brain") == ASK
  assert shell(f"find {root}/brain -name '*.md'") == ASK
  assert shell(f"cat {root}/brain/*/notes.md") == ASK
  assert ask("list_dir", {"DirectoryPath": str(root / "brain" / OWN)}) == (
      "allow")


def test_reading_the_summaries_db_asks(root: Path) -> None:
  db = root / "conversation_summaries.db"
  assert view(db) == ASK
  assert shell(f"sqlite3 {db} 'select * from conversation_summaries'") == ASK
  assert view(root / "brain" / OWN / "notes.md") == "allow"


# --- path shapes --------------------------------------------------------------


def test_shell_words_quotes_and_flag_values(root: Path) -> None:
  sib = root / "brain" / SIB / "notes.md"
  own = root / "brain" / OWN / "notes.md"
  assert shell(f'cat "{sib}"') == ASK
  assert shell(f"rg -n x --file={sib}") == ASK
  assert shell(f"head -5 {own} && wc -l {own}") == "allow"


def test_relative_paths_resolve_against_cwd(root: Path, tmp_path: Path) -> None:
  assert shell(f"cat brain/{SIB}/notes.md", cwd=str(root)) == ASK
  assert shell(f"cat brain/{SIB}/notes.md", cwd=str(tmp_path)) == "allow"
  assert shell(f"cat brain/{OWN}/notes.md", cwd=str(root)) == "allow"


def test_home_relative_root(root: Path, monkeypatch: pytest.MonkeyPatch,
                            tmp_path: Path) -> None:
  monkeypatch.setenv("HOME", str(tmp_path))
  monkeypatch.setenv("PAWL_CONVERSATION_ROOTS", "~/.gemini/antigravity")
  assert shell(f"cat ~/.gemini/antigravity/brain/{SIB}/notes.md") == ASK
  assert shell(f"cat ~/.gemini/antigravity/brain/{OWN}/notes.md") == "allow"


def test_symlink_into_a_store_is_followed(root: Path, tmp_path: Path) -> None:
  link = tmp_path / "shortcut"
  link.symlink_to(root / "brain" / SIB)
  assert view(link / "notes.md") == ASK
  own_link = tmp_path / "mine"
  own_link.symlink_to(root / "brain" / OWN)
  assert view(own_link / "notes.md") == "allow"


def test_nested_argument_values_are_checked(root: Path) -> None:
  args = {"Paths": [str(root / "README"), {"p": str(root / "brain" / SIB)}]}
  assert ask("grep_search", args) == ASK
  args = {"Paths": [str(root / "brain" / OWN)]}
  assert ask("grep_search", args) == "allow"


def test_brain_dirs_outside_the_store_roots_pass(
    root: Path, tmp_path: Path
) -> None:
  other = tmp_path / "work" / "brain" / SIB
  other.mkdir(parents=True)
  assert view(other / "x.md") == "allow"
  assert view(root / "brain" / SIB / "x.md") == ASK


# --- lineage lookup -----------------------------------------------------------


def test_failed_lineage_lookup_counts_as_not_lineage(root: Path) -> None:
  assert view(root / "brain" / KID / "notes.md") == "allow"
  (root / "conversation_summaries.db").write_text("not a database")
  assert view(root / "brain" / KID / "notes.md") == ASK
  (root / "conversation_summaries.db").unlink()
  assert view(root / "brain" / KID / "notes.md") == ASK


def test_lineage_lookup_opens_the_db_read_only(root: Path) -> None:
  db = root / "conversation_summaries.db"
  before = (db.read_bytes(), db.stat().st_mtime_ns)
  assert view(root / "brain" / PAR / "notes.md") == "allow"
  assert (db.read_bytes(), db.stat().st_mtime_ns) == before
  assert sorted(p.name for p in root.iterdir()) == [
      "brain", "conversation_summaries.db", "conversations"]


# --- switches and failure modes -----------------------------------------------


def test_strict_turns_the_prompt_into_a_deny(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  monkeypatch.setenv("PAWL_CONVERSATION_FENCE_STRICT", "1")
  assert view(root / "brain" / SIB / "notes.md") == "deny"
  assert view(root / "brain" / OWN / "notes.md") == "allow"


def test_off_switch(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setenv("PAWL_CONVERSATION_FENCE_OFF", "1")
  assert view(root / "brain" / SIB / "notes.md") == "allow"
  monkeypatch.delenv("PAWL_CONVERSATION_FENCE_OFF")
  assert view(root / "brain" / SIB / "notes.md") == ASK


def test_missing_conversation_fails_open(root: Path) -> None:
  assert view(root / "brain" / SIB / "notes.md", conv="") == "allow"
  assert view(root / "brain" / SIB / "notes.md") == ASK


def test_row_logs_sha_only(root: Path, tmp_path: Path) -> None:
  view(root / "brain" / SIB / "notes.md")
  rows = (tmp_path / "data" / "denials.jsonl").read_text().splitlines()
  assert len(rows) == 1
  row = json.loads(rows[0])
  assert row["gate"] == "CONVERSATION_FENCE" and row["outcome"] == ASK
  assert SIB not in rows[0] and "notes.md" not in rows[0]


def run_hook(raw: str) -> Dict[str, Any]:
  proc = subprocess.run(
      [sys.executable, "-B", str(HOOK)], input=raw, text=True,
      capture_output=True, env=dict(os.environ), timeout=20, check=False,
  )
  assert proc.returncode == 0, proc.stderr
  return json.loads(proc.stdout)


def test_hook_process(root: Path) -> None:
  raw = json.dumps({"conversationId": OWN, "toolCall": {
      "name": "view_file",
      "args": {"AbsolutePath": str(root / "brain" / SIB / "notes.md")}}})
  assert run_hook(raw)["decision"] == ASK
  for bad in ("{nope", "42", "", "[]"):
    assert run_hook(bad) == {"decision": "allow"}
