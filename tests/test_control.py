"""`for`, `break` and `continue` (PLAN.md phase N5).

The gate has four parts:

1. **Semantics against independent references** — every looping program is
   compared with the same computation written in Python, over nested loops,
   early exits, empty and reversed ranges, parameters and array literals.
2. **Rules** — `break`/`continue` outside a loop, a writable loop variable, a
   non-`i64` bound, an iterable that is not an array, and the documented
   interaction with the return analysis.
3. **Release** — arrays owned by a loop body are released when `break` or
   `continue` leaves the iteration, checked with `-DNAVA_TRACK_ALLOCS`.
4. **Documentation** — the claimed output of the `for` example in
   `LANGUAGE.md` is produced by the program shown there.
"""
from __future__ import annotations

import pathlib
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


def run_nava(source: str, stdin: str = "", extra=(), timeout: int = 60):
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
                              text=True, timeout=timeout)


def live_arrays(stderr: str) -> int:
    for line in stderr.splitlines():
        if line.startswith("nava: live arrays at exit:"):
            return int(line.rsplit(":", 1)[1])
    raise AssertionError(f"no live-array report in:\n{stderr}")


class ForFrontendTests(unittest.TestCase):
    def assert_code(self, code: str, source: str, message: str = ""):
        with self.assertRaises(NavaError) as caught:
            compile_source(source)
        self.assertEqual(caught.exception.code, code, caught.exception.message)
        if message:
            self.assertIn(message, caught.exception.message)

    def test_break_and_continue_need_a_loop(self):
        self.assert_code("E0343", "fn main() { break }", "only allowed inside a loop")
        self.assert_code("E0344", "fn main() { continue }")
        self.assert_code("E0343", "fn main() { if true { break } }")
        # A function called from a loop does not make its own body a loop.
        self.assert_code("E0343", "fn f() { break }\nfn main() { for i in 0..2 { f() } }")

    def test_loop_variable_is_read_only(self):
        self.assert_code("E0351", "fn main() { for i in 0..3 { i = 5 } }",
                         "loop variable")
        self.assert_code("E0351", "fn main() { for x in [1, 2] { x = 5 } }")
        # A separate local with the same value is the documented workaround.
        compile_source("fn main() { for i in 0..3 { let j = i; j = j + 1 } }")

    def test_iterable_rules(self):
        self.assert_code("E0349", "fn main() { for i in 0..true { print(i) } }",
                         "bound must be i64")
        self.assert_code("E0350", "fn main() { let n = 3; for x in n { print(x) } }")
        self.assert_code("E0350", "fn main() { let s = \"abc\"; for c in s { print(c) } }")
        self.assert_code("E0350", "fn main() { let r = 0..3 }", "only allowed directly after")
        # An empty literal has no element type to iterate over, and a for loop
        # is not a place to infer one.
        self.assert_code("E0335", "fn main() { for x in [] { print(x) } }")

    def test_loop_variable_scope(self):
        self.assert_code("E0308", "fn main() { for i in 0..3 { print(i) }; print(i) }",
                         "unknown variable")
        self.assert_code("E0307", "fn main() { for i in 0..3 { let i = 1 } }",
                         "duplicate variable")
        # Shadowing in the loop body with a different name is fine, and an
        # outer variable with the same name is hidden inside the body.
        compile_source("fn main() { let i = 9; for i in 0..3 { print(i) }; print(i) }")

    def test_return_analysis_is_still_conservative(self):
        """A loop does not count as a guaranteed return (documented)."""
        self.assert_code("E0306", "fn f() -> i64 { for i in 0..3 { return i } } fn main() {}",
                         "every path")
        self.assert_code("E0306", "fn f() -> i64 { while true { return 1 } } fn main() {}")
        compile_source("fn f() -> i64 { for i in 0..3 { return i }\n return 0 }\nfn main() {}")

    def test_dead_code_after_a_jump_is_a_warning(self):
        for statement in ("return", "break", "continue"):
            with self.subTest(statement=statement):
                body = {"return": "return", "break": "break", "continue": "continue"}[statement]
                compilation = compile_source(
                    f"fn main() {{ for i in 0..2 {{ {body}; print(i) }} }}")
                self.assertEqual([warning.code for warning in compilation.warnings], ["W0001"])


