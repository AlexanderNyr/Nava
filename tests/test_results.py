"""Edition 2, part three: `Option`, `Result` and `?` (INDEPENDENCE.md N10c).

`?` is the first construct in the language whose meaning is a *control-flow*
promise, so the tests are written around the promises rather than the syntax:

1. **A failure is forwarded, never converted** — the failure type must match and
   the success type may differ; both halves are checked.
2. **A failure really returns early** — the observable witness is that the rest
   of the function body does not run, and that the value reaching the caller is
   the failure the callee produced.
3. **`?` is only where it was promised** — a `let` initializer, in a function
   that returns the same enum. Everything else is a diagnostic, not a silent
   alternative.
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

HEADER = """edition 2;

fn parse_positive(n: i64) -> Result(i64, str) {
    if n > 0 { return Result(i64, str)::Ok(n) }
    return Result(i64, str)::Err("not positive")
}

fn found(n: i64) -> Option(i64) {
    if n > 0 { return Option(i64)::Some(n) }
    return Option(i64)::None
}

"""


def program(*lines: str) -> str:
    body = "".join(f"    {line}\n" for line in lines)
    return HEADER + "fn main() {\n" + body + "}\n"


class ResultFrontendTests(unittest.TestCase):
    def assert_code(self, text: str, code: str, message: str | None = None) -> NavaError:
        with self.assertRaises(NavaError) as caught:
            compile_source(text)
        self.assertEqual(caught.exception.code, code, caught.exception.message)
        if message is not None:
            self.assertIn(message, caught.exception.message)
        return caught.exception

    def test_the_prelude_enum_takes_its_arguments_in_parentheses(self):
        text = HEADER + ("fn main() {\n"
                         "    let r: Result(i64, str) = Result(i64, str)::Ok(1)\n"
                         "    match r {\n"
                         "        Result(i64, str)::Ok(v) => { print(v) }\n"
                         "        Result(i64, str)::Err(e) => { print(e) }\n"
                         "    }\n}\n")
        self.assertTrue(compile_source(text).code)

    def test_a_wrong_number_of_type_arguments_is_rejected(self):
        self.assert_code("edition 2;\nfn main() { let x = Result(i64)::Ok(1) }\n",
                         "E0412", "takes 2 type arguments")
        self.assert_code("edition 2;\nfn main() { let x = Option(i64, str)::Some(1) }\n",
                         "E0412", "takes 1 type argument")
        # A type argument that is a value is a shape error, not an arity error.
        self.assert_code("edition 2;\nfn main() { let x = Option(1, 2)::Some(1) }\n",
                         "E0415", "must be types")

    def test_only_the_prelude_takes_type_arguments(self):
        text = "edition 2;\nstruct P { x: i64 }\nfn main() { let p = P(i64)::Some(1) }\n"
        self.assert_code(text, "E0414", "cannot take type arguments")

    def test_propagation_rules(self):
        self.assert_code(program("let x = 1?"), "E0410", "expects an Option or a Result")
        self.assert_code(program("let x = parse_positive(1)?"), "E0411", "returns void")
        text = ("edition 2;\n"
                "fn inner() -> Result(bool, i64) { return Result(bool, i64)::Ok(true) }\n"
                "fn outer() -> Result(i64, str) {\n"
                "    let flag = inner()?\n"
                "    return Result(i64, str)::Ok(0)\n}\n"
                "fn main() { print(1) }\n")
        self.assert_code(text, "E0411", "forwards i64")
        text = HEADER + ("fn helper() -> Result(i64, str) {\n"
                         "    print(parse_positive(1)?)\n"
                         "    return Result(i64, str)::Ok(0)\n}\n"
                         "fn main() { print(1) }\n")
        self.assert_code(text, "E0413", "only allowed in a `let` initializer")

    def test_a_pattern_must_name_the_same_instantiation(self):
        text = program("let r = Result(i64, str)::Ok(1)",
                       "match r {",
                       "    Result(bool, str)::Ok(v) => { print(v) }",
                       "    _ => { print(0) }",
                       "}")
        self.assert_code(text, "E0395", "the value is Result(i64, str)")

    def test_the_success_type_may_differ_from_the_callers(self):
        text = ("edition 2;\n"
                "fn check() -> Result(bool, str) { return Result(bool, str)::Ok(true) }\n"
                "fn run() -> Result(i64, str) {\n"
                "    let flag = check()?\n"
                "    if flag { return Result(i64, str)::Ok(1) }\n"
                "    return Result(i64, str)::Ok(0)\n}\n"
                "fn main() { print(1) }\n")
        self.assertTrue(compile_source(text).code)

    def test_an_option_propagates_without_a_failure_type(self):
        text = ("edition 2;\n"
                "fn a() -> Option(bool) { return Option(bool)::Some(true) }\n"
                "fn b() -> Option(i64) {\n"
                "    let flag = a()?\n"
                "    return Option(i64)::Some(1)\n}\n"
                "fn main() { print(1) }\n")
        self.assertTrue(compile_source(text).code)

    def test_the_prelude_enums_are_ordinary_enums(self):
        self.assert_code(program("let r = Result(i64, str)::Ok(1)", "print(r)"), "E0323")
        self.assert_code(program("let r = Result(i64, str)::Ok(1)", "match r {",
                                 "    Result(i64, str)::Ok(v) => { print(v) }", "}"),
                         "E0396", "does not cover Result(i64, str)::Err")

    def test_an_unknown_type_argument_is_rejected(self):
        self.assert_code("edition 2;\nfn main() { let x = Option(Missing)::Some(1) }\n",
                         "E0203")


@unittest.skipUnless(CC, "a C compiler is required to observe propagation")
class ResultRuntimeTests(unittest.TestCase):
    def run_source(self, text: str) -> tuple[str, int]:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "program.nv"
            path.write_text(text, encoding="utf-8")
            result = subprocess.run([sys.executable, str(ROOT / "nava"), "run", str(path)],
                                    capture_output=True, text=True, timeout=120)
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout, result.returncode

    def test_a_failure_returns_early_and_reaches_the_caller(self):
        output, _ = self.run_source(HEADER + """fn doubled(n: i64) -> Result(i64, str) {
    let value = parse_positive(n)?
    print(1000)                 # must not run when parse_positive fails
    return Result(i64, str)::Ok(value * 2)
}

