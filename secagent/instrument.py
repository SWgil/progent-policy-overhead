"""Stage-level instrumentation for Progent's policy LLM.

Adds three things the upstream code does not have:

1. Per-stage wall-clock / token / retry accounting, written as JSONL so the
   runtime breakdown of the paper (v1 Figure 11) can be reproduced and compared
   across policy models.
2. Per-stage model routing, so the trusted-context stages (``init``,
   ``update_gate``) and the untrusted-data stage (``update_gen``) can be served
   by different models.
3. A record of whether the policy JSON parsed, kept separate from the security
   metrics.  Upstream retries on a parse failure with a raised temperature, so
   without this the cost of a badly-formatted model is invisible.

Everything is driven by environment variables and defaults to upstream
behaviour, so an unset environment reproduces the original numbers.
"""

import json
import os
import sys
import threading
import time

# ---------------------------------------------------------------- model routing

#: Model id the local server answers to. vLLM reports the path or repo id it
#: was launched with, so this must match ``vllm serve <id>`` exactly.
DEFAULT_LOCAL_MODEL = os.getenv("SECAGENT_LOCAL_MODEL", "Qwen/Qwen3-8B")

BASE_POLICY_MODEL = os.getenv("SECAGENT_POLICY_MODEL", DEFAULT_LOCAL_MODEL)

#: Stages a policy LLM call can belong to.  ``init`` and ``update_gate`` only
#: ever see trusted content (the tool list, the user query, the outgoing tool
#: call); ``update_gen`` is the only stage that reads tool results.
STAGES = ("init", "update_gate", "update_gen")


def stage_model(stage):
    """Model for ``stage``, falling back to the single-model setting."""
    return os.getenv("SECAGENT_POLICY_MODEL_" + stage.upper(), BASE_POLICY_MODEL)


def routing_summary():
    return {stage: stage_model(stage) for stage in STAGES}


# ---------------------------------------------------------------- metrics sink

_lock = threading.Lock()
_metrics_path = os.getenv("SECAGENT_METRICS_PATH", "")
_run_tag = os.getenv("SECAGENT_RUN_TAG", "")
_suite = os.getenv("SECAGENT_SUITE", "")

_totals = {}


def _bump(key, amount):
    _totals[key] = _totals.get(key, 0) + amount


def record(event):
    """Append one event to the metrics JSONL and keep a running total."""
    event.setdefault("ts", time.time())
    if _run_tag:
        event.setdefault("run_tag", _run_tag)
    if _suite:
        event.setdefault("suite", _suite)

    kind = event.get("kind", "?")
    _bump(kind + ".count", 1)
    _bump(kind + ".seconds", event.get("seconds", 0.0))
    _bump(kind + ".prompt_tokens", event.get("prompt_tokens", 0) or 0)
    _bump(kind + ".completion_tokens", event.get("completion_tokens", 0) or 0)

    if not _metrics_path:
        return
    line = json.dumps(event, ensure_ascii=False, default=str)
    try:
        with _lock:
            with open(_metrics_path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
    except Exception as exc:  # never let telemetry break a benchmark run
        print("[instrument] failed to write metrics: %s" % exc, file=sys.stderr)


def totals():
    return dict(_totals)


def dump_totals():
    print("[instrument] totals: " + json.dumps(totals(), sort_keys=True), file=sys.stderr)


class timer(object):
    """Context manager recording the duration of a non-LLM step (e.g. Z3)."""

    def __init__(self, kind, **fields):
        self.kind = kind
        self.fields = fields

    def __enter__(self):
        self.start = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb):
        event = {"kind": self.kind, "seconds": time.perf_counter() - self.start}
        event.update(self.fields)
        if exc_type is not None:
            event["error"] = repr(exc)
        record(event)
        return False


# ---------------------------------------------------------------- json mode

def json_mode_enabled():
    return os.getenv("SECAGENT_JSON_MODE", "False").lower() == "true"


#: Appended to the system prompt when JSON mode is on.  OpenAI-compatible JSON
#: mode requires the literal word "json" in the prompt and guarantees a
#: syntactically valid *object*, so the policy array is wrapped in a key.
#:
#: Note this is weaker than a full schema constraint: ``args`` holds an
#: arbitrary JSON Schema, which strict ``json_schema`` response formats cannot
#: express.  JSON mode removes syntax failures (code fences, prose preambles);
#: shape failures are counted separately by ``unwrap_policies``.
JSON_MODE_SUFFIX = (
    "\nRespond with a single json object of the form "
    '{"policies": [{"name": tool_name, "args": restrictions}, ...]} '
    "and nothing else."
)


def unwrap_policies(parsed):
    """Accept both the upstream bare array and the JSON-mode wrapper object."""
    if isinstance(parsed, dict) and "policies" in parsed:
        return parsed["policies"]
    return parsed


