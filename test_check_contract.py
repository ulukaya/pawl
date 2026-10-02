"""Tests for check_contract.py: hook configs, manifests, length, nesting.

Each failing fixture has a passing twin, and the live tree must be clean.

Run: python3 -m pytest -q test_check_contract.py
"""

from __future__ import annotations

import copy
import importlib.util
import json
import pathlib
import shutil
import sys
import tempfile
import textwrap
from typing import Any, Dict, List
import unittest

Path = pathlib.Path
ROOT = Path(__file__).resolve().parent
_SPEC = importlib.util.spec_from_file_location(
    "check_contract", ROOT / "check_contract.py"
)
cc = importlib.util.module_from_spec(_SPEC)
sys.modules["check_contract"] = cc
_SPEC.loader.exec_module(cc)

CONFIG_FILES = ("hooks.json", "hooks/hooks.json", "hooks/codex.json")
MANIFESTS = (".claude-plugin/plugin.json", ".codex-plugin/plugin.json",
             ".claude-plugin/marketplace.json")


class TreeCase(unittest.TestCase):
  """A scratch copy of the shipped configs and manifests."""

  def setUp(self) -> None:
    super().setUp()
    self.root = Path(tempfile.mkdtemp(prefix="pawl_contract_"))
    self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
    for rel in CONFIG_FILES + MANIFESTS + ("assets/logo.svg",
                                           "hooks/pawl.py"):
      dest = self.root / rel
      dest.parent.mkdir(parents=True, exist_ok=True)
      shutil.copy(ROOT / rel, dest)
    (self.root / "skills").mkdir()

  def read(self, rel: str) -> Dict[str, Any]:
    return json.loads((self.root / rel).read_text())

  def write(self, rel: str, data: Any) -> None:
    path = self.root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data))

  def errors(self, check) -> List[str]:
    out: List[str] = []
    check(out, root=self.root)
    return out


class HookConfigTest(TreeCase):

  def claude_command(self, command: str, event: str = "PreToolUse") -> None:
    cfg = self.read("hooks/hooks.json")
    cfg["hooks"][event][0]["hooks"][0]["command"] = command
    self.write("hooks/hooks.json", cfg)

  def test_shipped_copy_is_clean(self) -> None:
    self.assertEqual(self.errors(cc.check_hook_configs), [])

  def test_relative_script_path_fails_for_claude(self) -> None:
    # The pre-0.4 command: Claude Code runs hooks from the project dir, so
    # Python exits 2 ("can't open file") and every tool call is blocked.
    self.claude_command("python3 -B hooks/pawl_hook.py")
    errs = self.errors(cc.check_hook_configs)
    self.assertEqual(len(errs), 1)
    self.assertIn("hooks/hooks.json[PreToolUse]", errs[0])

  def test_unquoted_plugin_root_fails(self) -> None:
    self.claude_command(
        "python3 -B ${CLAUDE_PLUGIN_ROOT}/hooks/pawl.py pre --harness claude")
    self.assertIn("quote ${CLAUDE_PLUGIN_ROOT}",
                  self.errors(cc.check_hook_configs)[0])

  def test_wrong_harness_event_or_only_fails(self) -> None:
    root = '"${CLAUDE_PLUGIN_ROOT}/hooks/pawl.py"'
    for cmd, event, needle in (
        (f"python3 -B {root} pre --harness codex", "PreToolUse",
         "--harness must be claude"),
        (f"python3 -B {root} pre --harness claude", "Stop", "expected"),
        (f"python3 -B {root} pre --harness claude --only git", "PreToolUse",
         "drop --only"),
    ):
      self.setUp()
      self.claude_command(cmd, event)
      self.assertIn(needle, " ".join(self.errors(cc.check_hook_configs)), cmd)

  def test_antigravity_groups_must_cover_every_gate(self) -> None:
    cfg = self.read("hooks.json")
    del cfg["pawl-poll-loop-guard"]
    del cfg["pawl-reread-guard"]["Stop"]
    self.write("hooks.json", cfg)
    self.assertEqual(self.errors(cc.check_hook_configs), [
        "hooks.json: no Antigravity group runs gate 'poll' on pre",
        "hooks.json: no Antigravity group runs gate 'reread' on stop",
    ])

  def test_antigravity_gate_on_the_wrong_event_or_unknown_fails(self) -> None:
    cfg = self.read("hooks.json")
    original = copy.deepcopy(cfg)
    stop = cfg["pawl-idle-task-gate"]["Stop"][0]
    stop["command"] = stop["command"].replace("--only idle", "--only git")
    self.write("hooks.json", cfg)
    self.assertIn("gate 'git' does not run on Stop",
                  " ".join(self.errors(cc.check_hook_configs)))
    hook = original["pawl-send-gates"]["PreToolUse"][0]["hooks"][0]
    hook["command"] = hook["command"].replace("--only send", "--only nope")
    self.write("hooks.json", original)
    self.assertIn("gate 'nope'", " ".join(self.errors(cc.check_hook_configs)))

  def test_codex_needs_one_pre_and_one_stop(self) -> None:
    cfg = self.read("hooks/codex.json")
    del cfg["hooks"]["Stop"]
    self.write("hooks/codex.json", cfg)
    self.assertIn("needs one PreToolUse and one Stop",
                  " ".join(self.errors(cc.check_hook_configs)))


