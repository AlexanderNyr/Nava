"""N11, part two: fields and payloads that own array storage.

One claim, tested from both sides: **a struct field and a variant payload may own
an array, and a value that owns storage is copied when it crosses an ownership
boundary**. The boundary is a `let`, an assignment, a field write, a `match`
binding and a `return`; past any of them the value has its own buffer, so writing
through one value can never be seen through the other. The block that owns a
value releases that buffer when it ends, including on an early `path` out.

The second claim is what is still refused: a value that owns storage may not be
an *array element* (`E0426`). An element is copied on every read and on every
write, so an owning element would need those copies to be deep, which is the
move work of N11e. The refusal is one code, raised from the single place that
checks an element type, so `[Bag]` as a binding, as a parameter, as a literal and
as an `array_zeros` all fail the same way.

The leak counter is the load-bearing control of this file: every runtime test
runs a binary built with `-DNAVA_TRACK_ALLOCS` and asserts that no array outlives
the process. A missing release in any of the new paths shows up as a number
greater than zero rather than as a comment nobody checks. One further test runs a
program that writes through a borrowed value under AddressSanitizer and
UndefinedBehaviorSanitizer, because a release that fires on storage the caller
still owns is a double free that the counter alone cannot see.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nava_lang.cgen import CGenerator  # noqa: E402
from nava_lang.checker import check  # noqa: E402
from nava_lang.cli import compile_source  # noqa: E402
from nava_lang.model import NavaError  # noqa: E402
from nava_lang.parser import parse  # noqa: E402

CC = shutil.which("cc")
#: The same instrument the array gate uses; `tests/registry.py` fails when it is
#: unavailable, so this file cannot pass by skipping.
SANITIZER_FLAGS = ("-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-g")

BAG = ("struct Bag {\n"
       "    items: [i64]\n"
       "}\n")

SHAPE = ("enum Shape {\n"
         "    Dot,\n"
         "    Poly([i64])\n"
         "}\n")

NESTED = ("struct Inner {\n"
          "    xs: [i64]\n"
          "}\n"
          "\n"
          "struct Outer {\n"
          "    inner: Inner,\n"
          "    tag: i64\n"
          "}\n")


def frontend(text: str) -> str:
    """The generated C of a program that has already been checked."""
    return CGenerator(check(parse(text))).generate()


def compile_and_run(text: str, tracked: bool = True,
                    sanitize: bool = False) -> subprocess.CompletedProcess:
    """Build the generated C with the test instrumentation and run it."""
    with tempfile.TemporaryDirectory() as directory:
        c_file = Path(directory) / "program.c"
        binary = Path(directory) / "program"
        c_file.write_text(frontend(text), encoding="utf-8")
        extra = ["-DNAVA_TRACK_ALLOCS"] if tracked else []
        extra += list(SANITIZER_FLAGS) if sanitize else []
        build = subprocess.run([CC, "-std=c11", "-pedantic-errors",
                                "-O1" if sanitize else "-O2", *extra,
                                str(c_file), "-o", str(binary)],
                               capture_output=True, text=True, timeout=300)
        if build.returncode != 0:
            raise AssertionError(f"the generated C did not compile:\n{build.stderr}")
        return subprocess.run([str(binary)], capture_output=True, text=True, timeout=60)


def live_arrays(stderr: str) -> int:
    """The count printed by a `-DNAVA_TRACK_ALLOCS` build."""
    for line in stderr.splitlines():
        if line.startswith("nava: live arrays at exit:"):
            return int(line.rsplit(":", 1)[1])
    raise AssertionError(f"no live-array report in:\n{stderr}")


class OwningFieldFrontendTests(unittest.TestCase):
    """What an owning field may be, and what may not hold an owning value."""

    def assert_code(self, text: str, code: str, message: str | None = None) -> NavaError:
        with self.assertRaises(NavaError) as caught:
            compile_source(text)
        self.assertEqual(caught.exception.code, code, caught.exception.message)
        if message is not None:
            self.assertIn(message, caught.exception.message)
        return caught.exception

    def test_an_array_field_and_an_array_payload_are_values(self):
        source = ("edition 2;\n" + BAG + "\n" + SHAPE + "\n"
                  "fn main() {\n"
                  "    let b: Bag = Bag(items: [1, 2])\n"
                  "    print(b.items[0])\n"
                  "    let s: Shape = Shape::Poly([3])\n"
                  "    match s {\n"
                  "        Shape::Dot => { print(0) }\n"
                  "        Shape::Poly(xs) => { print(len(xs)) }\n"
                  "    }\n"
                  "}\n")
        self.assertTrue(compile_source(source))

    def test_an_owning_value_cannot_be_an_array_element(self):
        """One rule, four shapes, one code, and the message names the type."""
        cases = {
            "binding": ("fn main() {\n"
                        "    let bags: [Bag] = []\n"
                        "    print(len(bags))\n"
                        "}\n"),
            "parameter": ("fn take(bags: [Bag]) {\n"
                          "    print(len(bags))\n"
                          "}\n"
                          "fn main() {\n"
                          "    print(1)\n"
                          "}\n"),
            "literal": ("fn main() {\n"
                        "    let bags = [Bag(items: [1])]\n"
                        "    print(len(bags))\n"
                        "}\n"),
            "zeros": ("fn main() {\n"
                      "    let bags: [Bag] = array_zeros(2)\n"
                      "    print(len(bags))\n"
                      "}\n"),
        }
        for name, body in cases.items():
            with self.subTest(shape=name):
                error = self.assert_code("edition 2;\n" + BAG + "\n" + body,
                                         "E0426", "Bag holds an array")
                self.assertIn("move work of N11e", error.hint)

    def test_an_enum_with_an_owning_payload_cannot_be_an_element_either(self):
        self.assert_code("edition 2;\n" + BAG + "\n"
                         "enum Slot { Empty, Full(Bag) }\n"
                         "fn main() {\n"
                         "    let slots: [Slot] = []\n"
                         "    print(len(slots))\n"
                         "}\n", "E0426")

    def test_an_array_field_is_read_like_any_other_array(self):
        source = ("edition 2;\n" + BAG + "\n"
                  "fn total(xs: [i64]) -> i64 {\n"
                  "    let sum = 0\n"
                  "    for x in xs { sum = sum + x }\n"
                  "    return sum\n"
                  "}\n"
                  "fn main() {\n"
                  "    let b = Bag(items: [1, 2, 3])\n"
                  "    print(b.items[1])\n"
                  "    print(len(b.items))\n"
                  "    print(total(b.items))\n"
                  "    print(total(b.items[1..3]))\n"
                  "    for x in b.items { print(x) }\n"
                  "}\n")
        self.assertTrue(compile_source(source))

    def test_an_array_field_can_be_replaced(self):
        source = ("edition 2;\n" + BAG + "\n"
                  "fn main() {\n"
                  "    let b = Bag(items: [1, 2])\n"
                  "    b.items = [7, 8, 9]\n"
                  "    let other = [4]\n"
                  "    b.items = other\n"
                  "    print(len(b.items))\n"
                  "}\n")
        self.assertTrue(compile_source(source))

    def test_a_nested_owning_field_is_a_value_too(self):
        source = ("edition 2;\n" + NESTED + "\n"
                  "fn main() {\n"
                  "    let o = Outer(inner: Inner(xs: [1, 2]), tag: 7)\n"
                  "    let copy = o\n"
                  "    print(len(copy.inner.xs))\n"
                  "    print(copy.tag)\n"
                  "}\n")
        self.assertTrue(compile_source(source))

    def test_the_generated_c_copies_and_releases_only_where_needed(self):
        """The helpers exist for owning types and for no others."""
        source = ("edition 2;\n" + BAG + "\n"
                  "struct Plain { n: i64 }\n"
                  "fn main() {\n"
                  "    let b = Bag(items: [1])\n"
                  "    let p = Plain(n: 1)\n"
                  "    print(b.items[0] + p.n)\n"
                  "}\n")
        code = frontend(source)
        self.assertIn("static nv_struct_Bag nv_copy_nv_struct_Bag(", code)
        self.assertIn("static void nv_release_nv_struct_Bag(", code)
        self.assertNotIn("nv_copy_nv_struct_Plain", code)
        self.assertNotIn("nv_release_nv_struct_Plain", code)


class OwningFieldRuntimeTests(unittest.TestCase):
    """The copies themselves: independence, and nothing left behind."""

    def run_program(self, text: str) -> subprocess.CompletedProcess:
        result = compile_and_run("edition 2;\n" + text)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def test_a_constructed_field_copies_the_source_array(self):
        """The array the program still holds is not the array inside the value."""
        result = self.run_program(BAG + "\n"
                                  "fn main() {\n"
                                  "    let src = [1, 2, 3]\n"
                                  "    let b = Bag(items: src)\n"
                                  "    src[0] = 99\n"
                                  "    print(b.items[0])\n"
                                  "    print(src[0])\n"
                                  "}\n")
        self.assertEqual(result.stdout.split(), ["1", "99"])
        self.assertEqual(live_arrays(result.stderr), 0)

    def test_a_copied_value_keeps_its_own_storage(self):
        """Replacing a binding's array must not disturb the value it was copied from."""
        result = self.run_program(BAG + "\n"
                                  "fn main() {\n"
                                  "    let b = Bag(items: [1, 2, 3])\n"
                                  "    let copy = b\n"
                                  "    b.items = [8, 9]\n"
                                  "    print(copy.items[0])\n"
                                  "    print(len(copy.items))\n"
                                  "    print(b.items[0])\n"
                                  "}\n")
        self.assertEqual(result.stdout.split(), ["1", "3", "8"])
        self.assertEqual(live_arrays(result.stderr), 0)

    def test_an_enum_payload_is_copied_and_replaced(self):
        result = self.run_program(SHAPE + "\n"
                                  "fn main() {\n"
                                  "    let s = Shape::Poly([1, 2])\n"
                                  "    let t = s\n"
                                  "    s = Shape::Dot\n"
                                  "    match t {\n"
                                  "        Shape::Dot => { print(0) }\n"
                                  "        Shape::Poly(xs) => {\n"
                                  "            print(xs[0])\n"
                                  "            print(len(xs))\n"
                                  "        }\n"
                                  "    }\n"
                                  "}\n")
        self.assertEqual(result.stdout.split(), ["1", "2"])
        self.assertEqual(live_arrays(result.stderr), 0)

    def test_a_value_travels_through_functions_and_keeps_its_storage(self):
        result = self.run_program(BAG + "\n"
                                  "fn make(n: i64) -> Bag {\n"
                                  "    let b = Bag(items: array_zeros(n))\n"
                                  "    return b\n"
                                  "}\n"
                                  "fn read(b: Bag) -> i64 {\n"
                                  "    let local = b\n"
                                  "    return len(local.items)\n"
                                  "}\n"
                                  "fn main() {\n"
                                  "    let made = make(3)\n"
                                  "    print(read(made))\n"
                                  "    print(len(made.items))\n"
                                  "}\n")
        self.assertEqual(result.stdout.split(), ["3", "3"])
        self.assertEqual(live_arrays(result.stderr), 0)

    def test_nothing_is_leaked_on_the_normal_path(self):
        result = self.run_program(BAG + "\n" + NESTED + "\n" + SHAPE + "\n"
                                  "fn main() {\n"
                                  "    let b = Bag(items: [1, 2])\n"
                                  "    let copy = b\n"
                                  "    let o = Outer(inner: Inner(xs: [3, 4, 5]), tag: 1)\n"
                                  "    let s = Shape::Poly([6])\n"
                                  "    b.items = [9]\n"
                                  "    print(len(copy.items) + len(o.inner.xs) + len(b.items))\n"
                                  "    match s {\n"
                                  "        Shape::Dot => { print(0) }\n"
                                  "        Shape::Poly(xs) => { print(len(xs)) }\n"
                                  "    }\n"
                                  "}\n")
        self.assertEqual(result.stdout.split(), ["6", "1"])
        self.assertEqual(live_arrays(result.stderr), 0)

    def test_nothing_is_leaked_when_a_block_is_left_early(self):
        result = self.run_program(BAG + "\n"
                                  "fn find(n: i64) -> i64 {\n"
                                  "    let b = Bag(items: [1, 2, 3])\n"
                                  "    let i = 0\n"
                                  "    while i < 3 {\n"
                                  "        let inside = Bag(items: [4])\n"
                                  "        if i == n { return len(inside.items) + len(b.items) }\n"
                                  "        i = i + 1\n"
                                  "    }\n"
                                  "    return 0\n"
                                  "}\n"
                                  "fn main() {\n"
                                  "    print(find(1))\n"
                                  "}\n")
        self.assertEqual(result.stdout.split(), ["4"])
        self.assertEqual(live_arrays(result.stderr), 0)

    def test_a_write_through_a_parameter_leaves_the_caller_alone(self):
        """A parameter is a borrow: its storage is the caller's, not this block's.

        Assigning to the parameter, or to a field of it, replaces what the callee
        holds. The caller's array must survive both, which is exactly the case
        that a release without an ownership check gets wrong: it frees the
        caller's buffer and the caller reads it afterwards.
        """
        result = self.run_program(BAG + "\n"
                                  "fn replace(b: Bag) -> i64 {\n"
                                  "    b = Bag(items: [9, 9])\n"
                                  "    return len(b.items)\n"
                                  "}\n"
                                  "fn refill(b: Bag) -> i64 {\n"
                                  "    b.items = [5]\n"
                                  "    return len(b.items)\n"
                                  "}\n"
                                  "fn sum(b: Bag) -> i64 {\n"
                                  "    let total = 0\n"
                                  "    for x in b.items { total = total + x }\n"
                                  "    return total\n"
                                  "}\n"
                                  "fn main() {\n"
                                  "    let mine = Bag(items: [1, 2, 3])\n"
                                  "    print(replace(mine))\n"
                                  "    print(sum(mine))\n"
                                  "    print(refill(mine))\n"
                                  "    print(sum(mine))\n"
                                  "}\n")
        self.assertEqual(result.stdout.split(), ["2", "6", "1", "6"])
        self.assertEqual(live_arrays(result.stderr), 0)

    def test_the_copies_are_memory_safe_under_the_sanitizers(self):
        """AddressSanitizer and UBSan over every path this increment touches."""
        result = compile_and_run("edition 2;\n" + BAG + "\n" + NESTED + "\n" + SHAPE + "\n"
                                 "fn replace(b: Bag) -> i64 {\n"
                                 "    b.items = [4, 5]\n"
                                 "    return len(b.items)\n"
                                 "}\n"
                                 "fn make(n: i64) -> Bag {\n"
                                 "    let fresh = Bag(items: array_zeros(n))\n"
                                 "    return fresh\n"
                                 "}\n"
                                 "fn main() {\n"
                                 "    let src = [1, 2, 3]\n"
                                 "    let b = Bag(items: src)\n"
                                 "    src[0] = 99\n"
                                 "    let copy = b\n"
                                 "    b.items = [7]\n"
                                 "    print(replace(copy))\n"
                                 "    print(len(copy.items))\n"
                                 "    let o = Outer(inner: Inner(xs: [3]), tag: 1)\n"
                                 "    let o2 = o\n"
                                 "    print(len(o2.inner.xs))\n"
                                 "    let s = Shape::Poly([1, 2])\n"
                                 "    let t = s\n"
                                 "    s = Shape::Dot\n"
                                 "    match t {\n"
                                 "        Shape::Dot => { print(0) }\n"
                                 "        Shape::Poly(xs) => {\n"
                                 "            print(xs[1])\n"
                                 "        }\n"
                                 "    }\n"
                                 "    let made = make(2)\n"
                                 "    print(len(made.items))\n"
                                 "}\n", sanitize=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.split(), ["2", "3", "1", "2", "2"])
        self.assertEqual(live_arrays(result.stderr), 0)

    def test_a_defer_can_read_the_array_a_value_owns(self):
        result = self.run_program(BAG + "\n"
                                  "fn main() {\n"
                                  "    let b = Bag(items: [1, 2])\n"
                                  "    defer { print(len(b.items)) }\n"
                                  "    print(7)\n"
                                  "}\n")
        self.assertEqual(result.stdout.split(), ["7", "2"])
        self.assertEqual(live_arrays(result.stderr), 0)


if __name__ == "__main__":
    unittest.main()
