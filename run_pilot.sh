#!/bin/bash
# Feasibility pilot: can a sub-8B policy model be studied on this machine?
#
# Runs a small banking subset three times - no defence, then Progent with a
# 0.6B and a 4B policy model - and prints what is needed to decide whether the
# full experiment is worth running. Roughly an hour on a single consumer GPU.
#
# Prerequisite: an Ollama server with qwen3:4b and qwen3:0.6b pulled, and
# OLLAMA_MAX_LOADED_MODELS=2 so the agent and policy models stay resident.
# Without that, Ollama swaps them on every alternation and the per-stage
# latency numbers measure model loading rather than inference.

set -euo pipefail
cd "$(dirname "$0")"

export LOCAL_BASE_URL="${LOCAL_BASE_URL:-http://127.0.0.1:11434/v1}"
export LOCAL_MODEL="${LOCAL_MODEL:-qwen3:4b}"   # the agent, held fixed
export AGENT_MODEL="${AGENT_MODEL:-qwen-local}"
export SECAGENT_JSON_MODE=True

# Five banking tasks: two read-only, three that move money, so both utility
# and the attack surface are exercised.
export USER_TASKS="${USER_TASKS:-user_task_0 user_task_1 user_task_3 user_task_5 user_task_7}"
# Two direct-action injections - the clearest signal that an attack landed.
export INJECTION_TASKS="${INJECTION_TASKS:-injection_task_4 injection_task_5}"

POLICY_SMALL="${POLICY_SMALL:-qwen3:0.6b}"
POLICY_LARGE="${POLICY_LARGE:-qwen3:4b}"

banner() { echo; echo "############ $* ############"; echo; }

banner "P1  server and structured-output probe"
python analysis/probe_models.py "$POLICY_SMALL" "$POLICY_LARGE" 2>&1 | grep -v SyntaxWarning

banner "P2+P3  agent floor: utility with no attack, ASR with no defence"
./run_sweep.sh m0-nodefense "$POLICY_LARGE" banking

banner "P4a  Progent, policy = $POLICY_SMALL"
./run_sweep.sh m3-auto-approve "$POLICY_SMALL" banking

banner "P4b  Progent, policy = $POLICY_LARGE"
./run_sweep.sh m3-auto-approve "$POLICY_LARGE" banking

banner "P5  residency check (both models should be listed)"
"${OLLAMA:-ollama}" ps || echo "could not run 'ollama ps'; check residency by hand"

banner "P5  per-stage cost"
for f in metrics/*.jsonl; do
  echo "--- $f"
  python analysis/summarize_metrics.py "$f" --group stage --tasks 5
done

echo
echo "Utility and ASR are in the benchmark output above, per run directory:"
ls -d logs/*/ 2>/dev/null || true
