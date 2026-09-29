"""Tests for send_budget.py and send_budget_hook.py against a real temp state dir, no mocks.

Run:  python3 -m pytest -q test_send_budget.py
"""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

HERE = Path(__file__).resolve().parent
HOOK = HERE / "send_budget_hook.py"
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

OWNER = "spaces/OWNER_DM"


@pytest.fixture()
def budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("SEND_BUDGET_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("SEND_BUDGET_OWNER_SPACE", OWNER)
    monkeypatch.delenv("SEND_BUDGET_OVERRIDE", raising=False)
    monkeypatch.delenv("SEND_BUDGET_CEILINGS", raising=False)
    monkeypatch.delenv("SEND_BUDGET_TZ", raising=False)
    import send_budget
    return importlib.reload(send_budget)


def _noon() -> datetime:
    return datetime(2026, 9, 12, 12, 0)


def _midnight() -> datetime:
    return datetime(2026, 9, 12, 23, 30)


def test_classify_maps_channels(budget: Any) -> None:
    assert budget.classify_command(f"gchat mutate send-message --space {OWNER} --text hi") == "dm_owner"
    assert budget.classify_command("gchat mutate send-message --space spaces/TEAM --text hi") == "chat_space"
    assert budget.classify_command("gmail mutate send --to a@b.com --subject x") == "email"
    assert budget.classify_command("gmail mutate reply --thread t1 --body ok") == "email"
    assert budget.classify_command("bsky post --text hello") == "social"


def test_classify_ignores_reads_and_itself(budget: Any) -> None:
    assert budget.classify_command(f"gchat query list-messages --space {OWNER}") is None
    assert budget.classify_command("ls -la") is None
    assert budget.classify_command("") is None
    assert budget.classify_command("python3 send_budget.py classify --command 'gmail send --to a@b.com'") is None


def test_classify_self_check_looks_at_the_executable_not_the_text(budget: Any) -> None:
    assert budget.classify_command(
        'gchat mutate send-message --space spaces/TEAM --text "status of send_budget"') == "chat_space"
    assert budget.classify_command("gmail mutate send --to a@b.com --body 'see send_budget.py'") == "email"
    assert budget.classify_command("python3 /x/y/send_budget.py status") is None
    assert budget.classify_command("SEND_BUDGET_TZ=UTC python3 send_budget.py check chat_space") is None
    assert budget.classify_command("env SEND_BUDGET_TZ=UTC python3.12 -B ./send_budget.py spend --channel email") is None
    assert budget.classify_command("./send_budget spend --channel email") is None
    assert budget.classify_command("env -i A=1 env B=2 python3 -B -X dev send_budget.py spend --channel email") is None
    assert budget.classify_command("A=1 env B=2 gchat mutate send-message --space spaces/X --text hi") == "chat_space"


def test_classify_keys_on_the_executable_and_its_verb(budget: Any) -> None:
    for cmd in (
        "cat <<'EOF' > /tmp/briefing.txt\nnext: gchat mutate send-message --space spaces/A --text hi\nEOF\n",
        "egrep -n 'gchat.*send' /tmp/notes.txt",
        "diff /tmp/a/SKILL.md ~/.config/skills/pawl-send-gates/SKILL.md",
        "gchat mutate send-message --help",
        "echo 'Blog post published, tell the chat'",
        "gchat query list-messages --space spaces/A | grep post",
    ):
        assert budget.classify_command(cmd) is None, cmd
    for cmd in (
        "/opt/releases/chat-tools/gchat mutate send-message --space spaces/A --text hi 2>&1 | tail -5",
        "cd /tmp && ./gchat send --space spaces/A --text hi",
        "cat /tmp/m.txt | gchat mutate send-message --space spaces/A --stdin",
        "gchat mutate send-direct-message --user a@b.com --text hi",
    ):
        assert budget.classify_command(cmd) == "chat_space", cmd
    assert budget.classify_command("gmail mutate reply-all --thread t1 --body ok") == "email"
    assert budget.classify_command("bsky publish --text hello") == "social"


def test_describe_keeps_tool_path_and_drops_every_value(budget: Any) -> None:
    assert budget.describe('gchat mutate send-message --space spaces/TEAM --text "hello alice@example.com"') == \
        "gchat mutate send-message"
    assert budget.describe("gmail mutate send --to a@b.com --body 'see send_budget.py'") == "gmail mutate send"
    assert budget.describe("/opt/bin/bsky post --text hi") == "bsky post"
    assert budget.describe("SEND_BUDGET_TZ=UTC python3 /x/y/send_budget.py status") == "send_budget.py status"
    assert budget.describe("gchat send alice@example.com hi") == "gchat send"
    assert budget.describe("gchat spaces/TEAM send") == "gchat"
    assert budget.describe("mail 'a b' send") == "mail"
    assert budget.describe("") == ""
    assert budget.describe("gchat 'unterminated send") == "gchat"
    long_sub = "x" * 80
    assert len(budget.describe(f"gchat {long_sub} send")) <= budget.DESCRIBE_MAX


def test_hook_state_file_carries_no_message_text(tmp_path: Path, budget: Any) -> None:
    cmd = 'gchat mutate send-message --space spaces/TEAM --text "hello alice@example.com"'
    assert _hook(cmd, tmp_path)["decision"] == "allow"
    raw = (tmp_path / "send_budget.json").read_text()
    for secret in ("alice@example.com", "hello", "spaces/TEAM"):
        assert secret not in raw
    assert json.loads(raw)["last_spend"]["chat_space"]["reason"] == "gchat mutate send-message"

def test_spend_burns_then_denies_at_ceiling(budget: Any) -> None:
    ceiling = budget.ceilings()["email"]
    for i in range(ceiling):
        allowed, reason = budget.spend("email", reason=f"msg {i}", now=_noon())
        assert allowed, reason
    allowed, reason = budget.spend("email", now=_noon())
    assert not allowed
    assert "ceiling" in reason
    snap = budget.status(_noon())["channels"]["email"]
    assert snap["remaining"] == 0
    assert snap["denials"] == 1


def test_check_does_not_burn(budget: Any) -> None:
    budget.spend("email", now=_noon())
    before = budget.status(_noon())["channels"]["email"]["used"]
    budget.check("email", now=_noon())
    budget.check("email", now=_noon())
    assert budget.status(_noon())["channels"]["email"]["used"] == before


def test_dry_run_does_not_burn(budget: Any) -> None:
    allowed, _ = budget.spend("social", now=_noon(), dry_run=True)
    assert allowed
    assert budget.status(_noon())["channels"]["social"]["used"] == 0


def test_quiet_window_blocks_owner_dm_then_clears(budget: Any) -> None:
    allowed, reason = budget.spend("dm_owner", now=_midnight())
    assert not allowed
    assert "quiet window" in reason
    allowed, _ = budget.spend("dm_owner", now=_noon())
    assert allowed


def test_quiet_window_does_not_block_email(budget: Any) -> None:
    allowed, _ = budget.spend("email", now=_midnight())
    assert allowed


def test_day_rollover_resets_counts_and_keeps_history(budget: Any) -> None:
    for _ in range(budget.ceilings()["social"]):
        budget.spend("social", now=_noon())
    assert not budget.spend("social", now=_noon())[0]
    next_day = datetime(2026, 9, 13, 12, 0)
    allowed, _ = budget.spend("social", now=next_day)
    assert allowed
    snap = budget.status(next_day)
    assert snap["channels"]["social"]["used"] == 1
    yesterday = snap["history"]["2026-09-12"]
    assert yesterday["counts"]["social"] == budget.ceilings()["social"]
    assert yesterday["denials"]["social"] == 1


def test_reset_keeps_history(budget: Any) -> None:
    budget.spend("email", now=_noon())
    next_day = datetime(2026, 9, 13, 12, 0)
    budget.spend("email", now=next_day)
    budget.reset(None, now=next_day)
    snap = budget.status(next_day)
    assert snap["channels"]["email"]["used"] == 0
    assert snap["history"]["2026-09-12"]["counts"]["email"] == 1


def test_override_env_forces_allow_and_is_recorded(budget: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    for _ in range(budget.ceilings()["social"]):
        budget.spend("social", now=_noon())
    assert not budget.spend("social", now=_noon())[0]
    monkeypatch.setenv("SEND_BUDGET_OVERRIDE", "1")
    allowed, reason = budget.spend("social", now=_noon())
    assert allowed
    assert "override" in reason
    assert budget.read_state(_noon())["last_spend"]["social"]["overridden"] is True


def test_ceilings_env_override(budget: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEND_BUDGET_CEILINGS", json.dumps({"email": 1}))
    assert budget.spend("email", now=_noon())[0]
    assert not budget.spend("email", now=_noon())[0]


def test_unbudgeted_channel_passes_through(budget: Any) -> None:
    allowed, reason = budget.spend("carrier_pigeon", now=_noon())
    assert allowed
    assert "not budgeted" in reason


def test_concurrent_spends_do_not_lose_counts(budget: Any, tmp_path: Path) -> None:
    """Twelve parallel processes against one counter land exactly twelve outcomes."""
    code = (f"import sys; sys.path.insert(0, {str(HERE)!r}); "
            "import send_budget as s; s.spend('email', reason='race')")
    env = dict(os.environ, SEND_BUDGET_STATE_DIR=str(tmp_path))
    env.pop("SEND_BUDGET_OVERRIDE", None)
    procs = [subprocess.Popen([sys.executable, "-c", code], env=env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) for _ in range(12)]
    for proc in procs:
        proc.wait(timeout=60)
    state = json.loads((tmp_path / "send_budget.json").read_text())
    used = state["counts"].get("email", 0)
    denied = state["denials"].get("email", 0)
    assert used + denied == 12
    assert used == budget.ceilings()["email"]


def test_cli_exit_codes(budget: Any, tmp_path: Path) -> None:
    env = dict(os.environ, SEND_BUDGET_STATE_DIR=str(tmp_path),
               SEND_BUDGET_CEILINGS=json.dumps({"social": 1}))
    env.pop("SEND_BUDGET_OVERRIDE", None)
    run = lambda *a: subprocess.run([sys.executable, str(HERE / "send_budget.py"), *a],  # noqa: E731
                                    env=env, capture_output=True, text=True, check=False)
    assert run("spend", "--channel", "social").returncode == 0
    assert run("spend", "--channel", "social").returncode == 3
    assert run("check", "--channel", "social").returncode == 3
    assert run("status", "--compact").returncode == 0


def _hook(command: str, state_dir: Path, **extra: str) -> dict:
    env = dict(os.environ, SEND_BUDGET_STATE_DIR=str(state_dir), **extra)
    env.pop("SEND_BUDGET_OVERRIDE", None)
    payload = json.dumps({"toolCall": {"name": "run_command", "args": {"CommandLine": command}}})
    proc = subprocess.run([sys.executable, str(HOOK)], input=payload, env=env,
                          capture_output=True, text=True, timeout=60, check=False)
    return json.loads(proc.stdout)


def test_hook_allows_non_send_command(tmp_path: Path) -> None:
    assert _hook("ls -la", tmp_path)["decision"] == "allow"


def test_hook_denies_once_ceiling_is_burned(tmp_path: Path, budget: Any) -> None:
    cmd = 'gchat mutate send-message --space spaces/TEAM --text "ping"'
    ceiling = budget.ceilings()["chat_space"]
    decisions = [_hook(cmd, tmp_path)["decision"] for _ in range(ceiling + 1)]
    assert decisions[:-1] == ["allow"] * ceiling
    assert decisions[-1] == "deny"
    assert "SEND BUDGET" in _hook(cmd, tmp_path)["reason"]


def test_hook_fails_open_on_garbage_payload(tmp_path: Path) -> None:
    proc = subprocess.run([sys.executable, str(HOOK)], input="not json",
                          capture_output=True, text=True, timeout=60, check=False)
    assert json.loads(proc.stdout)["decision"] == "allow"
