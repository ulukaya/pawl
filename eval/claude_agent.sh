#!/usr/bin/env bash
# claude_agent.sh: the Claude Code agent for run_arms.sh, capped and metered.
#
#   PAWL_EVAL_AGENT=eval/claude_agent.sh eval/run_arms.sh
#
# Called as `claude_agent.sh <workdir> <prompt-file> <plugin-dir>`. Runs one
# headless session in <workdir> on the prompt, loading the plugin at
# <plugin-dir> on the on arm (empty on the off arm), and saves the session's
# stream-json events to stream.jsonl beside the prompt file, which is the
# run directory. usage_table.py reads them for turns, tokens and cost, and
# checks that each arm loaded the plugin it was given.
#
# Both arms skip user settings (--setting-sources project,local), so a pawl
# installed for daily use does not load into the off arm. Anything that
# would prompt is refused (--permission-prompts none): a gate's ask counts as
# not run, as it would with nobody at the keyboard. Sessions are not saved
# for --resume; stream.jsonl holds every event.
#
# Billing is Claude Code's own: an exported ANTHROPIC_API_KEY bills the API;
# otherwise the runs count against the logged-in plan's usage limits.
#
# Environment:
#   PAWL_EVAL_MODEL      model for both arms (default: Claude Code's)
#   PAWL_EVAL_MAX_USD    spend cap per run, in dollars (default 1)
#   PAWL_EVAL_MAX_TURNS  turn cap per run (default 40)
#   PAWL_EVAL_TOOLS      tools allowed without a prompt (default
#                        Bash,Edit,Write,Read,Glob,Grep)
#   PAWL_EVAL_CLAUDE     claude binary (default: claude)
set -euo pipefail

if [[ $# -ne 3 ]]; then
  echo "usage: claude_agent.sh <workdir> <prompt-file> <plugin-dir>" >&2
  exit 2
fi
WORK="$1"
PROMPT="$2"
PLUGIN="$3"
if [[ ! -d "$WORK" || ! -f "$PROMPT" ]]; then
  echo "claude_agent: missing workdir '$WORK' or prompt '$PROMPT'" >&2
  exit 2
fi
RUN="$(cd "$(dirname "$PROMPT")" && pwd)"

ARGS=(
  -p
  --output-format stream-json --verbose
  --setting-sources project,local
  --permission-prompts none
  --no-session-persistence
  --max-budget-usd "${PAWL_EVAL_MAX_USD:-1}"
  --max-turns "${PAWL_EVAL_MAX_TURNS:-40}"
  --allowedTools "${PAWL_EVAL_TOOLS:-Bash,Edit,Write,Read,Glob,Grep}"
)
if [[ -n "${PAWL_EVAL_MODEL:-}" ]]; then
  ARGS+=(--model "$PAWL_EVAL_MODEL")
fi
if [[ -n "$PLUGIN" ]]; then
  ARGS+=(--plugin-dir "$PLUGIN")
fi

cd "$WORK"
exec "${PAWL_EVAL_CLAUDE:-claude}" "${ARGS[@]}" \
  < "$PROMPT" > "$RUN/stream.jsonl"
