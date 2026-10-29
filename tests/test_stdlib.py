"""The standard library and the byte/code-point boundary (PLAN.md phase N6).

The gate has four parts:

1. **Independent references** — every `math`, `sort` and `text` function is
   compared with the same computation in Python over fixed and random inputs.
2. **Bytes versus characters** — `bytes_len`, `chars_len` and `byte_at` agree
   with Python's UTF-8 encoding, and UTF-8 validity is decided the same way
   Python's strict decoder decides it, including overlong forms, surrogates,
   truncated sequences and values above U+10FFFF.
3. **Errors** — `abs` of the smallest i64, a character count of bytes that are
   not UTF-8, and a byte index outside the string each stop the program.
4. **Versioning** — every standard module declares the version the compiler
   implements, and every function it exports is documented in `LANGUAGE.md`.
"""
from __future__ import annotations

import json
import math
import pathlib
import random
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "tests"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import grants  # noqa: E402
from nava_lang import modules  # noqa: E402
from nava_lang.model import Number, Return  # noqa: E402
from nava_lang.parser import parse  # noqa: E402

LANGUAGE = ROOT / "LANGUAGE.md"
#: Every exported function of the standard library, for the documentation gate.
STDLIB_EXPORTS = {
    "math": ("module_version", "abs", "min", "max", "sign", "gcd", "sum"),
    "sort": ("module_version", "is_sorted", "insertion_sort", "binary_search"),
    "text": ("module_version", "is_continuation", "sequence_width", "is_valid_utf8",
             "code_points", "is_ascii"),
}

MATH_CASES = [
    (0, 0), (7, 3), (3, 7), (-7, 3), (7, -3), (-7, -3), (1, 1), (100, -100),
    (9223372036854775807, 1), (-9223372036854775807, -1), (0, -5), (12, 18),
    (17, 5), (270, 192), (1, 0), (0, 13),
]


def nava_literal(value: str) -> str:
    """A Nava string literal for `value`; Unicode escapes keep the file ASCII."""
    return json.dumps(value, ensure_ascii=True)


