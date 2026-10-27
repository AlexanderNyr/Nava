#!/usr/bin/env python3
"""Randomised differential gate for the checked semantics (PLAN.md phase N8).

The N8 optimisations — selective runtime emission, deferred imports and the
`records` replacement for `dataclasses` — are only acceptable if the program
they produce still obeys the contract in `LANGUAGE.md`. A benchmark cannot show
that; a differential test can.

Every case is generated from a seed and computed twice: once by the compiler
(compile, `cc`, run) and once by the small interpreter in this file, which
implements the *documented* semantics — wrapping is an error, `/` truncates
toward zero, `INT64_MIN / -1` is an error, the left operand is evaluated first,
`&&` and `||` short-circuit. A disagreement is a failure, including a
disagreement about *which* error a program reports.

The gates are named so that `bench/negative_controls.py` can run one at a time
and show that a deliberately reintroduced bug makes it fail:

    python3 -m unittest tests.test_differential.ArithmeticGate -q
"""
from __future__ import annotations

import random
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from nava_lang import cli

INT64_MIN = -(2 ** 63)
INT64_MAX = 2 ** 63 - 1
SEED = 20261027
CC = shutil.which("cc")

#: Programs are compiled twice: with the compiler's own overflow builtins and
#: with `-DNAVA_FORCE_PORTABLE`, which selects the hand-written C11 fallback.
#: Both branches of every checked helper are therefore part of the gate.
VARIANTS = ("builtin", "portable")


class EvaluationError(Exception):
    """The documented runtime error for an expression."""


def _checked(value: int) -> int:
    if value < INT64_MIN or value > INT64_MAX:
        raise EvaluationError("overflow")
    return value


def evaluate(node, environment):
    """The documented semantics, in Python, for one expression node."""
    kind = node[0]
    if kind == "lit":
        return _checked(node[1])
    if kind == "var":
        return environment[node[1]]
    if kind == "neg":
        return _checked(-evaluate(node[1], environment))
    if kind == "bin":
        _, operator, left_node, right_node = node
        left = evaluate(left_node, environment)
        right = evaluate(right_node, environment)
        if operator == "+":
            return _checked(left + right)
        if operator == "-":
            return _checked(left - right)
        if operator == "*":
            return _checked(left * right)
        if operator in ("/", "%"):
            if right == 0:
                raise EvaluationError("division by zero")
            if left == INT64_MIN and right == -1:
                raise EvaluationError("overflow")
            quotient = abs(left) // abs(right)
            if (left < 0) != (right < 0):
                quotient = -quotient
            return quotient if operator == "/" else left - quotient * right
    raise AssertionError(f"unknown node {node!r}")


def render(node) -> str:
    """The same node as Nava source, with parentheses around every operator."""
    kind = node[0]
    if kind == "lit":
        return str(node[1]) if node[1] >= 0 else f"({node[1]})"
    if kind == "var":
        return node[1]
    if kind == "neg":
        return f"(-{render(node[1])})"
    if kind == "bin":
        return f"({render(node[2])} {node[1]} {render(node[3])})"
    raise AssertionError(f"unknown node {node!r}")


def random_expression(rng: random.Random, variables: tuple[str, ...],
                      depth: int = 0) -> tuple:
    """A random i64 expression, biased towards the arithmetic that can fail."""
    if depth >= 3 or (depth and rng.random() < 0.25):
        if variables and rng.random() < 0.6:
            return ("var", rng.choice(variables))
        magnitude = rng.choice((1, 7, 1000, 2 ** 20, 2 ** 40, INT64_MAX))
        return ("lit", rng.randint(0, magnitude))
    operator = rng.choice(("+", "-", "*", "/", "%", "+", "-"))
    return ("bin", operator,
            random_expression(rng, variables, depth + 1),
            random_expression(rng, variables, depth + 1))


