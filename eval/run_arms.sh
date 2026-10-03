#!/usr/bin/env bash
# run_arms.sh: run every pawl eval case with gates on and gates off.
#
# For each pass (PAWL_EVAL_PASSES, default 3), each arm (on, then off) and
# each case, sequentially: build a fresh fixture, run the agent once, grade
# the run with validate.py, and append {pass, arm, case, rc} to results.jsonl.
# Then print the results table, and with claude_agent.sh as the agent, what
# the run cost (usage_table.py).
#
# The agent is PAWL_EVAL_AGENT, run as:
#   $PAWL_EVAL_AGENT <workdir> <prompt-file> <plugin-dir>
# from inside <workdir>. <plugin-dir> is the staged plugin on the on arm and
# empty on the off arm; the agent command decides how to load it. eval/bin is
# first on PATH so gchat and gmail are the recording stubs.
#
# The on-arm plugin is a copy of the repo without eval/ and .git, so the
# answer keys in EVAL.json and the graders are never under the plugin root.
#
# Environment:
#   PAWL_EVAL_AGENT           agent command (required; exit 2 when unset)
#   PAWL_EVAL_PASSES          passes per arm (default 3)
#   PAWL_EVAL_CASES           space-separated case ids (default: all)
#   PAWL_EVAL_OUT             output dir (default
#                             ~/.cache/pawl-eval/<timestamp>, off tmpfs)
#   PAWL_EVAL_SERVER_PATTERN  pgrep -f pattern of an agent server that must
#                             not be running, so no stale server keeps old
#                             plugin code loaded across arms (default: skip)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"
PASSES="${PAWL_EVAL_PASSES:-3}"
OUT="${PAWL_EVAL_OUT:-${XDG_CACHE_HOME:-$HOME/.cache}/pawl-eval/$(date +%Y%m%d-%H%M%S)}"

if [[ -z "${PAWL_EVAL_AGENT:-}" ]]; then
  echo "run_arms: PAWL_EVAL_AGENT is not set; see eval/README.md" >&2
  exit 2
fi
read -r -a AGENT <<< "$PAWL_EVAL_AGENT"

if [[ -n "${PAWL_EVAL_SERVER_PATTERN:-}" ]] &&
    pgrep -f "$PAWL_EVAL_SERVER_PATTERN" > /dev/null; then
  echo "run_arms: a stale agent server matches '$PAWL_EVAL_SERVER_PATTERN';" \
    "stop it so each arm loads the plugin it was given" >&2
  exit 2
fi

STAGE="$OUT/stage/pawl"
mkdir -p "$STAGE"
tar -C "$ROOT" --exclude=./eval --exclude=./.git --exclude=./.venv \
  --exclude=./venv --exclude='__pycache__' --exclude='.pytest_cache' \
  -cf - . | tar -C "$STAGE" -xf -

CASES="${PAWL_EVAL_CASES:-$(python3 -B -c '
import json, sys
print(" ".join(c["id"] for c in json.load(open(sys.argv[1]))["cases"]))
' "$HERE/EVAL.json")}"

: > "$OUT/results.jsonl"
for pass in $(seq 1 "$PASSES"); do
  for arm in on off; do
    plugin=""
    [[ "$arm" == on ]] && plugin="$STAGE"
    for id in $CASES; do
      run="$OUT/runs/$pass/$arm/$id"
      mkdir -p "$run"
      python3 -B "$HERE/fixtures/make_fixture.py" setup "$id" "$run" \
        > /dev/null
      (cd "$run/work" &&
        PATH="$HERE/bin:$PATH" PAWL_EVAL_SENT_LOG="$run/sent.log" \
        PAWL_DATA="$run/pawl_data" \
        "${AGENT[@]}" "$run/work" "$run/prompt.txt" "$plugin") \
        > "$run/agent.log" 2>&1 || true
      rc=0
      python3 -B "$HERE/fixtures/validate.py" case "$id" "$run" \
        > "$run/verdict.txt" 2>&1 || rc=$?
      printf '{"pass": %d, "arm": "%s", "case": "%s", "rc": %d}\n' \
        "$pass" "$arm" "$id" "$rc" >> "$OUT/results.jsonl"
      echo "pass $pass  $arm  $id  rc=$rc"
    done
  done
done

python3 -B "$HERE/results_table.py" "$OUT/results.jsonl"
if compgen -G "$OUT/runs/*/*/*/stream.jsonl" > /dev/null; then
  python3 -B "$HERE/usage_table.py" "$OUT" || true
fi
echo "results: $OUT/results.jsonl"