fn main() {
    match doubled(4) {
        Result(i64, str)::Ok(v) => { print(v) }
        Result(i64, str)::Err(e) => { print(e) }
    }
    match doubled(0 - 1) {
        Result(i64, str)::Ok(v) => { print(v) }
        Result(i64, str)::Err(e) => { print(e) }
    }
}
""")
        # Ok path: 1000 then 8. Err path: no 1000 at all, the message is forwarded.
        self.assertEqual(output, "1000\n8\nnot positive\n")

    def test_a_none_propagates_like_an_error(self):
        output, _ = self.run_source(HEADER + """fn twice(n: i64) -> Option(i64) {
    let value = found(n)?
    return Option(i64)::Some(value * 2)
}

fn main() {
    match twice(5) {
        Option(i64)::Some(v) => { print(v) }
        Option(i64)::None => { print(0 - 1) }
    }
    match twice(0) {
        Option(i64)::Some(v) => { print(v) }
        Option(i64)::None => { print(0 - 1) }
    }
}
""")
        self.assertEqual(output, "10\n-1\n")

    def test_a_chain_stops_at_the_first_failure(self):
        output, _ = self.run_source("""edition 2;

fn clamp(n: i64) -> Result(i64, str) {
    if n > 10 { return Result(i64, str)::Err("too big") }
    if n < 0 { return Result(i64, str)::Err("negative") }
    return Result(i64, str)::Ok(n)
}

fn total(a: i64, b: i64) -> Result(i64, str) {
    print(1)
    let x = clamp(a)?
    print(2)
    let y = clamp(b)?
    print(3)
    return Result(i64, str)::Ok(x + y)
}

fn main() {
    match total(1, 2) {
        Result(i64, str)::Ok(v) => { print(v) }
        Result(i64, str)::Err(e) => { print(e) }
    }
    match total(1, 99) {
        Result(i64, str)::Ok(v) => { print(v) }
        Result(i64, str)::Err(e) => { print(e) }
    }
}
""")
        # The second call stops inside the second `?`: `1` and `2` ran, `3` did not.
        self.assertEqual(output, "1\n2\n3\n3\n1\n2\ntoo big\n")

    def test_the_success_value_is_bound_with_its_own_type(self):
        output, _ = self.run_source("""edition 2;

fn check(n: i64) -> Result(bool, str) {
    if n > 0 { return Result(bool, str)::Ok(true) }
    return Result(bool, str)::Ok(false)
}

fn number(n: i64) -> Result(i64, str) {
    let flag = check(n)?
    if flag { return Result(i64, str)::Ok(1) }
    return Result(i64, str)::Ok(0)
}

fn main() {
    match number(5) {
        Result(i64, str)::Ok(v) => { print(v) }
        Result(i64, str)::Err(e) => { print(e) }
    }
    match number(0 - 5) {
        Result(i64, str)::Ok(v) => { print(v) }
        Result(i64, str)::Err(e) => { print(e) }
    }
}
""")
        self.assertEqual(output, "1\n0\n")

    def test_two_instances_of_one_prelude_enum_coexist(self):
        output, _ = self.run_source("""edition 2;

fn as_int(n: i64) -> Result(i64, str) {
    if n > 0 { return Result(i64, str)::Ok(n) }
    return Result(i64, str)::Err("bad")
}

fn as_text(s: str) -> Result(str, str) {
    return Result(str, str)::Ok(s)
}

fn main() {
    match as_int(3) {
        Result(i64, str)::Ok(v) => { print(v) }
        Result(i64, str)::Err(e) => { print(e) }
    }
    match as_text("hi") {
        Result(str, str)::Ok(v) => { print(v) }
        Result(str, str)::Err(e) => { print(e) }
    }
}
""")
        self.assertEqual(output, "3\nhi\n")


if __name__ == "__main__":
    unittest.main()
