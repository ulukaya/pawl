#!/usr/bin/env python3
"""Tests for egress_firewall.py.

Run: python3 -m unittest test_egress_firewall -v
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "egress_firewall.py"

RULES = {
    "literals": [".internal.example", "DO NOT FORWARD"],
    "regexes": [
        {"name": "long-token", "pattern": "[A-Za-z0-9+/_-]{40,}={0,2}"},
        {
            "name": "reasoning-tag",
            "pattern": "<\\s*/?\\s*(thought|reasoning)\\s*>",
        },
    ],
    "paths": [
        {"name": "hidden-config-dir", "pattern": "~/\\.[A-Za-z0-9_.-]+/"},
        {"name": "home-dir", "pattern": "/(home|Users)/[A-Za-z0-9_.-]+/"},
        {"name": "tmp-state", "pattern": "/tmp/[A-Za-z0-9_.-]+"},
    ],
    "email": {
        "pattern": "[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}",
        "allow": ["example.com"],
    },
}


def run(
    argv: list[str], stdin: str, **kw: object
) -> subprocess.CompletedProcess[str]:
  return subprocess.run(
      [sys.executable, str(SCRIPT), *argv],
      input=stdin,
      capture_output=True,
      text=True,
      timeout=30,
      **kw,
  )


def hook_payload(text: str, key: str = "message") -> str:
  return json.dumps({"toolCall": {"name": "run_command", "args": {key: text}}})


class EgressFirewallTest(unittest.TestCase):

  def setUp(self) -> None:
    self.tmp = tempfile.TemporaryDirectory()
    self.rules = os.path.join(self.tmp.name, "rules.json")
    with open(self.rules, "w", encoding="utf-8") as fh:
      json.dump(RULES, fh)

  def tearDown(self) -> None:
    self.tmp.cleanup()

  def check(
      self, text: str, rules: str | None = None
  ) -> subprocess.CompletedProcess[str]:
    return run(["check", "--rules", rules or self.rules], text)

  def hook(
      self, stdin: str, rules: str | None = None, extra: tuple[str, ...] = ()
  ) -> dict[str, object]:
    p = run(["hook", "--rules", rules or self.rules, *extra], stdin)
    self.assertEqual(p.returncode, 0, p.stderr)
    return json.loads(p.stdout)

  # ---- check: clean and each rule class

  def test_clean_text_passes(self):
    p = self.check("Thanks for the review, shipping the fix tomorrow morning.")
    self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
    self.assertEqual(p.stdout, "")

  def test_literal_hit_case_insensitive(self):
    p = self.check("see db01.INTERNAL.Example for the dump")
    self.assertEqual(p.returncode, 1)
    self.assertIn("DENY literal .internal.example at offset 8:", p.stdout)

  def test_regex_hit(self):
    p = self.check("token=" + "A" * 24 + "b9" * 10 + "==")
    self.assertEqual(p.returncode, 1)
    self.assertIn("DENY regex long-token at offset 6:", p.stdout)

  def test_path_hits(self):
    p = self.check("config lives in ~/.agentrc/state.json and /tmp/agent-cache")
    self.assertEqual(p.returncode, 1)
    self.assertIn("DENY path hidden-config-dir at offset 16:", p.stdout)
    self.assertIn("DENY path tmp-state", p.stdout)

  def test_email_allowlist(self):
    clean = self.check("write to help@example.com")
    self.assertEqual(clean.returncode, 0, clean.stdout)
    hit = self.check("write to alice@private-mail.net")
    self.assertEqual(hit.returncode, 1)
    self.assertIn("DENY email alice@private-mail.net at offset 9:", hit.stdout)

  # ---- check: obfuscation

  def test_html_entity_obfuscated_hit(self):
    p = self.check("&lt;thought&gt; the user is wrong &lt;/thought&gt;")
    self.assertEqual(p.returncode, 1)
    self.assertIn(
        "DENY regex reasoning-tag at offset 0: <thought> the user i", p.stdout
    )

  def test_zero_width_obfuscated_hit(self):
    p = self.check("host.inter\u200bnal.exa\u200dmple\ufeff is down")
    self.assertEqual(p.returncode, 1)
    self.assertIn("DENY literal .internal.example", p.stdout)

  def test_homoglyph_hit(self):
    # Cyrillic а (U+0430), е (U+0435), о (U+043e) and fullwidth ｅ (U+FF45)
    text = (
        "host.int\u0435rn\u0430l.\u0435x\u0430mpl\uff45 and DO N\u043eT FORWARD"
    )
    p = self.check(text)
    self.assertEqual(p.returncode, 1)
    self.assertIn("DENY literal .internal.example", p.stdout)
    self.assertIn("DENY literal do not forward", p.stdout)

  # ---- check: error paths exit 2

  def test_missing_rules_file_exits_2(self):
    p = self.check("anything", rules=os.path.join(self.tmp.name, "nope.json"))
    self.assertEqual(p.returncode, 2)
    self.assertIn("cannot read rules file", p.stderr)

  def test_bad_regex_exits_2(self):
    bad = os.path.join(self.tmp.name, "bad.json")
    with open(bad, "w", encoding="utf-8") as fh:
      json.dump({"regexes": [{"name": "broken", "pattern": "([unclosed"}]}, fh)
    p = self.check("anything", rules=bad)
    self.assertEqual(p.returncode, 2)
    self.assertIn("bad regex", p.stderr)

  # ---- hook

  def test_hook_allow(self):
    d = self.hook(hook_payload("Shipping the fix tomorrow morning."))
    self.assertEqual(d, {"decision": "allow"})

  def test_hook_deny(self):
    d = self.hook(hook_payload("dump is at ~/.agentrc/cache/"))
    self.assertEqual(d["decision"], "deny")
    self.assertIn("hidden-config-dir", d["reason"])

  def test_hook_falls_back_to_command_line(self):
    stdin = json.dumps({
        "toolCall": {
            "name": "run_command",
            "args": {"CommandLine": "mail --to bob@private-mail.net"},
        }
    })
    d = self.hook(stdin)
    self.assertEqual(d["decision"], "deny")
    self.assertIn("email", d["reason"])

  def test_hook_custom_field(self):
    payload = {"subject": "ping /tmp/agent-cache"}
    stdin = json.dumps({"toolCall": {"args": {"payload": payload}}})
    d = self.hook(stdin, extra=["--field", "toolCall.args.payload.subject"])
    self.assertEqual(d["decision"], "deny")

  def test_hook_denies_on_internal_error(self):
    missing = os.path.join(self.tmp.name, "nope.json")
    d = self.hook(hook_payload("harmless"), rules=missing)
    self.assertEqual(d["decision"], "deny")
    self.assertIn("failing closed", d["reason"])
    d2 = self.hook("this is not json {{{")
    self.assertEqual(d2["decision"], "deny")

  # ---- init-rules

  def test_init_rules_parses_and_loads(self):
    p = run(["init-rules"], "")
    self.assertEqual(p.returncode, 0, p.stderr)
    data = json.loads(p.stdout)
    for key in ("literals", "regexes", "paths", "email"):
      self.assertIn(key, data)
    generated = os.path.join(self.tmp.name, "gen.json")
    with open(generated, "w", encoding="utf-8") as fh:
      fh.write(p.stdout)
    self.assertEqual(self.check("plain text", rules=generated).returncode, 0)


if __name__ == "__main__":
  unittest.main()