class ManifestTest(TreeCase):

  def test_shipped_copy_is_clean(self) -> None:
    self.assertEqual(self.errors(cc.check_manifests), [])

  def test_version_drift_fails(self) -> None:
    codex = self.read(".codex-plugin/plugin.json")
    codex["version"] = "9.9.9"
    self.write(".codex-plugin/plugin.json", codex)
    self.assertIn("disagree on the version",
                  " ".join(self.errors(cc.check_manifests)))

  def test_codex_paths_must_exist_and_be_relative(self) -> None:
    codex = self.read(".codex-plugin/plugin.json")
    codex["hooks"] = "./hooks/missing.json"
    codex["skills"] = "skills"
    self.write(".codex-plugin/plugin.json", codex)
    errs = " ".join(self.errors(cc.check_manifests))
    self.assertIn("'./hooks/missing.json' does not exist", errs)
    self.assertIn("skills must be a ./relative path", errs)

  def test_marketplace_source_must_be_this_repo(self) -> None:
    market = self.read(".claude-plugin/marketplace.json")
    market["plugins"][0]["source"] = "./plugins/pawl"
    self.write(".claude-plugin/marketplace.json", market)
    self.assertIn("source must be ./",
                  " ".join(self.errors(cc.check_manifests)))


  def test_claude_icon_must_exist(self) -> None:
    claude = self.read(".claude-plugin/plugin.json")
    claude["icon"] = "./assets/missing.svg"
    self.write(".claude-plugin/plugin.json", claude)
    self.assertIn("icon './assets/missing.svg' does not exist",
                  " ".join(self.errors(cc.check_manifests)))


class PluginSettingsTest(TreeCase):

  def test_shipped_copy_is_clean(self) -> None:
    self.assertEqual(self.errors(cc.check_plugin_settings), [])

  def test_setting_the_dispatcher_never_reads_fails(self) -> None:
    claude = self.read(".claude-plugin/plugin.json")
    claude["userConfig"]["verbose"] = dict(
        claude["userConfig"]["disable"], title="Verbose")
    self.write(".claude-plugin/plugin.json", claude)
    self.assertEqual(self.errors(cc.check_plugin_settings), [
        ".claude-plugin/plugin.json: userConfig verbose is not in"
        " hooks/pawl.py PLUGIN_OPTIONS, so it changes nothing"])

  def test_option_with_no_setting_fails(self) -> None:
    claude = self.read(".claude-plugin/plugin.json")
    del claude["userConfig"]["disable"]
    self.write(".claude-plugin/plugin.json", claude)
    self.assertEqual(self.errors(cc.check_plugin_settings), [
        "hooks/pawl.py: PLUGIN_OPTIONS reads DISABLE, which"
        " .claude-plugin/plugin.json userConfig does not declare"])


class GuardrailTest(TreeCase):

  def test_file_over_500_lines_fails_and_tooling_dirs_pass(self) -> None:
    self.write("pieces/x/big.py", "x = 1\n" * 501)
    self.write("pieces/x/ok.py", "x = 1\n" * 500)
    self.write(".venv/lib/vendored.py", "x = 1\n" * 900)
    self.assertEqual(self.errors(cc.check_file_length),
                     ["pieces/x/big.py: 501 lines, over 500; split it"])

  def test_nesting_over_three_fails_and_elif_chains_pass(self) -> None:
    self.write("deep.py", textwrap.dedent("""\
        def deep(xs):
          for x in xs:
            if x:
              while x:
                with open(x):
                  pass
        """))
    self.write("flat.py", textwrap.dedent("""\
        def flat(x):
          for y in x:
            if y == 1:
              pass
            elif y == 2:
              pass
            elif y == 3:
              if y:
                pass
            else:
              pass

        def outer():
          for a in []:
            if a:
              def inner():
                for b in []:
                  if b:
                    pass
        """))
    self.assertEqual(self.errors(cc.check_nesting),
                     ["deep.py:1: deep nests 4 blocks deep, over 3; use guard"
                      " clauses"])


class LiveTreeTest(unittest.TestCase):

  def test_live_tree_is_clean(self) -> None:
    for check in (cc.check_hook_configs, cc.check_manifests,
                  cc.check_plugin_settings, cc.check_file_length,
                  cc.check_nesting):
      errors: List[str] = []
      check(errors)
      self.assertEqual(errors, [], check.__name__)


if __name__ == "__main__":
  unittest.main()
