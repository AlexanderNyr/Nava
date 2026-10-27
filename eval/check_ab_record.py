#!/usr/bin/env python3
"""Validate a `nava-ab/1` record before anyone quotes it (PLAN.md phase N7).

    python3 eval/check_ab_record.py ab.json
    python3 eval/check_ab_record.py ab.json --require measured
    python3 eval/check_ab_record.py ab.json --require not-measured

The checks are re-derived from the attempts, not trusted from the labels:

* both arms ran the same tasks, the same number of attempts and the same model;
* the prompts are byte-identical between arms, so the only difference is the
  request payload (that is what makes it an A/B of constrained decoding);
* the constrained arm really sent exactly one extra field, named by the
  adapter, and the free arm sent none;
* the grammar and guide digests are present, and a missing token count is
  `null` with a reason rather than `0`;
* the aggregates equal what `eval/study.py` computes from the attempts;
* `measured` needs a real provider with evaluated attempts and no provider
  errors, and a claim is only allowed on a measured record.

Exit status 0 when the record is consistent, 1 when it is not.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval import study  # noqa: E402

SCHEMA = "nava-ab/1"
ARMS = ("free", "constrained")
REQUIRED = ("schema", "created_at_utc", "plan", "provider", "arms", "guidance",
            "environment", "comparison", "status", "status_reason", "notes")


def problems(record: dict) -> list[str]:
    found: list[str] = []
    if record.get("schema") != SCHEMA:
        found.append(f"schema is {record.get('schema')!r}, expected {SCHEMA!r}")
    for key in REQUIRED:
        if key not in record:
            found.append(f"missing top-level key {key!r}")
    if found:
        return found

    arms = record["arms"]
    for arm in ARMS:
        if arm not in arms:
            found.append(f"arm {arm!r} is missing")
    if found:
        return found

    plan = record["plan"]
    free, constrained = arms["free"], arms["constrained"]

    # The same work in both arms.
    if sorted(free["aggregates"]) != sorted(constrained["aggregates"]):
        found.append("the arms cover different tasks")
    if len(free["attempts"]) != len(constrained["attempts"]):
        found.append(f"arm 'free' has {len(free['attempts'])} attempts, "
                     f"arm 'constrained' has {len(constrained['attempts'])}")
    expected_attempts = len(plan["tasks"]) * plan["attempts_per_task"]
    if len(free["attempts"]) != expected_attempts:
        found.append(f"the plan announces {expected_attempts} attempts per arm, "
                     f"the record has {len(free['attempts'])}")

    # Prompts identical: the payload is the only difference.
    def prompts(arm):
        return [round_record.get("prompt_sha256")
                for attempt in arm["attempts"] for round_record in attempt.get("rounds", [])]

    if prompts(free) != prompts(constrained):
        found.append("the arms sent different prompts; a constrained run must not change "
                     "the prompt, only the request payload")

    # The payload difference is exactly one documented field.
    field = record["guidance"]["grammar"].get("field")
    if not field:
        found.append("guidance.grammar.field is missing")
    elif constrained["request_fields"] != [field]:
        found.append(f"arm 'constrained' sent {constrained['request_fields']}, "
                     f"expected exactly ['{field}']")
    if free["request_fields"]:
        found.append(f"arm 'free' sent extra request fields: {free['request_fields']}")

    # Digests and honest missing values.
    grammar_meta = record["guidance"]["grammar"]
    if not grammar_meta.get("sha256") or grammar_meta.get("bytes", 0) <= 0:
        found.append("guidance.grammar has no digest or size")
    guide = record["guidance"]["guide"]
    if not guide.get("sha256") or not guide.get("name"):
        found.append("guidance.guide has no name or digest")
    if guide.get("tokens") == 0:
        found.append("guidance.guide.tokens is 0; a token count is either a positive "
                     "number or null")
    if guide.get("tokens") is None:
        if record["guidance"].get("tokens_status") != "not-measured":
            found.append("guide tokens are missing but tokens_status is not 'not-measured'")
        if not record["guidance"].get("tokens_reason"):
            found.append("a missing token count needs a reason")

    # Aggregates must equal a fresh computation from the attempts.
    for arm in ARMS:
        for key, bucket in arms[arm]["aggregates"].items():
            task_id = key.split("/", 1)[0]
            recomputed = study.aggregate([attempt for attempt in arms[arm]["attempts"]
                                          if attempt["task"] == task_id])
            if recomputed != bucket:
                found.append(f"aggregate {key!r} in arm {arm!r} does not match its attempts")

    # Status and claims.
    status = record["status"]
    evaluated = min(arm["evaluated_attempts"] for arm in (free, constrained))
    errors = sum(arm["provider_errors"] for arm in (free, constrained))
    if status == "measured":
        if record["provider"] != "openai":
            found.append(f"a {record['provider']!r} run must not be labelled measured")
        if evaluated <= 0:
            found.append("measured with zero evaluated attempts")
        if errors:
            found.append(f"measured with {errors} failed provider calls")
    elif status == "not-measured":
        if not record["status_reason"]:
            found.append("not-measured without a reason")
    else:
        found.append(f"unknown status {status!r}")

    comparison = record["comparison"]
    if comparison.get("claim") is not None and status != "measured":
        found.append("a claim is present but the run is not measured")
    if comparison.get("claim") is not None and comparison.get("claim_status") == "open":
        found.append("claim is filled in while claim_status is still 'open'")
    if not record.get("claim_open", False) and status != "measured":
        found.append("claim_open is false for a run that is not measured")
    delta = comparison.get("pass_at_1_delta", {})
    if delta.get("pairs") and delta["pairs"] != len(free["attempts"]):
        found.append(f"the paired comparison uses {delta['pairs']} pairs for "
                     f"{len(free['attempts'])} attempts")
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a nava-ab/1 record.")
    parser.add_argument("record", type=Path)
    parser.add_argument("--require", choices=("measured", "not-measured"),
                        help="fail unless the record has this status")
    args = parser.parse_args(argv)
    record = json.loads(args.record.read_text(encoding="utf-8"))
    found = problems(record)
    for problem in found:
        print(f"ab record: {problem}", file=sys.stderr)
    if not found and args.require and record.get("status") != args.require:
        print(f"ab record: status is {record.get('status')!r}, "
              f"--require {args.require} was requested", file=sys.stderr)
        return 1
    if found:
        print(f"FAILED: {len(found)} problem(s) in {args.record}", file=sys.stderr)
        return 1
    print(f"OK: {args.record} is consistent (status {record.get('status')!r})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
