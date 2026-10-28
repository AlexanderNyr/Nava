"""Edition 2, part seven: function values and function pointers.

One claim, tested from both sides: **a function value is the address of a
top-level function**. There is no closure, nothing is captured, and no language
construct creates a new function at run time, so:

1. A declared function used as a value has the type of its signature,
   `fn(i64) -> i64`; that type is a value type exactly like `i64` or a struct —
   it can be bound with `let`, passed, returned, held in a field or a variant
   payload, and stored in a generic instantiation.
2. A call through a variable is checked against the signature the variable
   carries: the arity, every argument type and the result are known before the
   program runs, and every failure is a diagnostic, not an unchecked call.
3. The generated C has no `void *`: one `typedef` per distinct signature and one
   `&nv_fn_N` per function value, so the C compiler checks the call as well.
4. Things that would be a guess are refused rather than guessed: a generic
   function has no single signature (`E0437`), a field cannot be called in place
   (`E0441`), two function values cannot be compared (`E0442`), and a function
   type takes no `mut` parameter (`E0443`).
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

DOUBLE = "fn double(x: i64) -> i64 {\n    return x * 2\n}\n"


def run_nava(text: str) -> subprocess.CompletedProcess:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "program.nv"
        path.write_text(text, encoding="utf-8")
        return subprocess.run([sys.executable, str(ROOT / "nava"), "run", str(path)],
                              capture_output=True, text=True, timeout=120)


class FunctionValueFrontendTests(unittest.TestCase):
    def assert_code(self, text: str, code: str, message: str | None = None) -> NavaError:
        with self.assertRaises(NavaError) as caught:
            compile_source(text)
        self.assertEqual(caught.exception.code, code, caught.exception.message)
        if message is not None:
            self.assertIn(message, caught.exception.message)
        return caught.exception

    def test_a_function_type_needs_edition_two(self):
        self.assert_code("fn double(x: i64) -> i64 { return x * 2 }\n"
                         "fn main() {\n"
                         "    let f: fn(i64) -> i64 = double\n"
                         "    print(f(2))\n"
                         "}\n",
                         "E0372", "function type")

    def test_a_declared_function_can_be_bound_and_called(self):
        compilation = compile_source("edition 2;\n" + DOUBLE +
                                     "fn main() {\n"
                                     "    let f = double\n"
                                     "    print(f(21))\n"
                                     "}\n")
        self.assertTrue(compilation.code)

    def test_a_value_carries_its_signature(self):
        error = self.assert_code("edition 2;\n" + DOUBLE +
                                 "fn main() {\n"
                                 "    let f: fn(i64) -> i64 = double\n"
                                 "    print(f(true))\n"
                                 "}\n",
                                 "E0327", "argument 1 of f expects i64, got bool")
        self.assertIsNotNone(error.loc)

    def test_the_arity_is_checked_through_the_value(self):
        self.assert_code("edition 2;\n" + DOUBLE +
                         "fn main() {\n"
                         "    let f: fn(i64) -> i64 = double\n"
                         "    print(f(1, 2))\n"
                         "}\n",
                         "E0326", "f expects 1 argument, got 2")

    def test_only_a_function_type_can_be_called(self):
        error = self.assert_code("edition 2;\n"
                                 "fn main() {\n"
                                 "    let x: i64 = 3\n"
                                 "    print(x(1))\n"
                                 "}\n",
                                 "E0439", "is i64, so it cannot be called")
        self.assertIn("fn(i64) -> i64", error.hint)

    def test_a_signature_is_part_of_the_type(self):
        self.assert_code("edition 2;\n" + DOUBLE +
                         "fn truthy(x: i64) -> bool {\n    return x > 0\n}\n"
                         "fn main() {\n"
                         "    let f: fn(i64) -> i64 = double\n"
                         "    f = truthy\n"
                         "    print(f(1))\n"
                         "}\n",
                         "E0312", "cannot assign fn(i64) -> bool to fn(i64) -> i64")

    def test_a_generic_function_is_not_a_value(self):
        self.assert_code("edition 2;\n"
                         "fn identity<T>(x: T) -> T {\n    return x\n}\n"
                         "fn main() {\n"
                         "    let f = identity\n"
                         "    print(1)\n"
                         "}\n",
                         "E0437", "is a generic function")

    def test_a_call_cannot_go_through_a_field(self):
        error = self.assert_code("edition 2;\n" + DOUBLE +
                                 "struct Holder {\n"
                                 "    value: i64,\n"
                                 "    op: fn(i64) -> i64,\n"
                                 "}\n"
                                 "fn main() {\n"
                                 "    let h: Holder = Holder(value: 2, op: double)\n"
                                 "    print(h.op(h.value))\n"
                                 "}\n",
                                 "E0441", "a call cannot go through a field")
        self.assertIn("let f = h.op", error.hint)

    def test_two_function_values_cannot_be_compared(self):
        self.assert_code("edition 2;\n" + DOUBLE +
                         "fn main() {\n"
                         "    let f: fn(i64) -> i64 = double\n"
                         "    let g: fn(i64) -> i64 = double\n"
                         "    if f == g {\n"
                         "        print(1)\n"
                         "    }\n"
                         "}\n",
                         "E0442", "cannot compare function values")

    def test_a_function_type_takes_no_mut_parameter(self):
        self.assert_code("edition 2;\n"
                         "fn fill(a: mut [i64]) -> i64 {\n"
                         "    a[0] = 1\n"
                         "    return a[0]\n"
                         "}\n"
                         "fn main() {\n"
                         "    let arr: [i64] = array_zeros(2)\n"
                         "    let f: fn(mut [i64]) -> i64 = fill\n"
                         "    print(f(arr))\n"
                         "}\n",
                         "E0443", "a function type has no `mut` parameter")

    def test_a_function_value_cannot_be_printed(self):
        self.assert_code("edition 2;\n" + DOUBLE +
                         "fn main() {\n"
                         "    print(double)\n"
                         "}\n",
                         "E0323", "print expects")

    def test_every_part_of_a_function_type_must_be_a_type(self):
        self.assert_code("edition 2;\n"
                         "fn main() {\n"
                         "    let f: fn(Nope) -> i64 = 1\n"
                         "}\n",
                         "E0203", "unknown type")

    def test_a_function_value_fits_every_value_position(self):
        text = ("edition 2;\n"
                + DOUBLE +
                "struct Ops {\n"
                "    op: fn(i64) -> i64,\n"
                "    label: i64,\n"
                "}\n"
                "enum Step {\n"
                "    Apply(fn(i64) -> i64),\n"
                "    Stop,\n"
                "}\n"
                "fn pick(which: bool) -> fn(i64) -> i64 {\n"
                "    if which {\n"
                "        return double\n"
                "    }\n"
                "    return double\n"
                "}\n"
                "fn main() {\n"
                "    let o: Ops = Ops(op: double, label: 1)\n"
                "    let from_field: fn(i64) -> i64 = o.op\n"
                "    let step: Step = Step::Apply(pick(true))\n"
                "    let box: Option(fn(i64) -> i64) = Option(fn(i64) -> i64)::Some(double)\n"
                "    print(from_field(o.label))\n"
                "    match step {\n"
                "        Step::Apply(f) => { print(f(2)) }\n"
                "        Step::Stop => { print(-1) }\n"
                "    }\n"
                "    match box {\n"
                "        Option::Some(f) => { print(f(3)) }\n"
                "        Option::None => { print(-1) }\n"
                "    }\n"
                "}\n")
        self.assertTrue(compile_source(text).code)

    def test_a_template_instantiates_per_signature(self):
        source = ("edition 2;\n"
                  "fn apply<T>(f: fn(T) -> T, x: T) -> T {\n"
                  "    return f(x)\n"
                  "}\n"
                  + DOUBLE +
                  "fn flip(x: bool) -> bool {\n    return !x\n}\n"
                  "fn main() {\n"
                  "    print(apply(double, 4))\n"
                  "    print(apply(flip, true))\n"
                  "}\n")
        c_source = compile_source(source).code
        definitions = re.findall(r"^static .*?\bnv_fn_(\d+)\(.*\) \{$", c_source,
                                 re.MULTILINE)
        self.assertEqual(len(definitions), len(set(definitions)),
                         "every generated C function is defined once")
        # main, double, flip and two copies of `apply`: one per signature.
        self.assertEqual(len(set(definitions)), 5)

    def test_the_generated_c_typedefs_each_signature(self):
        source = ("edition 2;\n" + DOUBLE +
                  "fn flip(x: bool) -> bool {\n    return !x\n}\n"
                  "fn main() {\n"
                  "    let a: fn(i64) -> i64 = double\n"
                  "    let b: fn(bool) -> bool = flip\n"
                  "    let c: fn(i64) -> i64 = double\n"
                  "    print(a(1))\n"
                  "    print(b(false))\n"
                  "    print(c(2))\n"
                  "}\n")
        c_source = compile_source(source).code
        typedefs = re.findall(r"^typedef .*?\(\*nv_fnp_(\d+)\)\(.*\);$", c_source,
                              re.MULTILINE)
        self.assertEqual(sorted(set(typedefs)), ["0", "1"],
                         "one typedef per distinct signature, and no more")
        self.assertIn("typedef int64_t (*nv_fnp_", c_source)
        self.assertIn("typedef bool (*nv_fnp_", c_source)


@unittest.skipIf(CC is None, "a C compiler is required to run programs")
class FunctionValueRuntimeTests(unittest.TestCase):
    def assert_output(self, text: str, expected: str) -> None:
        result = run_nava(text)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, expected, result.stderr)

    def test_a_value_is_called_through_the_pointer(self):
        self.assert_output("edition 2;\n" + DOUBLE +
                           "fn main() {\n"
                           "    let f: fn(i64) -> i64 = double\n"
                           "    print(f(21))\n"
                           "    print(double(21))\n"
                           "}\n",
                           "42\n42\n")

    def test_a_value_is_passed_and_returned(self):
        self.assert_output("edition 2;\n" + DOUBLE +
                           "fn twice(f: fn(i64) -> i64, x: i64) -> i64 {\n"
                           "    return f(f(x))\n"
                           "}\n"
                           "fn pick(which: bool) -> fn(i64) -> i64 {\n"
                           "    if which {\n"
                           "        return double\n"
                           "    }\n"
                           "    return double\n"
                           "}\n"
                           "fn main() {\n"
                           "    print(twice(double, 5))\n"
                           "    let g: fn(i64) -> i64 = pick(false)\n"
                           "    print(g(6))\n"
                           "}\n",
                           "20\n12\n")

    def test_two_values_of_one_signature_compose(self):
        self.assert_output("edition 2;\n" + DOUBLE +
                           "fn inc(x: i64) -> i64 {\n    return x + 1\n}\n"
                           "fn compose(f: fn(i64) -> i64, g: fn(i64) -> i64, x: i64) -> i64 {\n"
                           "    return f(g(x))\n"
                           "}\n"
                           "fn main() {\n"
                           "    let a: fn(i64) -> i64 = double\n"
                           "    let b: fn(i64) -> i64 = inc\n"
                           "    print(compose(a, b, 3))\n"
                           "    print(compose(b, a, 3))\n"
                           "}\n",
                           "8\n7\n")

    def test_a_void_function_is_a_value_too(self):
        self.assert_output("edition 2;\n"
                           "fn hello() {\n    print(1)\n}\n"
                           "fn run(f: fn() -> void) {\n    f()\n}\n"
                           "fn main() {\n"
                           "    run(hello)\n"
                           "    let f: fn() -> void = hello\n"
                           "    f()\n"
                           "}\n",
                           "1\n1\n")

    def test_an_array_parameter_travels_through_the_value(self):
        self.assert_output("edition 2;\n"
                           "fn sum(a: [i64]) -> i64 {\n"
                           "    let total: i64 = 0\n"
                           "    for x in a {\n"
                           "        total = total + x\n"
                           "    }\n"
                           "    return total\n"
                           "}\n"
                           "fn main() {\n"
                           "    let a: [i64] = [1, 2, 3]\n"
                           "    let total: fn([i64]) -> i64 = sum\n"
                           "    print(total(a))\n"
                           "    print(sum(a))\n"
                           "}\n",
                           "6\n6\n")

    def test_a_field_and_a_variant_payload_hold_a_value(self):
        self.assert_output("edition 2;\n" + DOUBLE +
                           "struct Ops {\n"
                           "    op: fn(i64) -> i64,\n"
                           "    value: i64,\n"
                           "}\n"
                           "enum Step {\n"
                           "    Apply(fn(i64) -> i64),\n"
                           "    Stop,\n"
                           "}\n"
                           "fn main() {\n"
                           "    let o: Ops = Ops(op: double, value: 4)\n"
                           "    let f: fn(i64) -> i64 = o.op\n"
                           "    print(f(o.value))\n"
                           "    let step: Step = Step::Apply(f)\n"
                           "    match step {\n"
                           "        Step::Apply(g) => { print(g(5)) }\n"
                           "        Step::Stop => { print(-1) }\n"
                           "    }\n"
                           "}\n",
                           "8\n10\n")

    def test_a_generic_instance_takes_one_copy_per_signature(self):
        self.assert_output("edition 2;\n"
                           "fn apply<T>(f: fn(T) -> T, x: T) -> T {\n"
                           "    return f(x)\n"
                           "}\n"
                           + DOUBLE +
                           "fn flip(x: bool) -> bool {\n    return !x\n}\n"
                           "fn main() {\n"
                           "    print(apply(double, 4))\n"
                           "    print(apply(flip, true))\n"
                           "    print(apply(i64)(double, 5))\n"
                           "}\n",
                           "8\nfalse\n10\n")

    def test_a_nested_signature_is_its_own_type(self):
        self.assert_output("edition 2;\n" + DOUBLE +
                           "fn twice(f: fn(i64) -> i64, x: i64) -> i64 {\n"
                           "    return f(f(x))\n"
                           "}\n"
                           "fn apply2(f: fn(fn(i64) -> i64, i64) -> i64) -> i64 {\n"
                           "    return f(double, 3)\n"
                           "}\n"
                           "fn main() {\n"
                           "    print(apply2(twice))\n"
                           "}\n",
                           "12\n")


if __name__ == "__main__":
    unittest.main()
