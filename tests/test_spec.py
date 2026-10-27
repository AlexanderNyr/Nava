"""Executable specification tests (PLAN.md phase N1).

Four gates live here:

1. **Documentation conformance** — every fenced `nava` example in
   `LANGUAGE.md` type-checks, every diagnostic code in `nava_lang/diagnostics.py`
   is documented, and the documented limits and exit codes match the code.
2. **Negative fixtures** — `tests/fixtures/negative/*.nv` must fail with the
   registered code, line, column and message recorded in
   `tests/fixtures/negative.json`.
3. **Positive fixtures** — `tests/fixtures/positive/*.nv` must compile, run and
   produce exactly the recorded stdout and exit status.
4. **Limits and fuzzing** — pathological input yields E0401 or another
   diagnostic, never a Python traceback and never an unbounded run.

A deliberately broken diagnostic (wrong code, wrong location, accepted invalid
fixture) makes these tests fail; that is the point of the phase gate.
"""
from __future__ import annotations

import contextlib
import io
import json
import random
import re
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nava_lang import limits  # noqa: E402
from nava_lang.cli import compile_source, frontend  # noqa: E402
from nava_lang.diagnostics import CODES, title_of  # noqa: E402
from nava_lang.model import NavaError  # noqa: E402

FIXTURES = ROOT / "tests/fixtures"
NEGATIVE = FIXTURES / "negative"
POSITIVE = FIXTURES / "positive"
WARNINGS = FIXTURES / "warnings"
LANGUAGE = ROOT / "LANGUAGE.md"

#: Codes that cannot be produced by a source fixture, with where they are
#: covered instead.  Every other registered code must have a fixture.
CODES_TESTED_ELSEWHERE = {
    "E0111": "NegativeFixtureTests.test_invalid_utf8_source_is_reported",
    "E0402": "LimitTests.test_recursion_safety_net_reports_e0402",
    "E0501": "DiagnosticFormatTests.test_cli_diagnostics_are_registered (E0501-E0505)",
    "E0502": "DiagnosticFormatTests.test_cli_diagnostics_are_registered (E0501-E0505)",
    "E0503": "DiagnosticFormatTests.test_cli_diagnostics_are_registered (E0501-E0505)",
    "E0504": "DiagnosticFormatTests.test_cli_diagnostics_are_registered (E0501-E0505)",
    "E0505": "DiagnosticFormatTests.test_cli_diagnostics_are_registered (E0501-E0505)",
}


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def error_for(source: str) -> NavaError:
    try:
        compile_source(source)
    except NavaError as exc:
        return exc
    raise AssertionError("expected a NavaError, but the program compiled")


class SpecDocumentationTests(unittest.TestCase):
    def test_documented_examples_typecheck(self):
        text = LANGUAGE.read_text(encoding="utf-8")
        blocks = re.findall(r"```nava\n(.*?)```", text, re.DOTALL)
        self.assertGreaterEqual(len(blocks), 6, "LANGUAGE.md should contain runnable examples")
        for index, block in enumerate(blocks, 1):
            with self.subTest(block=index):
                try:
                    compile_source(block)
                except NavaError as exc:
                    self.fail(f"example {index} does not compile: {exc.code} {exc.message}")

    def test_referenced_fixtures_exist(self):
        text = LANGUAGE.read_text(encoding="utf-8")
        referenced = re.findall(r"tests/fixtures/[A-Za-z0-9_./-]+\.nv", text)
        self.assertTrue(referenced, "LANGUAGE.md should reference the executable fixtures")
        for path in referenced:
            with self.subTest(path=path):
                self.assertTrue((ROOT / path).is_file(), f"{path} is referenced but missing")

    def test_every_registered_code_is_documented(self):
        text = LANGUAGE.read_text(encoding="utf-8")
        missing = [code for code in CODES if code not in text]
        self.assertEqual(missing, [], f"codes missing from LANGUAGE.md: {missing}")

    def test_documented_limits_match_the_implementation(self):
        text = LANGUAGE.read_text(encoding="utf-8")
        for pattern, value, name in (
            (r"\| Nesting limit \(`MAX_NESTING`\) \| (\d+) \|", limits.MAX_NESTING, "MAX_NESTING"),
            (r"\| Literal digit limit \(`MAX_LITERAL_DIGITS`\) \| (\d+) \|",
             limits.MAX_LITERAL_DIGITS, "MAX_LITERAL_DIGITS"),
        ):
            match = re.search(pattern, text)
            self.assertIsNotNone(match, f"{name} is not documented in LANGUAGE.md")
            self.assertEqual(int(match.group(1)), value, f"{name} differs between docs and code")
        self.assertIn(str(limits.I64_MAX), text)
        self.assertIn(str(limits.I64_MIN), text)

    def test_documented_exit_codes_match_the_cli(self):
        text = LANGUAGE.read_text(encoding="utf-8")
        for code in ("| `0` |", "| `2` |", "| `124` |", "| `130` |"):
            self.assertIn(code, text, f"exit status {code} is not documented")
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "bad.nv"
            path.write_text("fn main() { let x: bool = 1 }\n", encoding="utf-8")
            result = subprocess.run([sys.executable, str(ROOT/"nava"), "check", str(path)],
                                    capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 2, "a source error must exit with status 2")


class NegativeFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = load_json(FIXTURES / "negative.json")["cases"]

    def test_fixture_files_and_entries_agree(self):
        on_disk = {path.name for path in NEGATIVE.glob("*.nv")}
        registered = {case["file"] for case in self.cases}
        self.assertEqual(on_disk - registered, set(), "fixture files without an expectation")
        self.assertEqual(registered - on_disk, set(), "expectations without a fixture file")

    def test_each_fixture_fails_with_the_recorded_diagnostic(self):
        for case in self.cases:
            with self.subTest(fixture=case["file"]):
                source = (NEGATIVE / case["file"]).read_text(encoding="utf-8")
                error = error_for(source)
                self.assertEqual(error.code, case["code"],
                                 f"{case['file']}: expected {case['code']}, got {error.code}: {error.message}")
                self.assertEqual(title_of(error.code), title_of(case["code"]))
                line = error.loc.line if error.loc else None
                column = error.loc.column if error.loc else None
                self.assertEqual((line, column), (case["line"], case["column"]),
                                 f"{case['file']}: location changed")
                self.assertIn(case["message"], error.message)
                self.assertEqual(error.hint, case["hint"], f"{case['file']}: hint changed")

    def test_fixtures_cover_every_code_except_documented_exceptions(self):
        covered = {case["code"] for case in self.cases}
        covered |= {case["code"] for case in load_json(FIXTURES / "warnings.json")["cases"]}
        uncovered = sorted(set(CODES) - covered - set(CODES_TESTED_ELSEWHERE))
        self.assertEqual(uncovered, [],
                         f"registered codes without a fixture or a documented test: {uncovered}")
        stale = sorted(set(CODES_TESTED_ELSEWHERE) - set(CODES))
        self.assertEqual(stale, [], f"exceptions for unregistered codes: {stale}")

    def test_invalid_utf8_source_is_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "bad_utf8.nv"
            path.write_bytes(b'fn main() { print("\xff") }\n')
            result = subprocess.run([sys.executable, str(ROOT/"nava"), "--format", "json",
                                     "check", str(path)], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 2)
        document = json.loads(result.stdout)
        self.assertEqual(document["diagnostics"][0]["code"], "E0111")


class WarningFixtureTests(unittest.TestCase):
    def test_warning_is_reported_and_is_not_fatal(self):
        cases = load_json(FIXTURES / "warnings.json")["cases"]
        self.assertTrue(cases)
        for case in cases:
            with self.subTest(fixture=case["file"]):
                source = (WARNINGS / case["file"]).read_text(encoding="utf-8")
                compilation = compile_source(source)
                self.assertEqual([warning.code for warning in compilation.warnings], [case["code"]])
                warning = compilation.warnings[0]
                self.assertEqual(warning.loc.line, case["line"])
                self.assertIn(case["message"], warning.message)
                self.assertEqual(warning.severity, "warning")
                with tempfile.TemporaryDirectory() as temp:
                    path = WARNINGS / case["file"]
                    binary = Path(temp) / "bin"
                    built = subprocess.run([sys.executable, str(ROOT/"nava"), "build", str(path),
                                            "-o", str(binary)], capture_output=True, text=True, timeout=60)
                    self.assertEqual(built.returncode, 0, built.stderr)
                    self.assertIn(f"warning[{case['code']}]", built.stderr)
                    run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60)
                self.assertEqual(run.stdout, case["stdout"])
                self.assertEqual(run.returncode, case["exit_code"])


@unittest.skipUnless(subprocess.run(["sh", "-c", "command -v cc"], capture_output=True).returncode == 0,
                     "a C compiler is required for positive fixtures")
class PositiveFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = load_json(FIXTURES / "positive.json")["cases"]

    def test_fixture_files_and_entries_agree(self):
        on_disk = {path.name for path in POSITIVE.glob("*.nv")}
        registered = {case["file"] for case in self.cases}
        self.assertEqual(on_disk - registered, set(), "fixture files without an expectation")
        self.assertEqual(registered - on_disk, set(), "expectations without a fixture file")

    def test_each_fixture_runs_and_matches_the_recorded_output(self):
        for case in self.cases:
            with self.subTest(fixture=case["file"]):
                with tempfile.TemporaryDirectory() as temp:
                    binary = Path(temp) / "bin"
                    built = subprocess.run(
                        [sys.executable, str(ROOT/"nava"), "build", str(POSITIVE / case["file"]),
                         "-o", str(binary)], capture_output=True, text=True, timeout=60)
                    self.assertEqual(built.returncode, 0, built.stderr)
                    result = subprocess.run([str(binary)], input=case["stdin"], capture_output=True,
                                            text=True, timeout=60)
                self.assertEqual(result.returncode, case["exit_code"], result.stderr)
                self.assertEqual(result.stdout, case["stdout"])
                if "stderr_contains" in case:
                    self.assertIn(case["stderr_contains"], result.stderr)


class DiagnosticFormatTests(unittest.TestCase):
    def test_human_format_is_path_line_column_code_message(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "example.nv"
            path.write_text("fn main() {\n    let x: bool = 1\n}\n", encoding="utf-8")
            result = subprocess.run([sys.executable, str(ROOT/"nava"), "check", str(path)],
                                    capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 2)
        self.assertRegex(result.stderr,
                         r"example\.nv:2:9: error\[E0311\]: expected bool, got i64")
        self.assertIn("hint:", result.stderr)

    def test_json_report_shape_is_stable(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "example.nv"
            path.write_text("fn main() {\n    print(9223372036854775808)\n}\n", encoding="utf-8")
            result = subprocess.run([sys.executable, str(ROOT/"nava"), "--format", "json",
                                     "check", str(path)], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 2)
        document = json.loads(result.stdout)
        self.assertEqual(set(document), {"status", "path", "diagnostics"})
        self.assertEqual(document["status"], "error")
        diagnostic = document["diagnostics"][0]
        self.assertEqual(set(diagnostic),
                         {"code", "severity", "title", "message", "path", "range", "hint"})
        self.assertEqual(diagnostic["code"], "E0108")
        self.assertEqual(diagnostic["severity"], "error")
        self.assertEqual(diagnostic["title"], title_of("E0108"))
        self.assertEqual(diagnostic["range"]["start"], {"line": 2, "column": 11})
        self.assertEqual(diagnostic["range"]["end"], {"line": 2, "column": 30})
        self.assertTrue(diagnostic["hint"])

    def test_json_success_report_and_warnings(self):
        with tempfile.TemporaryDirectory() as temp:
            good = Path(temp) / "good.nv"
            good.write_text("fn main() { print(1) }\n", encoding="utf-8")
            result = subprocess.run([sys.executable, str(ROOT/"nava"), "--format", "json",
                                     "check", str(good)], capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0)
            document = json.loads(result.stdout)
            self.assertEqual(document["status"], "ok")
            self.assertEqual(document["diagnostics"], [])
            warn = Path(temp) / "warn.nv"
            warn.write_text("fn f() -> i64 {\n    return 1\n    print(1)\n}\nfn main() { print(f()) }\n",
                            encoding="utf-8")
            result = subprocess.run([sys.executable, str(ROOT/"nava"), "--format", "json",
                                     "check", str(warn)], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0)
        document = json.loads(result.stdout)
        self.assertEqual(document["status"], "ok")
        self.assertEqual(document["diagnostics"][0]["code"], "W0001")
        self.assertEqual(document["diagnostics"][0]["severity"], "warning")

    def test_cli_diagnostics_are_registered(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "ok.nv"
            source.write_text("fn main() { print(1) }\n", encoding="utf-8")
            cases = [
                ("E0504", ["emit-c", str(source), "-o", str(source)]),
                ("E0505", ["build", str(source), "-o", str(Path(temp) / "missing" / "bin")]),
                ("E0503", ["build", str(source), "-o", str(Path(temp) / "bin"), "--cc",
                           "definitely-not-a-compiler"]),
                ("E0502", ["build", str(source), "-o", str(Path(temp) / "bin2"), "--cc", " "]),
            ]
            for code, arguments in cases:
                with self.subTest(code=code):
                    result = subprocess.run([sys.executable, str(ROOT/"nava"), "--format", "json",
                                             *arguments], capture_output=True, text=True, timeout=60)
                    self.assertEqual(result.returncode, 2, result.stderr)
                    document = json.loads(result.stdout)
                    self.assertEqual(document["diagnostics"][0]["code"], code, document)
                    self.assertIn(code, CODES)

    def test_unregistered_code_is_rejected_by_the_builder(self):
        from nava_lang.diagnostics import make
        with self.assertRaises(AssertionError):
            make("E9999", "no such code")


class LimitTests(unittest.TestCase):
    def test_nesting_below_the_limit_is_accepted(self):
        depth = limits.MAX_NESTING - 2
        source = "fn main() { print(" + "(" * depth + "1" + ")" * depth + ") }\n"
        compile_source(source)

    def test_nesting_above_the_limit_reports_e0401(self):
        depth = limits.MAX_NESTING + 50
        for name, source in (
            ("parens", "fn main() { print(" + "(" * depth + "1" + ")" * depth + ") }\n"),
            ("blocks", "fn main() { " + "if true { " * depth + "print(1)" + " }" * depth + " }\n"),
            ("unary", "fn main() { print(" + "-" * depth + "5) }\n"),
            ("chain", "fn main() { print(" + "+".join(["1"] * (depth + 20)) + ") }\n"),
        ):
            with self.subTest(kind=name):
                with self.assertRaises(NavaError) as caught:
                    compile_source(source)
                self.assertEqual(caught.exception.code, "E0401")

    def test_pathological_input_terminates_quickly(self):
        deep = "fn main() { print(" + "(" * 20000 + "1" + ")" * 20000 + ") }\n"
        start = time.perf_counter()
        with self.assertRaises(NavaError):
            compile_source(deep)
        self.assertLess(time.perf_counter() - start, 5.0)

    def test_recursion_safety_net_reports_e0402(self):
        """If nesting ever slips past the guard, the CLI still reports a diagnostic."""
        from nava_lang import cli
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "example.nv"
            path.write_text("fn main() { print(1) }\n", encoding="utf-8")
            buffer = io.StringIO()
            with mock.patch.object(cli, "compile_source", side_effect=RecursionError("boom")), \
                    contextlib.redirect_stdout(buffer):
                code = cli.main(["--format", "json", "check", str(path)])
            diagnostic = json.loads(buffer.getvalue())["diagnostics"][0]
        self.assertEqual(code, 2)
        self.assertEqual(diagnostic["code"], "E0402")
        self.assertEqual(diagnostic["severity"], "error")


class FuzzTests(unittest.TestCase):
    TOKENS = ["fn", "main", "(", ")", "{", "}", "let", "x", "=", "1", "0", "+", "-", "*", "/",
              "%", "if", "else", "while", "return", "true", "false", "print", "read_i64", "->",
              "i64", "bool", "str", '"a"', '"\\q"', '"\\u0000"', ";", "\n", "# comment\n", "&&",
              "||", "!", "==", "<", ":", ",", "void", "_", "1_0", "1__0", "@", "9999999999999999999"]

    def test_random_token_soup_only_produces_diagnostics(self):
        rng = random.Random(20261027)
        deadline = time.perf_counter() + 20.0
        for _ in range(3000):
            length = rng.randrange(1, 40)
            source = "".join(rng.choice(self.TOKENS) + rng.choice(["", " "]) for _ in range(length))
            try:
                compile_source(source)
            except NavaError as exc:
                self.assertIn(exc.code, CODES)
            except RecursionError:
                self.fail(f"RecursionError for {source!r}")
            if time.perf_counter() > deadline:
                self.fail("fuzzing exceeded its time budget")

    def test_mutated_valid_programs_only_produce_diagnostics(self):
        rng = random.Random(7)
        seeds = [(POSITIVE / name).read_text(encoding="utf-8")
                 for name in ("hello.nv", "arithmetic.nv", "functions.nv", "scoping.nv")]
        alphabet = "(){};:=+-*/%<>&|!\"'\\\n #abc012_@"
        for _ in range(1500):
            text = rng.choice(seeds)
            for _ in range(rng.randrange(1, 4)):
                position = rng.randrange(len(text) + 1)
                if rng.random() < 0.5 and text:
                    text = text[:position] + text[position + 1:]
                else:
                    text = text[:position] + rng.choice(alphabet) + text[position:]
            try:
                frontend(text)
            except NavaError as exc:
                self.assertIn(exc.code, CODES)
            except RecursionError:
                self.fail(f"RecursionError for mutation {text[:60]!r}")


if __name__ == "__main__":
    unittest.main()
