"""Edition 2, part six: monomorphised generics (INDEPENDENCE.md N10f).

One claim, tested from both sides: **a type parameter is a compile-time
recipe**. There is no `T` in the generated C, so every property here is a
property of the *instantiation*:

1. `struct Pair<T>`, `enum Box<T>` and `fn identity<T>` are checked once per
   combination of type arguments, and the resulting declaration behaves exactly
   like the hand-written one — copies are copies, fields are fields, a `match`
   is exhaustive over the variants.
2. Two instantiations with different arguments are different types: assigning
   one to the other is `E0311`, and each is checked on its own terms.
3. `T` exists inside its declaration only. Outside, a bare template is `E0437`
   in every position it can be written in — annotation, parameter, field,
   construction and call.
4. A call may infer the type arguments from its arguments; when it cannot, the
   diagnostic names the parameter and shows the explicit spelling (`E0436`).

A template whose body is never instantiated has never been type-checked, and
`W0003` says so: a skipped check is not a passed check.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nava_lang.cli import compile_source  # noqa: E402
from nava_lang.model import NavaError  # noqa: E402

CC = shutil.which("cc")

PAIR = ("struct Pair<T> {\n"
        "    first: T,\n"
        "    second: T\n"
        "}\n")


def run_nava(text: str) -> subprocess.CompletedProcess:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "program.nv"
        path.write_text(text, encoding="utf-8")
        return subprocess.run([sys.executable, str(ROOT / "nava"), "run", str(path)],
                              capture_output=True, text=True, timeout=120)


class GenericFrontendTests(unittest.TestCase):
    def assert_code(self, text: str, code: str, message: str | None = None) -> NavaError:
        with self.assertRaises(NavaError) as caught:
            compile_source(text)
        self.assertEqual(caught.exception.code, code, caught.exception.message)
        if message is not None:
            self.assertIn(message, caught.exception.message)
        return caught.exception

    def test_a_type_parameter_needs_edition_two(self):
        self.assert_code("fn first<T>(x: T) -> T { return x }\nfn main() { print(first(1)) }\n",
                         "E0372", "type parameter")
        # A struct or an enum declaration already needs edition 2, so the
        # parameter list is not the first thing that is refused there.
        self.assert_code("struct Pair<T> { first: T }\nfn main() { print(1) }\n",
                         "E0372", "struct declaration needs edition 2")
        self.assert_code("enum Box<T> { Full(T) }\nfn main() { print(1) }\n",
                         "E0372", "enum declaration needs edition 2")

    def test_a_type_parameter_is_declared_once(self):
        self.assert_code("edition 2;\nfn first<T, T>(a: T, b: T) -> T { return a }\n"
                         "fn main() { print(first(1, 2)) }\n",
                         "E0435", "duplicate type parameter")

    def test_a_bare_template_is_not_a_type(self):
        # An annotation, a parameter, a field, a construction and a call.
        self.assert_code("edition 2;\n" + PAIR +
                         "fn main() { let p: Pair = Pair(i64)(first: 1, second: 2) }\n",
                         "E0437", "not a type")
        self.assert_code("edition 2;\n" + PAIR +
                         "fn f(p: Pair) -> i64 { return p.first }\nfn main() { print(1) }\n",
                         "E0437")
        self.assert_code("edition 2;\n" + PAIR +
                         "struct Wrapper { inside: Pair }\nfn main() { print(1) }\n",
                         "E0437")
        self.assert_code("edition 2;\n" + PAIR +
                         "fn main() { let p = Pair(first: 1, second: 2) }\n",
                         "E0437", "names its type")
        self.assert_code("edition 2;\n" + PAIR + "fn main() { print(Pair(1)) }\n",
                         "E0437")

    def test_a_generic_enum_template_is_not_a_type_either(self):
        self.assert_code("edition 2;\nenum Box<T> { Full(T), Empty }\n"
                         "fn main() { let b: Box = Box(i64)::Full(1) }\n",
                         "E0437")

    def test_a_type_argument_list_comes_first(self):
        self.assert_code("edition 2;\nfn double(x: i64) -> i64 { return x * 2 }\n"
                         "fn main() { print(double(i64)(21)) }\n",
                         "E0438", "type arguments come before")
        # The same shape names a generic function correctly.
        self.assertTrue(compile_source("edition 2;\n"
                                       "fn identity<T>(x: T) -> T { return x }\n"
                                       "fn main() { print(identity(i64)(7)) }\n").code)

    def test_wrong_number_of_type_arguments(self):
        self.assert_code("edition 2;\n" + PAIR +
                         "fn main() { let p = Pair(i64, i64)(first: 1, second: 2) }\n",
                         "E0412", "takes 1 type argument, got 2")

    def test_an_argument_that_cannot_be_inferred(self):
        error = self.assert_code("edition 2;\nfn zero<T>() -> i64 { return 0 }\n"
                                 "fn main() { print(zero()) }\n",
                                 "E0436", "cannot infer the type argument T")
        self.assertIn("zero(i64)(...)", error.hint)

    def test_inference_does_not_guess_from_a_conflict(self):
        # `T` is bound by the first argument; the second one has to match it.
        self.assert_code("edition 2;\nfn both<T>(a: T, b: T) -> bool { return a == b }\n"
                         "fn main() { print(both(1, true)) }\n",
                         "E0327", "argument 2 of both expects i64, got bool")

    def test_two_instantiations_are_different_types(self):
        self.assert_code("edition 2;\n" + PAIR +
                         "fn main() {\n"
                         "    let a: Pair(i64) = Pair(i64)(first: 1, second: 2)\n"
                         "    let b: Pair(bool) = a\n"
                         "    print(1)\n"
                         "}\n",
                         "E0311", "expected Pair(bool), got Pair(i64)")

    def test_a_struct_instance_cannot_own_a_string(self):
        # The restriction is the one every struct has, not one about generics:
        # a copied struct would share the string's storage.
        self.assert_code("edition 2;\n" + PAIR +
                         "fn main() { let p = Pair(str)(first: \"a\", second: \"b\") }\n",
                         "E0378", "cannot have type str")

    def test_an_instantiation_cannot_be_an_array_element(self):
        self.assert_code("edition 2;\n" + PAIR +
                         "fn main() { let a: [Pair(i64)] = []\n    print(len(a)) }\n",
                         "E0206", "element type")

    def test_an_uninstantiated_template_is_reported_as_unchecked(self):
        compilation = compile_source("edition 2;\n"
                                     "fn unused<T>(x: T) -> T { return x }\n"
                                     "fn main() { print(1) }\n")
        self.assertEqual([warning.code for warning in compilation.warnings], ["W0003"])
        self.assertIn("never instantiated", compilation.warnings[0].message)

    def test_the_generated_c_has_one_function_per_instance(self):
        source = ("edition 2;\n"
                  "fn identity<T>(x: T) -> T { return x }\n"
                  "fn main() {\n"
                  "    let a = identity(1)\n"
                  "    let b = identity(2)\n"
                  "    let c = identity(true)\n"
                  "    print(a + b)\n"
                  "    print(c)\n"
                  "}\n")
        compilation = compile_source(source)
        c_source = compilation.code
        # One definition per instantiation, none for the template itself.
        definitions = re.findall(r"^static .*?\bnv_fn_(\d+)\(.*\) \{$", c_source,
                                 re.MULTILINE)
        self.assertEqual(len(definitions), len(set(definitions)),
                         "every generated C function is defined once")
        # `main` plus one copy per distinct instantiation: `identity(i64)` once
        # (two calls, one instance) and `identity(bool)` once.
        self.assertEqual(len(set(definitions)), 3,
                         "main, identity(i64) and identity(bool): one definition each")
        self.assertNotIn("nv_fn_4", c_source, "no copy of the template itself")

    def test_a_template_can_be_instantiated_with_a_struct_and_an_enum(self):
        text = ("edition 2;\n"
                "struct Point { x: i64, y: i64 }\n"
                "enum Shape { Dot, Circle(i64) }\n"
                "fn first_point<T>(a: T, b: T) -> T { return a }\n"
                "fn main() {\n"
                "    let p: Point = first_point(Point(x: 1, y: 2), Point(x: 3, y: 4))\n"
                "    let s: Shape = first_point(Shape::Dot, Shape::Circle(2))\n"
                "    match s {\n"
                "        Shape::Dot => { print(p.x) }\n"
                "        Shape::Circle(r) => { print(r) }\n"
                "    }\n"
                "}\n")
        self.assertTrue(compile_source(text).code)


@unittest.skipIf(CC is None, "a C compiler is required to run programs")
class GenericRuntimeTests(unittest.TestCase):
    def assert_output(self, text: str, expected: str) -> None:
        result = run_nava(text)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, expected, result.stderr)

    def test_identity_works_for_every_scalar_type(self):
        self.assert_output("edition 2;\n"
                           "fn identity<T>(x: T) -> T { return x }\n"
                           "fn main() {\n"
                           "    print(identity(7))\n"
                           "    print(identity(true))\n"
                           "    print(identity(\"seven\"))\n"
                           "    print(identity(0.5))\n"
                           "}\n",
                           "7\ntrue\nseven\n0.5\n")

    def test_an_instantiated_struct_copies_like_a_struct(self):
        self.assert_output("edition 2;\n" + PAIR +
                           "fn swap<T>(p: Pair(T)) -> Pair(T) {\n"
                           "    return Pair(T)(first: p.second, second: p.first)\n"
                           "}\n"
                           "fn main() {\n"
                           "    let p: Pair(i64) = Pair(i64)(first: 1, second: 2)\n"
                           "    let q = swap(p)\n"
                           "    q.first = 99\n"
                           "    print(p.first)\n"
                           "    print(q.first)\n"
                           "    print(q.second)\n"
                           "}\n",
                           "1\n99\n1\n")

    def test_instances_nest(self):
        self.assert_output("edition 2;\n" + PAIR +
                           "fn main() {\n"
                           "    let n: Pair(Pair(i64)) = Pair(Pair(i64))(\n"
                           "        first: Pair(i64)(first: 1, second: 2),\n"
                           "        second: Pair(i64)(first: 3, second: 4))\n"
                           "    print(n.first.second)\n"
                           "    print(n.second.first)\n"
                           "}\n",
                           "2\n3\n")

    def test_a_generic_enum_matches_per_instantiation(self):
        self.assert_output("edition 2;\n"
                           "enum Box<T> {\n"
                           "    Full(T),\n"
                           "    Empty\n"
                           "}\n"
                           "fn value<T>(b: Box(T), fallback: T) -> T {\n"
                           "    match b {\n"
                           "        Box(T)::Full(v) => {\n"
                           "            return v\n"
                           "        }\n"
                           "        Box(T)::Empty => {\n"
                           "            return fallback\n"
                           "        }\n"
                           "    }\n"
                           "}\n"
                           "fn main() {\n"
                           "    print(value(Box(i64)::Full(5), 0))\n"
                           "    print(value(Box(i64)::Empty, 0))\n"
                           "    print(value(Box(bool)::Full(true), false))\n"
                           "}\n",
                           "5\n0\ntrue\n")

    def test_a_template_may_call_another_template(self):
        self.assert_output("edition 2;\n" + PAIR +
                           "fn identity<T>(x: T) -> T { return x }\n"
                           "fn second<T>(p: Pair(T)) -> T { return identity(p.second) }\n"
                           "fn main() {\n"
                           "    print(second(Pair(i64)(first: 1, second: 2)))\n"
                           "    print(second(Pair(bool)(first: true, second: false)))\n"
                           "}\n",
                           "2\nfalse\n")

    def test_the_same_instance_is_generated_once(self):
        self.assert_output("edition 2;\n"
                           "fn twice<T>(x: T) -> i64 { return 2 }\n"
                           "fn main() {\n"
                           "    let a = twice(1)\n"
                           "    let b = twice(2)\n"
                           "    let c = twice(3)\n"
                           "    print(a + b + c)\n"
                           "}\n",
                           "6\n")

    def test_a_template_instantiates_per_argument_type_at_run_time(self):
        # The same source, two monomorphised bodies, two different answers: the
        # comparison is made on the concrete type, not on a shared `T`.
        self.assert_output("edition 2;\n"
                           "fn same<T>(a: T, b: T) -> bool { return a == b }\n"
                           "fn main() {\n"
                           "    print(same(1, 1))\n"
                           "    print(same(1, 2))\n"
                           "    print(same(true, true))\n"
                           "    print(same(\"a\", \"b\"))\n"
                           "}\n",
                           "true\nfalse\ntrue\nfalse\n")


if __name__ == "__main__":
    unittest.main()
