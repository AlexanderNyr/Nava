"""Documented implementation limits.

These values are part of the specification: LANGUAGE.md states them, the front
end enforces them and `tests/test_spec.py` fails if the two disagree.  A limit
exists so that pathological input produces a diagnostic instead of a Python
traceback; it is not a tuning knob.
"""
from __future__ import annotations

#: Maximum nesting counted per construct: function bodies and blocks,
#: parenthesised subexpressions, unary operator chains, and the depth of the
#: expression tree (which also bounds long left-associative chains such as
#: `1 + 1 + 1 + ...`).  Exceeding it raises E0401.
MAX_NESTING = 200

#: Longest run of decimal digits allowed before the literal is rejected as out
#: of range (i64 needs at most 19).  Raised as E0108.
MAX_LITERAL_DIGITS = 19

#: Maximum number of elements in one array, checked by the generated runtime
#: before allocating.  Exceeding it is a runtime error, never a truncation.
#: LANGUAGE.md states the value and `tests/test_spec.py` compares the two.
MAX_ARRAY_ELEMENTS = 4 * 1024 * 1024

#: Most decimal digits accepted in one floating-point literal, counting both
#: sides of the point and the exponent.  Exceeding it raises E0346.
MAX_FLOAT_DIGITS = 40

I64_MIN = -(2**63)
I64_MAX = 2**63 - 1