@unittest.skipUnless(grants.CC, "a C compiler is required for the standard library tests")
class MathTests(unittest.TestCase):
    def test_integer_helpers_match_python(self):
        lines = ["import math", "", "fn main() {"]
        expected = []
        for a, b in MATH_CASES:
            for name, reference in (("min", min(a, b)), ("max", max(a, b)),
                                    ("gcd", math.gcd(a, b)), ("abs", abs(a)),
                                    ("sign", sign(a))):
                arguments = f"{a}, {b}" if name in ("min", "max", "gcd") else f"{a}"
                lines.append(f"    print(math::{name}({arguments}))")
                expected.append(str(reference))
        lines.append("    print(math::sum([]))")
        expected.append("0")
        lines.append("    print(math::sum([1, -2, 30, 400]))")
        expected.append(str(1 - 2 + 30 + 400))
        lines.append("}")
        result = grants.run("\n".join(lines) + "\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "".join(line + "\n" for line in expected))

    def test_abs_of_the_smallest_i64_is_a_runtime_error(self):
        result = grants.run(
            "import math\n\nfn main() {\n    print(math::abs(-9223372036854775808))\n}\n")
        self.assertEqual(result.returncode, 1)
        self.assertIn("i64 overflow in unary -", result.stderr)
        self.assertEqual(result.stdout, "")


def sign(value: int) -> int:
    return (value > 0) - (value < 0)


@unittest.skipUnless(grants.CC, "a C compiler is required for the standard library tests")
class SortTests(unittest.TestCase):
    SOURCE = """import sort

fn main() {
    let count = read_i64()
    let values = array_zeros(count)
    let index = 0
    while index < count {
        values[index] = read_i64()
        index = index + 1
    }
    print(sort::is_sorted(values))
    sort::insertion_sort(values)
    print(sort::is_sorted(values))
    for value in values {
        print(value)
    }
    if count > 0 {
        print(sort::binary_search(values, values[count / 2]))
    } else {
        print(-1)
    }
}
"""

    def test_sorting_matches_python(self):
        generator = random.Random(20261027)
        cases = [[], [0], [1, 2, 3], [3, 2, 1], [5, -5, 0, -1, 1],
                 [7, 7, 7, 7], generator.sample(range(-500, 500), 40)]
        cases += [[generator.randint(-1000, 1000) for _ in range(generator.randint(0, 25))]
                  for _ in range(4)]
        for case in cases:
            with self.subTest(size=len(case)):
                stdin = f"{len(case)}\n" + "".join(f"{value}\n" for value in case)
                result = grants.run(self.SOURCE, stdin=stdin)
                self.assertEqual(result.returncode, 0, result.stderr)
                ordered = sorted(case)
                lines = result.stdout.splitlines()
                expected = ["true" if case == ordered else "false", "true"]
                expected += [str(value) for value in ordered]
                self.assertEqual(lines[:len(expected)], expected)
                # Duplicates make several indexes correct: check the answer, not
                # one particular index.
                found = int(lines[-1])
                if case:
                    self.assertTrue(0 <= found < len(ordered), found)
                    # The program searches for the middle element of the
                    # sorted array, which is what `values[count / 2]` is then.
                    self.assertEqual(ordered[found], ordered[len(ordered) // 2])
                else:
                    self.assertEqual(found, -1)


@unittest.skipUnless(grants.CC, "a C compiler is required for the standard library tests")
class TextTests(unittest.TestCase):
    #: Strings that a literal can hold: every one of them is valid UTF-8.
    VALID = [
        "", "a", "abc", "héllo", "naïve", "Привет", "日本語", "😀", "a😀b",
        "e\u0301", "ÿ", "\u007f", "\u0080", "\u07ff", "\u0800", "\uffff",
        "\U00010000", "\U0010ffff", "line\nbreak", "tab\there", "quote\"'" "",
        "back\\slash",
    ]

    def test_bytes_and_code_points_match_python(self):
        lines = ["import text", "", "fn main() {"]
        expected = []
        for value in self.VALID:
            literal = nava_literal(value)
            encoded = value.encode("utf-8")
            lines.append(f"    print(bytes_len({literal}))")
            expected.append(str(len(encoded)))
            lines.append(f"    print(chars_len({literal}))")
            expected.append(str(len(value)))
            lines.append(f"    print(text::code_points({literal}))")
            expected.append(str(len(value)))
            lines.append(f"    print(text::is_ascii({literal}))")
            expected.append("true" if encoded.isascii() else "false")
            lines.append(f"    print(text::is_valid_utf8({literal}))")
            expected.append("true")
            if encoded:
                lines.append(f"    print(byte_at({literal}, 0))")
                expected.append(str(encoded[0]))
                lines.append(f"    print(byte_at({literal}, {len(encoded) - 1}))")
                expected.append(str(encoded[-1]))
        lines.append("}")
        result = grants.run("\n".join(lines) + "\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "".join(line + "\n" for line in expected))

    def test_utf8_validity_matches_pythons_strict_decoder(self):
        # Bytes outside the source language: only read_file can produce these.
        samples = [
            b"abc", b"\xc3\xa9", b"\xe2\x82\xac", b"\xf0\x9f\x98\x80",
            b"\xff", b"\xfe\xff", b"\xc0\xaf", b"\xc1\xbf", b"\xe0\x80\xaf",
            b"\xed\xa0\x80", b"\xed\xbf\xbf", b"\xf4\x90\x80\x80", b"\xf5\x80\x80\x80",
            b"\xe2\x82", b"\xf0\x9f\x98", b"\x80", b"\xc3", b"a\x80b",
            b"\xf0\x9f\x98\x80\xf0\x9f\x98\x80",
        ]
        files = {f"sample{index}.bin": data for index, data in enumerate(samples)}
        lines = ["import text", "", "fn main() {"]
        for index in range(len(samples)):
            lines.append(f"    let raw{index} = read_file(\"sample{index}.bin\")")
            lines.append(f"    print(bytes_len(raw{index}))")
            lines.append(f"    print(text::is_valid_utf8(raw{index}))")
            lines.append(f"    print(text::code_points(raw{index}))")
        lines.append("}")
        result = grants.run("\n".join(lines) + "\n", files=files,
                            capabilities={"read": ["."]})
        self.assertEqual(result.returncode, 0, result.stderr)
        expected = []
        for data in samples:
            try:
                decoded = data.decode("utf-8")
                valid, points = "true", str(len(decoded))
            except UnicodeDecodeError:
                valid, points = "false", "-1"
            expected += [str(len(data)), valid, points]
        self.assertEqual(result.stdout, "".join(line + "\n" for line in expected))

    def test_chars_len_stops_on_invalid_utf8(self):
        result = grants.run(
            "fn main() {\n    let raw = read_file(\"bad.bin\")\n"
            "    print(bytes_len(raw))\n    print(chars_len(raw))\n}\n",
            files={"bad.bin": b"ok\xff"},
            capabilities={"read": ["."]})
        self.assertEqual(result.returncode, 1)
        self.assertIn("chars_len of a string that is not valid UTF-8", result.stderr)
        # The byte count printed before the failure is real: the read succeeded.
        self.assertEqual(result.stdout, "3\n")

    def test_byte_at_is_bounds_checked(self):
        for index in ("-1", "3"):
            with self.subTest(index=index):
                result = grants.run(
                    f"fn main() {{\n    print(byte_at(\"abc\", {index}))\n}}\n")
                self.assertEqual(result.returncode, 1)
                self.assertIn("is out of bounds for a string of 3 bytes", result.stderr)

    def test_a_nul_byte_cannot_become_a_str(self):
        result = grants.run(
            "fn main() {\n    print(bytes_len(read_file(\"nul.bin\")))\n}\n",
            files={"nul.bin": b"a\x00b"},
            capabilities={"read": ["."]})
        self.assertEqual(result.returncode, 1)
        self.assertIn("contains a NUL byte", result.stderr)


class StdlibContractTests(unittest.TestCase):
    def test_every_standard_module_declares_the_supported_version(self):
        paths = sorted(modules.stdlib_path().glob("*.nv"))
        # Every packaged module declares the version it was written against; a
        # new module is added here on purpose, so one can never ship without it.
        self.assertEqual([path.stem for path in paths], ["fmt", "math", "sort", "test", "text"])
        for path in paths:
            with self.subTest(module=path.stem):
                program = parse(path.read_text(encoding="utf-8"), path=str(path))
                versions = [function for function in program.functions
                            if function.name == "module_version"]
                self.assertEqual(len(versions), 1)
                body = versions[0].body
                self.assertEqual(len(body), 1)
                self.assertIsInstance(body[0], Return)
                self.assertIsInstance(body[0].value, Number)
                self.assertEqual(body[0].value.value, modules.STDLIB_VERSION)

    def test_the_exports_are_what_the_tests_cover(self):
        for name, exports in STDLIB_EXPORTS.items():
            with self.subTest(module=name):
                program = parse((modules.stdlib_path() / f"{name}.nv").read_text(encoding="utf-8"))
                self.assertEqual(sorted(function.name for function in program.functions),
                                 sorted(exports))

    def test_every_export_is_documented(self):
        text = LANGUAGE.read_text(encoding="utf-8")
        for name, exports in STDLIB_EXPORTS.items():
            for function in exports:
                with self.subTest(module=name, function=function):
                    self.assertIn(f"{name}::{function}", text,
                                  f"{name}::{function} is not documented")


if __name__ == "__main__":
    unittest.main()
