"""Tests for check_portable.py: CR bytes, plugin.json fields, markdown width."""

from __future__ import annotations

import importlib.util
import json
import pathlib
import shutil
import sys
import tempfile
import unittest

Path = pathlib.Path
ROOT = Path(__file__).resolve().parent
_SPEC = importlib.util.spec_from_file_location(
    "check_portable", ROOT / "check_portable.py"
)
check_portable = importlib.util.module_from_spec(_SPEC)
sys.modules["check_portable"] = check_portable
_SPEC.loader.exec_module(check_portable)


class LineEndingsTest(unittest.TestCase):
  """check_line_endings: any CR byte in a text file fails."""

  def _tree(self, files: dict[str, bytes]) -> Path:
    d = Path(tempfile.mkdtemp(prefix="pawl_crlf_"))
    self.addCleanup(lambda: shutil.rmtree(d, ignore_errors=True))
    for rel, data in files.items():
      p = d / rel
      p.parent.mkdir(parents=True, exist_ok=True)
      p.write_bytes(data)
    return d

  def test_crlf_line_fails_with_path_and_line(self):
    root = self._tree({
        "README.md": b"# t\n\n| a | b |\r\n| c | d |\n",
        "pieces/x/x.py": b"print(1)\n",
    })
    errors: list[str] = []
    check_portable.check_line_endings(errors, root=root)
    self.assertEqual(errors, ["non-unix line ending: README.md:3"])

  def test_lf_only_tree_passes(self):
    root = self._tree({
        "README.md": b"# t\n\n| a | b |\n",
        "hooks.json": b'{"a": {}}\n',
        "pieces/x/x.py": b"print(1)\n",
    })
    errors: list[str] = []
    check_portable.check_line_endings(errors, root=root)
    self.assertEqual(errors, [])

  def test_pycache_is_skipped(self):
    root = self._tree({
        "pieces/x/__pycache__/x.cpython-313.py": b"\r\n",
        "pieces/x/x.py": b"print(1)\n",
    })
    errors: list[str] = []
    check_portable.check_line_endings(errors, root=root)
    self.assertEqual(errors, [])

  def test_live_tree_is_clean(self):
    errors: list[str] = []
    check_portable.check_line_endings(errors)
    self.assertEqual(errors, [])


class ToolingDirsTest(unittest.TestCase):
  """Virtualenvs and tool caches are not part of the shipped tree."""

  def _tree(self) -> Path:
    d = Path(tempfile.mkdtemp(prefix="pawl_venv_"))
    self.addCleanup(lambda: shutil.rmtree(d, ignore_errors=True))
    files = {
        ".venv/lib/site.py": b"HOME = '/home/someone/x'\r\n",
        ".venv/lib/README.md": b"# t\n\n" + b"word " * 20 + b"\n",
        "venv/agents/x.py": b"print(1)\n",
        ".pytest_cache/README.md": b"# t\n\n" + b"word " * 20 + b"\n",
        "node_modules/m/a.txt": b"x\r\n",
        "README.md": b"# t\n",
    }
    for rel, data in files.items():
      p = d / rel
      p.parent.mkdir(parents=True, exist_ok=True)
      p.write_bytes(data)
    return d

  def test_line_endings_skip_tooling_dirs(self):
    errors: list[str] = []
    check_portable.check_line_endings(errors, root=self._tree())
    self.assertEqual(errors, [])

  def test_markdown_width_skips_tooling_dirs(self):
    errors: list[str] = []
    check_portable.check_markdown_width(errors, root=self._tree())
    self.assertEqual(errors, [])

  def test_home_dirs_skip_tooling_dirs(self):
    errors: list[str] = []
    check_portable.check_home_dirs(errors, root=self._tree())
    self.assertEqual(errors, [])

  def test_forbidden_paths_skip_tooling_dirs(self):
    errors: list[str] = []
    check_portable.check_paths(errors, root=self._tree())
    self.assertEqual(errors, [])

  def test_home_dir_outside_tooling_dirs_still_fails(self):
    root = self._tree()
    (root / "notes.md").write_text("see /home/someone/notes\n")
    errors: list[str] = []
    check_portable.check_home_dirs(errors, root=root)
    self.assertEqual(
        errors, ["notes.md:1: absolute home dir '/home/someone/'"]
    )


