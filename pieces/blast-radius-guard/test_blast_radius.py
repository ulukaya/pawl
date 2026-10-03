#!/usr/bin/env python3
"""Tests for the blast-radius guard: places, expansion, what commands run.

Each rule has a deny or ask case beside an allow twin. The corpora are
scored in test_corpus.py.

Run: python3 -m pytest -q test_blast_radius.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path  # pylint: disable=g-importing-member
import random
import subprocess
import sys
import time
from typing import Dict

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import blast_radius  # noqa: E402  pylint: disable=g-import-not-at-top


@pytest.fixture
def ws(tmp_path: Path) -> Path:
  home = tmp_path / "home"
  work = home / "proj"
  work.mkdir(parents=True)
  subprocess.run(["git", "init", "-q", str(work)], check=True)
  return work


def env_for(ws: Path) -> Dict[str, str]:
  return {"HOME": str(ws.parent), "TMPDIR": str(ws.parent.parent / "t"),
          "PATH": os.defpath}


def decide(ws: Path, cmd: str, cwd: str = ".") -> str:
  return blast_radius.assess(cmd, str(ws / cwd), env_for(ws)).decision


# --- where a deletion lands --------------------------------------------------


@pytest.mark.parametrize("cmd,expect", [
    ("rm -rf ~", "deny"),
    ("rm -rf build", "allow"),
    ("rm -rf /", "deny"),
    ("rm -rf /tmp/pawl-x", "allow"),
    ("rm -rf ~/Documents", "deny"),
    ("rm -rf ~/.cache/pip", "ask"),
    ("rm -rf .", "ask"),
    ("rm -rf ./build/", "allow"),
    ("rm -rf ../", "deny"),
    ("rm -rf ../other", "ask"),
    ("rm -rf /etc", "deny"),
    ("rm -rf /usr/local/lib/x", "ask"),
])
def test_places(ws: Path, cmd: str, expect: str) -> None:
  assert decide(ws, cmd) == expect


@pytest.mark.parametrize("cmd,expect", [
    ("rm -f *.pyc", "allow"),
    ("rm -rf *", "ask"),
    ("rm -rf ~/*", "deny"),
    ("rm -rf ~/**/*", "deny"),
    ("rm -rf /*/", "deny"),
    ('rm -rf "$DIR"/', "ask"),
    ('rm -rf "build/$X"', "allow"),
    ('rm -rf "$HOME/$X"', "ask"),
    ("rm -rf /tmp/$(whoami)-cache", "allow"),
    ("rm -rf //", "deny"),
])
def test_globs_and_unknowns(ws: Path, cmd: str, expect: str) -> None:
  assert decide(ws, cmd) == expect


def test_workspace_is_never_home_or_root(tmp_path: Path) -> None:
  home = tmp_path / "home"
  home.mkdir()
  env = {"HOME": str(home), "PATH": os.defpath}
  # cwd = HOME, not a repo: home is not a workspace, so its folders ask
  assert blast_radius.assess("rm -rf notes", str(home), env).decision == "ask"
  assert blast_radius.assess("rm -rf .", str(home), env).decision == "deny"


# --- spellings that hide a command -------------------------------------------


@pytest.mark.parametrize("cmd", [
    "r''m -rf ~", "$'\\x72\\x6d' -rf ~", "rm${IFS}-rf${IFS}$HOME",
    "X=rm; $X -rf ~", "echo cm0gLXJmIH4= | base64 -d | sh", "\\rm -rf ~",
    "command rm -rf ~", "/bin/rm -rf ~", "sudo -u root rm -rf ~",
    "V=-rf; rm $V ~", 'bash -lc "rm -rf ~"', 'eval "rm -rf ~"',
    "trap 'rm -rf ~' EXIT", "rm -rf {build,~}", "rm -rf ${HOME%/*}",
    'xargs rm -rf <<< "$HOME"', "( cd ~ && rm -rf * )",
])
def test_hidden_spellings_are_denied(ws: Path, cmd: str) -> None:
  assert decide(ws, cmd) == "deny"


@pytest.mark.parametrize("cmd", [
    'echo "rm -rf ~"', 'git commit -m "rm -rf ~"', 'grep -rn "rm -rf" .',
    "python3 -c \"print('rm -rf ~')\"", "command -v rm", "ls ~",
])
def test_mentions_are_not_deletions(ws: Path, cmd: str) -> None:
  assert decide(ws, cmd) == "allow"


# --- what a command runs -------------------------------------------------------


def write(ws: Path, rel: str, text: str) -> None:
  path = ws / rel
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(text)


def test_script_files_are_followed(ws: Path) -> None:
  write(ws, "bad.sh", "rm -rf ~/\n")
  write(ws, "ok.sh", "rm -rf build\n")
  for run in ("bash bad.sh", "sh ./bad.sh", "source bad.sh", "cat bad.sh | sh",
              "bash < bad.sh"):
    assert decide(ws, run) == "deny", run
  assert decide(ws, "bash ok.sh") == "allow"


def test_trap_sees_a_later_reassignment(ws: Path) -> None:
  write(ws, "t.sh", '_H=$(mktemp -d)\ntrap \'rm -rf "$_H"\' EXIT\n_H="$HOME"\n')
  write(ws, "u.sh", 'T=$(mktemp -d)\ntrap \'rm -rf "$T"\' EXIT\n')
  assert decide(ws, "bash t.sh") == "deny"
  assert decide(ws, "bash u.sh") == "allow"


def test_function_arguments_come_from_call_sites(ws: Path) -> None:
  write(ws, "f.sh", 'clean() {\n  local d="$1"\n  rm -rf "$d"\n}\nclean dist\n')
  write(ws, "g.sh", 'clean() { rm -rf "$1"; }\nclean ~\n')
  assert decide(ws, "bash f.sh") == "allow"
  assert decide(ws, "bash g.sh") == "deny"


def test_package_scripts_and_hooks(ws: Path) -> None:
  write(ws, "package.json", json.dumps({"scripts": {
      "prebuild": "rm -rf ~/", "build": "tsc", "clean": "rimraf dist"}}))
  assert decide(ws, "npm run build") == "deny"
  assert decide(ws, "pnpm clean") == "allow"


def test_make_recipes_with_variables(ws: Path) -> None:
  write(ws, "Makefile", "OUT := $(HOME)\nB := build\n\nnuke:\n\trm -rf $(OUT)\n"
        "\nclean:\n\trm -rf $(B)/*\n")
  assert decide(ws, "make nuke") == "deny"
  assert decide(ws, "make clean") == "allow"


def test_interpreters_and_shell_outs(ws: Path) -> None:
  write(ws, "a.py", "import shutil\nfrom pathlib import Path\n"
        "shutil.rmtree(Path.home())\n")
  write(ws, "b.py", "import os\nos.system('rm -rf ~')\n")
  write(ws, "c.py", "CASES = ['shutil.rmtree(Path.home())']  # data\n")
  write(ws, "d.js", "require('fs').rmSync('dist', {recursive: true})\n")
  assert decide(ws, "python3 a.py") == "deny"
  assert decide(ws, "python3 b.py") == "deny"
  assert decide(ws, "python3 c.py") == "allow"
  assert decide(ws, "node d.js") == "allow"


def test_containers_judge_host_paths(ws: Path) -> None:
  assert decide(ws, "docker run --rm -v ~:/d alpine rm -rf /d") == "deny"
  assert decide(ws, 'docker run -v "$PWD":/app node rm -rf /app/x') == "allow"
  assert decide(ws, "docker run --rm alpine rm -rf /") == "allow"


def test_written_and_run_in_one_command(ws: Path) -> None:
  assert decide(ws, "cat > x.sh <<'EOF'\nrm -rf ~/\nEOF\nbash x.sh") == "deny"
  assert decide(ws, "echo 'rm -rf ~' > y.sh && sh y.sh") == "deny"


# --- contract: hook, CLI, robustness ----------------------------------------------


def test_decide_maps_ask_to_force_ask(ws: Path, monkeypatch) -> None:
  monkeypatch.setenv("HOME", str(ws.parent))
  payload = {"toolCall": {"name": "run_command", "args": {
      "CommandLine": "rm -rf ../other", "Cwd": str(ws)}}}
  out = blast_radius.decide(payload)
  assert out["decision"] == "force_ask"
  assert out["reason"].startswith("[PAWL blast] `rm -rf ../other`")
  payload["toolCall"]["args"]["CommandLine"] = "rm -rf ~"
  assert blast_radius.decide(payload)["decision"] == "deny"
  assert blast_radius.decide({})["decision"] == "allow"


def test_cli_exit_codes(ws: Path) -> None:
  def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(HERE / "blast_radius.py"),
                           *args], capture_output=True, text=True,
                          env=dict(os.environ, **env_for(ws)), check=False)
  assert run("check", "--cwd", str(ws), "--", "rm", "-rf", "build"
             ).returncode == 0
  denied = run("check", "--cwd", str(ws), "--", "rm", "-rf", "/")
  assert denied.returncode == 1 and denied.stdout.startswith("deny: ")
  assert run("nope").returncode == 2
  hook = subprocess.run([sys.executable, str(HERE / "blast_radius_hook.py")],
                        input="{not json", capture_output=True, text=True,
                        check=False)
  assert json.loads(hook.stdout) == {"decision": "allow"}


def test_reason_names_the_route(ws: Path) -> None:
  write(ws, "c.sh", "rm -rf ~/\n")
  reason = blast_radius.assess("bash c.sh", str(ws), env_for(ws)).reason
  assert reason.startswith("[PAWL blast] `bash c.sh` runs `rm -rf ~/`")
  assert "home directory" in reason and "by hand" in reason


def test_fuzz_never_raises_and_stays_fast(ws: Path) -> None:
  tokens = ["rm", "-rf", "~", "$HOME", '"$X"', ";", "&&", "|", "$(", ")",
            "`", "<<EOF\nx\nEOF\n", "<<<", "<(", "{", "}", "bash", "-c",
            "eval", "trap", "find", "-delete", "xargs", "python3", "docker",
            "run", "-v", "make", "npm", "cmd", "/c", "\\", "'", '"', "*",
            "..", "cd", "for", "in", "do", "done", "read", "$1", "#", "\n"]
  rng = random.Random(11)
  slowest = 0.0
  for _ in range(1500):
    cmd = " ".join(rng.choice(tokens) for _ in range(rng.randint(1, 12)))
    start = time.perf_counter()
    verdict = blast_radius.assess(cmd, str(ws), env_for(ws))
    slowest = max(slowest, time.perf_counter() - start)
    assert "could not finish" not in verdict.reason, cmd
  assert slowest < 1.0


def test_large_inputs_are_bounded(ws: Path) -> None:
  big = "echo hi; " * 5000 + "rm -rf ~"
  start = time.perf_counter()
  assert blast_radius.assess(big, str(ws), env_for(ws)).decision == "deny"
  assert time.perf_counter() - start < 5.0
  nested = "$(" * 200 + "rm -rf ~" + ")" * 200
  assert blast_radius.assess(nested, str(ws), env_for(ws)).decision in (
      "allow", "ask", "deny")
