#!/usr/bin/env python3
"""One reproducible microbenchmark; not an LLM generation benchmark.

The script separates the phases that are usually mixed together in one number:

* ``frontend_ms``  — Nava front end plus C emission (Nava only; C/C++ fold this
  into their own compiler invocation).
* ``compile_ms``   — native compilation of the generated/`provided` C or C++.
* ``startup_ms``   — median wall time of a run with a zero-iteration workload,
  i.e. process start plus a trivial amount of work. Compare it with the main
  workload before drawing conclusions about arithmetic speed.
* ``runs_ms``      — raw samples of the main workload, in execution order.

Nothing here measures model generation. Fields that would require a model run
(``model_measurements``) are recorded as ``null`` with an explicit status
instead of an estimate.

Use ``--json PATH`` (or ``--json -``) to store the complete machine-readable
record; ``BENCHMARK.md`` and ``bench/results/`` hold recorded runs.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import platform
import shlex
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "nava-bench/1"
WARMUP_RUNS = 1
REFERENCE_ITERATIONS = (0, 1, 100)


def positive_int(value):
    result = int(value)
    if result <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return result


def nonnegative_int(value):
    result = int(value)
    if result < 0:
        raise argparse.ArgumentTypeError("must be non-negative")
    return result


def run_binary(path: Path, n: int) -> tuple[str, float]:
    start = time.perf_counter_ns()
    result = subprocess.run([str(path)], input=f"{n}\n", text=True, capture_output=True, timeout=120)
    elapsed_ms = (time.perf_counter_ns() - start) / 1_000_000
    if result.returncode != 0:
        raise RuntimeError(f"{path.name} failed: {result.stderr}")
    return result.stdout, elapsed_ms


def reference(n: int) -> str:
    x = 1
    for _ in range(n):
        x = (x * 1664525 + 1013904223) % 1000000007
    return f"{x}\n"


def command_version(command: list[str]) -> str | None:
    """Return the first line of a compiler banner, or None if it cannot be read."""
    try:
        result = subprocess.run(command + ["--version"], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    first = result.stdout.strip().splitlines()
    return first[0].strip() if first else None


def collect_environment(cc: list[str], cxx: list[str], flags: dict) -> dict:
    uname = platform.uname()
    return {
        "recorded_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "platform": platform.platform(),
        "system": uname.system,
        "release": uname.release,
        "machine": uname.machine,
        "processor": uname.processor or None,
        "cpu_count": os.cpu_count(),
        "python": sys.version.split()[0],
        "python_implementation": platform.python_implementation(),
        "cc_command": cc,
        "cc_version": command_version(cc),
        "cxx_command": cxx,
        "cxx_version": command_version(cxx),
        "flags": flags,
        "isolation": "none: timings are from a shared, non-isolated host",
    }


def main():
    ap = argparse.ArgumentParser(description="Compare one native arithmetic loop; timings include process startup.")
    ap.add_argument("--iterations", type=positive_int, default=5_000_000)
    ap.add_argument("--samples", type=positive_int, default=5)
    ap.add_argument("--warmup", type=nonnegative_int, default=WARMUP_RUNS,
                    help=f"discarded warm-up runs per program (default: {WARMUP_RUNS})")
    ap.add_argument("--encoding", help="optional tiktoken encoding, e.g. cl100k_base; requires tiktoken")
    ap.add_argument("--json", metavar="PATH", help="write the machine-readable record ('-' for stdout only)")
    ap.add_argument("--quiet", action="store_true", help="suppress the human-readable table")
    args = ap.parse_args()
    if args.iterations > 2**63 - 1:
        ap.error("iterations must fit i64")
    if args.json == "-":
        args.quiet = True
    encoder = None
    if args.encoding:
        try:
            import tiktoken
            encoder = tiktoken.get_encoding(args.encoding)
        except (ImportError, ValueError, OSError) as exc:
            ap.error(f"could not load tokenizer {args.encoding}: {exc}")
    record = {
        "schema": SCHEMA,
        "workload": {
            "program": "bench/loop.nv: x = (x * 1664525 + 1013904223) % 1000000007",
            "iterations": args.iterations,
            "samples": args.samples,
            "warmup_runs": args.warmup,
            "reference_iterations": list(REFERENCE_ITERATIONS),
            "order_policy": "rotated by sample index; warm-up runs precede all samples",
            "tokenizer": ("tiktoken:" + args.encoding) if args.encoding else None,
        },
        "programs": [],
        "reference_check": {"ok": None, "cases": []},
        "failures": [],
        "model_measurements": {
            "status": "not-measured",
            "note": "this script never calls a model; generation time and token usage are not estimated",
            "prompt_tokens": None,
            "completion_tokens": None,
            "generation_ms": None,
        },
    }
    try:
        cc = shlex.split(os.environ.get("CC", "cc"))
        cxx = shlex.split(os.environ.get("CXX", "c++"))
        if not cc:
            ap.error("empty CC")
        if not cxx:
            ap.error("empty CXX")
        record["environment"] = collect_environment(
            cc, cxx,
            {"nava": "-std=c11 -O2 (via nava), front end then cc",
             "c": "not used: this benchmark compares Nava with C++",
             "cpp": "-std=c++17 -O2"},
        )
        with tempfile.TemporaryDirectory(prefix="nava-bench-") as temp:
            temp = Path(temp)
            nava_source = ROOT / "bench/loop.nv"
            nava_c = temp / "loop.c"
            nava_bin = temp / "nava"

            # Nava is built in two phases so the front end can be reported apart
            # from the C compiler, which is where C and C++ have no equivalent.
            start = time.perf_counter_ns()
            emitted = subprocess.run(
                [sys.executable, str(ROOT / "nava"), "emit-c", str(nava_source), "-o", str(nava_c)],
                capture_output=True, text=True, timeout=60,
            )
            frontend_ms = (time.perf_counter_ns() - start) / 1_000_000
            if emitted.returncode:
                raise RuntimeError(f"Nava front end failed:\n{emitted.stderr}")
            start = time.perf_counter_ns()
            compiled = subprocess.run(
                cc + ["-std=c11", "-O2", str(nava_c), "-o", str(nava_bin)],
                capture_output=True, text=True, timeout=60,
            )
            nava_cc_ms = (time.perf_counter_ns() - start) / 1_000_000
            if compiled.returncode:
                raise RuntimeError(f"compiling emitted C failed:\n{compiled.stderr}")

            specs = [
                {
                    "name": "Nava",
                    "source": nava_source,
                    "binary": nava_bin,
                    "frontend_ms": frontend_ms,
                    "compile_ms": nava_cc_ms,
                    "compile_parts": {"nava_frontend_and_emit_c": frontend_ms, "cc": nava_cc_ms},
                },
            ]
            for name, source, standard in (
                ("C++ checked", ROOT / "bench/loop_checked.cpp", "c++17"),
                ("C++ unchecked", ROOT / "bench/loop_unchecked.cpp", "c++17"),
            ):
                binary = temp / name.replace(" ", "_").replace("+", "p")
                start = time.perf_counter_ns()
                result = subprocess.run(
                    cxx + [f"-std={standard}", "-O2", str(source), "-o", str(binary)],
                    capture_output=True, text=True, timeout=60,
                )
                compile_ms = (time.perf_counter_ns() - start) / 1_000_000
                if result.returncode:
                    raise RuntimeError(f"compiling {name} failed:\n{result.stderr}")
                specs.append({
                    "name": name,
                    "source": source,
                    "binary": binary,
                    "frontend_ms": None,  # the C++ compiler owns this phase
                    "compile_ms": compile_ms,
                    "compile_parts": {"cxx": compile_ms},
                })

            for spec in specs:
                for n in REFERENCE_ITERATIONS:
                    actual, _ = run_binary(spec["binary"], n)
                    expected = reference(n)
                    ok = actual == expected
                    record["reference_check"]["cases"].append(
                        {"program": spec["name"], "iterations": n, "ok": ok}
                    )
                    if not ok:
                        raise RuntimeError(f"{spec['name']} failed the {n}-iteration reference check")
            record["reference_check"]["ok"] = True

            for spec in specs:
                spec["startup_ms"] = statistics.median(
                    run_binary(spec["binary"], 0)[1] for _ in range(args.samples)
                )
                for _ in range(args.warmup):
                    run_binary(spec["binary"], args.iterations)

            for spec in specs:
                spec["runs_ms"] = []
                spec["output"] = None
            expected = None
            order_log = []
            for trial in range(args.samples):
                ordered = specs[trial % len(specs):] + specs[:trial % len(specs)]
                order_log.append([spec["name"] for spec in ordered])
                for spec in ordered:
                    output, elapsed = run_binary(spec["binary"], args.iterations)
                    if expected is None:
                        expected = output
                    if output != expected:
                        raise RuntimeError(f"{spec['name']} produced a different output")
                    spec["output"] = output.strip()
                    spec["runs_ms"].append(elapsed)
            record["workload"]["run_order"] = order_log

            for spec in specs:
                text = spec["source"].read_text(encoding="utf-8")
                timings = spec["runs_ms"]
                record["programs"].append({
                    "name": spec["name"],
                    "source_path": str(spec["source"].relative_to(ROOT)),
                    "source_bytes": len(text.encode("utf-8")),
                    "source_tokens": len(encoder.encode(text)) if encoder else None,
                    "frontend_ms": round(spec["frontend_ms"], 3) if spec["frontend_ms"] is not None else None,
                    "compile_ms": round(spec["compile_ms"], 3),
                    "compile_parts_ms": {key: round(value, 3) for key, value in spec["compile_parts"].items()},
                    "startup_ms": round(spec["startup_ms"], 3),
                    "runs_ms": [round(value, 3) for value in timings],
                    "median_ms": round(statistics.median(timings), 3),
                    "mean_ms": round(statistics.fmean(timings), 3),
                    "min_ms": round(min(timings), 3),
                    "max_ms": round(max(timings), 3),
                    "stdev_ms": round(statistics.stdev(timings), 3) if len(timings) > 1 else None,
                    "output": spec["output"],
                })

        if not args.quiet:
            print(f"iterations={args.iterations}, samples={args.samples}, warmup={args.warmup}, "
                  f"output={record['programs'][0]['output']}")
            print(f"environment: {record['environment']['platform']}; python {record['environment']['python']}; "
                  f"cc: {record['environment']['cc_version']}; cxx: {record['environment']['cxx_version']}")
            print("Times in ms. Startup is a zero-iteration run; run times include startup and I/O.")
            headings = (f"{'program':<16} {'source bytes':>12} {'frontend ms':>12} {'compile ms':>12} "
                        f"{'startup ms':>11} {'run median':>11} {'run min':>9} {'run max':>9} {'run stdev':>10}")
            if encoder:
                headings += f" {'source tokens':>14}"
            print(headings)
            for entry in record["programs"]:
                frontend = "-" if entry["frontend_ms"] is None else f"{entry['frontend_ms']:.2f}"
                stdev = "-" if entry["stdev_ms"] is None else f"{entry['stdev_ms']:.2f}"
                line = (f"{entry['name']:<16} {entry['source_bytes']:>12} {frontend:>12} "
                        f"{entry['compile_ms']:>12.2f} {entry['startup_ms']:>11.2f} "
                        f"{entry['median_ms']:>11.2f} {entry['min_ms']:>9.2f} "
                        f"{entry['max_ms']:>9.2f} {stdev:>10}")
                if encoder:
                    line += f" {entry['source_tokens']:>14}"
                print(line)
            print("Nava compile ms is reported as two phases: frontend_ms plus the cc part (see compile_parts_ms).")
            print("Note: checked C++ includes overflow helpers; unchecked C++ is equivalent only for this bounded workload.")
            print("Source bytes are NOT LLM tokens; this script does not measure generation speed or accuracy.")
        if args.json:
            text = json.dumps(record, ensure_ascii=False, indent=2) + "\n"
            if args.json == "-":
                sys.stdout.write(text)
            else:
                path = Path(args.json)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")
                if not args.quiet:
                    print(f"record: {path}")
        return 0
    except (OSError, subprocess.TimeoutExpired, RuntimeError) as exc:
        record["failures"].append({"error": str(exc)})
        if args.json and args.json != "-":
            path = Path(args.json)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"benchmark failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
