"""`f64` semantics (PLAN.md phase N5).

The point of this gate is that floating-point behaviour is *stated* and then
checked, not assumed:

1. **Printing** — the documented rule is "shortest form that round-trips, fixed
   notation between decimal exponents -4 and 15, `.0` so a float never looks
   like an integer". Python's `repr` implements exactly that rule, so every
   produced value is compared against it for a wide table of numbers including
   subnormals, `DBL_MAX`, `-0.0` and values at the notation boundary.
2. **Arithmetic and comparison** — checked against Python's IEEE-754 results,
   including `inf`, `-inf`, `nan` and the NaN comparison rules.
3. **Conversions** — exact where documented, rounding where documented, and a
   deterministic runtime error instead of undefined behaviour where the value
   does not fit. `-fsanitize=undefined` would report a bare C cast.
4. **The type boundary** — no implicit `i64`/`f64` conversion, no `f64` arrays,
   no `%` on floats, no float condition.
5. **Input and locale** — `read_f64` accepts only decimal forms and the program
   never calls `setlocale`, so `.` stays the decimal separator.
6. **Documentation** — the examples in `LANGUAGE.md` print what the document
   claims.
"""
from __future__ import annotations

import math
import os
import pathlib
import random
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

from nava_lang.cli import compile_source, frontend  # noqa: E402
from nava_lang.model import NavaError  # noqa: E402
from spec_example import documented_output  # noqa: E402

CC = shutil.which("cc")
SANITIZER_FLAGS = ("-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-g")

#: Values that make the documented formatting rule visible: both notation
#: boundaries, exact and inexact decimals, subnormals, and the extremes.
FORMAT_VALUES = [
    0.0, -0.0, 1.0, -1.0, 0.5, 7.0, 100.0, 1234.5, 0.1, 0.2, 0.1 + 0.2,
    1.0 / 3.0, 2.0 / 3.0, math.pi,
    1e15, 1e16, 1e21, 1e22, 1e300, 1e-4, 1e-5, 1e-7, 1.5e-7, 1e-300,
    2.0**53, 2.0**53 + 2, 9_999_999_999_999_998.0, 1.7976931348623157e308,
    5e-324, 2.2250738585072014e-308, 0.0001234, 123456789.0,
]


def build(source: str, extra=(), timeout: int = 300):
    temp = tempfile.mkdtemp()
    c_file = pathlib.Path(temp) / "program.c"
    binary = pathlib.Path(temp) / "program"
    c_file.write_text(frontend(source), encoding="utf-8")
    built = subprocess.run(
        [CC, "-std=c11", "-pedantic-errors", "-O2", *extra, str(c_file), "-o", str(binary)],
        capture_output=True, text=True, timeout=timeout,
    )
    if built.returncode != 0:
        raise AssertionError(f"the generated C did not compile:\n{built.stderr}")
    return binary


def run_nava(source: str, stdin: str = "", extra=(), timeout: int = 120, env=None):
    with tempfile.TemporaryDirectory() as temp:
        c_file = pathlib.Path(temp) / "program.c"
        binary = pathlib.Path(temp) / "program"
        c_file.write_text(frontend(source), encoding="utf-8")
        built = subprocess.run(
            [CC, "-std=c11", "-pedantic-errors", "-O2", *extra, str(c_file), "-o", str(binary)],
            capture_output=True, text=True, timeout=300,
        )
        if built.returncode != 0:
            raise AssertionError(f"the generated C did not compile:\n{built.stderr}")
        return subprocess.run([str(binary)], input=stdin, capture_output=True,
                              text=True, timeout=timeout, env=env)


