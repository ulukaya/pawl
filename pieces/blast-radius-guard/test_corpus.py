#!/usr/bin/env python3
"""Ratchet: every labelled case in the five corpora keeps its answer.

The dev corpus and the four held-out sets (see HILLCLIMB.md) are scored
with score.py's sandbox. Only the cases in KNOWN_MISSES may differ, and
none of them may be a false alarm. A change that loses a case fails here.

Run: python3 -m pytest -q test_corpus.py
"""

from __future__ import annotations

from pathlib import Path  # pylint: disable=g-importing-member
import sys

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import score  # noqa: E402  pylint: disable=g-import-not-at-top

CORPORA = ("corpus.json", "holdout.json", "holdout2.json", "holdout3.json",
           "holdout4.json")
# A deletion computed at run time from os.walk(home): no target is written.
KNOWN_MISSES = frozenset({"h4-py-walk-home"})


@pytest.mark.parametrize("name", CORPORA)
def test_corpus_keeps_every_answer(name: str) -> None:
  results = [score.run_case(c) for c in score.load(HERE / name)]
  wrong = [f"{r.id}: expected {r.expect}, got {r.got}" for r in results
           if r.got != r.expect and r.id not in KNOWN_MISSES]
  assert wrong == []


def test_known_misses_are_still_misses_and_never_false_alarms() -> None:
  cases = [c for name in CORPORA for c in score.load(HERE / name)
           if c["id"] in KNOWN_MISSES]
  assert {c["id"] for c in cases} == KNOWN_MISSES
  for case in cases:
    assert case["expect"] != "allow"
    # if this starts passing, move it out of KNOWN_MISSES
    assert score.run_case(case).got != case["expect"], case["id"]


def test_corpora_have_unique_ids_and_valid_labels() -> None:
  ids = []
  for name in CORPORA:
    for case in score.load(HERE / name):
      ids.append(case["id"])
      assert case["expect"] in ("allow", "ask", "deny"), case["id"]
      assert isinstance(case["cmd"], str) and case["cmd"], case["id"]
  assert len(ids) == len(set(ids))
  assert len(ids) == 329
