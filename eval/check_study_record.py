#!/usr/bin/env python3
"""Validate a `nava-study/1` record before anyone quotes it.

The point is to make a lying record hard to keep: the schema, the scale label
and the measurement status are cross-checked against the attempts themselves.

    python3 eval/check_study_record.py study.json
    python3 eval/check_study_record.py study.json --require measured
    python3 eval/check_study_record.py study.json --require not-measured

Exit status 0 when the record is consistent, 1 when it is not.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

MIN_TASKS = 10
MIN_ATTEMPTS = 30
REQUIRED_TOP_LEVEL = ("schema", "created_at_utc", "plan", "scale", "provider",
                      "request", "prompt_policy", "environment", "attempts",
                      "aggregates", "model_measurements", "notes")


def problems(record: dict) -> list[str]:
    found: list[str] = []
    if record.get("schema") != "nava-study/1":
        found.append(f"schema is {record.get('schema')!r}, expected 'nava-study/1'")
    for key in REQUIRED_TOP_LEVEL:
        if key not in record:
            found.append(f"missing top-level key {key!r}")
    if found:
        return found

    attempts = record["attempts"]
    plan = record["plan"]
    scale = record["scale"]
    measurements = record["model_measurements"]
    task_count = len(plan.get("tasks") or [])
    attempts_per = plan.get("attempts_per_task_language")

    if not attempts:
        found.append("the record contains no attempts")
    if scale.get("label") not in ("exploratory", "empirical"):
        found.append(f"unknown scale label {scale.get('label')!r}")
    if scale.get("label") == "empirical":
        if task_count < MIN_TASKS:
            found.append(f"labelled empirical with only {task_count} tasks")
        if not isinstance(attempts_per, int) or attempts_per < MIN_ATTEMPTS:
            found.append(f"labelled empirical with only {attempts_per} attempts per task and language")
    if measurements.get("status") not in ("measured", "not-measured"):
        found.append(f"unknown measurement status {measurements.get('status')!r}")
    if measurements.get("status") == "measured":
        if record["provider"].get("provider") == "mock":
            found.append("a mock run must not be labelled measured")
        if not measurements.get("evaluated_attempts"):
            found.append("labelled measured with zero evaluated attempts")
        if any(attempt.get("provider_error") for attempt in attempts):
            found.append("labelled measured although some attempts failed at the provider")
        if measurements.get("median_completion_tokens") is None and not any(
                attempt.get("completion_tokens") is not None for attempt in attempts):
            found.append("labelled measured although no token usage was ever reported")
    if measurements.get("status") == "not-measured" and not measurements.get("reason"):
        found.append("not-measured without a reason")
    total = record["aggregates"].get("all/all", {})
    if total.get("attempts") != len(attempts):
        found.append("the aggregate attempt count does not match the attempt list")
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a nava-study/1 record.")
    parser.add_argument("record", type=Path)
    parser.add_argument("--require", choices=("measured", "not-measured"),
                        help="fail unless the record's measurement status matches")
    args = parser.parse_args()
    try:
        record = json.loads(args.record.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"cannot read {args.record}: {error}", file=sys.stderr)
        return 1
    found = problems(record)
    status = (record.get("model_measurements") or {}).get("status")
    if args.require and status != args.require:
        found.append(f"measurement status is {status!r}, --require {args.require} was requested")
    for problem in found:
        print(f"record: {problem}", file=sys.stderr)
    if found:
        return 1
    print(f"OK: {args.record.name}: {len(record['attempts'])} attempts, "
          f"scale={record['scale']['label']}, status={status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
