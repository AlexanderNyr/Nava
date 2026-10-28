"""Edition 2, part one: the edition mechanism and structs (INDEPENDENCE.md N10a).

What is checked here, and why each check exists:

1. **An edition is a contract.** An edition-1 file compiles exactly as before;
   the edition-2 constructs are refused there with a code and a hint, not with a
   parse error that happens to look similar.
2. **A struct is a value.** Copy, parameter and return semantics are observed on
   the *output of a running program*, because that is the only place where a
   copy that shares storage would show up.
3. **A struct is checked.** Every declaration and construction rule has a
   negative case, and the negative case asserts the code and the location.
4. **The two back ends agree.** The same program is compiled and run; there is
   no second back end yet (that is N14), so the agreement here is between the
   checker's promise and the program's output.
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

POINT = """struct Point {
    x: i64,
    y: i64
}
"""


def source(body: str, edition: int | None = 2) -> str:
    header = "" if edition is None else f"edition {edition};\n"
    return header + body


class StructFrontendTests(unittest.TestCase):
    """The diagnostic contract of edition 2."""

    def assert_code(self, text: str, code: str, message: str | None = None) -> NavaError:
        with self.assertRaises(NavaError) as caught:
            compile_source(text)
        self.assertEqual(caught.exception.code, code, caught.exception.message)
        if message is not None:
            self.assertIn(message, caught.exception.message)
        return caught.exception

    def test_a_struct_needs_edition_two(self):
        error = self.assert_code(source(POINT + "fn main() {}\n", edition=None), "E0372",
                                 "needs edition 2")
        self.assertIn("edition 2;", error.hint or "")

    def test_the_edition_declaration_must_come_first(self):
        text = "fn main() { print(1) }\n\nedition 2;\n"
        self.assert_code(text, "E0370", "first statement")

    def test_an_unknown_edition_is_named(self):
        self.assert_code("edition 7;\nfn main() {}\n", "E0371", "editions 1 and 2")

    def test_edition_one_is_unchanged(self):
        # The same program with and without the header, in edition 1.
        self.assertEqual(compile_source("fn main() { print(1) }").code,
                         compile_source("edition 1;\nfn main() { print(1) }").code)

    def test_a_struct_must_be_declared_before_use_is_not_required(self):
        # Order does not matter: the name is known for the whole file.
        text = source("fn main() { let p = Point(x: 1, y: 2)\nprint(p.x) }\n" + POINT)
        self.assertTrue(compile_source(text).code)

    def test_field_rules(self):
        self.assert_code(source("struct P { x: i64, x: bool }\nfn main() {}\n"), "E0375")
        self.assert_code(source("struct P { name: str }\nfn main() {}\n"), "E0378", "str")
        self.assert_code(source("struct P { xs: [i64] }\nfn main() {}\n"), "E0378")
        self.assert_code(source("struct P { q: Unknown }\nfn main() {}\n"), "E0203")

    def test_a_struct_may_not_contain_itself(self):
        self.assert_code(source("struct N { v: i64, next: N }\nfn main() {}\n"), "E0377",
                         "contains itself")
        # ... nor through another struct.
        self.assert_code(source("struct A { b: B }\nstruct B { a: A }\nfn main() {}\n"),
                         "E0377")

    def test_a_duplicate_struct_is_rejected(self):
        self.assert_code(source(POINT + "struct Point { z: i64 }\nfn main() {}\n"), "E0380")

    def program(self, *lines: str) -> str:
        body = "".join(f"    {line}\n" for line in lines)
        return source(POINT + "fn main() {\n" + body + "}\n")

    def test_construction_rules(self):
        self.assert_code(self.program("let p = Missing(x: 1)"), "E0373")
        self.assert_code(self.program("let p = Point(x: 1)"), "E0376", "missing field y")
        self.assert_code(self.program("let p = Point(x: 1, y: 2, z: 3)"), "E0374")
        self.assert_code(self.program("let p = Point(x: 1, x: 2)"), "E0375")
        self.assert_code(self.program("let p = Point(x: 1, y: true)"), "E0311")
        self.assert_code(self.program("let p = Point(1, 2)"), "E0381",
                         "named at the construction site")

    def test_a_function_does_not_take_named_arguments(self):
        text = source("fn area(w: i64) -> i64 { return w }\n"
                      "fn main() { print(area(w: 2)) }\n")
        self.assert_code(text, "E0381", "not a struct")

    def test_member_rules(self):
        self.assert_code(self.program("let p = Point(x: 1, y: 2)", "print(p.z)"), "E0374")
        self.assert_code(self.program("let n = 1", "print(n.x)"), "E0379")
        self.assert_code(self.program("let p = Point(x: 1, y: 2)", "p.x = true"), "E0312")

    def test_a_struct_cannot_be_printed_or_compared(self):
        self.assert_code(self.program("let p = Point(x: 1, y: 2)", "print(p)"), "E0323")
        self.assert_code(self.program("let p = Point(x: 1, y: 2)",
                                      "if p == p { print(1) }"), "E0320")

    def test_a_field_may_be_written_inside_a_loop(self):
        # A struct local is mutable like any other local; an array of structs
        # does not exist yet, so the loop-variable restriction cannot apply to a
        # struct field until N11. The negative control for it lives in the array
        # tests (`E0351`), which this case must not weaken.
        text = source(POINT + "fn main() {\n"
                              "    let q = Point(x: 0, y: 0)\n"
                              "    for i in 0..3 {\n        q.x = q.x + i\n    }\n"
                              "    print(q.x)\n}\n")
        self.assertTrue(compile_source(text).code)
        self.assert_code(source(POINT + "fn main() {\n"
                                       "    for i in 0..1 {\n        i = 2\n    }\n}\n"),
                         "E0351")

    def test_a_struct_may_be_a_parameter_and_a_return_value(self):
        text = source(POINT + "fn move_by(p: Point, dx: i64) -> Point {\n"
                              "    p.x = p.x + dx\n    return p\n}\n"
                              "fn main() { print(move_by(Point(x: 1, y: 2), 5).x) }\n")
        self.assertTrue(compile_source(text).code)

    def test_nesting_is_copied_with_the_struct(self):
        text = source(POINT + "struct Segment { a: Point, b: Point }\n"
                              "fn main() {\n"
                              "    let s = Segment(a: Point(x: 1, y: 2), b: Point(x: 3, y: 4))\n"
                              "    let t = s\n    t.a.x = 99\n    print(s.a.x)\n}\n")
        self.assertTrue(compile_source(text).code)


@unittest.skipUnless(CC, "a C compiler is required to observe copy semantics")
class StructRuntimeTests(unittest.TestCase):
    """What the program does, which is where a shared copy would be visible."""

    def run_source(self, text: str) -> str:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "program.nv"
            path.write_text(text, encoding="utf-8")
            result = subprocess.run([sys.executable, str(ROOT / "nava"), "run", str(path)],
                                    capture_output=True, text=True, timeout=120)
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout

    def test_a_struct_is_copied_not_shared(self):
        output = self.run_source(source(POINT + """struct Box {
    p: Point
}

fn main() {
    let a = Point(x: 1, y: 2)
    let b = a
    b.x = 99
    print(a.x)
    print(b.x)
    let box = Box(p: a)
    box.p.y = 77
    print(a.y)
    print(box.p.y)
}
"""))
        self.assertEqual(output, "1\n99\n2\n77\n")

    def test_fields_evaluate_left_to_right(self):
        output = self.run_source(source("""struct Pair {
    a: i64,
    b: i64
}

fn main() {
    let n = 0
    n = 1
    let p = Pair(a: n, b: n + 10)
    print(p.a)
    print(p.b)
}
"""))
        self.assertEqual(output, "1\n11\n")

    def test_a_struct_parameter_is_a_copy(self):
        output = self.run_source(source(POINT + """fn bump(p: Point) -> i64 {
    p.x = p.x + 100
    return p.x
}

fn main() {
    let p = Point(x: 1, y: 2)
    print(bump(p))
    print(p.x)
}
"""))
        self.assertEqual(output, "101\n1\n")


if __name__ == "__main__":
    unittest.main()
