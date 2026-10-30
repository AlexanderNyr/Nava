#!/usr/bin/env python3
"""A corpus of `f64` literals for the two backends' differential (INDEPENDENCE.md N14j).

Printing an `f64` is the one place where the native backend has to reproduce the C
library's answer rather than its own idea of one: `nv_format_f64` in `cgen.py` asks
`snprintf` for the shortest `%.*e`/`%.*f` text that `strtod` reads back as the same
double, and the native runtime answers the same question with integers only. Two
implementations of one rule is exactly the shape of thing that agrees on the
fixtures and disagrees on the four-thousandth value, so the differential is run on
a corpus, not on a sample.

The corpus is deterministic for a given seed and is built out of four families,
because each has its own way of being wrong:

* every power of two in the range the format has (``2**-1074`` … ``2**1023``), its
  negation and both of its neighbours — a power of two is the case where the lower
  gap is *half* the upper one, and where the shortest text very often sits exactly
  on a midpoint;
* the curated edges: ``5e-324`` (751 digits of exact expansion, five of text),
  ``1.7976931348623157e308``, the smallest normal, ``0.1 + 0.2``, the value whose
  nearest sixteen-digit decimal does *not* read back (``2**-1017``);
* uniformly random 64-bit patterns, filtered to the finite values — this is where
  the odd significand and the twelve-digit exponent turn up;
* uniformly random 52-bit patterns, which are all subnormal: a uniform exponent
  field is 1 in 2048 normal, so subnormals need their own family.

Usage::

    python3 tools/f64_corpus.py > /tmp/f64_corpus.nv
    python3 tools/f64_corpus.py --seed 7 --random 5000 > /tmp/f64_corpus.nv

Then the differential is the two runs and their difference::

    python3 nava run --backend c      /tmp/f64_corpus.nv > /tmp/c.out
    python3 nava run --backend native /tmp/f64_corpus.nv > /tmp/native.out
    cmp /tmp/c.out /tmp/native.out
"""
from __future__ import annotations

import argparse
import math
import random
import struct


def finite(value: float) -> bool:
    """The corpus is the finite values; `nan` and the two infinities are expressions."""
    return value == value and value not in (float("inf"), float("-inf"))


def curated() -> list[float]:
    """Values whose answer was argued about at least once."""
    values = [0.0, -0.0, 5e-324, 1e-323, 2.2250738585072014e-308,
              1.7976931348623157e308, 1e-7, 1e-5, 1e16, 1e15, 1e-4, 1e-3, 0.0001,
              0.00001, 0.03, 0.1 + 0.2, 1.5, 1.75, 0.375, 1 / 3, 100.0, 1.0, -1.0,
              2.5, 12.5, 9007199254740992.0, 123456789012345.6, 2.0 ** 60,
              2.0 ** -1017, 1e300, 1e-300, 4.9406564584124654e-324,
              1.7881393432617188e-07, 3.3201876582270392e16, -3.3201876582270392e16]
    return [value for value in values if finite(value)]


def powers() -> list[float]:
    """Every power of two the format has, with both neighbours and the negations."""
    values: list[float] = []
    for exponent in range(-1074, 1024):
        value = 2.0 ** exponent
        if not finite(value) or value == 0.0:
            continue
        values.extend([value, -value,
                       math.nextafter(value, 0.0), math.nextafter(value, float("inf"))])
    return values


def patterns(seed: int, random_count: int, subnormal_count: int) -> list[float]:
    """Random bit patterns: sixty-four bits, and fifty-two (which is all subnormal)."""
    generator = random.Random(seed)
    values: list[float] = []
    while len(values) < random_count:
        value = struct.unpack("<d", struct.pack("<Q", generator.getrandbits(64)))[0]
        if finite(value):
            values.append(value)
    for _ in range(subnormal_count):
        value = struct.unpack("<d", struct.pack("<Q", generator.getrandbits(52)))[0]
        values.extend([value, -value])
    return values


def program(values: list[float]) -> str:
    """One program that prints every value, with the three names as expressions."""
    lines = ["edition 2;", "", "fn main() -> i64 {"]
    lines.append("    let zero = 0.0")
    lines.append("    print(1.0 / zero)")
    lines.append("    print(-1.0 / zero)")
    lines.append("    print(zero / zero)")
    for value in values:
        lines.append(f"    print({value!r})")
    lines.append("    return 0")
    lines.append("}")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seed", type=int, default=4242, help="the random families' seed")
    parser.add_argument("--random", type=int, default=2500,
                        help="random 64-bit patterns (finite ones are kept)")
    parser.add_argument("--subnormal", type=int, default=700,
                        help="random 52-bit patterns, which are subnormals")
    parser.add_argument("--no-powers", action="store_true",
                        help="skip the 2 098 powers of two and their neighbours")
    arguments = parser.parse_args()
    values = curated()
    if not arguments.no_powers:
        values.extend(powers())
    values.extend(patterns(arguments.seed, arguments.random, arguments.subnormal))
    print(program(values), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
