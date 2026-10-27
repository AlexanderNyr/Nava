#!/usr/bin/env python3
"""Classify the difference between two `nava-profile/1` records (N8).

A performance claim is only worth making if a reader can see the samples behind
it and if the tool refuses to dress noise up as a result. So every phase is
compared with an explicit rule:

* the median difference must exceed `--sigma` (default 2) times the larger
  standard deviation of the two sides, **and** be at least `--floor` (default
  5%) of the baseline median. Anything smaller is reported as `inconclusive`,
  which is a result in its own right;
* sizes (`emitted_c_bytes`, `binary_bytes`) are exact counts, not timings, so
  they are compared exactly and labelled `size`;
* phases missing on either side are reported as `missing`, never as zero.

The tool never edits a record and never merges runs from different hosts
silently: if the two records disagree about host, compiler or optimisation
level, it says so up front.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCHEMA = "nava-comparison/1"
PHASES = ("frontend_total", "lex", "parse", "check", "emit")


def _median_spread(entry: dict | None) -> tuple[float | None, float | None]:
    if not isinstance(entry, dict):
        return None, None
    return entry.get("median_ms"), entry.get("stdev_ms")


def _judge(before: dict | None, after: dict | None, *, sigma: float,
           floor: float) -> dict:
    """`classify` over two stage entries, with the medians and spreads unpacked.

    `classify` takes (median, median, spread, spread): passing the raw pairs
    through `*before, *after` silently compares a median against a standard
    deviation, which reports nonsense as a result.
    """
    before_median, before_spread = _median_spread(before)
    after_median, after_spread = _median_spread(after)
    return {"before_ms": before_median, "after_ms": after_median,
            **classify(before_median, after_median, before_spread, after_spread,
                       sigma=sigma, floor=floor)}


def classify(before: float | None, after: float | None, spread_before: float | None,
             spread_after: float | None, *, sigma: float, floor: float) -> dict:
    """`improved`, `regressed` or `inconclusive`, with the numbers behind it."""
    if before is None or after is None:
        return {"verdict": "missing", "delta_ms": None, "relative": None,
                "spread_ms": None, "delta_over_spread": None}
    delta = after - before
    relative = delta / before if before else None
    spread = max(spread_before or 0.0, spread_after or 0.0)
    big_enough = abs(delta) > sigma * spread
    meaningful = relative is not None and abs(relative) >= floor
    if big_enough and meaningful:
        verdict = "improved" if delta < 0 else "regressed"
    else:
        verdict = "inconclusive"
    return {"verdict": verdict, "delta_ms": round(delta, 4),
            "relative": None if relative is None else round(relative, 4),
            "spread_ms": round(spread, 4), "delta_over_spread":
                round(abs(delta) / spread, 3) if spread else None}


def compare(before: dict, after: dict, *, sigma: float = 2.0, floor: float = 0.05) -> dict:
    mismatches = []
    for key in ("platform", "cc_version", "cc_flags"):
        if before.get("environment", {}).get(key) != after.get("environment", {}).get(key):
            mismatches.append(f"{key}: {before.get('environment', {}).get(key)!r} vs "
                              f"{after.get('environment', {}).get(key)!r}")
    if before.get("configuration", {}).get("opt") != after.get("configuration", {}).get("opt"):
        mismatches.append("optimisation level differs")

    result = {
        "schema": SCHEMA,
        "rule": {"sigma": sigma, "floor": floor,
                 "note": "a phase is improved or regressed only when the median difference "
                         "exceeds sigma times the larger standard deviation and floor of the "
                         "baseline median; everything else is inconclusive, and a missing "
                         "phase is never zero"},
        "before": {"label": before.get("label"), "runtime": before.get("runtime"),
                   "recorded_at_utc": before.get("environment", {}).get("recorded_at_utc")},
        "after": {"label": after.get("label"), "runtime": after.get("runtime"),
                  "recorded_at_utc": after.get("environment", {}).get("recorded_at_utc")},
        "environment_mismatches": mismatches,
        "processes": {},
        "workloads": [],
        "summary": {},
    }

    for name in ("python_startup", "import_and_cli"):
        result["processes"][name] = _judge(
            before.get("processes", {}).get(name),
            after.get("processes", {}).get(name), sigma=sigma, floor=floor)

    def by_name(record):
        return {entry["workload"]: entry for entry in record.get("workloads", [])}

    before_workloads, after_workloads = by_name(before), by_name(after)
    for name in [entry["workload"] for entry in before.get("workloads", [])]:
        old = before_workloads.get(name)
        new = after_workloads.get(name)
        if old is None or new is None:
            result["workloads"].append({"workload": name, "phases": {},
                                        "sizes": {}, "verdict": "missing"})
            continue
        phases = {}
        for phase in PHASES:
            phases[phase] = _judge(old["stages_ms"].get(phase),
                                   new["stages_ms"].get(phase), sigma=sigma, floor=floor)
        for phase in ("cli_emit_c", "cc", "binary_startup", "run"):
            phases[phase] = _judge(old["processes_ms"].get(phase),
                                   new["processes_ms"].get(phase), sigma=sigma, floor=floor)
        sizes = {}
        for key in ("emitted_c_bytes", "binary_bytes", "source_bytes"):
            before_value, after_value = old.get(key), new.get(key)
            if before_value is None or after_value is None:
                sizes[key] = {"before": before_value, "after": after_value,
                              "delta": None, "verdict": "missing"}
            else:
                delta = after_value - before_value
                sizes[key] = {"before": before_value, "after": after_value, "delta": delta,
                              "relative": round(delta / before_value, 4)
                              if before_value else None,
                              "verdict": "reduced" if delta < 0 else
                                         ("unchanged" if delta == 0 else "increased")}
        result["workloads"].append({
            "workload": name,
            "phases": phases,
            "sizes": sizes,
            "verdict": _workload_verdict(phases),
        })

    verdicts = [phase["verdict"] for entry in result["workloads"]
                for phase in entry["phases"].values()]
    # A workload that only one record contains has no phases at all, so it would
    # otherwise vanish from the counts instead of being reported as missing.
    absent = [entry["workload"] for entry in result["workloads"]
              if entry["verdict"] == "missing"]
    result["summary"] = {
        "improved": verdicts.count("improved"),
        "regressed": verdicts.count("regressed"),
        "inconclusive": verdicts.count("inconclusive"),
        "missing": verdicts.count("missing") + len(absent),
        "missing_workloads": absent,
        "regressions": [f"{entry['workload']}.{phase}"
                        for entry in result["workloads"]
                        for phase, detail in entry["phases"].items()
                        if detail["verdict"] == "regressed"],
        "improvements": [f"{entry['workload']}.{phase}"
                         for entry in result["workloads"]
                         for phase, detail in entry["phases"].items()
                         if detail["verdict"] == "improved"],
    }
    return result


def _workload_verdict(phases: dict) -> str:
    verdicts = {detail["verdict"] for detail in phases.values()}
    for candidate in ("regressed", "improved"):
        if candidate in verdicts:
            return candidate
    if verdicts == {"missing"}:
        return "missing"
    return "inconclusive"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare two nava-profile/1 records and classify each phase.",
        epilog="Phases that differ by less than the noise rule are reported as "
               "inconclusive; that is a result, not a failure.")
    parser.add_argument("before", type=Path)
    parser.add_argument("after", type=Path)
    parser.add_argument("--sigma", type=float, default=2.0)
    parser.add_argument("--floor", type=float, default=0.05)
    parser.add_argument("--fail-on-regression", action="store_true",
                        help="exit 1 when any phase is classified as a regression")
    parser.add_argument("--json", metavar="PATH", help="write the comparison here ('-' for stdout)")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    before = json.loads(args.before.read_text(encoding="utf-8"))
    after = json.loads(args.after.read_text(encoding="utf-8"))
    for name, record in (("before", before), ("after", after)):
        if record.get("schema") != "nava-profile/1":
            parser.error(f"{name}: {args.before if name == 'before' else args.after} is not a "
                         f"nava-profile/1 record")
    result = compare(before, after, sigma=args.sigma, floor=args.floor)

    if not args.quiet:
        if result["environment_mismatches"]:
            print("warning: the two records do not describe the same setup:",
                  file=sys.stderr)
            for mismatch in result["environment_mismatches"]:
                print(f"  {mismatch}", file=sys.stderr)
        print(f"{'workload':<10} {'phase':<15} {'before ms':>10} {'after ms':>10} "
              f"{'delta':>10} {'verdict':>13}")
        for name, detail in result["processes"].items():
            print(f"{'-':<10} {name:<15} "
                  f"{_cell(detail['before_ms']):>10} {_cell(detail['after_ms']):>10} "
                  f"{_delta(detail):>10} {detail['verdict']:>13}")
        for entry in result["workloads"]:
            for phase, detail in entry["phases"].items():
                print(f"{entry['workload']:<10} {phase:<15} "
                      f"{_cell(detail['before_ms']):>10} {_cell(detail['after_ms']):>10} "
                      f"{_delta(detail):>10} {detail['verdict']:>13}")
            sizes = entry["sizes"]
            if sizes.get("emitted_c_bytes", {}).get("verdict") not in (None, "missing"):
                print(f"{entry['workload']:<10} {'emitted C bytes':<15} "
                      f"{sizes['emitted_c_bytes']['before']:>10} "
                      f"{sizes['emitted_c_bytes']['after']:>10} "
                      f"{sizes['emitted_c_bytes']['delta']:>10} "
                      f"{sizes['emitted_c_bytes']['verdict']:>13}")
            if sizes.get("binary_bytes", {}).get("verdict") not in (None, "missing"):
                print(f"{entry['workload']:<10} {'binary bytes':<15} "
                      f"{sizes['binary_bytes']['before']:>10} "
                      f"{sizes['binary_bytes']['after']:>10} "
                      f"{sizes['binary_bytes']['delta']:>10} "
                      f"{sizes['binary_bytes']['verdict']:>13}")
        summary = result["summary"]
        print(f"improved {summary['improved']}, regressed {summary['regressed']}, "
              f"inconclusive {summary['inconclusive']}, missing {summary['missing']}")
        for improvement in summary["improvements"]:
            print(f"  improved: {improvement}")
        for regression in summary["regressions"]:
            print(f"  regressed: {regression}")

    if args.json:
        text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        if args.json == "-":
            sys.stdout.write(text)
        else:
            path = Path(args.json)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            if not args.quiet:
                print(f"record: {path}")
    if args.fail_on_regression and result["summary"]["regressions"]:
        return 1
    return 0


def _cell(value: float | None) -> str:
    return "-" if value is None else f"{value:.3f}"


def _delta(detail: dict) -> str:
    if detail.get("delta_ms") is None:
        return "-"
    relative = detail.get("relative")
    suffix = "" if relative is None else f"{relative * 100:+.1f}%"
    return f"{detail['delta_ms']:+.3f} {suffix}"


if __name__ == "__main__":
    raise SystemExit(main())
