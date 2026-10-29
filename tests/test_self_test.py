"""The standard library tests itself, inside the language (PLAN.md N12c).

N12 asks for two things a contract cannot show on its own:

* **a `test` module** — an assertion and a runner inside Nava, so a Nava program
  can test itself without the Python suite;
* **a test for every public name** — the phase's gate says 100 % of public names
  carry a contract *and* at least one test, and here the test is written in Nava
  and run by Nava.

`tests/fixtures/positive/self_test.nv` is that program: one body per public
function of `math`, `sort` and `text`, each run through `test::run`, with
`test::summary` handing the failure count back so `main` can return it as the
exit status. This file is the guard around it:

1. every public name of every packaged module is named by the program;
2. it exits 0 with the output recorded in `positive.json`;
3. a one-line mutation makes it fail — naming the test, printing the expected
   and the actual value, and exiting nonzero — because a runner whose negative
   control passes is not a runner;
4. the verdict does not depend on how it was built or how often it runs.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nava_lang import contracts  # noqa: E402
from nava_lang.parser import parse  # noqa: E402

CC = shutil.which("cc")
PROGRAM = ROOT / "tests" / "fixtures" / "positive" / "self_test.nv"
STDLIB = ROOT / "nava_lang" / "stdlib"
NAVA = ROOT / "nava"
#: The modules the program is expected to cover: every one that ships.
MODULES = tuple(sorted(path.stem for path in STDLIB.glob("*.nv")
                       if path.stem != "test"))
#: Public names whose test cannot live in a self-test, and where it lives
#: instead. `io`'s file functions need a directory the program is *granted*, and
#: a grant is the caller's decision rather than the library's: a self-test that
#: demanded one would be asking for a capability to test a capability rule. The
#: list is explicit so that a new file-touching name cannot quietly join it, and
#: two meta-tests keep it honest — every key has to be a real public name, and
#: every key has to be mentioned by the file it points at.
TESTED_ELSEWHERE = {
    "io::read_lines": "tests/test_io.py",
    "io::write_lines": "tests/test_io.py",
    "io::append_line": "tests/test_io.py",
    # `args` reads the command line, and the self-test is started with none: the
    # four accessors are tested where a command line exists.
    "args::count": "tests/test_args.py",
    "args::get": "tests/test_args.py",
    "args::flag": "tests/test_args.py",
    "args::value": "tests/test_args.py",
    "fs::exists": "tests/test_fs.py",
    "fs::size": "tests/test_fs.py",
    "fs::entries": "tests/test_fs.py",
}


def public_names() -> list[str]:
    """Every public function of every packaged module, as `module::name`."""
    names = []
    for module in MODULES:
        program = parse((STDLIB / f"{module}.nv").read_text(encoding="utf-8"))
        for function in program.functions:
            if contracts.is_public(program, function):
                names.append(f"{module}::{function.name}")
    return names


def run_program(path: Path, opt: str = "-O2") -> subprocess.CompletedProcess:
    """Build a Nava program with a chosen optimisation level, then run it."""
    with tempfile.TemporaryDirectory() as directory:
        binary = Path(directory) / "self_test"
        built = subprocess.run([sys.executable, str(NAVA), "build", str(path),
                                "-o", str(binary), "--opt", opt.lstrip("-O")],
                               capture_output=True, text=True, timeout=300)
        if built.returncode != 0:
            raise AssertionError(f"the program did not build:\n{built.stderr}")
        return subprocess.run([str(binary)], capture_output=True, text=True, timeout=120)


def copy_with(source: Path, replacement: tuple[str, str], into: Path) -> Path:
    """A copy of the self-test with one line replaced, in the given directory."""
    old, new = replacement
    text = source.read_text(encoding="utf-8")
    if old not in text:
        raise AssertionError(f"{old!r} is not in {source.name}")
    target = into / source.name
    target.write_text(text.replace(old, new, 1), encoding="utf-8")
    return target


class SelfTestCoverageTests(unittest.TestCase):
    """Every public name of the library has a test written in Nava."""

    def test_the_program_names_every_public_function(self):
        source = PROGRAM.read_text(encoding="utf-8")
        missing = [name for name in public_names()
                   if name not in source and name not in TESTED_ELSEWHERE]
        self.assertEqual(missing, [],
                         f"these public names have no test in {PROGRAM.name}: {missing}")

    def test_every_name_tested_elsewhere_is_a_real_public_name(self):
        """The exception list cannot outlive the names it excuses."""
        real = set(public_names())
        self.assertEqual(sorted(set(TESTED_ELSEWHERE) - real), [],
                         "these names are excused from the self-test but do not exist")

    def test_a_name_tested_elsewhere_is_mentioned_where_it_says(self):
        """An exception is a pointer to a test, not a promise that one exists."""
        for name, where in TESTED_ELSEWHERE.items():
            with self.subTest(name=name):
                path = ROOT / where
                self.assertTrue(path.is_file(), f"{where} does not exist")
                self.assertIn(name.split("::")[1], path.read_text(encoding="utf-8"),
                              f"{where} never mentions {name}")

    def test_the_program_tests_every_module(self):
        source = PROGRAM.read_text(encoding="utf-8")
        for module in MODULES:
            with self.subTest(module=module):
                self.assertIn(f"import {module};", source)

    def test_the_library_has_public_names_to_test(self):
        """A coverage check over an empty surface would pass for the wrong reason."""
        self.assertGreaterEqual(len(public_names()), 14)


class SelfTestRunTests(unittest.TestCase):
    """The program passes, and its verdict is the one recorded in the fixture."""

    def test_it_exits_zero_with_the_recorded_output(self):
        cases = json.loads((ROOT / "tests/fixtures/positive.json").read_text())["cases"]
        recorded = next(case for case in cases if case["file"] == PROGRAM.name)
        result = run_program(PROGRAM)
        self.assertEqual(result.returncode, recorded["exit_code"], result.stderr)
        self.assertEqual(result.stdout, recorded["stdout"])

    def test_every_line_of_the_report_is_a_passing_test(self):
        result = run_program(PROGRAM)
        lines = result.stdout.splitlines()
        self.assertEqual([line for line in lines if line.startswith("ok   ")],
                         lines[:-1], "every test line is a pass")
        self.assertEqual(lines[-1], "standard library: ok")

    def test_the_verdict_does_not_depend_on_how_it_was_built(self):
        """`-O0` and `-O3` are two builds of one program, and must agree."""
        outputs = {(opt, run_program(PROGRAM, opt).stdout)
                   for opt in ("-O0", "-O2", "-O3")}
        self.assertEqual(len(outputs), 3, "three builds, three answers")
        self.assertEqual(len({stdout for _, stdout in outputs}), 1,
                         "the report changed with the optimisation level")

    def test_the_verdict_does_not_depend_on_how_often_it_runs(self):
        first = run_program(PROGRAM)
        second = run_program(PROGRAM)
        self.assertEqual((first.returncode, first.stdout), (second.returncode, second.stdout))


class SelfTestNegativeControlTests(unittest.TestCase):
    """A runner that cannot fail is not a runner."""

    def mutated(self, replacement: tuple[str, str]) -> Path:
        """One variant of the program, in a directory that is cleaned up after."""
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return copy_with(PROGRAM, replacement, Path(directory.name))

    def test_one_wrong_expectation_fails_the_run_and_names_the_test(self):
        entry = self.mutated(('test::expect_i64(math::abs(-7), 7, "abs of -7")',
                              'test::expect_i64(math::abs(-7), 8, "abs of -7")'))
        result = run_program(entry)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("  expected 8, got 7: abs of -7", result.stdout)
        self.assertIn("FAIL math::abs: 1 failed", result.stdout)
        self.assertIn("standard library: 1 failed", result.stdout)
        # Every other test still passes: one failure does not stop the suite,
        # and the ones whose test lives elsewhere were never in this run.
        self.assertEqual(sum(1 for line in result.stdout.splitlines()
                             if line.startswith("ok   ")),
                         len(public_names()) - len(TESTED_ELSEWHERE) - 1)

    def test_a_failing_expectation_is_the_exit_status(self):
        entry = self.mutated(('test::expect_true(text::is_ascii("hello"), "ASCII is ASCII")',
                              'test::expect_true(!text::is_ascii("hello"), "ASCII is not ASCII")'))
        result = run_program(entry)
        self.assertEqual(result.returncode, 1)
        self.assertIn("  not true: ASCII is not ASCII", result.stdout)

    def test_the_module_reports_its_own_expectations(self):
        """`run` and `summary` are the module's own code, so they are checked here too."""
        with tempfile.TemporaryDirectory() as directory:
            entry = Path(directory) / "case.nv"
            entry.write_text(
                "import test;\n\n"
                "fn body() -> i64 {\n"
                "    let failures = 0\n"
                '    failures = failures + test::expect_i64(1, 2, "one is not two")\n'
                '    failures = failures + test::expect_str("a", "b", "a is not b")\n'
                "    return failures\n"
                "}\n\n"
                "fn main() -> i64 {\n"
                '    return test::summary("two checks", test::run("body", body))\n'
                "}\n", encoding="utf-8")
            result = run_program(entry)
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertEqual(result.stdout.splitlines(), [
            "  expected 2, got 1: one is not two",
            '  expected "b", got "a": a is not b',
            "FAIL body: 2 failed",
            "two checks: 2 failed",
        ])

    @unittest.skipUnless(CC is not None, "a C compiler is required")
    def test_a_passing_body_reports_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            entry = Path(directory) / "case.nv"
            entry.write_text(
                "import test;\n\n"
                "fn body() -> i64 {\n"
                "    let failures = 0\n"
                '    failures = failures + test::expect_i64(2, 2, "two is two")\n'
                '    failures = failures + test::expect_true(1 < 2, "one is less than two")\n'
                '    failures = failures + test::expect_str("x", "x", "the same")\n'
                "    return failures\n"
                "}\n\n"
                "fn main() -> i64 {\n"
                '    return test::summary("three checks", test::run("body", body))\n'
                "}\n", encoding="utf-8")
            result = run_program(entry)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(result.stdout.splitlines(), ["ok   body", "three checks: ok"])


if __name__ == "__main__":
    unittest.main()