def base_url():
    """Endpoint for OpenAI-compatible self-hosted models (vLLM, Ollama, ...)."""
    return os.getenv("SECAGENT_POLICY_BASE_URL", "http://127.0.0.1:8000/v1")


# ---------------------------------------------------------------- providers

#: Model-id prefixes historically routed to a local server by upstream.
_LOCAL_PREFIXES = ("Qwen/", "meta-llama/", "mistralai/", "google/gemma", "local/")


def provider_for(model):
    """Which client should serve `model`.

    SECAGENT_POLICY_PROVIDER overrides everything, which is what lets a locally
    served model keep a name the prefix rules would otherwise claim (a vLLM
    server answering to "gpt-4o-mini", say). Otherwise the id is matched
    against known hosted families, and anything unrecognised is treated as
    locally served - the useful default once the study runs off a local model.
    """
    forced = os.getenv("SECAGENT_POLICY_PROVIDER", "").strip().lower()
    if forced:
        return forced
    if model.startswith("claude"):
        return "anthropic"
    if model.startswith("gemini") or model.startswith("vertex_ai/"):
        return "gemini"
    if model.startswith(_LOCAL_PREFIXES):
        return "local"
    if model.startswith(("gpt-", "o1", "o3", "o4", "chatgpt")):
        return "openai"
    return "local"


def send_seed():
    """Whether to pin `seed`.

    vLLM honours it; some other OpenAI-compatible servers reject the field
    outright, so it can be turned off without editing code.
    """
    return os.getenv("SECAGENT_SEND_SEED", "True").lower() == "true"


# ---------------------------------------------------------------- guided json

#: Shape of a generated policy: an array of per-tool restrictions. `args` holds
#: an arbitrary JSON Schema, so it stays an open object - the point of guided
#: decoding here is to force the *envelope*, which is where small models fail.
POLICY_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "policies": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "args": {"type": "object"},
                },
                "required": ["name", "args"],
            },
        }
    },
    "required": ["policies"],
}


#: How the policy envelope is constrained. Servers disagree here, and the
#: choice decides whether a small model's failure can be read as a security
#: failure or only as a formatting one - so it is explicit rather than implied.
#:
#: guided_json    vLLM: the schema is enforced during decoding.
#: ollama_format  Ollama's native `format` field, passed through /v1.
#: json_object    OpenAI-style JSON mode. Guarantees syntax, not shape.
#: off            No constraint; the model is asked in the prompt only.
STRUCTURED_MODES = (
    "guided_json",     # vLLM extra_body
    "json_schema",     # OpenAI-style response_format with a schema
    "ollama_format",   # Ollama native `format` passed through /v1
    "json_object",     # plain JSON mode: syntax only
    "off",
)

#: Tried in this order by `auto`, strongest constraint first.
AUTO_ORDER = ("guided_json", "json_schema", "ollama_format", "json_object")

_resolved_mode = None


def structured_mode():
    """The configured mode, or 'auto' to be settled against the live server."""
    mode = os.getenv("SECAGENT_STRUCTURED_MODE", "auto").strip().lower()
    if mode not in STRUCTURED_MODES and mode != "auto":
        raise ValueError(
            "SECAGENT_STRUCTURED_MODE must be one of %s or 'auto', got %r"
            % (", ".join(STRUCTURED_MODES), mode)
        )
    return mode


def set_resolved_mode(mode):
    """Pin the mode that `auto` settled on, so it is decided once per run."""
    global _resolved_mode
    _resolved_mode = mode


def resolved_mode():
    return _resolved_mode


#: A prompt that fights the schema, used to tell an enforced constraint from an
#: ignored one. Servers drop unknown request fields silently rather than
#: rejecting them, so "the call succeeded" proves nothing.
ENFORCEMENT_PROBE_SYS = (
    "Answer in one short English sentence of plain prose. "
    "Do not use JSON. Do not use braces or brackets."
)
ENFORCEMENT_PROBE_USR = "What is the capital of France?"


def looks_like_envelope(text):
    """Whether `text` parses as the policy envelope, empty or not."""
    try:
        parsed = unwrap_policies(json.loads(text))
    except Exception:
        return False
    return isinstance(parsed, list)


def request_kwargs_for(mode):
    """Extra request fields that impose `mode` on an OpenAI-compatible call."""
    if mode == "guided_json":
        return {"extra_body": {"guided_json": POLICY_JSON_SCHEMA}}
    if mode == "json_schema":
        return {
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "policy", "schema": POLICY_JSON_SCHEMA},
            }
        }
    if mode == "ollama_format":
        return {"extra_body": {"format": POLICY_JSON_SCHEMA}}
    if mode == "json_object":
        return {"response_format": {"type": "json_object"}}
    return {}


def guided_decoding_enabled():
    """Backwards-compatible switch for the vLLM path.

    Kept so an existing SECAGENT_GUIDED_JSON=False still turns the strongest
    constraint off without having to know about the mode names.
    """
    return os.getenv("SECAGENT_GUIDED_JSON", "True").lower() == "true"
