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

#: Largest file `read_file` accepts, in bytes.  Exceeding it is a runtime error,
#: never a truncated read.
MAX_FILE_BYTES = 1024 * 1024

#: Total bytes one program may read with `read_file` across all calls.  The
#: returned text lives until the process exits, so the total is what bounds
#: memory use, not the number of calls.
MAX_READ_TOTAL_BYTES = 4 * 1024 * 1024

#: Most `read_file` calls in one run; a companion bound to the total above.
MAX_READ_CALLS = 64

#: Most entries `list_dir` will accept in one directory.  A listing is a copy of
#: names into the arena, so the count is what bounds the memory a call can take;
#: exceeding it is a runtime error rather than a truncated listing, because a
#: short listing looks like a short directory.
MAX_DIR_ENTRIES = 4096

I64_MIN = -(2**63)
I64_MAX = 2**63 - 1

#: Isolation modes accepted by `nava run`.  They live here, next to the other
#: documented limits, so that parsing the command line does not have to import
#: `nava_lang/sandbox.py` (and with it `subprocess`, `tempfile` and `shutil`):
#: `bench/profile.py` shows imports dominating the start-up of a small build.
ISOLATION_MODES = ("off", "auto", "required")
