# CLAUDE.md — Pawl Engineering Discipline & Architectural Invariants

## 1. Project Philosophy
Pawl is the open-source realization of the principles articulated on
https://ulukaya.dev:
- "The Crutch vs. the Operating System" (Deterministic hooks over prompts).
- "Code Over Context" (Keep skills under 150 tokens; use executable hooks).
- "The Repro Fence" (No bug fix ships without a proven failing test).

## 2. Strict Software Engineering Guardrails
1. **File Length Limit:** No single file may exceed **500 lines**.
   If logic grows beyond 400 lines, extract helper modules or split
   responsibilities cleanly.
2. **Cyclomatic Complexity:** Flat control flow. Maximum nesting depth <= 3.
   Use guard clauses and early returns.
3. **Error Contracts (Defect D1):**
   - Validate input shapes at module boundaries.
   - Return friendly, actionable error messages. Never allow raw tracebacks
     (`KeyError`, `IndexError`, `AttributeError`).
   - Exit codes: `0` = PASS/auto_approve, `1` = DENY/violation,
     `2` = Missing config/file (e.g., `denials.jsonl missing`).
4. **Markdown Formatting:** All prose must wrap at **80 columns**
   (enforced by `check_portable.py`). Fenced code, URLs, and table rows are
   exempt.
5. **Stdlib-Only Kernel:** Pure Python 3 standard library only. No third-party
   dependencies (`pip`). `check_portable.py` must stay clean.
6. **The Test Bite Standard:** Every new test must be proven to fail against
   unpatched code before passing. Graders must have known-bad fixture twins.
7. **Battery-at-Head Hygiene:** Run `.venv/bin/python3 -B run_tests.py` on
   every phase. Embed suite and test pass counts directly in commit messages.

## 3. Working Reference
- Hook layer: `hooks/pawl.py` (dispatcher), `hooks/harness.py` (Antigravity,
  Claude Code and Codex payloads and answers), `hooks/gates.py` (registry).
  Pieces only ever see the canonical Antigravity call.
- Wiring and guardrail checks: `check_contract.py`, run by
  `check_portable.py`.
- Phased implementation specs: `RECONSTRUCTION_SPEC.md`
- Source changelists & review transcripts: `docs/cls/*.txt`
- Core conceptual canon: Essays at `ulukaya.dev/src/pages/posts/`
