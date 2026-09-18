#!/bin/bash
# Feasibility pilot: Progent overhead with a single local model.
#
# Runs a small banking subset twice - no defence, then Progent with the same
# model serving both the agent and the policy LLM - and prints what is needed
# to decide whether the full experiment is worth running.
#
# Prerequisite: the Ollama server at LOCAL_BASE_URL with MODEL pulled, plus
#   OLLAMA_CONTEXT_LENGTH=8192   the default 4096 silently truncates: the
#                                banking tool schemas alone are ~1.2k tokens
#                                before any history.
#
# Qwen3 thinks by default. The schema constraint only covers `content`, so it
# does not suppress that, and `think` is not reachable over the
# OpenAI-compatible endpoint - expect the thinking tokens to show up in the
# per-stage completion counts.

set -euo pipefail
cd "$(dirname "$0")"

export LOCAL_BASE_URL="${LOCAL_BASE_URL:-http://10.251.36.222:11434/v1}"
# run_sweep.sh derives this per arm, but P1 runs the probe directly. An
# explicit value still wins, so the policy model can sit on a second server.
export SECAGENT_POLICY_BASE_URL="${SECAGENT_POLICY_BASE_URL:-$LOCAL_BASE_URL}"
# One model for both the agent and the policy LLM.
MODEL="${MODEL:-qwen3.8:27b}"
export LOCAL_MODEL="${LOCAL_MODEL:-$MODEL}"        # the agent
export AGENT_MODEL="${AGENT_MODEL:-qwen-local}"
export SECAGENT_JSON_MODE=True

# Five banking tasks: two read-only, three that move money, so both utility
# and the attack surface are exercised.
export USER_TASKS="${USER_TASKS:-user_task_0 user_task_1 user_task_3 user_task_5 user_task_7}"
# Two direct-action injections - the clearest signal that an attack landed.
export INJECTION_TASKS="${INJECTION_TASKS:-injection_task_4 injection_task_5}"

POLICY_MODEL="${POLICY_MODEL:-$MODEL}"             # the policy LLM

banner() { echo; echo "############ $* ############"; echo; }

banner "P1  server and structured-output probe"
python analysis/probe_models.py "$POLICY_MODEL" 2>&1 | grep -v SyntaxWarning

banner "P2+P3  agent floor: utility with no attack, ASR with no defence"
./run_sweep.sh m0-nodefense "$POLICY_MODEL" banking

banner "P4  Progent, policy = $POLICY_MODEL"
./run_sweep.sh m3-auto-approve "$POLICY_MODEL" banking

banner "P5  residency check ($MODEL should be listed)"
# `ollama ps` talks to the local daemon by default; point it at the server
# the benchmark actually used.
OLLAMA_HOST="${OLLAMA_HOST:-${LOCAL_BASE_URL%/v1}}" "${OLLAMA:-ollama}" ps \
  || echo "could not run 'ollama ps'; check residency by hand"

banner "P5  per-stage cost"
for f in metrics/*.jsonl; do
  echo "--- $f"
  python analysis/summarize_metrics.py "$f" --group stage --tasks 5
done

echo
echo "Utility and ASR are in the benchmark output above, per run directory:"
ls -d logs/*/ 2>/dev/null || true
