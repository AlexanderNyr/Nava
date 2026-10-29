"""The `time` module: the clock, and what `pure:` means when the answer moves
(PLAN.md N12i).

This is the last module of the phase and the only one whose subject is not
deterministic, so the file is organised around the question the phase's gate asks
of pure functions, read against a module that cannot answer it:

* **A reading is an effect, and the guard says so.** `now_seconds`, `now_millis`,
  `monotonic_millis` and `elapsed_millis` are `pure: no`; the three conversions
  (`seconds_to_millis`, `millis_to_seconds`, `millis_in_second`) are `pure: yes`.
  The claim is not a matter of taste: `contracts.purity_problems` reads the body,
  and `PURE_BUILTINS` does not contain the clock — so a module that claims
  `pure: yes` and calls a clock is refused. That refusal is a test here
  (`GuardTests`), and it is the negative control the phase asks for, in the one
  module where the temptation to claim purity is strongest.
* **The two clocks are different clocks.** The wall clock can be corrected
  backwards; the monotonic clock cannot, and it is what `elapsed_millis`
  subtracts. The tests check that the monotonic reading advances while a program
  spins, and that the wall clock agrees with the harness's own `time.time()` —
  a broken clock that answered `0` would satisfy neither.
* **The subtraction is not clamped.** A mark that lies in the future gives a
  negative elapsed time, because hiding that would turn a mistake into a
  measurement of zero.
* **The arithmetic is checked, not wrapped.** `seconds_to_millis(i64_max)` stops
  the program with `i64 overflow in *`; the failure is documented in the module
  and executed by the contract lane, and this file asserts the exact text.

The clock functions are covered by the standard library's own self-test as well —
each asserts a property that is true of every correct clock (later than 2020,
monotonic readings never decrease, `elapsed_millis(mark) >= 0`) — so this module
adds nothing to `TESTED_ELSEWHERE`. What the self-test cannot do is compare against
another clock or measure that time really passes; that is what this file adds.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "tests"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import grants  # noqa: E402
from nava_lang import contracts  # noqa: E402
from nava_lang.parser import parse  # noqa: E402


def program(body: str) -> str:
    """A small edition-2 program that imports `time`, so a case reads as its body."""
    return ("edition 2;\n\nimport time;\n\nfn main() {\n"
            + "".join(f"    {line}\n" for line in body.strip().splitlines())
            + "}\n")


def run(body: str) -> subprocess.CompletedProcess:
    return grants.run(program(body))


class WallClockTests(unittest.TestCase):
    """`now_seconds` / `now_millis`: one clock, read two ways."""

    def test_the_wall_clock_agrees_with_the_harness(self):
        before = time.time()
        result = run("print(time::now_seconds())")
        after = time.time()
        self.assertEqual(result.returncode, 0, result.stderr)
        # The judge is Python's own clock, so a Nava clock that is off by a year —
        # or one that answers the start of the monotonic epoch as if it were 1970 —
        # is caught rather than excused by a looser bound.
        self.assertTrue(before - 2 <= int(result.stdout) <= after + 2,
                        f"{result.stdout!r} is not between {before} and {after}")

    def test_milliseconds_and_seconds_read_the_same_clock(self):
        result = run("""
            print(time::now_millis() / 1000)
            print(time::now_seconds())
        """)
        self.assertEqual(result.returncode, 0, result.stderr)
        millis, seconds = (int(line) for line in result.stdout.split())
        # Two calls, so the pair may straddle a second boundary; more than that
        # would mean the two functions read different clocks.
        self.assertLessEqual(abs(millis - seconds), 1)

    def test_two_runs_of_the_same_binary_see_different_times(self):
        """A clock that is really a constant would pass every other test here."""
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            entry = root / "clock.nv"
            entry.write_text(program("print(time::now_millis())"), encoding="utf-8")
            binary = grants.build(grants.emit(program("print(time::now_millis())"),
                                              path=entry), root)
            first = subprocess.run([str(binary)], capture_output=True, text=True)
            time.sleep(0.02)
            second = subprocess.run([str(binary)], capture_output=True, text=True)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertLess(int(first.stdout), int(second.stdout),
                        "20 ms apart, the wall clock answered the same millisecond")


class MonotonicClockTests(unittest.TestCase):
    """`monotonic_millis` / `elapsed_millis`: a duration, not a date."""

    def test_the_monotonic_clock_advances_while_the_program_spins(self):
        result = run("""
            let mark = time::monotonic_millis()
            let spin = 0
            while time::elapsed_millis(mark) < 30 {
                spin = spin + 1
            }
            print(time::elapsed_millis(mark))
        """)
        self.assertEqual(result.returncode, 0, result.stderr)
        elapsed = int(result.stdout)
        # The loop exits when the module says 30 ms have passed, so the reading
        # has to have reached 30 by then; the upper bound only catches a clock
        # that jumps by hours.
        self.assertGreaterEqual(elapsed, 30)
        self.assertLess(elapsed, 10_000)

    def test_a_mark_in_the_future_gives_a_negative_elapsed_time(self):
        result = run("""
            let mark = time::monotonic_millis()
            print(time::elapsed_millis(mark + 5000) < 0)
        """)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "true")

    def test_a_second_mark_is_never_below_the_first(self):
        result = run("""
            let first = time::monotonic_millis()
            let second = time::monotonic_millis()
            print(second >= first)
        """)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "true")


class ArithmeticTests(unittest.TestCase):
    """The three pure conversions, and the one failure they can have."""

    def test_the_conversions_are_the_same_in_two_runs(self):
        """What `pure: yes` promises, checked by running the program twice."""
        body = """
            print(time::seconds_to_millis(3))
            print(time::millis_to_seconds(3761))
            print(time::millis_in_second(3761))
            print(time::millis_to_seconds(-3761))
            print(time::millis_in_second(-3761))
        """
        first = run(body)
        second = run(body)
        for result in (first, second):
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(first.stdout, second.stdout)
        self.assertEqual(first.stdout.split(), ["3000", "3", "761", "-3", "-761"])

    def test_a_second_is_rebuilt_from_its_two_parts(self):
        result = run("""
            let value = -1234567
            print(time::millis_to_seconds(value) * 1000 + time::millis_in_second(value) == value)
        """)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "true")

    def test_multiplication_that_does_not_fit_stops_the_program(self):
        result = run("""
            print(time::seconds_to_millis(9223372036854775807))
            print(1)
        """)
        self.assertEqual(result.returncode, 1)
        self.assertIn("i64 overflow in *", result.stderr)
        self.assertEqual(result.stdout, "")


class GuardTests(unittest.TestCase):
    """The `pure:` field read against a module whose subject is the clock."""

    def test_a_pure_claim_about_the_clock_is_refused(self):
        """The negative control: the one lie this module makes tempting."""
        source = ("edition 2;\n\n"
                  "/// A stamp, claimed to be pure.\n"
                  "///\n"
                  "/// pre: none\n"
                  "/// post: the clock\n"
                  "/// cost: O(1)\n"
                  "/// pure: yes\n"
                  "/// example: sample::stamp() != 0\n"
                  "pub fn stamp() -> i64 {\n"
                  "    return clock_millis()\n"
                  "}\n")
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "sample.nv"
            path.write_text(source, encoding="utf-8")
            program_ast = parse(source, path=str(path))
        problems = contracts.purity_problems(program_ast, "sample")
        self.assertEqual(problems,
                         ["sample::stamp: claims `pure: yes` but calls `clock_millis`, "
                          "which has an effect"])

    def test_the_clock_builtins_are_not_on_the_pure_list(self):
        for name in ("clock_seconds", "clock_millis", "clock_monotonic_millis"):
            with self.subTest(name=name):
                self.assertNotIn(name, contracts.PURE_BUILTINS)

    def test_the_module_marks_the_readings_impure_and_the_arithmetic_pure(self):
        source = (ROOT / "nava_lang" / "stdlib" / "time.nv").read_text(encoding="utf-8")
        parsed = parse(source, path="time.nv")
        claims = {name: contract.pure
                  for name, contract in contracts.contracts_of(parsed).items()}
        self.assertEqual(claims["seconds_to_millis"], "yes")
        self.assertEqual(claims["millis_to_seconds"], "yes")
        self.assertEqual(claims["millis_in_second"], "yes")
        self.assertEqual(claims["now_seconds"], "no")
        self.assertEqual(claims["now_millis"], "no")
        self.assertEqual(claims["monotonic_millis"], "no")
        self.assertEqual(claims["elapsed_millis"], "no")

    def test_a_wrapper_cannot_be_named_like_the_builtin_it_wraps(self):
        """Why the builtins are `clock_*` and the module owns `now_*`.

        A builtin's name is reserved in every module, not only in the entry one:
        a module that declared `pub fn clock_seconds` would be refused with
        `E0301`, so a builtin and the function that wraps it cannot share a word.
        The readings are therefore `clock_seconds`, `clock_millis` and
        `clock_monotonic_millis` behind the glass, and `time::now_seconds()`,
        `time::now_millis()` and `time::monotonic_millis()` in front of it — the
        name a program says belongs to the library, the primitive keeps its own.
        """
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / "clash.nv").write_text(
                "edition 2;\n\npub fn clock_seconds() -> i64 {\n"
                "    return 1\n}\n", encoding="utf-8")
            (root / "main.nv").write_text(
                "edition 2;\n\nimport clash;\n\nfn main() {\n"
                "    print(clash::clock_seconds())\n}\n", encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(ROOT / "nava"), "check",
                 str(root / "main.nv"), "--root", directory],
                capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(result.returncode, 2)
        self.assertIn("E0301", result.stderr)
        self.assertIn("'clock_seconds' is a reserved builtin", result.stderr)

    def test_the_module_reaches_the_clock_under_its_own_names(self):
        """The other half: the wrapper does call the clock, and says so."""
        result = run("""
            let seconds = time::now_seconds()
            let millis = time::now_millis()
            print(millis / 1000 - seconds < 2 && seconds - millis / 1000 < 2)
        """)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "true")


if __name__ == "__main__":
    unittest.main()
