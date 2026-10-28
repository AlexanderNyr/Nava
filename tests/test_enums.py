"""Edition 2, part two: enums and `match` (INDEPENDENCE.md N10b).

Three claims are tested, and the third is the one that matters most for a
language a model writes:

1. **Exhaustiveness is proved, not hoped for.** A `match` that misses a variant
   does not compile, and the message names the variant that is missing. The
   negative controls below each mutate one arm of a compiling program.
2. **A variant carries what it says it carries.** Bindings are typed copies of
   the fields; the generated C tests the tag, and the output of a running
   program is the witness.
3. **`match` is a statement, not an expression.** Arms return or assign; the
   checker counts an exhaustive, always-returning `match` as a return, which is
   what lets `fn area(s: Shape) -> i64` end in a `match` with no trailing
   `return`.
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

SHAPE = """enum Shape {
    Dot,
    Circle(i64),
    Rect(i64, i64)
}

"""


def program(*lines: str) -> str:
    body = "".join(f"    {line}\n" for line in lines)
    return "edition 2;\n\n" + SHAPE + "fn main() {\n" + body + "}\n"


class EnumFrontendTests(unittest.TestCase):
    def assert_code(self, text: str, code: str, message: str | None = None) -> NavaError:
        with self.assertRaises(NavaError) as caught:
            compile_source(text)
        self.assertEqual(caught.exception.code, code, caught.exception.message)
        if message is not None:
            self.assertIn(message, caught.exception.message)
        return caught.exception

    def test_an_enum_needs_edition_two(self):
        text = "enum Shape { Dot }\nfn main() {}\n"
        self.assert_code(text, "E0372", "needs edition 2")

    def test_declaration_rules(self):
        self.assert_code("edition 2;\nenum E { A }\nenum E { B }\nfn main() {}\n",
                         "E0390", "duplicate enum")
        self.assert_code("edition 2;\nenum E { A, A }\nfn main() {}\n",
                         "E0391", "twice")
        self.assert_code("edition 2;\nenum E { }\nfn main() {}\n", "E0396", "no variants")
        # A string payload is a value and an array payload is storage the value
        # owns and releases, so both are allowed (N11a, N11b).
        self.assertTrue(compile_source("edition 2;\nenum E { A(str) }\nfn main() {}\n").code)
        self.assertTrue(compile_source("edition 2;\nenum E { A([i64]) }\nfn main() {}\n").code)
        self.assert_code("edition 2;\nenum E { A(Missing) }\nfn main() {}\n", "E0203")

    def test_a_struct_and_an_enum_cannot_share_a_name(self):
        text = "edition 2;\nstruct S { x: i64 }\nenum S { A }\nfn main() {}\n"
        self.assert_code(text, "E0390", "share one namespace")

    def test_construction_rules(self):
        self.assert_code(program("let s = Shape::Nope(1)"), "E0393", "no variant")
        self.assert_code(program("let s = Shape::Circle(1, 2)"), "E0394", "takes 1 field")
        self.assert_code(program("let s = Shape::Circle(true)"), "E0311")

    def test_match_rules(self):
        self.assert_code(program("let n = 1", "match n { _ => { print(n) } }"),
                         "E0395", "expects an enum value")
        self.assert_code(program("let s = Shape::Dot",
                                 "match s { Shape::Dot => { print(1) } }"),
                         "E0396", "does not cover Shape::Circle, Shape::Rect")
        self.assert_code(program("let s = Shape::Dot",
                                 "match s { Shape::Dot => { print(1) }",
                                 "Shape::Dot => { print(2) }",
                                 "Shape::Rect(w, h) => { print(w + h) } }"),
                         "E0397")
        self.assert_code(program("let s = Shape::Dot",
                                 "match s { _ => { print(1) }",
                                 "Shape::Circle(r) => { print(r) } }"),
                         "E0398", "can never be reached")
        self.assert_code(program("let s = Shape::Dot", "match s { Dot => { print(1) } }"),
                         "E0392", "must name its enum")
        self.assert_code(program("let s = Shape::Dot",
                                 "match s { Shape::Dot => { print(1) }",
                                 "Shape::Rect(w) => { print(w) }",
                                 "Shape::Circle(r) => { print(r) } }"),
                         "E0394", "binds 1")
        self.assert_code(program("let s = Shape::Dot", "match s { }"), "E0396", "at least one arm")

    def test_an_enum_cannot_be_printed_or_compared(self):
        self.assert_code(program("let s = Shape::Dot", "print(s)"), "E0323")
        self.assert_code(program("let s = Shape::Dot", "let t = Shape::Dot",
                                 "if s == t { print(1) }"), "E0320")

    def test_an_exhaustive_match_counts_as_a_return(self):
        text = ("edition 2;\n" + SHAPE + "fn area(s: Shape) -> i64 {\n"
                "    match s {\n"
                "        Shape::Dot => { return 0 }\n"
                "        Shape::Circle(r) => { return 3 * r * r }\n"
                "        Shape::Rect(w, h) => { return w * h }\n"
                "    }\n}\nfn main() { print(area(Shape::Dot)) }\n")
        self.assertTrue(compile_source(text).code)
        # The same function with a wildcard arm and no variant arms at all.
        text = ("edition 2;\n" + SHAPE + "fn area(s: Shape) -> i64 {\n"
                "    match s {\n        _ => { return 1 }\n    }\n}\n"
                "fn main() { print(area(Shape::Dot)) }\n")
        self.assertTrue(compile_source(text).code)

    def test_a_match_that_does_not_return_everywhere_still_needs_a_return(self):
        text = ("edition 2;\n" + SHAPE + "fn area(s: Shape) -> i64 {\n"
                "    match s {\n        Shape::Dot => { print(0) }\n"
                "        _ => { return 1 }\n    }\n}\n"
                "fn main() { print(area(Shape::Dot)) }\n")
        self.assert_code(text, "E0306", "on every path")

    def test_enums_nest_in_structs_and_each_other(self):
        text = ("edition 2;\nenum Kind { Small, Big }\n"
                "struct Tagged { kind: Kind, value: i64 }\n"
                "enum Item { TaggedItem(Tagged), Empty }\n"
                "fn main() {\n"
                "    let t = Tagged(kind: Kind::Big, value: 3)\n"
                "    let i = Item::TaggedItem(t)\n"
                "    match i {\n"
                "        Item::TaggedItem(x) => { print(x.value) }\n"
                "        Item::Empty => { print(0) }\n"
                "    }\n}\n")
        self.assertTrue(compile_source(text).code)


@unittest.skipUnless(CC, "a C compiler is required to observe match behaviour")
class EnumRuntimeTests(unittest.TestCase):
    def run_source(self, text: str) -> str:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "program.nv"
            path.write_text(text, encoding="utf-8")
            result = subprocess.run([sys.executable, str(ROOT / "nava"), "run", str(path)],
                                    capture_output=True, text=True, timeout=120)
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout

    def test_every_variant_is_reachable_and_binds_its_fields(self):
        output = self.run_source("edition 2;\n" + SHAPE + """fn area(s: Shape) -> i64 {
    match s {
        Shape::Dot => { return 0 }
        Shape::Circle(r) => { return 3 * r * r }
        Shape::Rect(w, h) => { return w * h }
    }
}