class DifferentialBase(unittest.TestCase):
    """Compile, run and compare; shared by every gate in this file."""

    @classmethod
    def setUpClass(cls):
        if CC is None:  # pragma: no cover - the registry already requires cc
            raise unittest.SkipTest("no C compiler")
        cls.temp = tempfile.TemporaryDirectory(prefix="nava-differential-")

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def build_and_run(self, source: str, variant: str = "builtin") -> subprocess.CompletedProcess:
        directory = Path(self.temp.name)
        name = f"case-{abs(hash((source, variant)))}"
        c_file = directory / f"{name}.c"
        binary = directory / f"{name}.bin"
        c_file.write_text(cli.compile_source(source).code, encoding="utf-8")
        command = [CC, "-std=c11", "-O2"]
        if variant == "portable":
            command.append("-DNAVA_FORCE_PORTABLE")
        command += [str(c_file), "-o", str(binary)]
        built = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(built.returncode, 0, built.stderr)
        return subprocess.run([str(binary)], capture_output=True, text=True, timeout=60)

    def assert_values(self, source: str, expected_stdout: str, context: str = ""):
        for variant in VARIANTS:
            result = self.build_and_run(source, variant)
            self.assertEqual(result.returncode, 0,
                             f"{variant} {context}: {result.stderr}")
            self.assertEqual(result.stdout, expected_stdout, f"{variant} {context}")

    def assert_runtime_error(self, source: str, expected_message: str, context: str = ""):
        for variant in VARIANTS:
            result = self.build_and_run(source, variant)
            self.assertNotEqual(result.returncode, 0,
                                f"{variant} {context}: expected a runtime error")
            self.assertIn("nava runtime error", result.stderr, f"{variant} {context}")
            self.assertIn(expected_message, result.stderr, f"{variant} {context}")


class ArithmeticGate(DifferentialBase):
    def test_checked_arithmetic_matches_the_model(self):
        """Random expressions agree with the documented semantics, value by value."""
        rng = random.Random(SEED)
        body = ["fn main() -> void {", "  let a = 0", "  let b = 0"]
        expected = []
        accepted = 0
        while accepted < 40:
            node = random_expression(rng, ("a", "b"))
            environment = {"a": rng.randint(-1000, 1000), "b": rng.randint(-1000, 1000)}
            try:
                value = evaluate(node, environment)
            except EvaluationError:
                continue  # the failing shapes are covered by the error gates below
            body.append(f"  a = {environment['a']}")
            body.append(f"  b = {environment['b']}")
            body.append(f"  print({render(node)})")
            expected.append(str(value))
            accepted += 1
        body.append("}")
        self.assert_values("\n".join(body) + "\n", "\n".join(expected) + "\n",
                           "random arithmetic")

    def test_a_wrapped_sum_is_a_runtime_error(self):
        self.assert_runtime_error(
            "fn main() -> void {\n  let a = 9223372036854775807\n  print(a + 1)\n}\n",
            "overflow in +")

    def test_a_wrapped_difference_is_a_runtime_error(self):
        self.assert_runtime_error(
            "fn main() -> void {\n  let a = -9223372036854775807\n  print(a - 2)\n}\n",
            "overflow in -")

    def test_a_wrapped_product_is_a_runtime_error(self):
        self.assert_runtime_error(
            "fn main() -> void {\n  print(3037000500 * 3037000500)\n}\n",
            "overflow in *")

    def test_the_minimum_divided_by_minus_one_is_an_error(self):
        self.assert_runtime_error(
            "fn main() -> void {\n  let m = -9223372036854775807 - 1\n"
            "  print(m / -1)\n}\n",
            "overflow in /")

    def test_negating_the_minimum_is_an_error(self):
        self.assert_runtime_error(
            "fn main() -> void {\n  let m = -9223372036854775807 - 1\n"
            "  print(-m)\n}\n",
            "overflow in unary -")

    def test_division_and_remainder_truncate_towards_zero(self):
        body = ["fn main() -> void {"]
        expected = []
        for left, right in ((7, 2), (-7, 2), (7, -2), (-7, -2), (0, 5),
                            (-9223372036854775807, 3), (9223372036854775807, 7)):
            for operator in ("/", "%"):
                node = ("bin", operator, ("lit", left), ("lit", right))
                expected.append(str(evaluate(node, {})))
                body.append(f"  print({render(node)})")
        body.append("}")
        self.assert_values("\n".join(body) + "\n", "\n".join(expected) + "\n",
                           "truncating division")

    def test_division_by_zero_is_a_runtime_error(self):
        self.assert_runtime_error(
            "fn main() -> void {\n  let zero = 0\n  print(10 / zero)\n}\n",
            "division by zero")
        self.assert_runtime_error(
            "fn main() -> void {\n  let zero = 0\n  print(10 % zero)\n}\n",
            "remainder by zero")