class PluginJsonTest(unittest.TestCase):
  """check_plugin_json: field allowlist and logo file presence."""

  def _tree(
      self, plugin_json: str, extra: dict[str, bytes] | None = None
  ) -> Path:
    d = Path(tempfile.mkdtemp(prefix="pawl_pj_"))
    self.addCleanup(lambda: shutil.rmtree(d, ignore_errors=True))
    (d / "plugin.json").write_text(plugin_json)
    for rel, data in (extra or {}).items():
      p = d / rel
      p.parent.mkdir(parents=True, exist_ok=True)
      p.write_bytes(data)
    return d

  def test_display_name_and_logo_are_allowed(self):
    root = self._tree(
        '{"name": "x", "displayName": "X", "description": "d", "logo":'
        ' "assets/logo.svg"}',
        {"assets/logo.svg": b"<svg xmlns='http://www.w3.org/2000/svg'/>\n"},
    )
    errors: list[str] = []
    check_portable.check_plugin_json(errors, root=root)
    self.assertEqual(errors, [])

  def test_unknown_field_fails(self):
    root = self._tree('{"name": "x", "description": "d", "version": "1"}')
    errors: list[str] = []
    check_portable.check_plugin_json(errors, root=root)
    self.assertEqual(errors, ["plugin.json: unknown fields ['version']"])

  def test_missing_logo_file_fails(self):
    root = self._tree(
        '{"name": "x", "description": "d", "logo": "assets/logo.svg"}'
    )
    errors: list[str] = []
    check_portable.check_plugin_json(errors, root=root)
    self.assertEqual(
        errors,
        ["plugin.json: logo 'assets/logo.svg' is not a file in the plugin"],
    )

  def test_live_plugin_json_is_clean(self):
    errors: list[str] = []
    check_portable.check_plugin_json(errors)
    self.assertEqual(errors, [])


class MarkdownWidthTest(unittest.TestCase):
  """check_markdown_width: prose over 80 cols fails, code and tables pass."""

  def _tree(self, files: dict[str, str]) -> Path:
    d = Path(tempfile.mkdtemp(prefix="pawl_mdw_"))
    self.addCleanup(lambda: shutil.rmtree(d, ignore_errors=True))
    for rel, text in files.items():
      p = d / rel
      p.parent.mkdir(parents=True, exist_ok=True)
      p.write_text(text)
    return d

  def test_over_width_prose_fails_with_path_and_line(self):
    root = self._tree({"pieces/x/README.md": "# t\n\n" + "word " * 20 + "\n"})
    errors: list[str] = []
    check_portable.check_markdown_width(errors, root=root)
    self.assertEqual(errors, ["line over 80: pieces/x/README.md:3"])

  def test_fenced_code_is_exempt(self):
    root = self._tree({"README.md": "# t\n\n```bash\n" + "x" * 120 + "\n```\n"})
    errors: list[str] = []
    check_portable.check_markdown_width(errors, root=root)
    self.assertEqual(errors, [])

  def test_table_continuation_row_is_exempt(self):
    """mdformat splits wide cells into `:` continuation rows; they pass."""
    root = self._tree({
        "README.md": (
            "| a | b |\n|---|---|\n| c | d |\n:   : " + "e" * 100 + " :\n"
        ),
    })
    errors: list[str] = []
    check_portable.check_markdown_width(errors, root=root)
    self.assertEqual(errors, [])

  def test_table_row_url_and_lone_code_span_are_exempt(self):
    """Table rows, URL lines and lone code spans pass at any width."""
    root = self._tree({
        "README.md": (
            "| a | b |\n|---|---|\n| "
            + "c" * 100
            + " | d |\n\nsee https://example.com/"
            + "p" * 80
            + "\n\nCLI: `"
            + "q" * 90
            + "`\n"
        ),
    })
    errors: list[str] = []
    check_portable.check_markdown_width(errors, root=root)
    self.assertEqual(errors, [])

  def test_live_tree_is_clean(self):
    errors: list[str] = []
    check_portable.check_markdown_width(errors)
    self.assertEqual(errors, [])


