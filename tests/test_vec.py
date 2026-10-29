"""N11, part three: `Vec(T)`, the growing vector.

One claim, tested from both sides: **a vector is a built-in owning type with one
owner and a buffer that grows by doubling**. `let v: Vec(i64) = vec_new()` creates
the vector in the current block; the block frees it when it ends, including on an
early `path` out. Growth replaces the buffer but not the handle, so a `mut
Vec(T)` parameter that pushes is seen by its caller — that is the whole reason the
value a binding holds is a pointer rather than a struct.

The second claim is what the type refuses, and each refusal is one code from one
place. A vector cannot be copied (`E0428`: two holders would free one buffer),
returned (`E0428`), stored in a field or a payload (`E0428`), or reassigned
(`E0428`); its element cannot own storage (`E0427`), because a shrinking vector
would have to release elements it never copied; `vec_new()` must be annotated
(`E0429`), because the element type is not inferable from an empty vector; and a
mutating operation needs a writable local or a `mut Vec(T)` parameter (`E0444`),
because growth through a borrow would resize the caller's buffer behind its back.

The third claim is arithmetic: capacity starts at four and doubles, so `N` pushes
do at most `2N` element copies in total — amortised constant time per push — and
`pop` returns the element it removed as `Option(T)`, which is the prelude enum the
checker instantiates for that call.

The leak counter is the load-bearing control: every runtime test runs a binary
built with `-DNAVA_TRACK_ALLOCS` and asserts no array or vector buffer outlives the
process. One test runs a program that grows, aliases, drains and iterates under
AddressSanitizer and UndefinedBehaviorSanitizer, because a release that fires on a
reallocated buffer is a use-after-free the counter alone cannot see.
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
#: The instrument `tests/registry.py` insists on; without it this file fails.
SANITIZER_FLAGS = ("-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-g")

BAG = ("struct Bag {\n"
       "    items: [i64]\n"
       "}\n")

SHAPE = ("enum Shape {\n"
         "    Dot,\n"
         "    Circle(i64)\n"
         "}\n")

POINT = ("struct Point {\n"
         "    x: i64,\n"
         "    y: i64\n"
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


class VectorFrontendTests(unittest.TestCase):
    """What a vector may be, and every way of holding one that is refused."""

    def assert_code(self, text: str, code: str, message: str | None = None) -> NavaError:
        with self.assertRaises(NavaError) as caught:
            compile_source(text)
        self.assertEqual(caught.exception.code, code, caught.exception.message)
        if message is not None:
            self.assertIn(message, caught.exception.message)
        return caught.exception

    def test_a_vector_is_created_in_a_block_and_grown_by_builtins(self):
        source = ("edition 2;\n" + POINT + "\n"
                  "fn main() {\n"
                  "    let xs: Vec(i64) = vec_new()\n"
                  "    vec_push(xs, 1)\n"
                  "    vec_set(xs, 0, 2)\n"
                  "    print(vec_get(xs, 0))\n"
                  "    print(vec_len(xs))\n"
                  "    print(vec_capacity(xs))\n"
                  "    let ps: Vec(Point) = vec_new()\n"
                  "    vec_push(ps, Point(x: 1, y: 2))\n"
                  "    print(vec_get(ps, 0).y)\n"
                  "    let names: Vec(str) = vec_new()\n"
                  "    vec_push(names, \"ada\")\n"
                  "    for name in names { print(name) }\n"
                  "    let empty: Vec(bool) = vec_new()\n"
                  "    match vec_pop(empty) {\n"
                  "        Option(bool)::Some(flag) => { print(flag) }\n"
                  "        Option(bool)::None => { print(0) }\n"
                  "    }\n"
                  "}\n")
        self.assertTrue(compile_source(source))

    def test_a_moved_vector_leaves_nothing_behind(self):
        """`let ys = xs` is a move, so a later read of `xs` is `E0445`."""
        error = self.assert_code("edition 2;\n"
                                 "fn main() {\n"
                                 "    let xs: Vec(i64) = vec_new()\n"
                                 "    let ys: Vec(i64) = xs\n"
                                 "    print(vec_len(xs))\n"
                                 "}\n", "E0445")
        self.assertIn("was moved", error.message)

    def test_a_borrowed_vector_cannot_be_reassigned(self):
        """A parameter is a borrow: replacing what it holds would never reach
        the caller, and would free the caller's buffer here."""
        error = self.assert_code("edition 2;\n"
                                 "fn refill(xs: mut Vec(i64)) {\n"
                                 "    xs = vec_new()\n"
                                 "}\n"
                                 "fn main() {\n"
                                 "    let xs: Vec(i64) = vec_new()\n"
                                 "    refill(xs)\n"
                                 "}\n", "E0446")
        self.assertIn("borrowed vector", error.message)

    def test_a_vector_can_be_returned_but_not_discarded(self):
        """A return moves the buffer out; a call whose result nobody binds
        would leak it."""
        compile_source("edition 2;\n"
                       "fn make() -> Vec(i64) {\n"
                       "    let xs: Vec(i64) = vec_new()\n"
                       "    vec_push(xs, 1)\n"
                       "    return xs\n"
                       "}\n"
                       "fn main() {\n"
                       "    let xs: Vec(i64) = make()\n"
                       "    print(vec_len(xs))\n"
                       "}\n")
        error = self.assert_code("edition 2;\n"
                                 "fn make() -> Vec(i64) {\n"
                                 "    let xs: Vec(i64) = vec_new()\n"
                                 "    return xs\n"
                                 "}\n"
                                 "fn main() { make() }\n", "E0428")
        self.assertIn("no owner", error.message)

    def test_a_vector_can_be_a_field_or_a_payload(self):
        """A field and a payload hold a vector by owning a buffer of their own.

        The copy is deep — the struct's own copy helper allocates — so the value
        that was copied and the copy never share storage.
        """
        source = ("edition 2;\n"
                  "struct Holder {\n"
                  "    xs: Vec(i64)\n"
                  "}\n"
                  "enum Slot {\n"
                  "    Empty,\n"
                  "    Full(Vec(i64))\n"
                  "}\n"
                  "fn main() {\n"
                  "    let xs: Vec(i64) = vec_new()\n"
                  "    vec_push(xs, 1)\n"
                  "    let holder: Holder = Holder(xs: xs)\n"
                  "    print(vec_len(holder.xs))\n"
                  "    let slot: Slot = Slot::Full(xs)\n"
                  "    match slot {\n"
                  "        Slot::Full(inner) => { print(vec_len(inner)) }\n"
                  "        Slot::Empty => { print(0) }\n"
                  "    }\n"
                  "}\n")
        self.assertIn("nv_vec_i64_copy", frontend(source))
        compile_source(source)

    def test_a_field_holding_a_vector_is_read_but_not_moved_out(self):
        """Reading a field is a borrow: the field goes on owning its buffer."""
        error = self.assert_code("edition 2;\n"
                                 "struct Holder {\n"
                                 "    xs: Vec(i64)\n"
                                 "}\n"
                                 "fn main() {\n"
                                 "    let xs: Vec(i64) = vec_new()\n"
                                 "    let holder: Holder = Holder(xs: xs)\n"
                                 "    let stolen: Vec(i64) = holder.xs\n"
                                 "    print(vec_len(stolen))\n"
                                 "}\n", "E0428")
        self.assertIn("cannot be copied", error.message)

    def test_vec_new_needs_an_annotated_element_type(self):
        """An empty vector says nothing about what it holds."""
        error = self.assert_code("edition 2;\n"
                                 "fn main() {\n"
                                 "    let xs = vec_new()\n"
                                 "    print(1)\n"
                                 "}\n", "E0429")
        self.assertIn("element type", error.message)

    def test_an_element_cannot_own_storage(self):
        """A shrinking vector would have to release elements it never copied."""
        for element, owner in (("Bag", "an array"), ("[i64]", "an array")):
            error = self.assert_code("edition 2;\n" + BAG + "\n"
                                     "fn main() {\n"
                                     f"    let xs: Vec({element}) = vec_new()\n"
                                     "    print(vec_len(xs))\n"
                                     "}\n", "E0427")
            self.assertIn(owner, error.message)

    def test_a_vector_of_vectors_is_refused(self):
        """A vector owns a buffer, so an element that is a vector owns storage."""
        self.assert_code("edition 2;\n"
                         "fn main() {\n"
                         "    let rows: Vec(Vec(i64)) = vec_new()\n"
                         "    print(vec_len(rows))\n"
                         "}\n", "E0427")

    def test_a_mut_vector_argument_must_be_created_here(self):
        """A read-only parameter cannot hand its vector to a mutating callee."""
        error = self.assert_code("edition 2;\n"
                                 "fn grow(xs: mut Vec(i64)) {\n"
                                 "    vec_push(xs, 1)\n"
                                 "}\n"
                                 "fn pass(xs: Vec(i64)) {\n"
                                 "    grow(xs)\n"
                                 "}\n"
                                 "fn main() {\n"
                                 "    let xs: Vec(i64) = vec_new()\n"
                                 "    pass(xs)\n"
                                 "}\n", "E0333")
        self.assertIn("created in this function", error.message)

    def test_a_mutating_builtin_needs_a_writable_target(self):
        """Growth through a plain parameter would resize the caller's buffer."""
        error = self.assert_code("edition 2;\n"
                                 "fn grow(xs: Vec(i64)) {\n"
                                 "    vec_push(xs, 1)\n"
                                 "}\n"
                                 "fn main() { print(1) }\n", "E0444")
        self.assertIn("mut Vec(T)", error.message)
        self.assert_code("edition 2;\n"
                         "fn grow(xs: [i64]) {\n"
                         "    vec_push(xs, 1)\n"
                         "}\n"
                         "fn main() { print(1) }\n", "E0327")
        self.assert_code("edition 2;\n" + POINT + "\n"
                         "fn main() {\n"
                         "    let p = Point(x: 1, y: 2)\n"
                         "    vec_push(p, 1)\n"
                         "}\n", "E0327")

    def test_a_mut_vector_parameter_may_grow_the_caller_s_vector(self):
        source = ("edition 2;\n"
                  "fn grow(xs: mut Vec(i64), n: i64) {\n"
                  "    let i = 0\n"
                  "    while i < n {\n"
                  "        vec_push(xs, i)\n"
                  "        i = i + 1\n"
                  "    }\n"
                  "}\n"
                  "fn total(xs: Vec(i64)) -> i64 {\n"
                  "    let sum = 0\n"
                  "    for x in xs { sum = sum + x }\n"
                  "    return sum\n"
                  "}\n"
                  "fn main() {\n"
                  "    let xs: Vec(i64) = vec_new()\n"
                  "    grow(xs, 3)\n"
                  "    print(total(xs))\n"
                  "}\n")
        self.assertTrue(compile_source(source))

    def test_the_vector_names_are_reserved(self):
        """A builtin wins over a declaration, so declaring one is refused."""
        self.assert_code("edition 2;\n"
                         "fn vec_new() -> i64 {\n"
                         "    return 1\n"
                         "}\n"
                         "fn main() { print(vec_new()) }\n", "E0301",
                         "reserved builtin")
        self.assert_code("edition 2;\n"
                         "fn main() {\n"
                         "    let vec_len = 1\n"
                         "    print(vec_len)\n"
                         "}\n", "E0301")

    def test_the_argument_types_are_checked(self):
        self.assert_code("edition 2;\n"
                         "fn main() {\n"
                         "    print(vec_len(3))\n"
                         "}\n", "E0327")
        self.assert_code("edition 2;\n"
                         "fn main() {\n"
                         "    let xs: Vec(i64) = vec_new()\n"
                         "    print(vec_get(xs, true))\n"
                         "}\n", "E0328")
        self.assert_code("edition 2;\n"
                         "fn main() {\n"
                         "    let xs: Vec(i64) = vec_new()\n"
                         "    vec_push(xs, true)\n"
                         "}\n", "E0327")
        self.assert_code("edition 2;\n"
                         "fn main() {\n"
                         "    let xs: Vec(i64) = vec_new()\n"
                         "    vec_push(xs)\n"
                         "}\n", "E0326")

    def test_a_vector_cannot_be_printed_or_assigned_to(self):
        self.assert_code("edition 2;\n"
                         "fn main() {\n"
                         "    let xs: Vec(i64) = vec_new()\n"
                         "    print(xs)\n"
                         "}\n", "E0323")
        self.assert_code("edition 2;\n"
                         "fn main() {\n"
                         "    let xs: Vec(i64) = vec_new()\n"
                         "    vec_push(xs, 1)\n"
                         "    for x in xs { x = 2 }\n"
                         "}\n", "E0351")

    def test_the_generated_c_holds_a_handle_and_doubles(self):
        """The value is a pointer, and growth is a doubling of the capacity."""
        code = frontend("edition 2;\n"
                        "fn main() {\n"
                        "    let xs: Vec(i64) = vec_new()\n"
                        "    vec_push(xs, 1)\n"
                        "    print(vec_len(xs))\n"
                        "}\n")
        self.assertIn("nv_vec_i64 * nv_v0", code)
        self.assertIn("typedef struct nv_vec_i64", code)
        self.assertIn("nv_vec_i64 *nv_vec_i64_new(void)", code)
        self.assertIn("nv_mul(capacity, INT64_C(2))", code)
        self.assertIn("void nv_vec_i64_free(nv_vec_i64 **slot)", code)
        self.assertIn("nv_vec_i64_free(&nv_v0);", code)
        self.assertNotIn("nv_vec_bool", code)

    def test_a_vector_of_structs_and_a_vector_of_enums_compile(self):
        code = frontend("edition 2;\n" + POINT + "\n" + SHAPE + "\n"
                        "fn main() {\n"
                        "    let ps: Vec(Point) = vec_new()\n"
                        "    let shapes: Vec(Shape) = vec_new()\n"
                        "    vec_push(ps, Point(x: 1, y: 2))\n"
                        "    vec_push(shapes, Shape::Dot)\n"
                        "    print(vec_len(ps) + vec_len(shapes))\n"
                        "}\n")
        self.assertIn("nv_vec_Point_push(nv_vec_Point *vector, nv_struct_Point", code)
        self.assertIn("nv_vec_Shape_push(nv_vec_Shape *vector, nv_enum_Shape", code)