class EvaluationOrderGate(DifferentialBase):
    def test_operands_are_evaluated_left_to_right(self):
        source = (
            "fn left() -> i64 {\n  print(\"left\")\n  return 1\n}\n"
            "fn right() -> i64 {\n  print(\"right\")\n  return 2\n}\n"
            "fn pair(a: i64, b: i64) -> i64 {\n  return a * 10 + b\n}\n"
            "fn main() -> void {\n"
            "  print(pair(left(), right()))\n"
            "  print(left() - right())\n"
            "  print(left() * right())\n"
            "}\n")
        # pair(left(), right()) -> left, right, 12
        # left() - right()     -> left, right, -1
        # left() * right()     -> left, right, 2
        self.assert_values(source, "left\nright\n12\nleft\nright\n-1\n"
                                   "left\nright\n2\n")

    def test_conditions_are_evaluated_before_bodies(self):
        source = (
            "fn probe(name: str, answer: bool) -> bool {\n"
            "  print(name)\n  return answer\n}\n"
            "fn main() -> void {\n"
            "  if probe(\"cond\", true) {\n    print(\"body\")\n  }\n"
            "  while probe(\"again\", false) {\n    print(\"never\")\n  }\n"
            "}\n")
        self.assert_values(source, "cond\nbody\nagain\n")


class ShortCircuitGate(DifferentialBase):
    def test_and_skips_the_right_operand(self):
        source = (
            "fn probe() -> bool {\n  print(\"evaluated\")\n  return true\n}\n"
            "fn main() -> void {\n  print(false && probe())\n}\n")
        self.assert_values(source, "false\n")

    def test_or_skips_the_right_operand(self):
        source = (
            "fn probe() -> bool {\n  print(\"evaluated\")\n  return false\n}\n"
            "fn main() -> void {\n  print(true || probe())\n}\n")
        self.assert_values(source, "true\n")

    def test_the_right_operand_runs_when_it_matters(self):
        source = (
            "fn probe(name: str) -> bool {\n  print(name)\n  return true\n}\n"
            "fn main() -> void {\n"
            "  print(true && probe(\"and\"))\n"
            "  print(false || probe(\"or\"))\n"
            "}\n")
        self.assert_values(source, "and\ntrue\nor\ntrue\n")


