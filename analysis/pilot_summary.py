"""Summarise a pilot: utility, ASR and policy cost per arm.

AgentDojo writes one JSON per (user task, injection task) pair. `security` is
True when the injection did NOT succeed, so ASR is its complement, counted
only over runs that actually carried an injection.
"""
import collections
import glob
import json
import os
import sys


def arm_results(log_dir):
    util_no_attack = []
    util_under_attack = []
    secure = []
    seconds = 0.0
    for path in glob.glob(os.path.join(log_dir, "*", "*", "*", "*", "*.json")):
        d = json.load(open(path, encoding="utf-8"))
        if not d.get("duration"):
            continue
        seconds += d["duration"]
        if d.get("injection_task_id"):
            util_under_attack.append(bool(d.get("utility")))
            secure.append(bool(d.get("security")))
        else:
            util_no_attack.append(bool(d.get("utility")))
    return util_no_attack, util_under_attack, secure, seconds


def policy_cost(metrics_path):
    stages = collections.defaultdict(lambda: {"n": 0, "s": 0.0, "tok": 0})
    parse = {"n": 0, "fail": 0, "shape": 0, "empty": 0}
    checks = {"n": 0, "s": 0.0}
    mode = None
    if not os.path.exists(metrics_path):
        return stages, parse, checks, mode
    for line in open(metrics_path, encoding="utf-8"):
        e = json.loads(line)
        if e["kind"] == "policy_llm":
            st = stages[e.get("stage", "?")]
            st["n"] += 1
            st["s"] += e.get("seconds", 0)
            st["tok"] += (e.get("prompt_tokens") or 0) + (e.get("completion_tokens") or 0)
            mode = e.get("structured_mode") or mode
        elif e["kind"] == "policy_parse":
            parse["n"] += 1
            if not e.get("ok"):
                parse["fail"] += 1
            if e.get("shape_ok") is False:
                parse["shape"] += 1
            if e.get("empty"):
                parse["empty"] += 1
        elif e["kind"] == "policy_check":
            checks["n"] += 1
            checks["s"] += e.get("seconds", 0)
    return stages, parse, checks, mode


def pct(xs):
    return 100.0 * sum(xs) / len(xs) if xs else float("nan")


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    print("%-34s %10s %10s %8s %10s" % ("arm", "util(no atk)", "util(atk)", "ASR", "wall"))
    print("-" * 78)
    arms = sorted(glob.glob(os.path.join(root, "logs", "*")))
    for log_dir in arms:
        tag = os.path.basename(log_dir)
        una, ua, sec, secs = arm_results(log_dir)
        asr = 100.0 - pct(sec) if sec else float("nan")
        print("%-34s %9.1f%% %9.1f%% %7.1f%% %9.0fs  (n=%d/%d)"
              % (tag, pct(una), pct(ua), asr, secs, len(una), len(sec)))

    print()
    for log_dir in arms:
        tag = os.path.basename(log_dir)
        stages, parse, checks, mode = policy_cost(os.path.join(root, "metrics", tag + ".jsonl"))
        if not stages and not checks["n"]:
            print("%s: no policy events (undefended)" % tag)
            continue
        total = sum(v["s"] for v in stages.values())
        print("%s   structured_mode=%s" % (tag, mode))
        for st in ("init", "update_gate", "update_gen"):
            if st in stages:
                v = stages[st]
                print("    %-12s %3d calls  %7.1fs  %6.2fs/call  %6d tok" %
                      (st, v["n"], v["s"], v["s"] / v["n"], v["tok"]))
        print("    %-12s %3d calls  %7.3fs  (deterministic check)" %
              ("z3 check", checks["n"], checks["s"]))
        print("    policy LLM total %.1fs;  parses %d, failed %d, wrong shape %d, empty %d"
              % (total, parse["n"], parse["fail"], parse["shape"], parse["empty"]))
        print()


if __name__ == "__main__":
    main()
