# Progent: Securing AI Agents with Privilege Control
Check out our paper [here](https://arxiv.org/abs/2504.11703).

## Installation
```bash
pip install -e .
```

## Experiments in the paper
### Agentdojo
```bash
cd agentdojo
pip install -e . # install agentdojo
cd ..
pip install -e . # install progent
cd agentdojo
./run.sh
```
Check out more in [agentdojo/README.md](agentdojo/README.md)

### ASB
```bash
cd asb
pip install -r requirements.txt # install asb
cd ..
pip install -e . # install progent
cd asb
python scripts/agent_attack.py --cfg_path config/OPI.yml
```
Check out more in [asb/README.md](asb/README.md)

### Real world agents
```bash
cd agentdojo-mcp
pip install -e . # install agentdojo-mcp
python mcp_server.py # start the mcp server
cd ..
pip install -e . # install progent
cd real-world-agents
pip install -r requirements.txt
./run.sh
```

---

# Policy-LLM overhead study (fork additions)

This fork instruments Progent's policy LLM so its cost can be attributed per
stage, and lets each stage be served by a different model. Upstream behaviour
is unchanged when none of the environment variables below are set.

| Added | Purpose |
|---|---|
| `secagent/instrument.py` | Stage routing, JSONL metrics, provider detection, structured-output modes |
| `analysis/probe_models.py` | Pre-flight: which structured-output mode this server actually *enforces* |
| `analysis/summarize_metrics.py` | Per-stage latency / tokens / parse failures, in the shape of the paper's Figure 11 |
| `analysis/pilot_summary.py` | Utility, ASR and policy cost per arm |
| `run_sweep.sh` | One (mode, policy model) arm |
| `run_pilot.sh` | Small feasibility pilot end to end |

## Quick start against any OpenAI-compatible server

```bash
python analysis/probe_models.py <policy-model>       # check the server first

LOCAL_BASE_URL=http://127.0.0.1:8000/v1 \
LOCAL_MODEL=<agent-model> \
  ./run_sweep.sh m0-nodefense <policy-model> banking   # baseline, run this first
  ./run_sweep.sh m3-auto-approve <policy-model> banking

python analysis/pilot_summary.py
```

Modes: `m0-nodefense`, `m1-init-only` (≈ Conseca), `m2-auto-deny`,
`m3-auto-approve` (the paper's default), `m4-hybrid`.

`m4-hybrid` puts the trusted-context stages (`init`, `update_gate`) on the
light model and keeps only `update_gen` - the one stage that reads untrusted
tool results - on `HEAVY_MODEL`.

## Three things that fail silently

1. **A structured-output mode can be accepted without being enforced.**
   OpenAI-compatible servers drop unknown request fields rather than rejecting
   them, so a successful call proves nothing. `probe_models.py` settles it by
   imposing the constraint on a prompt that asks for prose.
2. **`ENABLE_SECAGENT=False` does not disable the defence under AgentDojo.**
   Use the `m0-nodefense` mode, which also leaves `SECAGENT_SUITE` unset so the
   suite is built with unwrapped tools. A correct baseline writes no metrics
   file at all.
3. **`update_gate` answers Yes/No, not a policy.** Constraining it to the
   policy schema makes small models generate until they hit a token ceiling.

Full setup, per-backend recipes, the environment variable reference and the
porting checklist are in **EXPERIMENT_README.md** of the study repository.
