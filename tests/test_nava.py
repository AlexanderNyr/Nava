from __future__ import annotations

import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from nava_lang.cli import frontend
from nava_lang.model import NavaError

CC = shutil.which("cc")


class FrontendTests(unittest.TestCase):
    def assert_bad(self, source: str, message: str):
        with self.assertRaises(NavaError) as caught:
            frontend(source)
        self.assertIn(message, str(caught.exception))

    def test_requires_main(self):
        self.assert_bad("fn f() {}", "must define fn main")

    def test_main_signature(self):
        self.assert_bad("fn main(x: i64) {}", "main must")
        self.assert_bad("fn main() -> bool { return true }", "main must")

    def test_duplicate_function(self):
        self.assert_bad("fn main() {}\nfn main() {}", "duplicate function")

    def test_duplicate_variable_or_parameter(self):
        self.assert_bad("fn main() { let a = 1; let a = 2 }", "duplicate variable")
        self.assert_bad("fn f(a: i64, a: i64) {}\nfn main() {}", "duplicate variable")

    def test_variable_scope(self):
        self.assert_bad("fn main() { if true { let x = 1 }; print(x) }", "unknown variable")
        self.assert_bad("fn main() { let x = x }", "unknown variable")

    def test_assignment_must_match_type(self):
        self.assert_bad("fn main() { let a = 1; a = true }", "cannot assign bool to i64")
        self.assert_bad("fn main() { let a: bool = 1 }", "expected bool, got i64")

    def test_conditions_require_bool(self):
        self.assert_bad("fn main() { if 1 {} }", "if condition must be bool")
        self.assert_bad("fn main() { while 1 {} }", "while condition must be bool")

    def test_operators_require_types(self):
        self.assert_bad("fn main() { print(true + 1) }", "expects two i64")
        self.assert_bad('fn main() { print("x" < "y") }', "expects two i64")
        self.assert_bad("fn main() { print(1 == true) }", "same type")
        self.assert_bad("fn main() { print(!1) }", "expects bool")

    def test_call_validation(self):
        self.assert_bad("fn main() { nonexistent() }", "unknown function")
        self.assert_bad("fn main() { print() }", "exactly one")
        self.assert_bad("fn main() { read_i64(1) }", "no arguments")
        self.assert_bad("fn f(a: bool) {} fn main() { f(1) }", "argument 1")

    def test_void_values_cannot_be_used(self):
        self.assert_bad('fn main() { let x = print("x") }', "void value")
        self.assert_bad('fn main() { return print("x") }', "void call")
        self.assert_bad("fn f(x: void) {} fn main() {}", "cannot have type void")

    def test_every_return_path_required(self):
        self.assert_bad("fn f(x: bool) -> i64 { if x { return 1 } } fn main() {}", "every path")
        frontend("fn f(x: bool) -> i64 { if x { return 1 } else { return 2 } } fn main() {}")
        self.assert_bad("fn main() -> i64 { return true }", "return expects i64")

    def test_invalid_literals_and_strings(self):
        self.assert_bad("fn main() { print(9223372036854775808) }", "outside the i64 range")
        self.assert_bad("fn main() { print(" + "9" * 5000 + ") }", "outside the i64 range")
        self.assert_bad("fn main() { print(1__2) }", "underscore")
        self.assert_bad('fn main() { print("\\u0000") }', "NUL")
        self.assert_bad('fn main() { print("\\ud800") }', "surrogate")
        self.assert_bad('fn main() { print("\\q") }', "escape")

    def test_reserved_and_unknown_types(self):
        self.assert_bad("fn print() {} fn main() {}", "reserved builtin")
        self.assert_bad("fn main() { let read_i64 = 1 }", "reserved builtin")
        self.assert_bad("fn f(a: int) {} fn main() {}", "unknown type")

    def test_expression_statement_must_be_a_call(self):
        self.assert_bad("fn main() { 1 + 2 }", "only function calls")

    def test_newlines_comments_and_semicolons(self):
        source = '''// C-like comment
fn main(
) {
    # hash comment
    let x = (1_000 +
             2)
    print(x); print(3)
}
'''
        frontend(source)