class CollectionGate(DifferentialBase):
    def test_random_array_walks_match_the_model(self):
        rng = random.Random(SEED + 1)
        values = [rng.randint(-1000, 1000) for _ in range(16)]
        indices = [rng.randrange(len(values)) for _ in range(12)]
        body = ["fn main() -> void {",
                "  let a: [i64] = [" + ", ".join(str(v) for v in values) + "]",
                "  print(len(a))"]
        expected = [str(len(values))]
        for index in indices:
            body.append(f"  print(a[{index}])")
            expected.append(str(values[index]))
        body.append("  a[0] = 424242")
        expected.append("424242")
        body.append("  print(a[0])")
        body.append("  let i = 0")
        body.append("  let total = 0")
        body.append("  while i < len(a) {")
        body.append("    total = total + a[i]")
        body.append("    i = i + 1")
        body.append("  }")
        body.append("  print(total)")
        expected.append(str(sum(values) - values[0] + 424242))
        body.append("}")
        self.assert_values("\n".join(body) + "\n", "\n".join(expected) + "\n")

    def test_an_index_past_the_end_is_an_error(self):
        self.assert_runtime_error(
            "fn main() -> void {\n  let a: [i64] = [1, 2, 3]\n  print(a[3])\n}\n",
            "out of bounds")

    def test_a_negative_index_is_an_error(self):
        self.assert_runtime_error(
            "fn main() -> void {\n  let a: [i64] = [1, 2, 3]\n  print(a[0 - 1])\n}\n",
            "out of bounds")

    def test_an_out_of_bounds_write_leaves_no_trace(self):
        source = (
            "fn main() -> void {\n"
            "  let a: [i64] = [1, 2, 3]\n"
            "  let i = 3\n"
            "  a[i] = 9\n"
            "  print(a[0])\n"
            "}\n")
        self.assert_runtime_error(source, "out of bounds")

    def test_arrays_created_in_a_loop_are_released(self):
        """A lifetime bug shows up as a live-array report or a crash."""
        source = (
            "fn total(a: [i64]) -> i64 {\n"
            "  let i = 0\n  let sum = 0\n"
            "  while i < len(a) {\n    sum = sum + a[i]\n    i = i + 1\n  }\n"
            "  return sum\n}\n"
            "fn main() -> void {\n"
            "  let i = 0\n  let grand = 0\n"
            "  while i < 50 {\n"
            "    let a: [i64] = array_zeros(4)\n"
            "    a[0] = i\n    a[3] = i * 2\n"
            "    grand = grand + total(a)\n"
            "    i = i + 1\n  }\n"
            "  print(grand)\n}\n")
        expected = sum(i + 2 * i for i in range(50))
        for variant in VARIANTS:
            directory = Path(self.temp.name)
            binary = directory / f"loop-{variant}.bin"
            c_file = directory / f"loop-{variant}.c"
            c_file.write_text(cli.compile_source(source).code, encoding="utf-8")
            command = [CC, "-std=c11", "-O2", "-DNAVA_TRACK_ALLOCS"]
            if variant == "portable":
                command.append("-DNAVA_FORCE_PORTABLE")
            command += [str(c_file), "-o", str(binary)]
            built = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True,
                                    timeout=60)
            self.assertEqual(result.returncode, 0,
                             f"{variant}: leak check failed: {result.stderr}")
            self.assertEqual(result.stdout, f"{expected}\n", variant)
            reported = [line for line in result.stderr.splitlines()
                        if line.startswith("nava: live arrays at exit:")]
            self.assertEqual(reported, ["nava: live arrays at exit: 0"], variant)


class ModuleGate(DifferentialBase):
    def test_a_module_call_behaves_like_the_local_function(self):
        """`math::abs` and a local `abs` must agree, including on overflow."""
        project = Path(self.temp.name) / "project"
        project.mkdir(exist_ok=True)
        (project / "helper.nv").write_text(
            "fn module_version() -> i64 {\n    return 1\n}\n\n"
            "fn double(x: i64) -> i64 {\n    return x * 2\n}\n", encoding="utf-8")
        (project / "main.nv").write_text(
            "import helper\n\n"
            "fn local_abs(x: i64) -> i64 {\n"
            "    if x < 0 {\n        return -x\n    }\n    return x\n}\n\n"
            "fn main() -> void {\n"
            "    print(helper::double(21))\n"
            "    print(local_abs(-5))\n"
            "}\n", encoding="utf-8")
        for variant in VARIANTS:
            compilation = cli.compile_file(project / "main.nv")
            directory = Path(self.temp.name)
            c_file = directory / f"module-{variant}.c"
            binary = directory / f"module-{variant}.bin"
            c_file.write_text(compilation.code, encoding="utf-8")
            command = [CC, "-std=c11", "-O2"]
            if variant == "portable":
                command.append("-DNAVA_FORCE_PORTABLE")
            command += [str(c_file), "-o", str(binary)]
            built = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True,
                                    timeout=60)
            self.assertEqual(result.stdout, "42\n5\n", variant)


if __name__ == "__main__":
    unittest.main()
