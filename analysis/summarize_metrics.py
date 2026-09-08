#!/usr/bin/env python3
"""Aggregate secagent instrumentation JSONL into a runtime-cost breakdown.

Reproduces the shape of Progent v1 Figure 11 (policy generation / policy
update / policy check) and adds the columns the paper does not report:
per-stage token spend, retry counts and policy-JSON parse failure rate.

    python analysis/summarize_metrics.py metrics/*.jsonl
    python analysis/summarize_metrics.py metrics/ --group model,stage --csv out.csv
"""

import argparse
import collections
import csv
import glob
import json
import os
import sys

STAGE_ORDER = ["init", "update_gate", "update_gen"]
STAGE_LABEL = {
    "init": "policy generation",
    "update_gate": "policy update (gate)",
    "update_gen": "policy update (generate)",
}


def iter_events(paths):
    for path in paths:
        if not os.path.exists(path):
            # Correct for an undefended arm: no policy LLM ran, so no file.
            print("no metrics at %s" % path, file=sys.stderr)
            continue
        with open(path, encoding="utf-8") as handle:
            for lineno, line in enumerate(handle, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    print("skipping malformed line %s:%d" % (path, lineno), file=sys.stderr)


def expand(paths):
    out = []
    for path in paths:
        if os.path.isdir(path):
            out.extend(sorted(glob.glob(os.path.join(path, "*.jsonl"))))
        else:
            out.extend(sorted(glob.glob(path)) or [path])
    return out


def summarize(events, group_keys):
    rows = collections.defaultdict(lambda: {
        "calls": 0, "seconds": 0.0, "prompt_tokens": 0, "completion_tokens": 0,
        "errors": 0, "retries": 0, "parse_attempts": 0, "parse_failures": 0,
        "shape_failures": 0,
    })
    checks = {"count": 0, "seconds": 0.0}

    for event in events:
        kind = event.get("kind")
        if kind == "policy_check":
            checks["count"] += 1
            checks["seconds"] += event.get("seconds", 0.0)
            continue

        key = tuple(event.get(k, "") for k in group_keys)
        row = rows[key]

        if kind == "policy_llm":
            row["calls"] += 1
            row["seconds"] += event.get("seconds", 0.0)
            row["prompt_tokens"] += event.get("prompt_tokens", 0) or 0
            row["completion_tokens"] += event.get("completion_tokens", 0) or 0
            if event.get("error"):
                row["errors"] += 1
            if (event.get("temperature") or 0) > 0:
                # upstream raises temperature by 0.2 on every retry
                row["retries"] += 1
        elif kind == "policy_parse":
            row["parse_attempts"] += 1
            if not event.get("ok"):
                row["parse_failures"] += 1
            elif event.get("shape_ok") is False:
                row["shape_failures"] += 1

    return rows, checks


def stage_sort_key(key, group_keys):
    if "stage" in group_keys:
        stage = key[group_keys.index("stage")]
        return (STAGE_ORDER.index(stage) if stage in STAGE_ORDER else 99, key)
    return (0, key)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="+", help="JSONL files, globs or directories")
    parser.add_argument("--group", default="stage",
                        help="comma-separated event fields to group by (default: stage)")
    parser.add_argument("--tasks", type=int, default=0,
                        help="number of benchmark tasks, to report per-task averages")
    parser.add_argument("--csv", help="also write the table to this CSV path")
    args = parser.parse_args()

    group_keys = [k.strip() for k in args.group.split(",") if k.strip()]
    paths = expand(args.paths)
    if not paths:
        parser.error("no metrics files matched")

    rows, checks = summarize(iter_events(paths), group_keys)
    if not rows:
        print("no policy LLM events found", file=sys.stderr)
        return 0

    total_seconds = sum(r["seconds"] for r in rows.values()) + checks["seconds"]

    header = group_keys + ["calls", "seconds", "s/call", "share",
                           "prompt_tok", "completion_tok", "retries", "parse_fail", "shape_fail"]
    table = []
    for key in sorted(rows, key=lambda k: stage_sort_key(k, group_keys)):
        r = rows[key]
        label = list(key)
        if "stage" in group_keys:
            i = group_keys.index("stage")
            label[i] = "%s (%s)" % (label[i], STAGE_LABEL.get(label[i], "?"))
        table.append(label + [
            r["calls"],
            round(r["seconds"], 3),
            round(r["seconds"] / r["calls"], 3) if r["calls"] else 0.0,
            "%.1f%%" % (100.0 * r["seconds"] / total_seconds) if total_seconds else "-",
            r["prompt_tokens"],
            r["completion_tokens"],
            r["retries"],
            r["parse_failures"],
            r["shape_failures"],
        ])

    table.append(["policy check (Z3/SMT)"] + [""] * (len(group_keys) - 1) + [
        checks["count"], round(checks["seconds"], 4),
        round(checks["seconds"] / checks["count"], 6) if checks["count"] else 0.0,
        "%.2f%%" % (100.0 * checks["seconds"] / total_seconds) if total_seconds else "-",
        "", "", "", "", "",
    ])

    widths = [max(len(str(row[i])) for row in [header] + table) for i in range(len(header))]
    fmt = "  ".join("{:<%d}" % w for w in widths)
    print(fmt.format(*header))
    print("-" * (sum(widths) + 2 * (len(widths) - 1)))
    for row in table:
        print(fmt.format(*[str(c) for c in row]))

    print()
    print("total policy overhead: %.2fs across %d LLM calls" %
          (total_seconds, sum(r["calls"] for r in rows.values())))
    if args.tasks:
        print("per task (%d tasks): %.2fs, %d prompt + %d completion tokens" % (
            args.tasks,
            total_seconds / args.tasks,
            sum(r["prompt_tokens"] for r in rows.values()) // args.tasks,
            sum(r["completion_tokens"] for r in rows.values()) // args.tasks,
        ))

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(header)
            writer.writerows(table)
        print("wrote %s" % args.csv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
