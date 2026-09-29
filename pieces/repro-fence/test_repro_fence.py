#!/usr/bin/env python3
"""Tests for repro_fence.py.

Run with:  python3 -m unittest test_repro_fence -v

Every test builds a real throwaway git repository in a temporary directory
(git init, config user, one commit) and drives the CLI as a subprocess, so R1
and R2 are proven against real git plumbing rather than mocks.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
GATE = HERE / "repro_fence.py"

BASE_MODULE = """\
def public_api(a, b):
    return a + b


def _helper(x):
    return x


class Service:
    def start(self, port=8080):
        return port

    def _reset(self):
        return None
"""

TEST_MODULE = """\
def test_adds():
    assert 1 + 1 == 2
"""


def clean_env() -> dict[str, str]:
  """Drop GIT_* overrides so a test never touches the caller's repository."""
  return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def git(repo: Path, *args: str) -> None:
  subprocess.run(
      ["git", *args],
      cwd=str(repo),
      capture_output=True,
      text=True,
      timeout=60,
      check=True,
      env=clean_env(),
  )


def gate(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
  return subprocess.run(
      [sys.executable, str(GATE), *args, "--repo", str(repo)],
      capture_output=True,
      text=True,
      timeout=120,
      check=False,
      env=clean_env(),
  )


def write_script(repo: Path, name: str, body: str) -> None:
  path = repo / name
  path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
  path.chmod(0o755)


class RepoCase(unittest.TestCase):
  """One fresh git repository per test."""

  def setUp(self) -> None:
    self._tmp = tempfile.TemporaryDirectory()
    self.repo = Path(self._tmp.name) / "repo"
    (self.repo / "src").mkdir(parents=True)
    (self.repo / "tests").mkdir()
    self.mod = self.repo / "src" / "mod.py"
    self.mod.write_text(BASE_MODULE, encoding="utf-8")
    (self.repo / "tests" / "test_mod.py").write_text(
        TEST_MODULE, encoding="utf-8"
    )
    (self.repo / "notes.txt").write_text("plain text\n", encoding="utf-8")
    write_script(self.repo, "failing.sh", "exit 3\n")
    write_script(self.repo, "passing.sh", "exit 0\n")
    write_script(self.repo, "usage.sh", "exit 2\n")
    write_script(self.repo, "collect.sh", "exit 5\n")
    write_script(self.repo, "slow.sh", "sleep 20\n")
    git(self.repo, "init", "-q", "-b", "main")
    git(self.repo, "config", "user.email", "fence@example.invalid")
    git(self.repo, "config", "user.name", "fence")
    git(self.repo, "add", "-A")
    git(self.repo, "commit", "-q", "-m", "base")

  def tearDown(self) -> None:
    self._tmp.cleanup()

  def write_mod(self, body: str) -> None:
    self.mod.write_text(body, encoding="utf-8")


# ---------------------------------------------------------------- R1 red


class R1ReproducerTests(RepoCase):

  def test_failing_reproducer_accepted(self) -> None:
    res = gate(self.repo, "red", "--cmd", "sh failing.sh")
    self.assertEqual(res.returncode, 0, res.stderr)
    self.assertIn("R1 reproducer gate PASSED", res.stdout)

  def test_passing_reproducer_rejected(self) -> None:
    res = gate(self.repo, "red", "--cmd", "sh passing.sh")
    self.assertEqual(res.returncode, 1)
    self.assertIn("exited 0", res.stderr)
    self.assertIn("proves nothing", res.stderr)

  def test_exit_2_usage_rejected(self) -> None:
    res = gate(self.repo, "red", "--cmd", "sh usage.sh")
    self.assertEqual(res.returncode, 1)
    self.assertIn("exited 2", res.stderr)

  def test_exit_5_collection_error_rejected(self) -> None:
    res = gate(self.repo, "red", "--cmd", "sh collect.sh")
    self.assertEqual(res.returncode, 1)
    self.assertIn("exited 5", res.stderr)

  def test_exit_127_missing_binary_rejected(self) -> None:
    res = gate(self.repo, "red", "--cmd", "definitely-not-a-real-binary-xyz")
    self.assertEqual(res.returncode, 1)
    self.assertIn("not found or not executable", res.stderr)

  def test_timeout_rejected(self) -> None:
    res = gate(self.repo, "red", "--cmd", "sh slow.sh", "--timeout", "1")
    self.assertEqual(res.returncode, 1)
    self.assertIn("timed out after 1s", res.stderr)

  def test_or_true_shape_rejected_without_running(self) -> None:
    res = gate(self.repo, "red", "--cmd", "sh failing.sh || true")
    self.assertEqual(res.returncode, 1)
    self.assertIn("[R1-shape]", res.stderr)
    self.assertIn("||", res.stderr)
    self.assertNotIn("[R1-repro]", res.stderr)

  def test_sh_c_exit_shape_rejected(self) -> None:
    res = gate(self.repo, "red", "--cmd", "sh -c 'sh passing.sh; exit 1'")
    self.assertEqual(res.returncode, 1)
    self.assertIn("[R1-shape]", res.stderr)
    self.assertIn("exit", res.stderr)

  def test_sh_c_without_shape_tokens_still_runs(self) -> None:
    res = gate(self.repo, "red", "--cmd", "sh -c 'sh failing.sh'")
    self.assertEqual(res.returncode, 0, res.stderr)
    self.assertIn("R1 reproducer gate PASSED", res.stdout)


# ---------------------------------------------------------------- R2 fence


class R2FenceTests(RepoCase):

  def test_unchanged_file_passes(self) -> None:
    res = gate(self.repo, "fence", "--file", "src/mod.py")
    self.assertEqual(res.returncode, 0, res.stderr)
    self.assertIn("R2 signature fence PASSED", res.stdout)

  def test_removed_public_function_rejects(self) -> None:
    self.write_mod(
        BASE_MODULE.replace("def public_api(a, b):\n    return a + b\n", "")
    )
    res = gate(self.repo, "fence", "--file", "src/mod.py")
    self.assertEqual(res.returncode, 1)
    self.assertIn("public symbol 'public_api' was removed", res.stderr)

  def test_added_default_kwarg_rejects(self) -> None:
    # Gotcha 1: a new keyword default is still a signature change.
    self.write_mod(
        BASE_MODULE.replace(
            "def public_api(a, b):", "def public_api(a, b, c=None):"
        )
    )
    res = gate(self.repo, "fence", "--file", "src/mod.py")
    self.assertEqual(res.returncode, 1)
    self.assertIn(
        "'public_api' signature changed (a,b)d0 -> (a,b,c)d1", res.stderr
    )

  def test_renamed_public_method_rejects(self) -> None:
    self.write_mod(
        BASE_MODULE.replace(
            "def start(self, port=8080):", "def boot(self, port=8080):"
        )
    )
    res = gate(self.repo, "fence", "--file", "src/mod.py")
    self.assertEqual(res.returncode, 1)
    self.assertIn("public symbol 'Service.start' was removed", res.stderr)

  def test_private_symbol_change_passes(self) -> None:
    body = BASE_MODULE.replace("def _helper(x):", "def _helper(x, scale=2):")
    body = body.replace("def _reset(self):", "def _wipe(self):")
    self.write_mod(body)
    res = gate(self.repo, "fence", "--file", "src/mod.py")
    self.assertEqual(res.returncode, 0, res.stderr)

  def test_new_public_symbol_passes(self) -> None:
    self.write_mod(BASE_MODULE + "\n\ndef brand_new(z):\n    return z\n")
    res = gate(self.repo, "fence", "--file", "src/mod.py")
    self.assertEqual(res.returncode, 0, res.stderr)

  def test_non_py_file_skipped(self) -> None:
    (self.repo / "notes.txt").write_text("changed\n", encoding="utf-8")
    res = gate(self.repo, "fence", "--file", "notes.txt")
    self.assertEqual(res.returncode, 0)
    self.assertIn("[R2-skip] notes.txt", res.stderr)

  def test_class_removed_rejects(self) -> None:
    self.write_mod("def public_api(a, b):\n    return a + b\n")
    res = gate(self.repo, "fence", "--file", "src/mod.py")
    self.assertEqual(res.returncode, 1)
    self.assertIn("public symbol 'Service' was removed", res.stderr)
    self.assertIn("public symbol 'Service.start' was removed", res.stderr)

  def test_renamed_test_function_rejects(self) -> None:
    # Gotcha 2: def test_* names are public symbols too.
    (self.repo / "tests" / "test_mod.py").write_text(
        TEST_MODULE.replace("def test_adds", "def test_sums"), encoding="utf-8"
    )
    res = gate(self.repo, "fence", "--file", "tests/test_mod.py")
    self.assertEqual(res.returncode, 1)
    self.assertIn("public symbol 'test_adds' was removed", res.stderr)

  def test_explicit_rev_is_honored(self) -> None:
    self.write_mod(
        BASE_MODULE.replace("def public_api(a, b):", "def public_api(a, b, c):")
    )
    git(self.repo, "commit", "-qam", "widen signature")
    first = gate(self.repo, "fence", "--file", "src/mod.py")
    self.assertEqual(first.returncode, 0)
    res = gate(self.repo, "fence", "--file", "src/mod.py", "--rev", "HEAD~1")
    self.assertEqual(res.returncode, 1)
    self.assertIn("signature changed", res.stderr)
    # No --file at all is a usage error, not a pass.
    self.assertEqual(gate(self.repo, "fence").returncode, 2)


# ---------------------------------------------------------------- signature shape


def _sig(src: str) -> str:
  """signature_of straight from the AST: no repo, no subprocess."""
  sys.path.insert(0, str(HERE))
  try:
    import repro_fence  # noqa: PLC0415  (stdlib piece, imported in place)
  finally:
    sys.path.pop(0)
  return repro_fence.signature_of(ast.parse(src).body[0])


class SignatureTests(unittest.TestCase):
  """signature_of straight from the AST: no repo, no subprocess."""

  def test_plain_positional_shape_is_unchanged(self) -> None:
    self.assertEqual(_sig("def f(a, b): pass"), "(a,b)d0")
    self.assertEqual(_sig("def f(a, b, c=1): pass"), "(a,b,c)d1")

  def test_keyword_only_boundary_is_a_different_shape(self) -> None:
    self.assertNotEqual(_sig("def f(a, b): pass"), _sig("def f(a, *, b): pass"))
    self.assertEqual(_sig("def f(a, *, b): pass"), "(a,*,b)d0")

  def test_positional_only_boundary_is_a_different_shape(self) -> None:
    self.assertNotEqual(_sig("def f(a, b): pass"), _sig("def f(a, /, b): pass"))
    self.assertEqual(_sig("def f(a, /, b): pass"), "(a,/,b)d0")

  def test_star_args_already_marks_the_keyword_only_boundary(self) -> None:
    self.assertEqual(_sig("def f(a, *args, b): pass"), "(a,*args,b)d0")

  def test_both_markers_with_defaults_and_kwargs(self) -> None:
    self.assertEqual(_sig("def f(a, /, b, *, c=1): pass"), "(a,/,b,*,c)d1")
    self.assertEqual(
        _sig("async def f(a, /, b, *, c=1, **kw): pass"), "(a,/,b,*,c,**kw)d1"
    )


# ---------------------------------------------------------------- both


class BothTests(RepoCase):

  def test_both_runs_r1_then_r2(self) -> None:
    clean = gate(
        self.repo, "both", "--cmd", "sh failing.sh", "--file", "src/mod.py"
    )
    self.assertEqual(clean.returncode, 0, clean.stderr)
    self.assertIn("R1 reproducer + R2 signature fence PASSED", clean.stdout)

    self.write_mod("def public_api(a):\n    return a\n")
    res = gate(
        self.repo, "both", "--cmd", "sh passing.sh", "--file", "src/mod.py"
    )
    self.assertEqual(res.returncode, 1)
    self.assertIn("[R1-repro]", res.stderr)
    self.assertIn("[R2-fence]", res.stderr)

  def test_gate_never_writes_to_the_repo(self) -> None:
    self.write_mod("def public_api(a):\n    return a\n")

    def status() -> str:
      return subprocess.run(
          ["git", "status", "--porcelain"],
          cwd=str(self.repo),
          capture_output=True,
          text=True,
          check=True,
          env=clean_env(),
      ).stdout

    before = status()
    gate(self.repo, "both", "--cmd", "sh failing.sh", "--file", "src/mod.py")
    self.assertEqual(before, status())
    self.assertIn("def public_api(a):", self.mod.read_text(encoding="utf-8"))


if __name__ == "__main__":
  unittest.main()
