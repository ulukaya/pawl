#!/usr/bin/env python3
"""Tests for prompt_budget.py. Run: python3 -m unittest test_prompt_budget -v"""
from __future__ import annotations

import io
import json
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import prompt_budget as pb


def text_of(n_chars: int) -> str:
    """Deterministic filler of exactly n_chars characters."""
    return ("abcd efgh " * (n_chars // 10 + 1))[:n_chars]


class PromptBudgetTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="prompt_budget_"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    # helpers -----------------------------------------------------------
    def write(self, rel: str, content: str) -> Path:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return p

    def config(self, obj: dict) -> Path:
        return self.write("budget.json", json.dumps(obj))

    def run_cli(self, mode: str, cfg: Path, *extra: str) -> tuple[int, str]:
        """Drive the real argparse entry point and capture stdout."""
        buf = io.StringIO()
        argv = [mode, "--config", str(cfg), "--root", str(self.root), *extra]
        with redirect_stdout(buf):
            code = pb.main(argv)
        return code, buf.getvalue()

    # tests -------------------------------------------------------------
    def test_under_ceiling_passes(self) -> None:
        self.write("AGENT.md", text_of(400))  # 100 tokens
        cfg = self.config({"total_tokens": 1000, "files": [{"path": "AGENT.md", "max_tokens": 200}]})
        code, out = self.run_cli("check", cfg)
        self.assertEqual(code, 0, out)
        self.assertIn("OK    100/200  AGENT.md", out)
        self.assertIn("OK    100/1000  total", out)

    def test_per_file_over_fails_exit_1(self) -> None:
        self.write("AGENT.md", text_of(1000))  # 250 tokens
        cfg = self.config({"total_tokens": 5000, "files": [{"path": "AGENT.md", "max_tokens": 200}]})
        code, out = self.run_cli("check", cfg)
        self.assertEqual(code, 1, out)
        self.assertIn("OVER  250/200  AGENT.md", out)

    def test_total_over_fails(self) -> None:
        self.write("a.md", text_of(400))
        self.write("b.md", text_of(400))
        cfg = self.config({"total_tokens": 150, "files": [
            {"path": "a.md", "max_tokens": 500}, {"path": "b.md", "max_tokens": 500}]})
        code, out = self.run_cli("check", cfg)
        self.assertEqual(code, 1, out)
        self.assertIn("OVER  200/150  total", out)
        self.assertNotIn("OVER  100/500", out)

    def test_glob_expansion(self) -> None:
        self.write("rules/one.md", text_of(40))    # 10 tokens, under 50
        self.write("rules/two.md", text_of(400))   # 100 tokens, over 50
        self.write("rules/skip.txt", text_of(4000))
        cfg = self.config({"total_tokens": 1000, "files": [{"glob": "rules/*.md", "max_tokens_each": 50}]})
        code, out = self.run_cli("check", cfg)
        self.assertEqual(code, 1, out)
        self.assertIn("OK    10/50  rules/one.md", out)
        self.assertIn("OVER  100/50  rules/two.md", out)
        self.assertNotIn("skip.txt", out)

    def test_missing_file_exit_2(self) -> None:
        cfg = self.config({"total_tokens": 1000, "files": [{"path": "nope.md", "max_tokens": 50}]})
        code, out = self.run_cli("check", cfg)
        self.assertEqual(code, 2, out)
        self.assertIn("nope.md", out)

    def test_optional_missing_passes(self) -> None:
        self.write("AGENT.md", text_of(40))
        cfg = self.config({"total_tokens": 1000, "files": [
            {"path": "AGENT.md", "max_tokens": 50},
            {"path": "extra.md", "max_tokens": 50, "optional": True}]})
        code, out = self.run_cli("check", cfg)
        self.assertEqual(code, 0, out)
        self.assertNotIn("extra.md", out)

    def test_frontmatter_present_passes(self) -> None:
        self.write("memory/preferences.md", "---\ndescription: prefs\nvisibility: PINNED\n---\n" + text_of(40))
        cfg = self.config({"total_tokens": 1000, "files": [
            {"path": "memory/preferences.md", "max_tokens": 100,
             "require_frontmatter": {"visibility": "PINNED"}}]})
        code, out = self.run_cli("check", cfg)
        self.assertEqual(code, 0, out)

    def test_frontmatter_missing_fails(self) -> None:
        self.write("memory/preferences.md", "---\ndescription: prefs\n---\n" + text_of(40))
        self.write("memory/plain.md", text_of(40))
        cfg = self.config({"total_tokens": 1000, "files": [
            {"path": "memory/preferences.md", "max_tokens": 100,
             "require_frontmatter": {"visibility": "PINNED"}},
            {"path": "memory/plain.md", "max_tokens": 100,
             "require_frontmatter": {"visibility": "PINNED"}}]})
        code, out = self.run_cli("check", cfg)
        self.assertEqual(code, 1, out)
        self.assertIn("OVER", out)
        self.assertIn("frontmatter visibility: PINNED missing", out)
        self.assertEqual(out.count("OVER"), 2, out)

    def test_report_never_fails(self) -> None:
        self.write("big.md", text_of(1000))
        self.write("small.md", text_of(40))
        cfg = self.config({"total_tokens": 10, "files": [
            {"path": "small.md", "max_tokens": 1}, {"path": "big.md", "max_tokens": 1}]})
        code, out = self.run_cli("report", cfg)
        self.assertEqual(code, 0, out)
        lines = out.strip().splitlines()
        self.assertIn("big.md", lines[0])
        self.assertIn("small.md", lines[1])
        self.assertTrue(lines[-1].endswith("total"))

    def test_report_missing_file_still_exit_0(self) -> None:
        cfg = self.config({"total_tokens": 10, "files": [{"path": "gone.md", "max_tokens": 1}]})
        code, out = self.run_cli("report", cfg)
        self.assertEqual(code, 0, out)
        self.assertIn("gone.md", out)

    def test_tokens_per_char_override_changes_count(self) -> None:
        self.write("AGENT.md", text_of(400))
        cfg = self.config({"total_tokens": 1000, "files": [{"path": "AGENT.md", "max_tokens": 150}]})
        code_default, out_default = self.run_cli("check", cfg)
        code_half, out_half = self.run_cli("check", cfg, "--tokens-per-char", "0.5")
        self.assertEqual(code_default, 0, out_default)
        self.assertIn("100/150", out_default)
        self.assertEqual(code_half, 1, out_half)
        self.assertIn("200/150", out_half)

    def test_offenders_listed_first(self) -> None:
        self.write("ok_big.md", text_of(2000))   # 500 tokens, under 600
        self.write("over_small.md", text_of(100))  # 25 tokens, over 10
        cfg = self.config({"total_tokens": 10000, "files": [
            {"path": "ok_big.md", "max_tokens": 600},
            {"path": "over_small.md", "max_tokens": 10}]})
        code, out = self.run_cli("check", cfg)
        self.assertEqual(code, 1, out)
        lines = out.strip().splitlines()
        self.assertTrue(lines[0].startswith("OVER"), lines)
        self.assertIn("over_small.md", lines[0])
        self.assertIn("ok_big.md", lines[1])

    def test_estimate_rounds_up(self) -> None:
        self.assertEqual(pb.estimate_tokens("abcde", 0.25), 2)
        self.assertEqual(pb.estimate_tokens("", 0.25), 0)
        self.assertEqual(pb.estimate_tokens("abcd", 0.25), 1)


if __name__ == "__main__":
    unittest.main()
