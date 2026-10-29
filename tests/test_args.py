"""The `args` module and the two argument builtins behind it (PLAN.md N12g).

A module about a command line can only be tested with one, and that is the whole
reason this file exists: the standard library's self-test runs its programs with
no arguments, so the half of `args` that reads the real command line is tested
here and named in `TESTED_ELSEWHERE`.

What is under test:

1. **The words arrive unchanged.** `arg_count` counts the words after the program
   name — not the name itself — and `arg_at` returns each one byte for byte,
   including an argument that begins with `-`, one with a space in it and an
   empty one. Nothing splits, joins or interprets them.
2. **An out-of-range position stops the program**, with the index, the count and
   the plural spelled out, the way an array index does. A program cannot read
   past its arguments and get a plausible empty string instead.
3. **The two layers agree.** `flag`/`value` over the real command line answer what
   `flag_in`/`value_in` answer for the same words as an array — which is a claim
   about code that could drift, so it is checked rather than assumed.
4. **The command line reaches the program through the CLI**, `nava run prog.nv --
   …`, and `--` itself is not an argument. A flag that looks like an option to
   `nava` is still a word the program receives.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "tests"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import grants  # noqa: E402


def program(body: str) -> str:
    """A small edition-2 program that imports `args`, so a case reads as its body."""
    return ("edition 2;\n\nimport args;\n\nfn main() {\n"
            + "".join(f"    {line}\n" for line in body.strip().splitlines())
            + "}\n")


class ArgumentTests(unittest.TestCase):
    """What the program was given is what the program sees."""

    def test_the_words_arrive_unchanged(self):
        result = grants.run(
            program("""
                print(args::count())
                let i = 0
                while i < args::count() {
                    print(args::get(i))
                    i = i + 1
                }
            """),
            arguments=["alpha", "two words", "", "-x"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["4", "alpha", "two words", "", "-x"])

    def test_the_program_name_is_not_an_argument(self):
        result = grants.run(
            program("""
                print(args::count())
            """),
            arguments=[])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["0"])

    def test_the_builtins_answer_the_same_as_the_module(self):
        result = grants.run(
            program("""
                print(arg_count())
                print(arg_at(1))
                print(arg_count() == args::count())
                print(arg_at(1) == args::get(1))
            """),
            arguments=["a", "b"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["2", "b", "true", "true"])

    def test_a_position_past_the_end_stops_the_program(self):
        result = grants.run(
            program("""
                print(args::get(2))
                print(1)
            """),
            arguments=["a", "b"])
        self.assertEqual(result.returncode, 1)
        self.assertIn("argument index 2 is out of bounds for 2 arguments", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_a_negative_position_stops_the_program(self):
        result = grants.run(
            program("""
                print(args::get(-1))
            """),
            arguments=["a"])
        self.assertEqual(result.returncode, 1)
        self.assertIn("argument index -1 is out of bounds for 1 argument", result.stderr)

    def test_the_message_uses_the_singular_for_one_argument(self):
        """`1 argument`, not `1 arguments`: the message is read by people."""
        result = grants.run(
            program("""
                print(args::get(5))
            """),
            arguments=["only"])
        self.assertEqual(result.returncode, 1)
        self.assertIn("out of bounds for 1 argument", result.stderr)
        self.assertNotIn("1 arguments", result.stderr)


class FlagAndValueTests(unittest.TestCase):
    """The two layers, and the agreement between them."""

    def test_flag_finds_a_whole_word_and_nothing_shorter(self):
        result = grants.run(
            program("""
                print(args::flag("--verbose"))
                print(args::flag("--verb"))
                print(args::flag("verbose"))
            """),
            arguments=["--verbose", "run"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["true", "false", "false"])

    def test_value_returns_the_next_word_or_the_fallback(self):
        result = grants.run(
            program("""
                print(args::value("--count", "1"))
                print(args::value("--missing", "1"))
                print(args::value("--count", "1"))
            """),
            arguments=["--count", "7", "--missing"])
        self.assertEqual(result.returncode, 0, result.stderr)
        # `--count` is worth 7; `--missing` is last, so it has no value at all and
        # the fallback answers — an empty value would hide the mistake.
        self.assertEqual(result.stdout.splitlines(), ["7", "1", "7"])

    def test_a_flag_with_nothing_after_it_has_no_value(self):
        result = grants.run(
            program("""
                print(args::value("--count", "fallback"))
            """),
            arguments=["--count"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["fallback"])

    def test_the_two_layers_agree(self):
        """`flag`/`value` are the array functions applied to the real arguments."""
        result = grants.run(
            program("""
                let words: [str] = ["--count", "7", "--verbose"]
                print(args::flag("--verbose") == args::flag_in(words, "--verbose"))
                print(args::value("--count", "1") == args::value_in(words, "--count", "1"))
                print(args::flag("--absent") == args::flag_in(words, "--absent"))
                print(args::value("--absent", "d") == args::value_in(words, "--absent", "d"))
            """),
            arguments=["--count", "7", "--verbose"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["true", "true", "true", "true"])

    def test_the_first_occurrence_wins(self):
        result = grants.run(
            program("""
                print(args::value("--a", "z"))
            """),
            arguments=["--a", "x", "--a", "y"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["x"])


class CommandLineTests(unittest.TestCase):
    """The words travel through `nava run`, and `--` is the fence."""

    def nava(self, *arguments):
        return subprocess.run([sys.executable, str(ROOT / "nava"), *arguments],
                              capture_output=True, text=True, timeout=180)

    def test_the_program_receives_what_follows_the_double_dash(self):
        with grants.workspace({
            "main.nv": program("""
                print(args::count())
                print(args::get(0))
                print(args::value("--name", "none"))
            """),
        }) as root:
            result = self.nava("run", "--isolation", "off", str(root / "main.nv"),
                               "--", "--name", "nava", "extra")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["3", "--name", "nava"])

    def test_a_word_that_looks_like_an_option_is_still_an_argument(self):
        """`--timeout` after `--` belongs to the program, not to `nava`."""
        with grants.workspace({
            "main.nv": program("""
                print(args::get(0))
                print(args::count())
            """),
        }) as root:
            result = self.nava("run", "--isolation", "off", str(root / "main.nv"),
                               "--", "--timeout", "5")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["--timeout", "2"])

    def test_an_option_of_nava_after_the_source_belongs_to_nava(self):
        """`--timeout` is a trap: the program must not receive it, and the run
        must be bounded. Regression for a REMAINDER parser that ate the option."""
        with grants.workspace({
            "main.nv": program("""
                print(args::count())
            """),
            "loop.nv": "fn main() {\n    let i = 0\n    while i < 1 {\n    }\n}\n",
        }) as root:
            counted = self.nava("run", "--isolation", "off", str(root / "main.nv"),
                                "--timeout", "5")
            bounded = self.nava("run", "--isolation", "off", str(root / "loop.nv"),
                                "--timeout", "0.2")
        self.assertEqual(counted.returncode, 0, counted.stderr)
        self.assertEqual(counted.stdout.splitlines(), ["0"],
                         "`--timeout 5` was passed to the program instead of to nava")
        self.assertEqual(bounded.returncode, 124, bounded.stderr)

    def test_no_double_dash_means_no_arguments(self):
        with grants.workspace({
            "main.nv": program("""
                print(args::count())
            """),
        }) as root:
            result = self.nava("run", "--isolation", "off", str(root / "main.nv"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["0"])


if __name__ == "__main__":
    unittest.main()
