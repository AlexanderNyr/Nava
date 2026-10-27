#!/usr/bin/env python3
"""One reproducible microbenchmark; not an LLM generation benchmark."""
from __future__ import annotations

import argparse
import os
import shlex
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def positive_int(value):
    result = int(value)
    if result <= 0:
        raise argparse.ArgumentTypeError("must be positive")
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


def main():
    ap = argparse.ArgumentParser(description="Compare one native arithmetic loop; timings include process startup.")
    ap.add_argument("--iterations", type=positive_int, default=5_000_000)
    ap.add_argument("--samples", type=positive_int, default=5)
    ap.add_argument("--encoding", help="optional tiktoken encoding, e.g. cl100k_base; requires tiktoken")
    args = ap.parse_args()
    if args.iterations > 2**63 - 1:
        ap.error("iterations must fit i64")
    encoder = None
    if args.encoding:
        try:
            import tiktoken
            encoder = tiktoken.get_encoding(args.encoding)
        except (ImportError, ValueError, OSError) as exc:
            ap.error(f"could not load tokenizer {args.encoding}: {exc}")
    try:
        cxx = shlex.split(os.environ.get("CXX", "c++"))
        if not cxx:
            ap.error("empty CXX")
        with tempfile.TemporaryDirectory(prefix="nava-bench-") as temp:
            temp = Path(temp)
            specs = [
                ("Nava", ROOT / "bench/loop.nv", [sys.executable, str(ROOT / "nava"), "build", str(ROOT / "bench/loop.nv"), "-o", str(temp / "nava")], temp / "nava"),
                ("C++ checked", ROOT / "bench/loop_checked.cpp", cxx + ["-std=c++17", "-O2", str(ROOT / "bench/loop_checked.cpp"), "-o", str(temp / "checked")], temp / "checked"),
                ("C++ unchecked", ROOT / "bench/loop_unchecked.cpp", cxx + ["-std=c++17", "-O2", str(ROOT / "bench/loop_unchecked.cpp"), "-o", str(temp / "unchecked")], temp / "unchecked"),
            ]
            compile_times = {}
            for name, source, command, binary in specs:
                start = time.perf_counter_ns()
                result = subprocess.run(command, capture_output=True, text=True, timeout=60)
                compile_times[name] = (time.perf_counter_ns() - start) / 1_000_000
                if result.returncode:
                    raise RuntimeError(f"compiling {name} failed:\n{result.stderr}")
                for n in (0, 1, 100):
                    actual, _ = run_binary(binary, n)
                    if actual != reference(n):
                        raise RuntimeError(f"{name} failed the {n}-iteration reference check")
            values = {name: [] for name, *_ in specs}
            expected = None
            for trial in range(args.samples):
                # Rotate order to reduce systematic warm-up/order bias.
                ordered = specs[trial % len(specs):] + specs[:trial % len(specs)]
                for name, source, command, binary in ordered:
                    output, elapsed = run_binary(binary, args.iterations)
                    if expected is None:
                        expected = output
                    if output != expected:
                        raise RuntimeError(f"{name} produced a different output")
                    values[name].append(elapsed)
            print(f"iterations={args.iterations}, samples={args.samples}, output={expected.strip()}")
            print("Times in ms; run includes startup and I/O. Compiler: -O2. Smaller is better.")
            headings = f"{'program':<16} {'source bytes':>12} {'compile ms':>12} {'run median':>12} {'run min':>10} {'run max':>10}"
            if encoder:
                headings += f" {'source tokens':>14}"
            print(headings)
            for name, source, command, binary in specs:
                text = source.read_text(encoding="utf-8")
                timings = values[name]
                line = f"{name:<16} {len(text.encode('utf-8')):>12} {compile_times[name]:>12.2f} {statistics.median(timings):>12.2f} {min(timings):>10.2f} {max(timings):>10.2f}"
                if encoder:
                    line += f" {len(encoder.encode(text)):>14}"
                print(line)
            print("Note: checked C++ includes overflow helpers; unchecked C++ is equivalent only for this bounded workload.")
            print("Source bytes are NOT LLM tokens; this script does not measure generation speed or accuracy.")
    except (OSError, subprocess.TimeoutExpired, RuntimeError) as exc:
        print(f"benchmark failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
