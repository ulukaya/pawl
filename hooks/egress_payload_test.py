#!/usr/bin/env python3
"""Tests for the outbound payload the egress gate scans in pawl_hook.py.

Run: python3 -m unittest egress_payload_test -v (from hooks/).
"""

from __future__ import annotations

from pathlib import Path  # pylint: disable=g-importing-member
import shutil
import tempfile
import unittest

import hooks_test


class EgressPayloadTest(unittest.TestCase):

  def setUp(self) -> None:
    super().setUp()
    self.tmp = tempfile.mkdtemp(prefix="pawl_test_")
    self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
    self.env = {
        "PAWL_DATA": self.tmp,
        "SEND_BUDGET_STATE_DIR": self.tmp,
        "SEND_BUDGET_OVERRIDE": "",
    }

  def run_hook(self, command: str) -> dict:
    return hooks_test.run_hook(command, self.env)

  def test_egress_matches_quoted_paths_to_the_heredoc(self) -> None:
    """`> "F"` pairs with `$(cat 'F')`; the draft is never on disk."""
    path = f"{self.tmp}/draft.txt"
    shapes = (
        (f'"{path}"', f"$(cat '{path}')"),
        (f"'{path}'", f'$(cat \\"{path}\\")'),
        (f'"{path}"', f"$(< '{path}')"),
    )
    for target, read in shapes:
      template = (
          f"cat <<'EOF' > {target}\n{{body}}\nEOF\n"
          f'gchat send --space spaces/A --text "{read}"'
      )
      out = self.run_hook(template.format(body="all clean"))
      self.assertEqual(out["decision"], "allow", (target, read, out))
      out = self.run_hook(template.format(body="see /home/someone/x/"))
      self.assertEqual(out["decision"], "deny", (target, read))
      self.assertIn("home-dir", out["reason"])

  def test_egress_fails_closed_on_quoted_unreadable_source(self) -> None:
    """A quoted path to a missing file is still a fail-closed deny."""
    missing = f"{self.tmp}/missing.txt"
    for operand in (f"'{missing}'", f'\\"{missing}\\"'):
      out = self.run_hook(
          f'gchat send --space spaces/A --text "$(cat {operand})"'
      )
      self.assertEqual(out["decision"], "deny", operand)
      self.assertIn("failing closed", out["reason"])
    src = Path(self.tmp) / "msg.txt"
    src.write_text("all clean here\n")
    out = self.run_hook(
        f"gchat send --space spaces/A --text \"$(cat '{src}')\""
    )
    self.assertEqual(out["decision"], "allow", out)

  def test_egress_treats_shell_syntax_inside_a_file_as_text(self) -> None:
    """Backticks or `$(` in a message file are text; bash never re-evals."""
    src = Path(self.tmp) / "msg.txt"
    src.write_text("run `make test`, then $(echo hi) shows the version\n")
    for read in (f"$(cat {src})", f"$(< {src})"):
      out = self.run_hook(f'gchat send --space spaces/A --text "{read}"')
      self.assertEqual(out["decision"], "allow", (read, out))
    out = self.run_hook(
        f'gchat send --space spaces/A --text "$(cat {src}) at $(date)"'
    )
    self.assertEqual(out["decision"], "deny")
    self.assertIn("substitution", out["reason"])


if __name__ == "__main__":
  unittest.main()