class FloatFrontendTests(unittest.TestCase):
    def assert_code(self, code: str, source: str, message: str = ""):
        with self.assertRaises(NavaError) as caught:
            compile_source(source)
        self.assertEqual(caught.exception.code, code, caught.exception.message)
        if message:
            self.assertIn(message, caught.exception.message)

    def test_no_implicit_conversion(self):
        self.assert_code("E0345", "fn main() { let a = 1.5; print(a + 1) }",
                         "cannot mix f64 and i64")
        self.assert_code("E0345", "fn main() { print(1 + 2.5) }")
        self.assert_code("E0345", "fn main() { let a = 1.5; print(a < 2) }")
        self.assert_code("E0311", "fn main() { let a: f64 = 1 }", "expected f64, got i64")
        self.assert_code("E0311", "fn main() { let a: i64 = 1.5 }", "expected i64, got f64")
        self.assert_code("E0327", "fn f(x: f64) {} fn main() { f(1) }", "argument 1")

    def test_float_is_not_an_integer_type(self):
        self.assert_code("E0319", "fn main() { print(1.5 % 1.0) }", "defined for i64 only")
        self.assert_code("E0316", "fn main() { if 1.5 {} }", "if condition must be bool")
        self.assert_code("E0317", "fn main() { while 1.5 {} }", "while condition must be bool")
        self.assert_code("E0349", "fn main() { for i in 0.0..2.0 {} }", "bound must be i64")
        self.assert_code("E0203", "fn main() { let a: [f64] = [1.0] }", "must be i64, bool, or str")

    def test_literal_rules(self):
        self.assert_code("E0346", "fn main() { print(1e999) }", "outside the f64 range")
        self.assert_code("E0346", "fn main() { print(2.5e) }", "malformed")
        self.assert_code("E0346", "fn main() { print(0." + "1" * 41 + ") }", "too long")
        # `1.` is not a literal; the point is not part of the number.
        with self.assertRaises(NavaError) as caught:
            compile_source("fn main() { print(1.) }")
        self.assertEqual(caught.exception.code, "E0101")
        # A range is two integers separated by `..`, never a float.
        compile_source("fn main() { for i in 1..3 { print(i) } }")

    def test_conversion_arguments(self):
        self.assert_code("E0347", "fn main() { print(to_f64(1, 2)) }")
        self.assert_code("E0347", "fn main() { print(to_f64(1.5 - 1.5)) }", "expects i64")
        self.assert_code("E0347", "fn main() { print(to_i64(1)) }", "expects f64")
        self.assert_code("E0348", "fn main() { print(read_f64(1)) }")

    def test_floats_work_where_values_are_expected(self):
        compile_source('''fn scale(x: f64) -> f64 { return x * 2.0 }
fn pick(flag: bool) -> f64 { if flag { return 1.5 } else { return 0.5 } }
fn main() {
    let a = 1.5
    a = a + 1.0
    print(scale(a))
    print(pick(a > 1.0))
    print(-a)
    print(a == 2.5)
    print(a != 2.5)
}''')