class HooksJsonTest(unittest.TestCase):
  """check_hooks: no absolute path in any hook command, Stop entries too."""

  def _tree(self, hooks: dict) -> Path:
    d = Path(tempfile.mkdtemp(prefix="pawl_hooks_"))
    self.addCleanup(lambda: shutil.rmtree(d, ignore_errors=True))
    (d / "hooks.json").write_text(json.dumps(hooks))
    return d

  def _pre(self, cmd: str) -> dict:
    return {"PreToolUse": [{"matcher": ".*", "hooks": [
        {"type": "command", "command": cmd}]}]}

  def test_absolute_path_in_a_stop_entry_fails(self):
    root = self._tree({
        "g": {"Stop": [{"type": "command",
                        "command": "python3 /opt/x/stop_hook.py"}]},
    })
    errors: list[str] = []
    check_portable.check_hooks(errors, root=root)
    self.assertEqual(
        errors, ["hooks.json[g][Stop]: absolute path in"
                 " 'python3 /opt/x/stop_hook.py'"]
    )

  def test_absolute_path_in_a_pre_tool_use_entry_fails(self):
    root = self._tree({"g": self._pre("python3 /opt/x/hook.py")})
    errors: list[str] = []
    check_portable.check_hooks(errors, root=root)
    self.assertEqual(len(errors), 1)

  def test_relative_commands_pass(self):
    root = self._tree({
        "g": dict(self._pre("python3 -B hooks/a.py"),
                  Stop=[{"type": "command",
                         "command": "python3 -B hooks/b.py stop"}]),
    })
    errors: list[str] = []
    check_portable.check_hooks(errors, root=root)
    self.assertEqual(errors, [])

  def test_live_hooks_json_is_clean(self):
    errors: list[str] = []
    check_portable.check_hooks(errors)
    self.assertEqual(errors, [])


