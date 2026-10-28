"""Array and ownership gates (PLAN.md phase N4).

Four things are checked here, and each can fail on its own:

1. **Type and ownership rules** — every new diagnostic has a program that must
   be rejected with the right code, and the rules that make the design sound
   (one owner, no copies, no array results, exclusive mutable borrows) are
   stated as tests rather than as intentions.
2. **Runtime behaviour** — elements, `len`, mutation through a `mut` borrow,
   nested scopes and early returns, checked against independent Python
   references on empty, single-element, duplicate and boundary inputs.
3. **Memory safety** — the generated C is compiled with the address and
   undefined-behaviour sanitizers and must stay quiet, and a deliberately
   disabled bounds check must be caught.
4. **Release** — compiled with `-DNAVA_TRACK_ALLOCS` the runtime reports how
   many arrays were still owned at exit, so a missing `free` is visible instead
   of being a silent leak.
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

from nava_lang.cli import compile_source, frontend  # noqa: E402
from nava_lang.model import NavaError  # noqa: E402

CC = shutil.which("cc")

#: The sanitizers the gate wants. `tests/registry.py` fails when they are
#: unavailable, so a skipped memory-safety test cannot pass as a green suite.
SANITIZER_FLAGS = ("-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-g")


def probe_sanitizers() -> bool:
    if CC is None:
        return False
    with tempfile.TemporaryDirectory() as temp:
        source = pathlib.Path(temp) / "probe.c"
        source.write_text("#include <stdint.h>\nint main(void){int64_t x=1;return (int)x-1;}\n",
                          encoding="utf-8")
        result = subprocess.run([CC, "-std=c11", *SANITIZER_FLAGS, str(source),
                                 "-o", str(pathlib.Path(temp) / "probe")],
                                capture_output=True, text=True, timeout=120)
    return result.returncode == 0


SANITIZERS = probe_sanitizers()


def compile_c(source: str, directory: str, extra=()) -> pathlib.Path:
    c_file = pathlib.Path(directory) / "program.c"
    binary = pathlib.Path(directory) / "program"
    c_file.write_text(frontend(source), encoding="utf-8")
    result = subprocess.run(
        [CC, "-std=c11", "-pedantic-errors", "-O2", *extra, str(c_file), "-o", str(binary)],
        capture_output=True, text=True, timeout=300,
    )
    if result.returncode != 0:
        raise AssertionError(f"the generated C did not compile:\n{result.stderr}")
    return binary


def run_nava(source: str, stdin: str = "", extra=(), timeout: int = 20):
    with tempfile.TemporaryDirectory() as temp:
        binary = compile_c(source, temp, extra=extra)
        return subprocess.run([str(binary)], input=stdin, capture_output=True,
                              text=True, timeout=timeout)


def live_arrays(stderr: str) -> int:
    """The count printed by a `-DNAVA_TRACK_ALLOCS` build."""
    for line in stderr.splitlines():
        if line.startswith("nava: live arrays at exit:"):
            return int(line.rsplit(":", 1)[1])
    raise AssertionError(f"no live-array report in:\n{stderr}")


class ArrayFrontendTests(unittest.TestCase):
    """Every array rule has a program that must be rejected, with its code."""

    def assert_code(self, code: str, source: str, message: str = ""):
        with self.assertRaises(NavaError) as caught:
            compile_source(source)
        self.assertEqual(caught.exception.code, code, caught.exception.message)
        if message:
            self.assertIn(message, caught.exception.message)

    def test_index_rules(self):
        self.assert_code("E0328", "fn main() { let a: [i64] = [1]; print(a[true]) }",
                         "an array index must be i64")
        self.assert_code("E0329", "fn main() { let x = 1; print(x[0]) }",
                         "only an array can be indexed")

    def test_only_a_variable_is_indexed(self):
        # The grammar has no postfix chain, so this is a syntax-level rejection
        # rather than a silently accepted temporary.
        with self.assertRaises(NavaError):
            compile_source("fn f() -> i64 { return 1 } fn main() { print(f()[0]) }")

    def test_arrays_cannot_be_copied_or_reassigned(self):
        self.assert_code("E0331", "fn f(a: [i64]) { let b = a } fn main() {}",
                         "arrays cannot be copied")
        self.assert_code(
            "E0332",
            "fn main() { let a: [i64] = [1]; let b: [i64] = [2]; a = b }",
            "cannot be reassigned")

    def test_arrays_cannot_be_returned(self):
        self.assert_code("E0330", "fn f() -> [i64] { return [1] } fn main() {}")
        self.assert_code("E0330", "fn g() -> i64 { return [1] } fn main() {}",
                         "cannot return an array")

    def test_borrow_rules(self):
        self.assert_code(
            "E0333",
            "fn clear(a: mut [i64]) { a[0] = 0 }\n"
            "fn wrap(a: [i64]) { clear(a) }\nfn main() {}",
            "must be an owned array variable")
        self.assert_code(
            "E0342",
            "fn zero(a: mut [i64], b: mut [i64]) { a[0] = 0; b[1] = 0 }\n"
            "fn main() { let a: [i64] = [1, 2]; zero(a, a) }",
            "two mutable parameters")
        self.assert_code(
            "E0336",
            "fn fill(a: [i64]) { a[0] = 1 }\nfn main() {}",
            "read-only parameter")
        self.assert_code("E0341", "fn f(n: mut i64) {} fn main() {}", "'mut' is only allowed")

    def test_an_owned_array_may_be_mutated(self):
        compile_source("fn main() { let a: [i64] = [1, 2]; a[0] = a[1] }")
        compile_source(
            "fn fill(a: mut [i64]) { a[0] = 1 }\n"
            "fn main() { let a: [i64] = [0]; fill(a); fill(a) }")

    def test_literal_rules(self):
        self.assert_code("E0334", "fn main() { let a = [1, true] }",
                         "must all have the same type")
        self.assert_code("E0335", "fn main() { let a = [] }", "needs a type annotation")
        self.assert_code("E0334", "fn f() {}\nfn main() { let a = [f()] }",
                         "i64, bool, or str")
        compile_source("fn main() { let a: [i64] = [] }")
        compile_source("fn main() { let a: [str] = [\"x\", \"y\"] }")

    def test_builtin_rules(self):
        self.assert_code("E0337", "fn main() { let a: [i64] = [1]; print(len(a, a)) }")
        self.assert_code("E0338", "fn main() { print(len(1)) }", "len expects an array")
        self.assert_code("E0339", "fn main() { let a: [i64] = array_zeros(1, 2) }")
        self.assert_code("E0340", "fn main() { let a: [i64] = array_zeros(true) }")
        compile_source("fn main() { print(len([1, 2, 3])) }")

    def test_arrays_are_not_scalars(self):
        self.assert_code("E0313", "fn main() { let a: [i64] = [1]; a[0] }")
        self.assert_code("E0311", "fn main() { let a: i64 = [1, 2] }")
        self.assert_code("E0320",
                         "fn main() { let a: [i64] = [1]; let b: [i64] = [1]; print(a == b) }")
        self.assert_code("E0323", "fn main() { let a: [i64] = [1]; print(a) }")
        self.assert_code("E0312", "fn main() { let a: [i64] = [1]; a[0] = true }")
        self.assert_code("E0316", "fn main() { let a: [i64] = [1]; if a {} }")

    def test_array_types_are_restricted(self):
        self.assert_code("E0206", "fn main() { let a: [[i64]] = [] }",
                         "cannot contain another array")
        self.assert_code("E0206", "fn main() { let a: [void] = [] }",
                         "must be i64, bool, or str")
        self.assert_code("E0330", "fn f(a: [i64], b: [i64]) -> [i64] { return a } fn main() {}",
                         "cannot return an array")

    def test_a_literal_may_span_lines_inside_the_brackets(self):
        compile_source("fn main() {\n"
                       "    let a: [i64] = [\n"
                       "        1,\n"
                       "        2,\n"
                       "        3\n"
                       "    ]\n"
                       "    print(len(\n"
                       "        a\n"
                       "    ))\n"
                       "}\n")
        # Outside the delimiters a newline still ends the statement.
        self.assert_code("E0202", "fn main() { let a: [i64] = [1, 2]\nprint(1) print(2) }")

    def test_bracket_diagnostics(self):
        self.assert_code("E0112", "fn main() { let a: [i64] = [1, 2]] }")
        self.assert_code("E0113", "fn main() { let a: [i64] = [1, 2 }")


@unittest.skipUnless(CC, "a C compiler is required for array runtime tests")
class ArrayRuntimeTests(unittest.TestCase):
    def test_literals_len_and_elements(self):
        result = run_nava('''fn main() {
    let a: [i64] = [3, 1, 2]
    print(len(a))
    print(a[0])
    print(a[2])
    a[1] = a[0] + a[2]
    print(a[1])
    let empty: [i64] = []
    print(len(empty))
    let words: [str] = ["alpha", "beta"]
    print(words[0])
    print(words[1])
    let flags: [bool] = [true, false]
    print(flags[0])
    print(flags[1])
    let single: [i64] = [7]
    print(single[0])
}''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "3\n3\n2\n5\n0\nalpha\nbeta\ntrue\nfalse\n7\n")

    def test_mutation_through_a_mutable_borrow_is_visible(self):
        result = run_nava('''fn fill(a: mut [i64], value: i64) {
    let i = 0
    while i < len(a) {
        a[i] = value
        i = i + 1
    }
}

fn total(a: [i64]) -> i64 {
    let sum = 0
    let i = 0
    while i < len(a) {
        sum = sum + a[i]
        i = i + 1
    }
    return sum
}

fn main() {
    let a: [i64] = [0, 0, 0]
    print(total(a))
    fill(a, 4)
    print(total(a))
    print(total(a))
}''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "0\n12\n12\n")

    def test_nested_scopes_loops_and_early_returns(self):
        result = run_nava('''fn first_positive(a: [i64]) -> i64 {
    let i = 0
    while i < len(a) {
        if a[i] > 0 {
            let pair: [i64] = [a[i], a[i] * 10]
            return pair[1]
        }
        i = i + 1
    }
    return 0
}

fn main() {
    let total = 0
    let round = 0
    while round < 5 {
        let scratch: [i64] = [round, round * 2]
        if round == 2 {
            total = total + scratch[1]
        }
        round = round + 1
    }
    print(total)
    print(first_positive([0 - 5, 3]))
    print(first_positive([]))
    print(first_positive([0 - 1]))
}''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "4\n30\n0\n0\n")

    def test_zeros_allocation_and_length_checks(self):
        source = ('fn main() { let n = read_i64(); let a: [i64] = array_zeros(n); '
                  'print(len(a)); if len(a) > 0 { print(a[0]) } }')
        with tempfile.TemporaryDirectory() as temp:
            binary = compile_c(source, temp)
            for data, expected in (("0\n", "0\n"), ("1\n", "1\n0\n"), ("4194304\n", "4194304\n0\n")):
                with self.subTest(data=data.strip()):
                    result = subprocess.run([str(binary)], input=data, capture_output=True,
                                            text=True, timeout=60)
                    self.assertEqual((result.returncode, result.stdout), (0, expected),
                                     result.stderr)
            for data, message in (("-1\n", "array length must not be negative"),
                                  ("4194305\n", "exceeds the limit of 4194304"),
                                  ("9223372036854775807\n", "exceeds the limit of 4194304")):
                with self.subTest(data=data.strip()):
                    result = subprocess.run([str(binary)], input=data, capture_output=True,
                                            text=True, timeout=60)
                    self.assertEqual(result.returncode, 1)
                    self.assertEqual(result.stdout, "")
                    self.assertIn(message, result.stderr)

    def test_out_of_range_access_fails_predictably(self):
        cases = [
            ("let a: [i64] = [1, 2]; print(a[2])", "array index 2 is out of bounds for length 2"),
            ("let a: [i64] = [1, 2]; print(a[0 - 1])", "array index -1 is out of bounds for length 2"),
            ("let a: [i64] = [1]; a[1] = 5", "array index 1 is out of bounds for length 1"),
            ("let a: [i64] = []; print(a[0])", "array index 0 is out of bounds for length 0"),
            ("let a: [bool] = [true]; print(a[1])", "array index 1 is out of bounds for length 1"),
            ("let a: [str] = [\"x\"]; print(a[5])", "array index 5 is out of bounds for length 1"),
            ("let a: [i64] = [1]; print(a[9223372036854775807])",
             "array index 9223372036854775807 is out of bounds for length 1"),
        ]
        for body, message in cases:
            with self.subTest(body=body):
                result = run_nava(f"fn main() {{ {body} }}")
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, "", "a failed access must not print a value")
                self.assertIn("nava runtime error:", result.stderr)
                self.assertIn(message, result.stderr)

    def test_a_failed_write_has_no_side_effect(self):
        # The index is checked before the value expression is evaluated, and the
        # program stops before any later statement runs.
        result = run_nava('''fn mark() -> i64 { print("must not print"); return 0 }
fn main() {
    let a: [i64] = [1]
    a[1] = mark()
    print("unreachable")
}''')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("array index 1 is out of bounds for length 1", result.stderr)
        stored = run_nava('''fn main() {
    let a: [i64] = [1, 2]
    a[1] = 9
    print(a[1])
}''')
        self.assertEqual((stored.returncode, stored.stdout), (0, "9\n"))

    def test_sorting_matches_an_independent_reference(self):
        program = '''fn sort(a: mut [i64]) {
    let i = 1
    while i < len(a) {
        let key = a[i]
        let j = i - 1
        while j >= 0 && a[j] > key {
            a[j + 1] = a[j]
            j = j - 1
        }
        a[j + 1] = key
        i = i + 1
    }
}

fn print_all(a: [i64]) {
    let i = 0
    while i < len(a) {
        print(a[i])
        i = i + 1
    }
}

fn main() {
    let a: [i64] = array_zeros(read_i64())
    let i = 0
    while i < len(a) {
        a[i] = read_i64()
        i = i + 1
    }
    sort(a)
    print_all(a)
}'''
        # Empty, single element, duplicates, already sorted, reversed, extremes.
        samples = [
            [],
            [0],
            [5, 5, 5, 5],
            [1, 2, 3, 4, 5],
            [5, 4, 3, 2, 1],
            [0, -1, 0, 1, -1, 2, -2],
            [-(2**63), 2**63 - 1, 0],
            [3, 1, 3, 1, 3, 1],
        ]
        for values in samples:
            with self.subTest(values=values):
                stdin = f"{len(values)}\n" + "".join(f"{value}\n" for value in values)
                result = run_nava(program, stdin=stdin)
                self.assertEqual(result.returncode, 0, result.stderr)
                expected = "".join(f"{value}\n" for value in sorted(values))
                self.assertEqual(result.stdout, expected)

    def test_sieve_matches_an_independent_reference(self):
        source = (ROOT / "tests/fixtures/positive/arrays_sieve.nv").read_text(encoding="utf-8")

        def primes_below(limit: int) -> int:
            if limit < 2:
                return 0
            sieve = [True] * (limit + 1)
            count = 0
            for value in range(2, limit + 1):
                if sieve[value]:
                    count += 1
                    for multiple in range(value * value, limit + 1, value):
                        sieve[multiple] = False
            return count

        with tempfile.TemporaryDirectory() as temp:
            binary = compile_c(source, temp)
            for limit in (0, 1, 2, 3, 4, 10, 97, 1000, 5000):
                with self.subTest(limit=limit):
                    result = subprocess.run([str(binary)], input=f"{limit}\n",
                                            capture_output=True, text=True, timeout=120)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout, f"{primes_below(limit)}\n")

    def test_index_access_goes_through_the_checked_helpers(self):
        """No generated code may index a buffer without the bounds check."""
        code = frontend('''fn total(a: [i64]) -> i64 {
    let sum = 0
    let i = 0
    while i < len(a) {
        sum = sum + a[i]
        i = i + 1
    }
    return sum
}

fn main() {
    let a: [i64] = [1, 2, 3]
    a[0] = total(a)
    print(a[0])
}''')
        raw = [line.strip() for line in code.splitlines() if ".data[" in line or "->data[" in line]
        self.assertTrue(raw, "the runtime helpers should be present")
        for line in raw:
            self.assertIn("nv_check_index", line,
                          f"buffer access without a bounds check: {line}")
        self.assertIn("nv_arr_i64_get(", code)
        self.assertIn("nv_arr_i64_set(", code)


@unittest.skipUnless(CC, "a C compiler is required for array memory tests")
class ArrayMemoryTests(unittest.TestCase):
    """Leaks, sanitizer silence and a disabled bounds check."""

    OWNERSHIP_PROGRAM = '''fn consume(a: [i64]) -> i64 {
    return len(a)
}

fn early(a: [i64]) -> i64 {
    let i = 0
    while i < len(a) {
        if a[i] > 0 {
            let pair: [i64] = [a[i], a[i]]
            return consume(pair)
        }
        i = i + 1
    }
    return 0
}

fn main() {
    let round = 0
    while round < 50 {
        let owned: [i64] = [round, round + 1]
        let zeros: [i64] = array_zeros(4)
        if round == 3 {
            let nested: [i64] = [1]
            print(consume(nested))
        }
        print(early(owned) + len(zeros))
        round = round + 1
    }
    print(len([1, 2, 3]))
}'''

    def test_every_normal_path_releases_its_storage(self):
        result = run_nava(self.OWNERSHIP_PROGRAM, extra=("-DNAVA_TRACK_ALLOCS",))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(live_arrays(result.stderr), 0,
                         "an array outlived the block that owned it")
        self.assertIn("1\n", result.stdout)

    def test_release_happens_once_per_iteration(self):
        # A leak would grow the live count with the number of iterations; a
        # double free would abort. Both are visible in this program.
        program = '''fn main() {
    let i = 0
    let total = 0
    while i < 2000 {
        let scratch: [i64] = [i, i + 1]
        total = total + scratch[0]
        i = i + 1
    }
    print(total)
}'''
        result = run_nava(program, extra=("-DNAVA_TRACK_ALLOCS",))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "1999000\n")
        self.assertEqual(live_arrays(result.stderr), 0)

    def test_an_error_path_ends_the_process_with_the_storage_still_owned(self):
        """A runtime error releases through process exit; the report says so."""
        program = '''fn main() {
    let a: [i64] = [1, 2]
    let i = 0
    while i < 5 {
        a[i] = i
        i = i + 1
    }
}'''
        result = run_nava(program, extra=("-DNAVA_TRACK_ALLOCS",))
        self.assertEqual(result.returncode, 1)
        self.assertIn("out of bounds for length 2", result.stderr)
        # Documented, not hidden: exit status 1 is the release mechanism here.
        self.assertEqual(live_arrays(result.stderr), 1)

    def test_arrays_are_memory_safe_under_sanitizers(self):
        for name, program, stdin in (
            ("ownership", self.OWNERSHIP_PROGRAM, ""),
            ("sort", (ROOT / "tests/fixtures/positive/arrays_sort.nv").read_text(encoding="utf-8"), ""),
            ("sieve", (ROOT / "tests/fixtures/positive/arrays_sieve.nv").read_text(encoding="utf-8"), "1000\n"),
            ("error", 'fn main() { let a: [i64] = [1]; print(a[3]) }', ""),
        ):
            with self.subTest(program=name):
                result = run_nava(program, stdin=stdin, extra=SANITIZER_FLAGS, timeout=300)
                self.assertNotIn("AddressSanitizer", result.stderr)
                self.assertNotIn("runtime error:", result.stderr.replace(
                    "nava runtime error: array index", ""))
                self.assertNotIn("undefined", result.stderr.lower())

    def test_a_disabled_bounds_check_is_caught(self):
        """The gate fails if the check is removed: the value is read anyway."""
        source = 'fn main() { let a: [i64] = [1, 2, 3]; print(a[3]) }'
        good = run_nava(source)
        self.assertEqual(good.returncode, 1)
        self.assertIn("out of bounds for length 3", good.stderr)

        broken = frontend(source).replace(
            "return array.data[nv_check_index(index, array.len)];", "return array.data[index];")
        self.assertNotIn("nv_check_index(index, array.len)", broken,
                         "the patch used to disable the check did not apply")
        with tempfile.TemporaryDirectory() as temp:
            c_file = pathlib.Path(temp) / "broken.c"
            binary = pathlib.Path(temp) / "broken"
            c_file.write_text(broken, encoding="utf-8")
            built = subprocess.run([CC, "-std=c11", "-O1", *SANITIZER_FLAGS, str(c_file),
                                    "-o", str(binary)], capture_output=True, text=True, timeout=300)
            self.assertEqual(built.returncode, 0, built.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=120)
        # Without the check the read is out of bounds: the sanitizer catches it,
        # and the documented error message never appears.
        self.assertNotIn("out of bounds for length 3", result.stderr)
        self.assertIn("AddressSanitizer", result.stderr)

    def test_the_runtime_documents_its_own_limit(self):
        code = frontend("fn main() { let a: [i64] = array_zeros(read_i64()); print(len(a)) }")
        self.assertIn("4194304", code)
        self.assertIn("array length must not be negative", code)
        # N8 emits only the runtime a program can reach, so the bounds-check
        # helper appears exactly when the program can index — and never
        # disappears from a program that can.
        self.assertNotIn("nv_check_index", code,
                         "a program that never indexes must not carry the check")
        indexing = frontend("fn main() {\n"
                            "    let a: [i64] = array_zeros(3)\n"
                            "    a[0] = 1\n"
                            "    print(a[0])\n"
                            "}")
        self.assertIn("nv_check_index", indexing)


if __name__ == "__main__":
    unittest.main()