@unittest.skipUnless(CC, "a C compiler is required for runtime tests")
class RuntimeTests(unittest.TestCase):
    def compile(self, source: str, directory: str, extra=()) -> Path:
        c_file = Path(directory) / "program.c"
        binary = Path(directory) / "program"
        c_file.write_text(frontend(source), encoding="utf-8")
        result = subprocess.run([CC, "-std=c11", "-pedantic-errors", "-O2", *extra, str(c_file), "-o", str(binary)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return binary

    def run_code(self, source: str, input_text="", extra=()):
        with tempfile.TemporaryDirectory() as temp:
            binary = self.compile(source, temp, extra=extra)
            return subprocess.run([str(binary)], input=input_text, capture_output=True, text=True, timeout=3)

    def test_examples(self):
        cases = [
            ("hello.nv", "", "Hello, world!\n14\ntrue\n"),
            ("sum.nv", "", "5050\n"),
            ("gcd.nv", "48 18\n", "6\n"),
            ("fibonacci.nv", "", "55\n"),
            ("logic.nv", "", "false\ntrue\n"),
        ]
        for name, data, expected in cases:
            with self.subTest(example=name):
                result = self.run_code((ROOT / "examples" / name).read_text(encoding="utf-8"), data)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, expected)

    def test_precedence_division_and_remainder(self):
        result = self.run_code('''fn main() {
print(2 + 3 * 4)
print(-7 / 3)
print(-7 % 3)
print(7 / -3)
print(7 % -3)
}''')
        self.assertEqual(result.stdout, "14\n-2\n-1\n-2\n1\n")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_shadowing_and_initializer_scope(self):
        result = self.run_code('''fn main() {
let x = 5
if true { let x = x + 1; print(x) }
print(x)
}''')
        self.assertEqual(result.stdout, "6\n5\n")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_functions_and_left_to_right_order(self):
        result = self.run_code('''fn mark(n: i64) -> i64 { print(n); return n }
fn add(a: i64, b: i64) -> i64 { return a + b }
fn main() { print(add(mark(1), mark(2)) + mark(3)) }
''')
        self.assertEqual(result.stdout, "1\n2\n3\n6\n")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_short_circuit(self):
        result = self.run_code('''fn bomb() -> bool { print("bad"); return true }
fn main() {
print(false && bomb())
print(true || bomb())
print(true && bomb())
print(false || bomb())
}''')
        self.assertEqual(result.stdout, "false\ntrue\nbad\ntrue\nbad\ntrue\n")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_runtime_error_precedes_later_side_effect(self):
        result = self.run_code('''fn mark() -> i64 { print("must not print"); return 1 }
fn main() { print((9223372036854775807 + 1) + mark()) }''')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("overflow", result.stderr)

    def test_while_condition_re_evaluated(self):
        result = self.run_code('''fn keep(i: i64) -> bool { print(i); return i < 3 }
fn main() { let i = 0; while keep(i) { i = i + 1 } }''')
        self.assertEqual(result.stdout, "0\n1\n2\n3\n")

    def test_strings_unicode_and_equality(self):
        source = r'''fn id(s: str) -> str { return s }
fn main() {
  print(id("Hello, \"world\"! 😀\nnext line"))
  print("emoji 😀" == id("emoji 😀"))
  print("caf\u00e9" == id("caf\u00e9"))
  print("one" != "two")
  print(true == false)
}'''
        result = self.run_code(source)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, 'Hello, "world"! 😀\nnext line\ntrue\ntrue\ntrue\nfalse\n')

    def test_read_i64_boundaries_and_invalid_input(self):
        source = "fn main() { print(read_i64()) }"
        with tempfile.TemporaryDirectory() as temp:
            binary = self.compile(source, temp)
            for data, expected in [("+00042", "42\n"), ("-9223372036854775808", "-9223372036854775808\n"), ("9223372036854775807", "9223372036854775807\n")]:
                with self.subTest(data=data):
                    result = subprocess.run([str(binary)], input=data, capture_output=True, text=True, timeout=3)
                    self.assertEqual((result.returncode, result.stdout), (0, expected), result.stderr)
            for data in ["", "+", "1x", "9223372036854775808", "-9223372036854775809"]:
                with self.subTest(data=data):
                    result = subprocess.run([str(binary)], input=data, capture_output=True, text=True, timeout=3)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("nava runtime error", result.stderr)

    def test_checked_arithmetic_errors(self):
        cases = [
            ("9223372036854775807 + 1", "overflow"),
            ("-9223372036854775808 - 1", "overflow"),
            ("3037000500 * 3037000500", "overflow"),
            ("--9223372036854775808", "overflow"),
            ("-9223372036854775808 / -1", "overflow"),
            ("-9223372036854775808 % -1", "overflow"),
            ("1 / 0", "division by zero"),
            ("1 % 0", "remainder by zero"),
        ]
        for expr, message in cases:
            with self.subTest(expr=expr):
                result = self.run_code(f"fn main() {{ print({expr}) }}")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)

    def test_portable_c_fallback_multiplication(self):
        source = "fn main() { let a = read_i64(); let b = read_i64(); print(a * b) }"
        low, high = -(2**63), 2**63 - 1
        pairs = [(low, -1), (low, 1), (low, 0), (high, 1), (high, 2),
                 (-3, -4), (-3, 4), (3, -4), (0, low), (3037000499, 3037000499)]
        rng = random.Random(42)
        pairs += [(rng.randrange(-(2**40), 2**40), rng.randrange(-(2**30), 2**30)) for _ in range(35)]
        with tempfile.TemporaryDirectory() as temp:
            binary = self.compile(source, temp, extra=("-DNAVA_FORCE_PORTABLE",))
            for a, b in pairs:
                with self.subTest(a=a, b=b):
                    result = subprocess.run([str(binary)], input=f"{a} {b}", capture_output=True, text=True, timeout=3)
                    value = a * b
                    if low <= value <= high:
                        self.assertEqual((result.returncode, result.stdout), (0, f"{value}\n"), result.stderr)
                    else:
                        self.assertNotEqual(result.returncode, 0)
                        self.assertIn("overflow", result.stderr)

    def test_main_exit_code(self):
        for value, expected in [("0", 0), ("300", 44), ("-1", 255)]:
            with self.subTest(value=value):
                result = self.run_code(f"fn main() -> i64 {{ return {value} }}")
                self.assertEqual(result.returncode, expected)

    def test_cli_build_check_emit_and_timeout(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source.nv"
            source.write_text('fn main() { print("ok") }', encoding="utf-8")
            for action in [["check", str(source)], ["emit-c", str(source), "-o", str(Path(temp)/"out.c")], ["build", str(source), "-o", str(Path(temp)/"out")]]:
                result = subprocess.run([sys.executable, str(ROOT / "nava"), *action], capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
            output = subprocess.run([str(Path(temp)/"out")], capture_output=True, text=True)
            self.assertEqual(output.stdout, "ok\n")
            result = subprocess.run([sys.executable, str(ROOT/"nava"), "emit-c", str(source), "-o", str(source)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn("different from the source", result.stderr)
            source.write_text("fn main() { while true {} }", encoding="utf-8")
            result = subprocess.run([sys.executable, str(ROOT/"nava"), "run", str(source), "--timeout", "0.1"], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 124)
            self.assertIn("exceeded", result.stderr)


@unittest.skipUnless(CC, "a C compiler is required for evaluation tests")
class EvaluationTests(unittest.TestCase):
    def test_public_gcd_samples_pass_and_bad_candidate_fails(self):
        for language, source in [("nava", "gcd.nv"), ("cpp", "gcd.cpp")]:
            if language == "cpp" and not shutil.which("c++"):
                continue
            result = subprocess.run(
                [sys.executable, str(ROOT/"eval/evaluate.py"), "--task", "gcd", "--language", language,
                 "--source", str(ROOT/"eval/solutions"/source)],
                capture_output=True, text=True, timeout=15,
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            report = json.loads(result.stdout)
            self.assertEqual(report["summary"], {"passed": 7, "total": 7, "all_passed": True})
            self.assertIsNone(report["generation"]["wall_ms_reported"])
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp)/"wrong.nv"
            source.write_text("fn main() { print(123) }", encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(ROOT/"eval/evaluate.py"), "--task", "gcd", "--language", "nava", "--source", str(source)],
                capture_output=True, text=True, timeout=15,
            )
            self.assertEqual(result.returncode, 1)
            self.assertFalse(json.loads(result.stdout)["summary"]["all_passed"])

    def test_c_candidate_passes_and_reports_labelled_semantics(self):
        if not shutil.which("cc"):
            self.skipTest("a C compiler is required for the C candidate")
        result = subprocess.run(
            [sys.executable, str(ROOT/"eval/evaluate.py"), "--task", "gcd", "--language", "c",
             "--source", str(ROOT/"eval/solutions/gcd.c")],
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["summary"], {"passed": 7, "total": 7, "all_passed": True})
        self.assertEqual(report["candidate"]["language"], "c")
        self.assertIn("unchecked-signed", report["candidate"]["arithmetic"])
        self.assertEqual(report["candidate"]["compile_command"][1:3], ["-std=c11", "-O2"])
        # The Nava candidate must be labelled with checked arithmetic, not the same text.
        nava = subprocess.run(
            [sys.executable, str(ROOT/"eval/evaluate.py"), "--task", "gcd", "--language", "nava",
             "--source", str(ROOT/"eval/solutions/gcd.nv")],
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(nava.returncode, 0, nava.stderr)
        self.assertIn("checked-i64", json.loads(nava.stdout)["candidate"]["arithmetic"])

    def test_other_public_tasks(self):
        programs = {
            "sum_to_n": "fn main() { let n = read_i64(); let i = 1; let s = 0; while i <= n { s = s + i; i = i + 1 }; print(s) }",
            "factorial": "fn main() { let n = read_i64(); let i = 2; let s = 1; while i <= n { s = s * i; i = i + 1 }; print(s) }",
            "fibonacci": "fn main() { let n = read_i64(); if n == 0 { print(0) } else { let a = 0; let b = 1; let i = 1; while i < n { let x = a + b; a = b; b = x; i = i + 1 }; print(b) } }",
            "is_prime": "fn prime(n: i64) -> bool { if n < 2 { return false }; if n % 2 == 0 { return n == 2 }; let d = 3; while d * d <= n { if n % d == 0 { return false }; d = d + 2 }; return true } fn main() { print(prime(read_i64())) }",
            "digit_sum": "fn main() { let n = read_i64(); let s = 0; while n > 0 { s = s + n % 10; n = n / 10 }; print(s) }",
            "max_of_three": "fn main() { let a = read_i64(); let b = read_i64(); let c = read_i64(); let m = a; if b > m { m = b }; if c > m { m = c }; print(m) }",
            "collatz_steps": "fn main() { let n = read_i64(); let steps = 0; while n != 1 { if n % 2 == 0 { n = n / 2 } else { n = 3 * n + 1 }; steps = steps + 1 }; print(steps) }",
        }
        with tempfile.TemporaryDirectory() as temp:
            for task, source in programs.items():
                with self.subTest(task=task):
                    candidate = Path(temp) / (task + ".nv")
                    candidate.write_text(source, encoding="utf-8")
                    result = subprocess.run(
                        [sys.executable, str(ROOT/"eval/evaluate.py"), "--task", task,
                         "--language", "nava", "--source", str(candidate)],
                        capture_output=True, text=True, timeout=15,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                    self.assertEqual(json.loads(result.stdout)["summary"]["passed"], 6)

    def test_prompt_contains_task_and_language(self):
        for language in ("nava", "c", "cpp"):
            result = subprocess.run(
                [sys.executable, str(ROOT/"eval/prompt.py"), "--task", "factorial", "--language", language],
                capture_output=True, text=True, timeout=5,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("n!", result.stdout)
            self.assertIn("source code only", result.stdout)
        c_prompt = subprocess.run(
            [sys.executable, str(ROOT/"eval/prompt.py"), "--task", "factorial", "--language", "c"],
            capture_output=True, text=True, timeout=5,
        )
        self.assertIn("C11", c_prompt.stdout)
        self.assertNotIn("Nava 0.1", c_prompt.stdout)


@unittest.skipUnless(CC, "a C compiler is required for benchmark tests")
@unittest.skipUnless(shutil.which("c++"), "a C++ compiler is required for benchmark tests")
class BenchTests(unittest.TestCase):
    def test_json_record_has_environment_samples_and_missing_model_fields(self):
        with tempfile.TemporaryDirectory() as temp:
            record_path = Path(temp) / "bench.json"
            result = subprocess.run(
                [sys.executable, str(ROOT/"bench/run.py"), "--iterations", "1000", "--samples", "2",
                 "--warmup", "0", "--json", str(record_path), "--quiet"],
                capture_output=True, text=True, timeout=120,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            record = json.loads(record_path.read_text(encoding="utf-8"))
        self.assertEqual(record["schema"], "nava-bench/1")
        self.assertEqual(record["failures"], [])
        self.assertTrue(record["reference_check"]["ok"])
        self.assertEqual(len(record["reference_check"]["cases"]), 9)
        environment = record["environment"]
        for field in ("platform", "python", "machine", "cc_version", "cxx_version", "flags", "isolation"):
            self.assertIn(field, environment)
        self.assertIsNotNone(environment["cc_version"])
        names = [program["name"] for program in record["programs"]]
        self.assertEqual(names, ["Nava", "C++ checked", "C++ unchecked"])
        for program in record["programs"]:
            self.assertEqual(len(program["runs_ms"]), 2)
            self.assertGreater(program["runs_ms"][0], 0)
            self.assertGreater(program["startup_ms"], 0)
            self.assertGreater(program["compile_ms"], 0)
            self.assertIn("median_ms", program)
            self.assertIsNone(program["source_tokens"])  # no tokenizer requested
        self.assertIsNotNone(record["programs"][0]["frontend_ms"])
        self.assertIsNone(record["programs"][1]["frontend_ms"])
        self.assertEqual(record["workload"]["warmup_runs"], 0)
        self.assertEqual(len(record["workload"]["run_order"]), 2)
        model = record["model_measurements"]
        self.assertEqual(model["status"], "not-measured")
        self.assertIsNone(model["generation_ms"])
        self.assertIsNone(model["prompt_tokens"])
        self.assertIsNone(model["completion_tokens"])


if __name__ == "__main__":
    unittest.main()