fn main() {
    print(area(Shape::Dot))
    print(area(Shape::Circle(2)))
    print(area(Shape::Rect(3, 4)))
}
""")
        self.assertEqual(output, "0\n12\n12\n")

    def test_the_scrutinee_is_evaluated_once(self):
        output = self.run_source("""edition 2;

enum Step {
    Tick,
    Done
}

fn next(n: i64) -> Step {
    print(n)
    if n > 1 { return Step::Done }
    return Step::Tick
}

fn main() {
    let n = 0
    match next(2) {
        Step::Tick => { print(0 - 1) }
        Step::Done => { print(0 - 2) }
    }
}
""")
        # `2` is printed once: the value is evaluated once, then tested.
        self.assertEqual(output, "2\n-2\n")

    def test_bindings_are_copies(self):
        output = self.run_source("edition 2;\n\nstruct Box {\n    v: i64\n}\n\n" + """
enum Holder {
    One(Box)
}

fn main() {
    let b = Box(v: 5)
    let h = Holder::One(b)
    match h {
        Holder::One(inner) => {
            inner.v = 99
            print(inner.v)
        }
    }
    print(b.v)
}
""")
        self.assertEqual(output, "99\n5\n")

    def test_a_wildcard_arm_catches_the_variants_it_did_not_name(self):
        output = self.run_source("edition 2;\n" + SHAPE + """fn label(s: Shape) -> i64 {
    let code = 0
    match s {
        Shape::Circle(r) => { code = 100 + r }
        _ => { code = 0 - 1 }
    }
    return code
}

fn main() {
    print(label(Shape::Circle(4)))
    print(label(Shape::Dot))
    print(label(Shape::Rect(1, 2)))
}
""")
        self.assertEqual(output, "104\n-1\n-1\n")

    def test_arms_are_tried_in_source_order_and_run_once(self):
        output = self.run_source("""edition 2;

enum Sign {
    Negative,
    Zero,
    Positive
}

fn classify(n: i64) -> i64 {
    let s = Sign::Zero
    if n < 0 { s = Sign::Negative }
    if n > 0 { s = Sign::Positive }
    let result = 0
    match s {
        Sign::Negative => { result = 0 - 1 }
        Sign::Zero => { result = 0 }
        Sign::Positive => { result = 1 }
    }
    return result
}

fn main() {
    print(classify(0 - 7))
    print(classify(0))
    print(classify(9))
}
""")
        self.assertEqual(output, "-1\n0\n1\n")


if __name__ == "__main__":
    unittest.main()
