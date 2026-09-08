#!/bin/bash
# Policy-model sweep for the overhead / lightweight-model study.
#
# Each (model, mode) pair is one AgentDojo run per suite, twice: once with no
# attack (utility) and once under important_instructions (utility + ASR).
# Metrics land in metrics/<tag>.jsonl; summarise with
#   python analysis/summarize_metrics.py metrics/<tag>.jsonl --group stage --tasks N
#
# Usage: ./run_sweep.sh <mode> <policy-model> [suites...]

set -euo pipefail

MODE="${1:?usage: run_sweep.sh <mode> <policy-model> [suites...]}"
POLICY_MODEL="${2:?missing policy model}"
shift 2
SUITES=("${@:-banking slack}")

# Agent LLM, held fixed across the sweep so only the policy model varies.
# `qwen-local` talks to the local OpenAI-compatible server; use
# `qwen-local-prompting` if the server was launched without a tool call parser.
AGENT_MODEL="${AGENT_MODEL:-qwen-local}"

# Local server. AGENTDOJO_* is read by the agent side, SECAGENT_* by the policy
# side; they point at the same endpoint unless deliberately split.
export AGENTDOJO_LOCAL_MODEL="${LOCAL_MODEL:-Qwen/Qwen3-8B}"
export AGENTDOJO_LOCAL_BASE_URL="${LOCAL_BASE_URL:-http://127.0.0.1:8000/v1}"
export SECAGENT_POLICY_BASE_URL="${LOCAL_BASE_URL:-http://127.0.0.1:8000/v1}"
TAG="$(echo "${MODE}_${POLICY_MODEL}" | tr '/.:' '___')"
LOG_DIR="logs/$TAG"
mkdir -p "$LOG_DIR" metrics

export ENABLE_SECAGENT="True"
export SECAGENT_POLICY_MODEL="$POLICY_MODEL"
export SECAGENT_IGNORE_UPDATE_ERROR="True"
export SECAGENT_METRICS_PATH="$PWD/metrics/$TAG.jsonl"
export SECAGENT_RUN_TAG="$TAG"
# On by default for local models: guided decoding pins the policy envelope so
# a format failure is not mistaken for a security-reasoning failure.
export SECAGENT_JSON_MODE="${SECAGENT_JSON_MODE:-True}"
export SECAGENT_GUIDED_JSON="${SECAGENT_GUIDED_JSON:-True}"
export COLUMNS=300

# Modes map onto the approver configurations of Progent section 8.2.
case "$MODE" in
  # Removing the defence takes two switches, neither of them ENABLE_SECAGENT
  # (which only gates the LangChain middleware).
  #
  # SECAGENT_SUITE is read when the task suite is built: with it set, every
  # tool is wrapped by check_tool_call AND update_always_allowed_tools installs
  # a base policy. Leaving it unset gives plain, unwrapped tools - without it,
  # "no defence" would really mean "allow-list of no-argument tools", which
  # blocks every money-moving call and collapses utility and ASR together.
  #
  # SECAGENT_GENERATE stops the policy LLM being called at all.
  m0-nodefense)
    export ENABLE_SECAGENT="False"
    export SECAGENT_GENERATE="False"
    export SECAGENT_UPDATE="False"
    WRAP_TOOLS=no
    ;;
  m1-init-only)   export SECAGENT_UPDATE="False" ;;                                   # ~= Conseca
  m2-auto-deny)   export SECAGENT_UPDATE="True";  export SECAGENT_ONLY_ALLOW_NARROW="True" ;;
  m3-auto-approve) export SECAGENT_UPDATE="True" ;;                                   # paper default
  m4-hybrid)
    # Trusted-context stages go to the light model; only the stage that reads
    # untrusted tool results stays on the frontier model.
    export SECAGENT_UPDATE="True"
    export SECAGENT_POLICY_MODEL_INIT="$POLICY_MODEL"
    export SECAGENT_POLICY_MODEL_UPDATE_GATE="$POLICY_MODEL"
    export SECAGENT_POLICY_MODEL_UPDATE_GEN="${HEAVY_MODEL:?m4-hybrid needs HEAVY_MODEL, the model that reads untrusted tool results}"
    ;;
  *) echo "unknown mode: $MODE" >&2; exit 1 ;;
esac

# Task subsetting, for pilots on hardware that cannot afford a full suite.
# USER_TASKS="user_task_0 user_task_1"  INJECTION_TASKS="injection_task_0"
WRAP_TOOLS="${WRAP_TOOLS:-yes}"

SUBSET=()
for task in ${USER_TASKS:-}; do SUBSET+=(--user-task "$task"); done
for task in ${INJECTION_TASKS:-}; do SUBSET+=(--injection-task "$task"); done

echo "mode=$MODE policy=$POLICY_MODEL agent=$AGENT_MODEL suites=${SUITES[*]}"
echo "local server: $AGENTDOJO_LOCAL_BASE_URL (model $AGENTDOJO_LOCAL_MODEL)"
echo "metrics -> $SECAGENT_METRICS_PATH"
if [ ${#SUBSET[@]} -gt 0 ]; then echo "subset: ${SUBSET[*]}"; fi

# --max-workers stays at its default of 1: a second worker would contend for
# the single GPU and make the per-stage latency numbers meaningless.
for suite in ${SUITES[*]}; do
  if [ "$WRAP_TOOLS" = yes ]; then export SECAGENT_SUITE="$suite"; else unset SECAGENT_SUITE; fi
  python -m agentdojo.scripts.benchmark \
    -s "$suite" --model "$AGENT_MODEL" --logdir "$LOG_DIR" "${SUBSET[@]}" \
    > "$LOG_DIR/$suite-no-attack.log" 2>&1
  python -m agentdojo.scripts.benchmark \
    -s "$suite" --model "$AGENT_MODEL" --attack important_instructions --logdir "$LOG_DIR" "${SUBSET[@]}" \
    > "$LOG_DIR/$suite-attack.log" 2>&1
done

echo "done: $TAG"
python analysis/summarize_metrics.py "$SECAGENT_METRICS_PATH" --group stage
