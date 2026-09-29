"""Tests for prose_gate.py. Run: python3 -m pytest -q test_prose_gate.py"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import prose_gate  # noqa: E402

CATALOG = prose_gate.load_catalog()

HUMAN = """Ran the migration on the staging copy this morning. 4,120 rows moved, 0 rejected,
took 11 seconds. The only surprise was the timezone column: 38 rows had naive timestamps,
so I stamped them UTC and logged the ids in migrate.log. Prod run is scheduled for Thursday
after the backup finishes. If you want to look at the 38 first, the list is attached.
"""

SLOP = """It's not just a migration, it's a fundamental shift in how we think about data. Let's dive in.
Here's the kicker: the real question is not whether to migrate but when. In other words, the
choice is clear. It's worth noting that this seamlessly delivers a game-changing, cutting-edge
result. To be honest, the most important thing is the paradigm. Think of it as a tapestry of
possibilities. In conclusion, ultimately, this fundamentally reshapes the future of the team.
Not a tool. Not a process. Just a mindset.
"""


def test_human_text_passes_chat_plane() -> None:
    r = prose_gate.score(HUMAN, "chat", CATALOG)
    assert not r["is_fail"], r
    assert r["score"] < r["threshold"]


def test_slop_fails_chat_plane() -> None:
    r = prose_gate.score(SLOP, "chat", CATALOG)
    assert r["is_fail"], r
    codes = {d["code"] for d in r["details"]}
    assert "reasoning_leak" in codes or "suspense_hook" in codes


def test_hard_fail_pattern_fails_on_one_hit() -> None:
    text = "Studies show that agents send too many messages. " * 3
    r = prose_gate.score(text, "deliverable", CATALOG)
    assert r["hard_fail"] == "vague_attribution"
    assert r["is_fail"]


def test_quoted_and_fenced_material_is_not_charged() -> None:
    quoted = 'The reviewer wrote "let\'s dive in and unpack this tapestry" and I disagreed.\n'
    fenced = "```\nlet's dive in and unpack this tapestry\n```\n"
    for text in (quoted, fenced):
        r = prose_gate.score(text + HUMAN, "chat", CATALOG)
        assert not r["is_fail"], r


def test_short_drafts_use_word_floor() -> None:
    r = prose_gate.score("Seamless.", "chat", CATALOG)
    assert r["words"] == 1
    hit = next(d for d in r["details"] if d["code"] == "promotional_superlative")
    assert hit["per_1000w"] == 1000 / prose_gate.WORD_FLOOR, "density uses the 250-word floor, not 1 word"
    mild = prose_gate.score("Roughly done, largely fine.", "chat", CATALOG)
    assert not mild["is_fail"], "a weak tell in a tiny draft stays under threshold"


def test_family_penalty_applies_above_free_count() -> None:
    r = prose_gate.score(SLOP, "chat", CATALOG)
    assert len(r["families_over"]) > CATALOG["planes"]["chat"]["family_free"]
    assert r["family_term"] > 0


def test_deliverable_plane_is_looser_than_chat() -> None:
    chat = prose_gate.score(SLOP, "chat", CATALOG)
    deliv = prose_gate.score(SLOP, "deliverable", CATALOG)
    assert deliv["threshold"] > chat["threshold"]


def test_catalog_entries_are_well_formed() -> None:
    codes = [p["code"] for p in CATALOG["patterns"]]
    assert len(codes) == len(set(codes)), "duplicate pattern codes"
    for p in CATALOG["patterns"]:
        assert p["plane"] in ("chat", "deliverable", "both"), p["code"]
        assert float(p["threshold_per_1000w"]) >= 0, p["code"]
        assert int(p["weight"]) >= 1, p["code"]


def _cli(*args: str, stdin: str = "") -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(HERE / "prose_gate.py"), *args], input=stdin,
                          capture_output=True, text=True, timeout=60, check=False)


def test_cli_exit_codes_and_json() -> None:
    ok = _cli("--plane", "chat", "--json", stdin=HUMAN)
    assert ok.returncode == 0
    assert json.loads(ok.stdout)["is_fail"] is False
    bad = _cli("--plane", "chat", "--why", stdin=SLOP)
    assert bad.returncode == 1
    assert bad.stdout.startswith("FAIL")
    assert "[why]" in bad.stderr


def test_cli_reads_files(tmp_path: Path) -> None:
    draft = tmp_path / "draft.md"
    draft.write_text(HUMAN)
    assert _cli("--plane", "deliverable", str(draft)).returncode == 0
