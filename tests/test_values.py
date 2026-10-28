"""N11, part one: values that hold text, and arrays of every value.

One claim, tested from both sides: **an array element and a struct field are
values with no owner**. A string value in this compiler is immutable and lives
for the whole run — nothing frees it and nothing writes it — so a `str` may sit
inside a struct, inside a variant payload, inside an element of an array, and no
copy of it can alias anything that matters. An *array* field still owns storage
with a single owner, so it stays refused until the memory work (N11b).

The second claim is about `array_zeros`: it hands the program elements it never
wrote, so it is allowed only when all-zero bits are a value of the element type.
`0`, `false` and `0.0` are; an all-zero `str` is a null pointer rather than an
empty string, and an all-zero function value cannot be called. Before this
increment `let names: [str] = array_zeros(2)` type-checked and then segfaulted on
`print(names[0])`; the negative control for that is `E0425`, and the fixture
`tests/fixtures/negative/array_zeros_str.nv` is that program.
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

from nava_lang.cli import compile_source  # noqa: E402
from nava_lang.model import NavaError  # noqa: E402

CC = shutil.which("cc")

PERSON = ("struct Person {\n"
          "    name: str,\n"
          "    age: i64\n"
          "}\n")

SHAPE = ("enum Shape {\n"
         "    Dot,\n"
         "    Circle(i64),\n"
         "    Label(str)\n"
         "}\n")


def run_nava(text: str) -> subprocess.CompletedProcess:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "program.nv"
        path.write_text(text, encoding="utf-8")
        return subprocess.run([sys.executable, str(ROOT / "nava"), "run", str(path)],
                              capture_output=True, text=True, timeout=120)


class ValueFrontendTests(unittest.TestCase):
    def assert_code(self, text: str, code: str, message: str | None = None) -> NavaError:
        with self.assertRaises(NavaError) as caught:
            compile_source(text)
        self.assertEqual(caught.exception.code, code, caught.exception.message)
        if message is not None:
            self.assertIn(message, caught.exception.message)
        return caught.exception

    def test_a_string_is_a_field_and_a_payload(self):
        self.assertTrue(compile_source("edition 2;\n" + PERSON +
                                       "fn main() {\n"
                                       "    let p: Person = Person(name: \"ada\", age: 36)\n"
                                       "    print(p.name)\n"
                                       "}\n").code)
        self.assertTrue(compile_source("edition 2;\n" + SHAPE +
                                       "fn main() {\n"
                                       "    let s: Shape = Shape::Label(\"x\")\n"
                                       "    print(1)\n"
                                       "}\n").code)

    def test_an_array_field_and_an_array_payload_are_still_refused(self):
        self.assert_code("edition 2;\nstruct Bag {\n    items: [i64]\n}\n"
                         "fn main() {\n    print(1)\n}\n",
                         "E0378", "cannot have type [i64]")
        self.assert_code("edition 2;\nenum Slot {\n    Items([i64])\n}\n"
                         "fn main() {\n    print(1)\n}\n",
                         "E0399", "cannot be [i64]")

    def test_an_enum_is_an_array_element(self):
        self.assertTrue(compile_source("edition 2;\n" + SHAPE +
                                       "fn main() {\n"
                                       "    let shapes: [Shape] = [Shape::Dot]\n"
                                       "    print(len(shapes))\n"
                                       "}\n").code)

    def test_an_undeclared_element_name_is_an_unknown_type(self):
        self.assert_code("edition 2;\nfn main() {\n"
                         "    let a: [Nope] = []\n    print(len(a))\n}\n",
                         "E0203", "unknown type 'Nope'")

    def test_array_zeros_needs_a_zero_value(self):
        error = self.assert_code("fn main() {\n"
                                 "    let names: [str] = array_zeros(2)\n"
                                 "    print(len(names))\n"
                                 "}\n",
                                 "E0425", "an all-zero str is a null pointer")
        self.assertIn("[str]", error.message)
        # The reason names the field that has no zero value.
        self.assert_code("edition 2;\n" + PERSON +
                         "fn main() {\n"
                         "    let people: [Person] = array_zeros(2)\n"
                         "    print(len(people))\n"
                         "}\n",
                         "E0425", "Person.name is str")
        # ... and the same rule covers a function value, whose zero is not callable.
        self.assert_code("edition 2;\n"
                         "fn double(x: i64) -> i64 { return x * 2 }\n"
                         "struct Ops {\n    op: fn(i64) -> i64\n}\n"
                         "fn main() {\n"
                         "    let a: [Ops] = array_zeros(2)\n"
                         "    print(len(a))\n"
                         "}\n",
                         "E0425", "an all-zero function value is not callable")
        # An enum whose *first* variant carries text has no zero value: the
        # zeroed tag names that variant, whose payload would be a null string.
        self.assert_code("edition 2;\n"
                         "enum Bad {\n    Named(str),\n    Plain\n}\n"
                         "fn main() {\n"
                         "    let a: [Bad] = array_zeros(2)\n"
                         "    print(len(a))\n"
                         "}\n",
                         "E0425", "Bad::Named(1) is str")

    def test_a_zeroable_element_type_still_works(self):
        # A payload-free first variant zeroes to itself, so a `str` in a later
        # variant is no obstacle.
        for element, decl in (("bool", ""), ("Point", "struct Point { x: i64, y: i64 }\n"),
                              ("Tag", "enum Tag { Plain, Named(str) }\n"),
                              ("Shape", SHAPE)):
            with self.subTest(element=element):
                self.assertTrue(compile_source(
                    "edition 2;\n" + decl +
                    f"fn main() {{\n    let a: [{element}] = array_zeros(2)\n"
                    "    print(len(a))\n}\n").code)


@unittest.skipIf(CC is None, "a C compiler is required to run programs")
class ValueRuntimeTests(unittest.TestCase):
    def assert_output(self, text: str, expected: str) -> None:
        result = run_nava(text)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, expected, result.stderr)

    def test_a_string_field_copies_while_the_string_is_shared(self):
        self.assert_output("edition 2;\n" + PERSON +
                           "fn main() {\n"
                           "    let ada: Person = Person(name: \"Ada\", age: 36)\n"
                           "    let copy = ada\n"
                           "    copy.age = 37\n"
                           "    print(ada.age)\n"
                           "    print(copy.age)\n"
                           "    print(copy.name)\n"
                           "}\n",
                           "36\n37\nAda\n")

    def test_a_struct_with_text_travels_through_functions_and_arrays(self):
        self.assert_output("edition 2;\n" + PERSON +
                           "fn older(p: Person) -> Person {\n"
                           "    return Person(name: p.name, age: p.age + 1)\n"
                           "}\n"
                           "fn main() {\n"
                           "    let people: [Person] = [Person(name: \"a\", age: 1),\n"
                           "                            Person(name: \"b\", age: 2)]\n"
                           "    for person in people {\n"
                           "        print(person.name)\n"
                           "        print(older(person).age)\n"
                           "    }\n"
                           "}\n",
                           "a\n2\nb\n3\n")

    def test_a_string_payload_matches(self):
        self.assert_output("edition 2;\n" + SHAPE +
                           "fn main() {\n"
                           "    let shapes: [Shape] = [Shape::Dot, Shape::Circle(2),\n"
                           "                            Shape::Label(\"hi\")]\n"
                           "    for shape in shapes {\n"
                           "        match shape {\n"
                           "            Shape::Dot => { print(0) }\n"
                           "            Shape::Circle(r) => { print(r) }\n"
                           "            Shape::Label(text) => { print(text) }\n"
                           "        }\n"
                           "    }\n"
                           "}\n",
                           "0\n2\nhi\n")

    def test_an_array_of_enums_is_written_and_read(self):
        self.assert_output("edition 2;\n" + SHAPE +
                           "fn main() {\n"
                           "    let shapes: [Shape] = array_zeros(3)\n"
                           "    shapes[0] = Shape::Circle(7)\n"
                           "    shapes[2] = Shape::Label(\"z\")\n"
                           "    print(len(shapes))\n"
                           "    for shape in shapes {\n"
                           "        match shape {\n"
                           "            Shape::Dot => { print(-1) }\n"
                           "            Shape::Circle(r) => { print(r) }\n"
                           "            Shape::Label(text) => { print(text) }\n"
                           "        }\n"
                           "    }\n"
                           "}\n",
                           "3\n7\n-1\nz\n")

    def test_an_array_of_enums_slices_and_is_passed(self):
        self.assert_output("edition 2;\n" + SHAPE +
                           "fn count_dots(shapes: [Shape]) -> i64 {\n"
                           "    let total = 0\n"
                           "    for shape in shapes {\n"
                           "        match shape {\n"
                           "            Shape::Dot => { total = total + 1 }\n"
                           "            Shape::Circle(r) => { total = total + r }\n"
                           "            Shape::Label(text) => { total = total + 1 }\n"
                           "        }\n"
                           "    }\n"
                           "    return total\n"
                           "}\n"
                           "fn main() {\n"
                           "    let shapes: [Shape] = [Shape::Dot, Shape::Circle(2), Shape::Dot]\n"
                           "    print(count_dots(shapes))\n"
                           "    print(count_dots(shapes[1..3]))\n"
                           "}\n",
                           "4\n3\n")

    def test_a_mutable_array_of_enums_is_written_in_place(self):
        self.assert_output("edition 2;\n" + SHAPE +
                           "fn fill(shapes: mut [Shape]) {\n"
                           "    shapes[0] = Shape::Circle(5)\n"
                           "}\n"
                           "fn main() {\n"
                           "    let shapes: [Shape] = [Shape::Dot, Shape::Dot]\n"
                           "    fill(shapes)\n"
                           "    match shapes[0] {\n"
                           "        Shape::Dot => { print(0) }\n"
                           "        Shape::Circle(r) => { print(r) }\n"
                           "        Shape::Label(text) => { print(text) }\n"
                           "    }\n"
                           "}\n",
                           "5\n")


if __name__ == "__main__":
    unittest.main()
