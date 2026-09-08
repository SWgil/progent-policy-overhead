"""Probe a policy model through the real api_request path before a sweep.

Answers three questions that otherwise only surface hours into a benchmark run:

1. Is the server up, and does it answer to the id it was launched with?
2. Does it accept the parameters `api_request` sends? Some OpenAI-compatible
   servers reject `seed`, and `tool_choice` is documented as unsupported by
   Ollama.
3. Which structured-output mode does it honour? This is the important one.
   vLLM takes `guided_json`, Ollama takes its own `format` field, hosted
   OpenAI takes `response_format`. Whether any of them is enforced decides
   whether a small model's bad policy reads as a security failure or merely a
   formatting one - so it is measured, not assumed.

    python analysis/probe_models.py                    # the configured model
    python analysis/probe_models.py qwen3:0.6b qwen3:4b
"""

import argparse
import json
import time

import secagent.tool as t
from secagent import instrument

#: A tool list and query small enough to be cheap, real enough that a policy
#: model has something to restrict.
SYS = (
    "Your task is to set permissions for each tool based on the user's query. "
    "List the needed tools and their parameter restrictions."
)
USR = (
    'TOOLS: [{"name": "send_money", "description": "send money", '
    '"args": {"recipient": {"type": "string"}, "amount": {"type": "number"}}}, '
    '{"name": "read_file", "description": "read a file", '
    '"args": {"path": {"type": "string"}}}]\n'
    "USER_QUERY: send 50 to alice"
)


#: Deliberately fights the schema. A server that really constrains decoding
#: still returns the envelope; one that quietly ignored the field returns
#: prose. Without this, a mode looks "supported" whenever the model happens to
#: comply on its own - which is exactly what a capable model does anyway.
ADVERSARIAL_SYS = (
    "Answer in one short English sentence of plain prose. "
    "Do not use JSON. Do not use braces or brackets."
)
ADVERSARIAL_USR = "What is the capital of France?"


def envelope(text):
    """Classify a response: 'ok', 'empty', or 'no'.

    'empty' is called out because a valid but empty policy array satisfies
    every structural check while granting nothing - the shape a tiny model
    falls back on under constraint, and the one that would silently read as a
    perfect ASR.
    """
    try:
        parsed = instrument.unwrap_policies(json.loads(text))
    except Exception:
        return "no"
    if not isinstance(parsed, list):
        return "no"
    if not parsed:
        return "empty"
    return "ok" if all(
        isinstance(item, dict) and "name" in item and "args" in item for item in parsed
    ) else "no"


def call(client, model, extra, sys_prompt=None, user_prompt=None):
    started = time.perf_counter()
    completion = client.chat.completions.create(
        messages=[
            {"role": "system", "content": sys_prompt or (SYS + instrument.JSON_MODE_SUFFIX)},
            {"role": "user", "content": user_prompt or USR},
        ],
        model=model,
        temperature=0.0,
        **extra,
    )
    text = completion.choices[0].message.content or ""
    usage = getattr(completion, "usage", None)
    return {
        "seconds": time.perf_counter() - started,
        "text": text,
        "tokens": (usage.prompt_tokens + usage.completion_tokens) if usage else 0,
    }


def probe_model(model):
    provider = instrument.provider_for(model)
    client = t._openai_client(provider)
    print("\n=== %s  (provider %s) ===" % (model, provider))

    # Plain call first: everything below is meaningless if this fails.
    try:
        base = call(client, model, {})
    except Exception as exc:
        print("  plain call FAILED: %s" % str(exc)[:160])
        return
    print("  unconstrained     OK    %5.2fs  %4d tok  envelope=%s"
          % (base["seconds"], base["tokens"], envelope(base["text"])))
    print("    -> %s" % " ".join(base["text"].split())[:100])

    # `seed` is sent by default; a server that rejects it fails every call.
    try:
        call(client, model, {"seed": 0})
        print("  seed accepted     OK")
    except Exception as exc:
        print("  seed accepted     NO    -> set SECAGENT_SEND_SEED=False (%s)"
              % str(exc)[:80])

    print("  structured modes            policy task    adversarial (enforcement)")
    enforced = []
    for mode in instrument.AUTO_ORDER:
        extra = instrument.request_kwargs_for(mode)
        try:
            on_task = call(client, model, extra)
        except Exception as exc:
            print("    %-14s REJECTED  %s" % (mode, str(exc)[:80]))
            continue
        # The same constraint against a prompt that asks for prose. Only a
        # server that truly enforces it can still return the envelope.
        try:
            against = call(client, model, extra,
                           sys_prompt=ADVERSARIAL_SYS, user_prompt=ADVERSARIAL_USR)
            forced = envelope(against["text"]) != "no"
        except Exception:
            forced = False
        if forced:
            enforced.append(mode)
        print("    %-14s accepted  envelope=%-5s  enforced=%s"
              % (mode, envelope(on_task["text"]), "YES" if forced else "no"))

    if enforced:
        print("  => enforced: %s" % ", ".join(enforced))
        print("     SECAGENT_STRUCTURED_MODE=%s" % enforced[0])
    else:
        print("  => nothing is enforced on this server; the model complies or it")
        print("     does not. shape_fail becomes load-bearing - report it beside")
        print("     every ASR, since an unparsed policy is an absent policy.")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("models", nargs="*",
                        help="model ids to probe (default: the configured policy model)")
    args = parser.parse_args()
    models = args.models or [instrument.BASE_POLICY_MODEL]

    print("endpoint: %s" % instrument.base_url())
    print("configured mode: %s   json mode: %s   seed: %s"
          % (instrument.structured_mode(),
             instrument.json_mode_enabled(),
             instrument.send_seed()))

    for model in models:
        probe_model(model)


if __name__ == "__main__":
    main()