class SkillEnvTest(unittest.TestCase):
  """check_skill_env: reference Environment vars must exist in piece source."""

  def _tree(self, files: dict[str, str]) -> Path:
    d = Path(tempfile.mkdtemp(prefix="pawl_skenv_"))
    self.addCleanup(lambda: shutil.rmtree(d, ignore_errors=True))
    for rel, data in files.items():
      p = d / rel
      p.parent.mkdir(parents=True, exist_ok=True)
      p.write_text(data)
    return d

  def _skill(self, name: str, env_lines: str) -> str:
    del self  # module-level helper kept beside its tests
    return (
        f"# pawl-{name}\n\n## Commands\n\n"
        "`X_UNRELATED` in another section is fine.\n\n## Environment\n\n"
        f"{env_lines}\n\n## Test\n\nrun it\n"
    )

  def test_pasted_foreign_block_fails_per_var(self):
    """A reference naming another piece's vars fails once per unknown var."""
    root = self._tree({
        "pieces/report/report.py": 'os.environ.get("PAWL_DATA")\n',
        "skills/pawl/references/report.md": self._skill(
            "report",
            "- `PAWL_DATA`: dir.\n- `SEND_BUDGET_TZ`: zone.\n"
            "- `SEND_BUDGET_OVERRIDE=1`: once.\n",
        ),
    })
    errors: list[str] = []
    check_portable.check_skill_env(errors, root=root)
    self.assertEqual(
        errors,
        [
            (
                "skills/pawl/references/report.md: env var"
                " SEND_BUDGET_OVERRIDE not in pieces/report"
            ),
            (
                "skills/pawl/references/report.md: env var SEND_BUDGET_TZ"
                " not in pieces/report"
            ),
        ],
    )

  def test_own_vars_and_none_row_pass(self):
    """Vars the piece reads pass; a `(none)` row names no variable."""
    root = self._tree({
        "pieces/prose-gate/prose_gate.py": (
            'os.environ.get("PROSE_GATE_PATTERNS")\n'
        ),
        "skills/pawl/references/prose-gate.md": self._skill(
            "prose-gate", "- `PROSE_GATE_PATTERNS`: catalog path.\n"
        ),
        "pieces/repro-fence/repro_fence.py": "print(1)\n",
        "skills/pawl/references/repro-fence.md": self._skill(
            "repro-fence", "- (none): all inputs are flags.\n"
        ),
    })
    errors: list[str] = []
    check_portable.check_skill_env(errors, root=root)
    self.assertEqual(errors, [])

  def test_ratchet_and_send_gates_map_to_their_dirs(self):
    """ratchet.md reads pieces/ratchet-baseline; send-gates.md reads hooks."""
    root = self._tree({
        "pieces/ratchet-baseline/ratchet.py": 'environ["RATCHET_X"]\n',
        "skills/pawl/references/ratchet.md": self._skill(
            "ratchet", "- `RATCHET_X`: set.\n- `RATCHET_Y`: unset.\n"
        ),
        "hooks/pawl_hook.py": 'environ.get("PAWL_DISABLE")\n',
        "pieces/send-budget/send_budget.py": '"SEND_BUDGET_TZ"\n',
        "pieces/egress-firewall/egress_firewall.py": "pass\n",
        "pieces/prose-gate/prose_gate.py": "pass\n",
        "skills/pawl/references/send-gates.md": self._skill(
            "send-gates",
            "- `PAWL_DISABLE`: skip.\n- `SEND_BUDGET_TZ`: zone.\n"
            "- `PAWL_NOPE`: missing.\n",
        ),
    })
    errors: list[str] = []
    check_portable.check_skill_env(errors, root=root)
    self.assertEqual(
        errors,
        [
            (
                "skills/pawl/references/ratchet.md: env var RATCHET_Y not in"
                " pieces/ratchet-baseline"
            ),
            (
                "skills/pawl/references/send-gates.md: env var PAWL_NOPE"
                " not in hooks pieces/send-budget pieces/egress-firewall"
                " pieces/prose-gate"
            ),
        ],
    )

  def test_missing_piece_dir_fails(self):
    """A reference with no matching pieces/ dir is an error, not a pass."""
    root = self._tree({
        "skills/pawl/references/ghost.md": self._skill(
            "ghost", "- `A_B`: x.\n"
        ),
    })
    errors: list[str] = []
    check_portable.check_skill_env(errors, root=root)
    self.assertEqual(len(errors), 1)
    self.assertIn("references/ghost.md", errors[0])

  def test_missing_environment_section_fails(self):
    """A reference with no `## Environment` heading is not silently skipped."""
    knobs = self._skill("report", "- `NOT_CHECKED`: x.\n").replace(
        "## Environment", "## Knobs (environment)"
    )
    root = self._tree({
        "pieces/report/report.py": 'os.environ.get("PAWL_DATA")\n',
        "skills/pawl/references/report.md": knobs,
        "pieces/prose-gate/prose_gate.py": "pass\n",
        "skills/pawl/references/prose-gate.md": self._skill(
            "prose-gate", "- (none): flags only.\n"
        ),
    })
    errors: list[str] = []
    check_portable.check_skill_env(errors, root=root)
    self.assertEqual(
        errors,
        ["skills/pawl/references/report.md: no `## Environment` section"],
    )

  def test_live_tree_is_clean(self):
    """Every shipped reference names only vars its piece reads."""
    errors: list[str] = []
    check_portable.check_skill_env(errors)
    self.assertEqual(errors, [])


if __name__ == "__main__":
  unittest.main()
