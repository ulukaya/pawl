"""Tests for install.py: Antigravity link and registry, plugin CLIs, exits.

The claude and codex CLIs are stand-ins that log their argv, so no test
touches a real harness config.

Run: python3 -m pytest -q test_install.py
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import pathlib
import shutil
import stat
import sys
import tempfile
from typing import Dict, List, Tuple
import unittest
from unittest import mock

Path = pathlib.Path
ROOT = Path(__file__).resolve().parent
_SPEC = importlib.util.spec_from_file_location("install", ROOT / "install.py")
install = importlib.util.module_from_spec(_SPEC)
sys.modules["install"] = install
_SPEC.loader.exec_module(install)

FAKE_CLI = """#!/bin/sh
echo "$@" >> "{log}"
case "$*" in
  *uninstall*|*remove*) [ -f "{log}.gone" ] && {{ echo "Plugin not found"; exit 1; }} ;;
esac
echo "ok: $*"
"""


class InstallTest(unittest.TestCase):

  def setUp(self) -> None:
    super().setUp()
    self.tmp = Path(tempfile.mkdtemp(prefix="pawl_install_"))
    self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
    self.config = self.tmp / "home" / ".gemini" / "config"
    self.env = {"PAWL_PLUGIN_CONFIG_DIR": str(self.config),
                "PAWL_CLAUDE_BIN": "", "PAWL_CODEX_BIN": "",
                "PATH": str(self.tmp / "nobin")}
    patcher = mock.patch.dict(os.environ, self.env)
    patcher.start()
    self.addCleanup(patcher.stop)

  def fake_cli(self, name: str) -> Path:
    path = self.tmp / f"fake-{name}"
    path.write_text(FAKE_CLI.format(log=self.tmp / f"{name}.log"))
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    os.environ[f"PAWL_{name.upper()}_BIN"] = str(path)
    return path

  def calls(self, name: str) -> List[str]:
    log = self.tmp / f"{name}.log"
    return log.read_text().splitlines() if log.exists() else []

  def run_main(self, *argv: str) -> Tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
      code = install.main(list(argv))
    return code, out.getvalue(), err.getvalue()

  def registry(self) -> Dict[str, object]:
    return json.loads((self.config / "plugins.json").read_text())

  # --- antigravity --------------------------------------------------------------

  def test_antigravity_links_and_registers_idempotently(self) -> None:
    for _ in range(2):
      code, _, err = self.run_main("--antigravity", "--no-verify")
      self.assertEqual(code, 0, err)
    dest = self.config / "plugins" / "pawl"
    self.assertTrue(dest.is_symlink())
    self.assertEqual(dest.resolve(), ROOT)
    self.assertEqual(self.registry(), {"entries": [{"path": "plugins/pawl"}]})

  def test_existing_entries_are_kept(self) -> None:
    self.config.mkdir(parents=True)
    (self.config / "plugins.json").write_text(
        json.dumps({"entries": [{"path": "plugins/other"}], "v": 1}))
    self.run_main("--antigravity", "--no-verify")
    self.assertEqual(self.registry(), {
        "entries": [{"path": "plugins/other"}, {"path": "plugins/pawl"}],
        "v": 1})

  def test_conflicting_dir_is_refused_then_moved_with_force(self) -> None:
    dest = self.config / "plugins" / "pawl"
    dest.mkdir(parents=True)
    code, _, err = self.run_main("--antigravity", "--no-verify")
    self.assertEqual(code, 1)
    self.assertIn("--force", err)
    self.assertFalse(dest.is_symlink())
    code, _, _ = self.run_main("--antigravity", "--no-verify", "--force")
    self.assertEqual(code, 0)
    self.assertTrue(dest.is_symlink())
    self.assertEqual(len(list(dest.parent.glob("pawl.bak.*"))), 1)

  def test_bad_registry_exits_2_without_linking(self) -> None:
    self.config.mkdir(parents=True)
    (self.config / "plugins.json").write_text("{not json")
    code, _, err = self.run_main("--antigravity", "--no-verify")
    self.assertEqual(code, 2)
    self.assertIn("not valid JSON", err)

  def test_uninstall_removes_only_what_points_here(self) -> None:
    self.run_main("--antigravity", "--no-verify")
    data = self.registry()
    data["entries"].insert(0, {"path": "plugins/other"})
    (self.config / "plugins.json").write_text(json.dumps(data))
    for _ in range(2):
      code, _, _ = self.run_main("--antigravity", "--uninstall")
      self.assertEqual(code, 0)
    self.assertFalse((self.config / "plugins" / "pawl").exists())
    self.assertEqual(self.registry(), {"entries": [{"path": "plugins/other"}]})

  def test_dry_run_changes_nothing(self) -> None:
    code, out, _ = self.run_main("--antigravity", "--dry-run")
    self.assertEqual(code, 0)
    self.assertIn("would point", out)
    self.assertFalse(self.config.exists())

  # --- claude and codex ---------------------------------------------------------

  def test_claude_and_codex_use_their_plugin_clis(self) -> None:
    self.fake_cli("claude")
    self.fake_cli("codex")
    code, _, err = self.run_main("--claude", "--codex", "--no-verify",
                                 "--source", "ulukaya/pawl")
    self.assertEqual(code, 0, err)
    self.assertEqual(self.calls("claude"), [
        "plugin marketplace add ulukaya/pawl", "plugin install pawl@pawl"])
    self.assertEqual(self.calls("codex"), [
        "plugin marketplace add ulukaya/pawl", "plugin add pawl@pawl"])

  def test_default_source_is_this_checkout(self) -> None:
    self.fake_cli("claude")
    self.run_main("--claude", "--no-verify")
    self.assertEqual(self.calls("claude")[0], f"plugin marketplace add {ROOT}")

  def test_uninstall_tolerates_already_removed(self) -> None:
    self.fake_cli("codex")
    (self.tmp / "codex.log.gone").touch()
    code, _, err = self.run_main("--codex", "--uninstall")
    self.assertEqual(code, 0, err)
    self.assertEqual(self.calls("codex"), [
        "plugin remove pawl@pawl", "plugin marketplace remove pawl"])

  def test_missing_cli_exits_2_and_prints_the_commands(self) -> None:
    code, _, err = self.run_main("--claude", "--no-verify")
    self.assertEqual(code, 2)
    self.assertIn("claude plugin install pawl@pawl", err)

  def test_failing_cli_exits_1(self) -> None:
    cli = self.fake_cli("claude")
    cli.write_text("#!/bin/sh\necho boom >&2\nexit 3\n")
    code, _, err = self.run_main("--claude", "--no-verify")
    self.assertEqual(code, 1)
    self.assertIn("boom", err)

  # --- detection ----------------------------------------------------------------

  def test_no_flag_targets_what_is_found(self) -> None:
    self.assertEqual(install.detect(), [])
    code, _, err = self.run_main("--no-verify")
    self.assertEqual(code, 2)
    self.assertIn("no harness found", err)
    self.config.parent.mkdir(parents=True)
    self.fake_cli("codex")
    self.assertEqual(install.detect(), ["antigravity", "codex"])


if __name__ == "__main__":
  unittest.main()
