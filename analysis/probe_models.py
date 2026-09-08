"""Probe candidate policy models through the real api_request path.

Run this before a sweep. It checks that the local server is up and answering
to the id it was launched with, that it accepts the parameters `api_request`
sends (some OpenAI-compatible servers reject `seed`, and guided decoding needs
a backend that supports it), and that the instrumentation records an event.

    python analysis/probe_models.py                       # default local model
    python analysis/probe_models.py Qwen/Qwen3-8B
    SECAGENT_JSON_MODE=True python analysis/probe_models.py   # with guided json
"""

import argparse
import time

import secagent.tool as t
from secagent import instrument

#: Default is whatever the local server is serving. Pass ids explicitly to
#: compare several, e.g. a 7B against a 14B on the same endpoint.
DEFAULT_CANDIDATES = [instrument.DEFAULT_LOCAL_MODEL]

SYS = "You are a policy generator. Reply with a JSON array of restrictions and nothing else."
USR = (
    'TOOLS: [{"name": "send_email", "description": "send an email", '
    '"args": {"to": {"type": "string"}}}]\n'
    "USER_QUERY: email alice@example.com"
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("models", nargs="*", default=DEFAULT_CANDIDATES)
    args = parser.parse_args()
    models = args.models or DEFAULT_CANDIDATES

    print("endpoint: %s" % instrument.base_url())
    print("json mode: %s  guided decoding: %s  seed: %s" % (
        instrument.json_mode_enabled(),
        instrument.guided_decoding_enabled(),
        instrument.send_seed(),
    ))
    print()
    print("%-30s %-8s %-6s %-7s %-6s %s" % (
        "model", "provider", "result", "seconds", "tokens", "detail"))
    print("-" * 118)
    for model in models:
        t.policy_model = model
        before = t.total_prompt_tokens + t.total_completion_tokens
        started = time.perf_counter()
        try:
            out = t.api_request(SYS, USR, 0.0, stage="init")
            elapsed = time.perf_counter() - started
            used = (t.total_prompt_tokens + t.total_completion_tokens) - before
            detail = " ".join((out or "").split())[:52]
            print("%-30s %-8s %-6s %-7.2f %-6d %s" % (
                model, instrument.provider_for(model), "OK", elapsed, used, detail))
        except Exception as exc:
            elapsed = time.perf_counter() - started
            print("%-30s %-8s %-6s %-7.2f %-6s %s" % (
                model, instrument.provider_for(model), "FAIL", elapsed, "-", str(exc)[:90]))


if __name__ == "__main__":
    main()