@unittest.skipUnless(CC, "a C compiler is required for float runtime tests")
class FloatRuntimeTests(unittest.TestCase):
    def test_printing_matches_python_repr(self):
        """The documented format is exactly Python's `repr` for a float."""
        source = "fn main() {\n" + "".join(f"    print({repr(v)})\n" for v in FORMAT_VALUES) + "}\n"
        result = run_nava(source)
        self.assertEqual(result.returncode, 0, result.stderr)
        produced = result.stdout.splitlines()
        self.assertEqual(len(produced), len(FORMAT_VALUES))
        for value, text in zip(FORMAT_VALUES, produced):
            with self.subTest(value=repr(value)):
                self.assertEqual(text, repr(value))

    def test_printing_random_values_matches_python_repr(self):
        """A wider sweep, including values whose digits do not round-trip at 17."""
        rng = random.Random(20261027)
        values = [rng.uniform(-1e6, 1e6) for _ in range(40)]
        values += [rng.uniform(-1e-7, 1e-7) for _ in range(20)]
        values += [rng.uniform(-1e18, 1e18) for _ in range(20)]
        source = ("fn main() {\n" + "".join(f"    let v{i} = {repr(v)}\n    print(v{i})\n"
                                           for i, v in enumerate(values)) + "}\n")
        result = run_nava(source)
        self.assertEqual(result.returncode, 0, result.stderr)
        for value, text in zip(values, result.stdout.splitlines()):
            with self.subTest(value=repr(value)):
                self.assertEqual(text, repr(value))

    def test_arithmetic_matches_python(self):
        values = [(1.5, 0.25), (0.1, 0.2), (1e300, 1e-300), (-3.5, 2.0), (7.0, -0.5)]
        for left, right in values:
            with self.subTest(left=left, right=right):
                result = run_nava(f'''fn main() {{
    print({repr(left)} + {repr(right)})
    print({repr(left)} - {repr(right)})
    print({repr(left)} * {repr(right)})
    print({repr(left)} / {repr(right)})
    print(-{repr(left)})
}}''')
                self.assertEqual(result.returncode, 0, result.stderr)
                expected = [left + right, left - right, left * right, left / right, -left]
                self.assertEqual(result.stdout.splitlines(), [repr(v) for v in expected])

    def test_exceptional_values_and_nan_comparisons(self):
        result = run_nava('''fn main() {
    let zero = 0.0
    print(1.0 / zero)
    print(-1.0 / zero)
    let none = zero / zero
    print(none)
    print(none == none)
    print(none != none)
    print(none < 0.0)
    print(none <= 0.0)
    print(none > 0.0)
    print(none >= 0.0)
    print(1.0e300 * 1.0e300)
    print(-1.0e300 * 1.0e300)
    print(none + 1.0)
}''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout,
                         "inf\n-inf\nnan\nfalse\ntrue\nfalse\nfalse\nfalse\nfalse\n"
                         "inf\n-inf\nnan\n")

    def test_rounding_is_round_to_nearest_even(self):
        result = run_nava('''fn main() {
    print(0.1 + 0.2)
    print(0.1 + 0.2 == 0.30000000000000004)
    print(1.0 / 3.0)
    print(2.0 / 3.0)
    print(1.0 + 0.5)
    print(0.5 + 0.25)
    print(1.0e16 + 1.0)
    print(1.0e16 + 2.0)
}''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "0.30000000000000004\ntrue\n"
                                        f"{repr(1.0 / 3.0)}\n{repr(2.0 / 3.0)}\n"
                                        "1.5\n0.75\n1e+16\n1.0000000000000002e+16\n")

    def test_int_to_float_conversion_is_exact_then_rounds(self):
        result = run_nava('''fn main() {
    print(to_f64(0))
    print(to_f64(-1))
    print(to_f64(9007199254740992))
    print(to_f64(9007199254740993))
    print(to_f64(9007199254740995))
    print(to_f64(-9223372036854775808))
    print(to_f64(9223372036854775807))
}''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), [
            repr(float(0)), repr(float(-1)), repr(float(2**53)), repr(float(2**53 + 1)),
            repr(float(2**53 + 3)), repr(float(-(2**63))), repr(float(2**63 - 1)),
        ])

    def test_float_to_int_truncates_and_refuses_the_rest(self):
        # 9223372036854775807 is not representable as an f64: the nearest value
        # is 2^63, which is out of range, so the literal itself must fail. The
        # accepted maximum is the largest representable f64 below 2^63.
        accepted = [("2.9", 2), ("-2.9", -2), ("0.5", 0), ("-0.5", 0), ("-0.0", 0),
                    ("9223372036854774784.0", 2**63 - 1024), ("-9223372036854775808.0", -(2**63))]
        for literal, expected in accepted:
            with self.subTest(literal=literal):
                result = run_nava(f"fn main() {{ print(to_i64({literal})) }}")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, f"{expected}\n")

        for literal, message in (("9223372036854775808.0", "outside the i64 range"),
                                 ("9223372036854775807.0", "outside the i64 range"),
                                 ("-1.0e300", "outside the i64 range"),
                                 ("1.0e308", "outside the i64 range")):
            with self.subTest(literal=literal):
                result = run_nava(f"fn main() {{ print(to_i64({literal})) }}")
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, "")
                self.assertIn(message, result.stderr)

        nan = run_nava("fn main() { let z = 0.0; print(to_i64(z / z)) }")
        self.assertEqual(nan.returncode, 1)
        self.assertIn("to_i64 of NaN", nan.stderr)

    def test_conversion_has_no_undefined_behaviour(self):
        """Under UBSan, a bare cast to int64_t would be reported, ours is checked."""
        program = '''fn main() {
    let a = 9223372036854775808.0
    print(to_i64(a))
}'''
        result = run_nava(program, extra=SANITIZER_FLAGS)
        self.assertEqual(result.returncode, 1)
        self.assertIn("outside the i64 range", result.stderr)
        self.assertNotIn("runtime error: nan is outside", result.stderr)
        self.assertNotIn("float-cast-overflow", result.stderr)

    def test_float_programs_are_sanitizer_clean(self):
        program = '''fn mean(a: f64, b: f64) -> f64 {
    return (a + b) / 2.0
}

fn main() {
    let total = 0.0
    for i in 0..50 {
        let scaled = to_f64(i) * 0.5
        if i == 40 {
            break
        }
        total = total + mean(scaled, scaled * 2.0)
    }
    print(total)
    print(to_i64(total))
    print(to_i64(-total))
}'''
        result = run_nava(program, extra=SANITIZER_FLAGS)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("AddressSanitizer", result.stderr)
        self.assertNotIn("runtime error", result.stderr)

    def test_read_f64_accepts_only_decimal_forms(self):
        source = "fn main() { print(read_f64() + read_f64()) }"
        for text, expected in (("1.5 0.25\n", "1.75\n"), ("1 2\n", "3.0\n"),
                               ("-2.5 2.5\n", "0.0\n"), ("+3.75 0.25\n", "4.0\n"),
                               (".5 .5\n", "1.0\n"), ("5. 1.\n", "6.0\n"),
                               ("1e3 1e-3\n", "1000.001\n"), ("1E2 0\n", "100.0\n")):
            with self.subTest(text=text.strip()):
                result = run_nava(source, stdin=text)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, expected)

        for text, message in (("", "reached end of input"), ("abc\n1\n", "expected a decimal f64"),
                              ("1.5x\n1\n", "invalid character"), ("inf\n1\n", "expected a decimal f64"),
                              ("nan\n1\n", "expected a decimal f64"),
                              ("0x1.8p1\n1\n", "invalid character"), ("1e\n1\n", "malformed exponent"),
                              ("1,5\n1\n", "invalid character"),
                              ("1e999\n1\n", "outside the f64 range")):
            with self.subTest(text=text.strip() or "empty"):
                result = run_nava(source, stdin=text)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, "")
                self.assertIn(message, result.stderr)

    def test_input_and_output_are_locale_independent(self):
        """Nava never calls setlocale, so `.` stays the separator."""
        generated = frontend("fn main() { let a = read_f64(); print(a) }")
        # The runtime mentions setlocale only in a comment; a call would need
        # parentheses, and that would be the bug this test exists to catch.
        self.assertNotIn("setlocale(", generated)
        self.assertIn('strtod', generated)
        for locale in ("de_DE.UTF-8", "ru_RU.UTF-8", "fr_FR.UTF-8"):
            with self.subTest(locale=locale):
                environment = dict(os.environ, LC_ALL=locale, LC_NUMERIC=locale, LANG=locale)
                result = run_nava("fn main() { let a = read_f64(); print(a * 1.5) }",
                                  stdin="2.5\n", env=environment)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "3.75\n")

    def test_documented_float_output_is_real(self):
        for marker in ("to_i64(2.9)", "print(none !="):
            with self.subTest(marker=marker):
                source, expected = documented_output(marker)
                result = run_nava(source)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, expected)


if __name__ == "__main__":
    unittest.main()