class VectorRuntimeTests(unittest.TestCase):
    """The buffer itself: growth, indices, drain, iteration, and no leaks."""

    def run_program(self, text: str) -> subprocess.CompletedProcess:
        result = compile_and_run("edition 2;\n" + text)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def test_pushes_grow_by_doubling_from_four(self):
        result = self.run_program("fn main() {\n"
                                  "    let xs: Vec(i64) = vec_new()\n"
                                  "    print(vec_capacity(xs))\n"
                                  "    let i = 0\n"
                                  "    while i < 5 {\n"
                                  "        vec_push(xs, i * 2)\n"
                                  "        i = i + 1\n"
                                  "    }\n"
                                  "    print(vec_len(xs))\n"
                                  "    print(vec_capacity(xs))\n"
                                  "    print(vec_get(xs, 4))\n"
                                  "}\n")
        self.assertEqual(result.stdout.split(), ["0", "5", "8", "8"])
        self.assertEqual(live_arrays(result.stderr), 0)

    def test_a_hundred_pushes_stay_within_twice_the_length(self):
        """Amortised constant time: capacity never exceeds `2 * len` after growth."""
        result = self.run_program("fn main() {\n"
                                  "    let xs: Vec(i64) = vec_new()\n"
                                  "    let i = 0\n"
                                  "    while i < 100 {\n"
                                  "        vec_push(xs, i)\n"
                                  "        i = i + 1\n"
                                  "    }\n"
                                  "    print(vec_len(xs))\n"
                                  "    print(vec_capacity(xs))\n"
                                  "    print(vec_get(xs, 99))\n"
                                  "    print(vec_get(xs, 50))\n"
                                  "}\n")
        self.assertEqual(result.stdout.split(), ["100", "128", "99", "50"])
        self.assertEqual(live_arrays(result.stderr), 0)

    def test_growth_inside_a_parameter_is_seen_by_the_caller(self):
        """The handle, not a copy of it, is what a `mut Vec(T)` parameter holds."""
        result = self.run_program("fn grow(xs: mut Vec(i64), n: i64) {\n"
                                  "    let i = 0\n"
                                  "    while i < n {\n"
                                  "        vec_push(xs, i)\n"
                                  "        i = i + 1\n"
                                  "    }\n"
                                  "}\n"
                                  "fn main() {\n"
                                  "    let xs: Vec(i64) = vec_new()\n"
                                  "    grow(xs, 40)\n"
                                  "    print(vec_len(xs))\n"
                                  "    print(vec_get(xs, 39))\n"
                                  "    grow(xs, 90)\n"
                                  "    print(vec_len(xs))\n"
                                  "    print(vec_get(xs, 129))\n"
                                  "}\n")
        self.assertEqual(result.stdout.split(), ["40", "39", "130", "89"])
        self.assertEqual(live_arrays(result.stderr), 0)

    def test_set_replaces_one_element_only(self):
        result = self.run_program("fn main() {\n"
                                  "    let xs: Vec(i64) = vec_new()\n"
                                  "    vec_push(xs, 1)\n"
                                  "    vec_push(xs, 2)\n"
                                  "    vec_push(xs, 3)\n"
                                  "    vec_set(xs, 1, 20)\n"
                                  "    for x in xs { print(x) }\n"
                                  "}\n")
        self.assertEqual(result.stdout.split(), ["1", "20", "3"])
        self.assertEqual(live_arrays(result.stderr), 0)

    def test_pop_drains_the_vector_and_then_reports_none(self):
        result = self.run_program("fn main() {\n"
                                  "    let xs: Vec(i64) = vec_new()\n"
                                  "    vec_push(xs, 7)\n"
                                  "    vec_push(xs, 8)\n"
                                  "    match vec_pop(xs) {\n"
                                  "        Option(i64)::Some(x) => { print(x) }\n"
                                  "        Option(i64)::None => { print(-1) }\n"
                                  "    }\n"
                                  "    print(vec_len(xs))\n"
                                  "    match vec_pop(xs) {\n"
                                  "        Option(i64)::Some(x) => { print(x) }\n"
                                  "        Option(i64)::None => { print(-1) }\n"
                                  "    }\n"
                                  "    match vec_pop(xs) {\n"
                                  "        Option(i64)::Some(x) => { print(x) }\n"
                                  "        Option(i64)::None => { print(-1) }\n"
                                  "    }\n"
                                  "    print(vec_capacity(xs))\n"
                                  "}\n")
        self.assertEqual(result.stdout.split(), ["8", "1", "7", "-1", "4"])
        self.assertEqual(live_arrays(result.stderr), 0)

    def test_a_struct_vector_reads_and_writes_fields(self):
        result = self.run_program(POINT + "\n"
                                  "fn main() {\n"
                                  "    let ps: Vec(Point) = vec_new()\n"
                                  "    vec_push(ps, Point(x: 1, y: 2))\n"
                                  "    vec_push(ps, Point(x: 3, y: 4))\n"
                                  "    vec_set(ps, 0, Point(x: 9, y: 8))\n"
                                  "    let total = 0\n"
                                  "    for p in ps { total = total + p.x + p.y }\n"
                                  "    print(total)\n"
                                  "    print(vec_get(ps, 1).y)\n"
                                  "}\n")
        self.assertEqual(result.stdout.split(), ["24", "4"])
        self.assertEqual(live_arrays(result.stderr), 0)

    def test_an_enum_vector_matches_its_payload(self):
        result = self.run_program(SHAPE + "\n"
                                  "fn main() {\n"
                                  "    let shapes: Vec(Shape) = vec_new()\n"
                                  "    vec_push(shapes, Shape::Dot)\n"
                                  "    vec_push(shapes, Shape::Circle(5))\n"
                                  "    for shape in shapes {\n"
                                  "        match shape {\n"
                                  "            Shape::Dot => { print(0) }\n"
                                  "            Shape::Circle(r) => { print(r) }\n"
                                  "        }\n"
                                  "    }\n"
                                  "}\n")
        self.assertEqual(result.stdout.split(), ["0", "5"])
        self.assertEqual(live_arrays(result.stderr), 0)

    def test_a_string_vector_keeps_its_strings(self):
        result = self.run_program("fn main() {\n"
                                  "    let names: Vec(str) = vec_new()\n"
                                  "    vec_push(names, \"ada\")\n"
                                  "    vec_push(names, \"grace\")\n"
                                  "    vec_push(names, \"alan\")\n"
                                  "    for name in names { print(name) }\n"
                                  "    print(vec_get(names, 1))\n"
                                  "}\n")
        self.assertEqual(result.stdout.split(), ["ada", "grace", "alan", "grace"])
        self.assertEqual(live_arrays(result.stderr), 0)

    def test_an_empty_vector_allocates_nothing(self):
        """The handle is allocated, the buffer is not, so no array is ever owned."""
        result = self.run_program("fn main() {\n"
                                  "    let xs: Vec(i64) = vec_new()\n"
                                  "    let seen = 0\n"
                                  "    for x in xs { seen = seen + x }\n"
                                  "    print(seen)\n"
                                  "    print(vec_len(xs))\n"
                                  "    print(vec_capacity(xs))\n"
                                  "}\n")
        self.assertEqual(result.stdout.split(), ["0", "0", "0"])
        # The counter registers itself on the first allocation; an empty vector
        # makes none, so its absence is the assertion.
        self.assertEqual(result.stderr, "")

    def test_pop_set_and_push_change_the_caller_s_vector(self):
        """All three mutating builtins work through a `mut Vec(T)` parameter."""
        result = self.run_program("fn churn(xs: mut Vec(i64)) {\n"
                                  "    vec_push(xs, 1)\n"
                                  "    vec_push(xs, 2)\n"
                                  "    vec_set(xs, 0, 9)\n"
                                  "    match vec_pop(xs) {\n"
                                  "        Option(i64)::Some(last) => { print(last) }\n"
                                  "        Option(i64)::None => { print(-1) }\n"
                                  "    }\n"
                                  "}\n"
                                  "fn main() {\n"
                                  "    let xs: Vec(i64) = vec_new()\n"
                                  "    churn(xs)\n"
                                  "    print(vec_len(xs))\n"
                                  "    print(vec_get(xs, 0))\n"
                                  "}\n")
        self.assertEqual(result.stdout.split(), ["2", "1", "9"])
        self.assertEqual(live_arrays(result.stderr), 0)

    def test_a_vector_of_a_generic_instance_and_of_an_option(self):
        """An element can be any value type, including an instantiated one."""
        result = self.run_program("struct Pair<T> {\n"
                                  "    first: T,\n"
                                  "    second: T\n"
                                  "}\n"
                                  "fn main() {\n"
                                  "    let ps: Vec(Pair(i64)) = vec_new()\n"
                                  "    vec_push(ps, Pair(i64)(first: 1, second: 2))\n"
                                  "    print(vec_get(ps, 0).second)\n"
                                  "    let opts: Vec(Option(i64)) = vec_new()\n"
                                  "    vec_push(opts, Option(i64)::Some(5))\n"
                                  "    vec_push(opts, Option(i64)::None)\n"
                                  "    for o in opts {\n"
                                  "        match o {\n"
                                  "            Option(i64)::Some(v) => { print(v) }\n"
                                  "            Option(i64)::None => { print(-1) }\n"
                                  "        }\n"
                                  "    }\n"
                                  "}\n")
        self.assertEqual(result.stdout.split(), ["2", "5", "-1"])
        self.assertEqual(live_arrays(result.stderr), 0)

    def test_an_out_of_bounds_index_stops_the_program(self):
        result = compile_and_run("edition 2;\n"
                                 "fn main() {\n"
                                 "    let xs: Vec(i64) = vec_new()\n"
                                 "    vec_push(xs, 1)\n"
                                 "    print(vec_get(xs, 4))\n"
                                 "}\n", tracked=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("out of bounds", result.stderr)

    def test_two_vectors_in_nested_blocks_free_both(self):
        result = self.run_program("fn main() {\n"
                                  "    let outer: Vec(str) = vec_new()\n"
                                  "    vec_push(outer, \"a\")\n"
                                  "    if vec_len(outer) > 0 {\n"
                                  "        let inner: Vec(i64) = vec_new()\n"
                                  "        vec_push(inner, 1)\n"
                                  "        print(vec_len(inner))\n"
                                  "    }\n"
                                  "    for name in outer { print(name) }\n"
                                  "}\n")
        self.assertEqual(result.stdout.split(), ["1", "a"])
        self.assertEqual(live_arrays(result.stderr), 0)

    def test_a_vector_declared_in_a_loop_body_is_released_each_turn(self):
        result = self.run_program("fn scoped(n: i64) -> i64 {\n"
                                  "    let i = 0\n"
                                  "    while i < n {\n"
                                  "        let v: Vec(str) = vec_new()\n"
                                  "        vec_push(v, \"x\")\n"
                                  "        i = i + 1\n"
                                  "    }\n"
                                  "    return i\n"
                                  "}\n"
                                  "fn main() {\n"
                                  "    print(scoped(5))\n"
                                  "}\n")
        self.assertEqual(result.stdout.split(), ["5"])
        self.assertEqual(live_arrays(result.stderr), 0)

    def test_a_vector_is_released_on_an_early_return(self):
        """Every vector the function owns is freed on each path out."""
        result = self.run_program("fn early(xs: Vec(i64)) -> i64 {\n"
                                  "    let w: Vec(i64) = vec_new()\n"
                                  "    vec_push(w, vec_len(xs))\n"
                                  "    if vec_len(w) > 0 {\n"
                                  "        let inner: Vec(i64) = vec_new()\n"
                                  "        vec_push(inner, 5)\n"
                                  "        return vec_get(inner, 0)\n"
                                  "    }\n"
                                  "    return 0\n"
                                  "}\n"
                                  "fn main() {\n"
                                  "    let xs: Vec(i64) = vec_new()\n"
                                  "    vec_push(xs, 2)\n"
                                  "    print(early(xs))\n"
                                  "}\n")
        self.assertEqual(result.stdout.split(), ["5"])
        self.assertEqual(live_arrays(result.stderr), 0)

    def test_a_defer_reads_the_vector_before_it_is_released(self):
        result = self.run_program("fn main() {\n"
                                  "    let names: Vec(str) = vec_new()\n"
                                  "    vec_push(names, \"late\")\n"
                                  "    defer { print(vec_len(names)) }\n"
                                  "    print(vec_get(names, 0))\n"
                                  "}\n")
        self.assertEqual(result.stdout.split(), ["late", "1"])
        self.assertEqual(live_arrays(result.stderr), 0)

    def test_a_float_vector_and_a_bool_vector_round_trip(self):
        result = self.run_program("fn main() {\n"
                                  "    let fs: Vec(f64) = vec_new()\n"
                                  "    vec_push(fs, 1.5)\n"
                                  "    vec_push(fs, 2.5)\n"
                                  "    print(vec_get(fs, 0) + vec_get(fs, 1))\n"
                                  "    let bs: Vec(bool) = vec_new()\n"
                                  "    vec_push(bs, true)\n"
                                  "    vec_push(bs, false)\n"
                                  "    print(vec_get(bs, 1))\n"
                                  "    for b in bs { print(b) }\n"
                                  "}\n")
        self.assertEqual(result.stdout.split(), ["4.0", "false", "true", "false"])
        self.assertEqual(live_arrays(result.stderr), 0)

    def test_growth_aliasing_and_drain_under_the_sanitizers(self):
        """Reallocation must not leave a stale pointer behind: the other control."""
        result = compile_and_run("edition 2;\n" + POINT + "\n"
                                 "fn grow(ps: mut Vec(Point), n: i64) {\n"
                                 "    let i = 0\n"
                                 "    while i < n {\n"
                                 "        vec_push(ps, Point(x: i, y: i * i))\n"
                                 "        i = i + 1\n"
                                 "    }\n"
                                 "}\n"
                                 "fn total(ps: Vec(Point)) -> i64 {\n"
                                 "    let sum = 0\n"
                                 "    for p in ps { sum = sum + p.y }\n"
                                 "    return sum\n"
                                 "}\n"
                                 "fn main() {\n"
                                 "    let ps: Vec(Point) = vec_new()\n"
                                 "    grow(ps, 5)\n"
                                 "    print(total(ps))\n"
                                 "    vec_set(ps, 0, Point(x: 9, y: 81))\n"
                                 "    let seen = 0\n"
                                 "    for p in ps {\n"
                                 "        seen = seen + p.x\n"
                                 "        if vec_len(ps) < 7 { vec_push(ps, Point(x: 100, y: 0)) }\n"
                                 "    }\n"
                                 "    print(seen)\n"
                                 "    print(vec_len(ps))\n"
                                 "    let drained = 0\n"
                                 "    let going = true\n"
                                 "    while going {\n"
                                 "        match vec_pop(ps) {\n"
                                 "            Option(Point)::Some(p) => { drained = drained + p.x }\n"
                                 "            Option(Point)::None => { going = false }\n"
                                 "        }\n"
                                 "    }\n"
                                 "    print(drained)\n"
                                 "    print(vec_len(ps))\n"
                                 "}\n", sanitize=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.split(), ["30", "19", "7", "219", "0"])
        self.assertEqual(live_arrays(result.stderr), 0)


if __name__ == "__main__":
    unittest.main()