@unittest.skipUnless(CC, "a C compiler is required for loop runtime tests")
class ForRuntimeTests(unittest.TestCase):
    def test_range_iteration_matches_python(self):
        for low, high in ((0, 3), (2, 2), (5, 2), (-2, 2), (7, 8), (-3, -1), (0, 0)):
            with self.subTest(low=low, high=high):
                result = run_nava(f"fn main() {{ for i in {low}..{high} {{ print(i) }} }}")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "".join(f"{i}\n" for i in range(low, high)))

    def test_element_iteration_matches_python(self):
        values = [3, -1, 0, 7]
        source = ("fn main() { for x in [" + ", ".join(str(v) for v in values) + "] { print(x) } }")
        result = run_nava(source)
        self.assertEqual(result.stdout, "".join(f"{value}\n" for value in values))

        for kind, literal in (("bool", "[true, false]"), ("str", '["a", "b"]')):
            with self.subTest(kind=kind):
                result = run_nava(f"fn main() {{ for x in {literal} {{ print(x) }} }}")
                expected = {"bool": "true\nfalse\n", "str": "a\nb\n"}[kind]
                self.assertEqual(result.stdout, expected)

    def test_element_iteration_over_a_parameter_and_an_array_variable(self):
        result = run_nava('''fn show(a: [i64]) {
    for x in a {
        print(x)
    }
}

fn main() {
    let values: [i64] = [4, 5]
    show(values)
    for x in values {
        print(x * 2)
    }
}''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "4\n5\n8\n10\n")

    def test_the_loop_variable_is_a_copy(self):
        result = run_nava('''fn main() {
    let values: [i64] = [1, 2, 3]
    for x in values {
        let doubled = x * 2
        print(doubled)
    }
    print(values[0])
}''')
        self.assertEqual(result.stdout, "2\n4\n6\n1\n")

    def test_break_and_continue_match_python(self):
        program = '''fn main() {
    let kept = 0
    for i in 0..20 {
        if i % 3 != 0 {
            continue
        }
        if i > 12 {
            break
        }
        kept = kept + i * i
    }
    print(kept)
}'''
        expected = 0
        for value in range(20):
            if value % 3 != 0:
                continue
            if value > 12:
                break
            expected += value * value
        result = run_nava(program)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, f"{expected}\n")

    def test_break_leaves_only_the_innermost_loop(self):
        program = '''fn main() {
    for i in 0..3 {
        for j in 0..10 {
            if j == 2 {
                break
            }
            print(i * 10 + j)
        }
    }
}'''
        result = run_nava(program)
        expected = "".join(f"{i * 10 + j}\n" for i in range(3) for j in range(2))
        self.assertEqual(result.stdout, expected)

    def test_continue_in_a_while_loop_rechecks_the_condition(self):
        program = '''fn main() {
    let i = 0
    let total = 0
    while i < 6 {
        i = i + 1
        if i % 2 == 0 {
            continue
        }
        total = total + i
    }
    print(total)
    print(i)
}'''
        # 1 + 3 + 5, and the counter still reaches 6: a `continue` that skipped
        # the comparison would loop forever or print a smaller total.
        result = run_nava(program, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "9\n6\n")

    def test_return_from_inside_a_loop_still_releases(self):
        program = '''fn first_big(a: [i64], limit: i64) -> i64 {
    for x in a {
        if x > limit {
            let pair: [i64] = [x, x]
            return pair[0]
        }
    }
    return 0 - 1
}

fn main() {
    print(first_big([1, 5, 9], 4))
    print(first_big([1, 2], 4))
}'''
        result = run_nava(program, extra=("-DNAVA_TRACK_ALLOCS",))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "5\n-1\n")
        self.assertEqual(live_arrays(result.stderr), 0)

    def test_break_and_continue_release_the_iteration_storage(self):
        program = '''fn main() {
    let total = 0
    for i in 0..5 {
        let owned: [i64] = [i, i + 1]
        if i == 1 {
            continue
        }
        if i == 3 {
            break
        }
        total = total + owned[0]
    }
    print(total)
}'''
        result = run_nava(program, extra=("-DNAVA_TRACK_ALLOCS",))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "2\n")
        self.assertEqual(live_arrays(result.stderr), 0,
                         "break or continue leaked the arrays of the iteration")

    def test_range_bounds_are_evaluated_once(self):
        program = '''fn limit() -> i64 {
    print("evaluated")
    return 3
}

fn main() {
    let total = 0
    for i in 0..limit() {
        total = total + i
    }
    print(total)
}'''
        result = run_nava(program)
        self.assertEqual(result.returncode, 0, result.stderr)
        # One call, not one per iteration: the bound is evaluated before the loop.
        self.assertEqual(result.stdout, "evaluated\n3\n")

    def test_nested_loops_match_a_python_reference(self):
        program = '''fn main() {
    let count = 0
    let checksum = 0
    for i in 0..7 {
        for j in 0..7 {
            if (i + j) % 4 == 0 {
                continue
            }
            if j > i + 2 {
                break
            }
            count = count + 1
            checksum = checksum + i * 8 + j
        }
    }
    print(count)
    print(checksum)
}'''
        count = 0
        checksum = 0
        for i in range(7):
            for j in range(7):
                if (i + j) % 4 == 0:
                    continue
                if j > i + 2:
                    break
                count += 1
                checksum += i * 8 + j
        result = run_nava(program)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, f"{count}\n{checksum}\n")

    def test_loops_are_memory_safe_under_sanitizers(self):
        program = '''fn build(n: i64) -> i64 {
    let scratch: [i64] = array_zeros(n)
    let sum = 0
    for i in 0..n {
        scratch[i] = i * i
        if i == 4 {
            continue
        }
        sum = sum + scratch[i]
    }
    return sum
}

fn main() {
    let total = 0
    for round in 0..50 {
        let owned: [i64] = [round, round + 1]
        total = total + owned[0] + build(8)
        if round == 40 {
            break
        }
    }
    print(total)
}'''
        result = run_nava(program, extra=SANITIZER_FLAGS, timeout=300)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("AddressSanitizer", result.stderr)
        self.assertNotIn("runtime error", result.stderr)

    def test_documented_example_output_is_real(self):
        """The `for` example in LANGUAGE.md prints what the document claims."""
        source, expected = documented_output("for word in [")
        result = run_nava(source)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, expected)


if __name__ == "__main__":
    unittest.main()
