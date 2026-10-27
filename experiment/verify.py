#!/usr/bin/env python3
"""Verify both GCD implementations against the same independent reference."""
from __future__ import annotations

import math
import os
import random
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent


def main() -> int:
    try:
        cc = shlex.split(os.environ.get("CC", "cc"))
        if not cc:
            raise ValueError("empty CC")
        with tempfile.TemporaryDirectory(prefix="nava-c-verify-") as temp:
            nava = Path(temp) / "nava_bin"
            c = Path(temp) / "c_bin"
            commands = [
                [sys.executable, str(ROOT / "nava"), "build", str(HERE / "gcd.nv"), "-o", str(nava), "--opt", "2"],
                cc + ["-std=c11", "-O2", str(HERE / "gcd.c"), "-o", str(c)],
            ]
            for command in commands:
                result = subprocess.run(command, capture_output=True, text=True, timeout=30)
                if result.returncode:
                    raise RuntimeError("compilation failed:\n" + result.stderr)
            cases = [
                (0, 0), (0, 1), (48, 18), (270, 192),
                (0, 1_000_000_000), (999_999_937, 1_000_000_000), (999_999_937, 999_999_937),
            ]
            rng = random.Random(20261027)
            cases += [(rng.randrange(1_000_000_001), rng.randrange(1_000_000_001)) for _ in range(100)]
            for a, b in cases:
                expected = f"{math.gcd(a, b)}\n"
                for label, binary in (("Nava", nava), ("C", c)):
                    result = subprocess.run([str(binary)], input=f"{a} {b}\n", capture_output=True, text=True, timeout=2)
                    if result.returncode or result.stdout != expected:
                        raise RuntimeError(f"{label} failed for {a}, {b}: {result.stdout!r} {result.stderr!r}")
        for name in ("gcd.nv", "gcd.c"):
            data = (HERE / name).read_bytes()
            print(f"{name}: {len(data)} bytes, {len(data.decode('utf-8'))} characters")
        print(f"Nava and C passed {len(cases)} of {len(cases)} reference tests.")
        return 0
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        print(f"Verification failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
