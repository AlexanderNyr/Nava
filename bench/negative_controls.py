#!/usr/bin/env python3
"""Reintroduce known bugs and prove the N8 gate catches them.

An optimisation is only trustworthy if the test that guards it can fail. Each
control below restores one specific defect in the compiler or the runtime —
a missing overflow check, a dropped bounds check, right-to-left argument
evaluation, an eagerly evaluated `&&`, an unclosed runtime closure, a broken
field factory — runs one named test, and expects that test to fail.

Nothing is committed: every file is backed up in memory, patched, exercised and
restored, and the restore is verified by SHA-256. If a replacement string has
drifted, the control reports `stale` instead of silently doing nothing, because
a control that no longer applies is not a control.

    python3 bench/negative_controls.py            # all controls
    python3 bench/negative_controls.py --list     # names and gates only
    python3 bench/negative_controls.py --only bounds_check

The script exits non-zero when a gate *survives* its defect: that is the only
outcome that means the gate, not the compiler, is broken.
"""
from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

ARITHMETIC = "tests.test_differential.ArithmeticGate"
COLLECTION = "tests.test_differential.CollectionGate"
ORDER = "tests.test_differential.EvaluationOrderGate"
SHORT = "tests.test_differential.ShortCircuitGate"
RUNTIME = "tests.test_runtime.ClosureTests"
RECORDS = "tests.test_records.RecordTests"

#: name, file, original text, replacement, the test that must fail.
CONTROLS = (
    {
        "name": "overflow_add_builtin",
        "why": "the compiler builtin for + stops checking",
        "path": "nava_lang/cgen.py",
        "old": 'if (__builtin_add_overflow(a, b, &result)) nv_fail("i64 overflow in +");',
        "new": "result = a + b;",
        "gate": f"{ARITHMETIC}.test_a_wrapped_sum_is_a_runtime_error",
    },
    {
        "name": "overflow_add_portable",
        "why": "the portable C11 fallback for + stops checking",
        "path": "nava_lang/cgen.py",
        "old": "if ((b > 0 && a > INT64_MAX - b) || (b < 0 && a < INT64_MIN - b))",
        "new": "if (0 && (b > 0 && a > INT64_MAX - b))",
        "gate": f"{ARITHMETIC}.test_a_wrapped_sum_is_a_runtime_error",
    },
    {
        "name": "overflow_mul_builtin",
        "why": "the compiler builtin for * stops checking",
        "path": "nava_lang/cgen.py",
        "old": 'if (__builtin_mul_overflow(a, b, &result)) nv_fail("i64 overflow in *");',
        "new": "result = a * b;",
        "gate": f"{ARITHMETIC}.test_a_wrapped_product_is_a_runtime_error",
    },
    {
        "name": "overflow_negation",
        "why": "negating INT64_MIN wraps instead of failing",
        "path": "nava_lang/cgen.py",
        "old": 'if (a == INT64_MIN) nv_fail("i64 overflow in unary -");',
        "new": 'if (0) nv_fail("i64 overflow in unary -");',
        "gate": f"{ARITHMETIC}.test_negating_the_minimum_is_an_error",
    },
    {
        "name": "division_by_zero",
        "why": "`/` no longer checks its divisor",
        "path": "nava_lang/cgen.py",
        "old": 'if (b == 0) nv_fail("division by zero");',
        "new": 'if (0) nv_fail("division by zero");',
        "gate": f"{ARITHMETIC}.test_division_by_zero_is_a_runtime_error",
    },
    {
        "name": "remainder_by_zero",
        "why": "`%` no longer checks its divisor",
        "path": "nava_lang/cgen.py",
        "old": 'if (b == 0) nv_fail("remainder by zero");',
        "new": 'if (0) nv_fail("remainder by zero");',
        "gate": f"{ARITHMETIC}.test_division_by_zero_is_a_runtime_error",
    },
    {
        "name": "bounds_check",
        "why": "array indices are no longer range checked",
        "path": "nava_lang/cgen.py",
        "old": "if (index < 0 || index >= length) {",
        "new": "if (0) {",
        "gate": f"{COLLECTION}.test_an_index_past_the_end_is_an_error",
    },
    {
        "name": "array_release",
        "why": "`nv_arr_i64_free` stops releasing storage",
        "path": "nava_lang/cgen.py",
        "old": "if (array->data != NULL) { free(array->data); nv_note_array_free(); }",
        "new": "if (array->data != NULL) { }",
        "gate": f"{COLLECTION}.test_arrays_created_in_a_loop_are_released",
    },
    {
        "name": "argument_order",
        "why": "call arguments are materialised right to left",
        "path": "nava_lang/cgen.py",
        "old": "            args = [self.expression(arg) for arg in expr.args]",
        "new": ("            args = [None] * len(expr.args)\n"
                "            for position in reversed(range(len(expr.args))):\n"
                "                args[position] = self.expression(expr.args[position])"),
        "gate": f"{ORDER}.test_operands_are_evaluated_left_to_right",
    },
    {
        "name": "short_circuit",
        "why": "`&&` and `||` evaluate the right operand eagerly",
        "path": "nava_lang/cgen.py",
        "old": ('                condition = result if expr.op == "&&" else f"!{result}"\n'
                '                self.line(f"if ({condition}) {{")\n'
                "                self.indent += 1\n"
                "                right = self.expression(expr.right)"),
        "new": ('                condition = result if expr.op == "&&" else f"!{result}"\n'
                "                right = self.expression(expr.right)\n"
                '                self.line(f"if ({condition}) {{")\n'
                "                self.indent += 1"),
        "gate": f"{SHORT}.test_and_skips_the_right_operand",
    },
    {
        "name": "runtime_closure",
        "why": "the runtime closure keeps roots but stops following references",
        "path": "nava_lang/runtime.py",
        "old": "        for reference in units[index].references:",
        "new": "        for reference in ():",
        "gate": f"{RUNTIME}.test_the_closure_is_closed",
    },
    {
        "name": "record_factory",
        "why": "a generated constructor drops its per-instance default",
        "path": "nava_lang/records.py",
        "old": '        body.append(f"    if {field_name} is _MISSING:")',
        "new": '        body.append(f"    if False:")',
        "gate": f"{RECORDS}.test_a_factory_runs_for_every_instance_and_a_default_is_shared",
    },
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_gate(gate: str) -> tuple[int, str]:
    result = subprocess.run([sys.executable, "-m", "unittest", gate], cwd=ROOT,
                            capture_output=True, text=True, timeout=900)
    tail = (result.stderr or result.stdout).strip().splitlines()
    return result.returncode, tail[-1] if tail else ""


def run_control(control: dict) -> dict:
    path = ROOT / control["path"]
    original = path.read_text(encoding="utf-8")
    before = digest(path)
    if control["old"] not in original:
        return {**control, "outcome": "stale",
                "detail": f"the text to replace is not in {control['path']}"}
    patched = original.replace(control["old"], control["new"], 1)
    try:
        path.write_text(patched, encoding="utf-8")
        code, detail = run_gate(control["gate"])
    finally:
        path.write_text(original, encoding="utf-8")
    if digest(path) != before:  # pragma: no cover - would be a bug in this script
        raise SystemExit(f"failed to restore {control['path']}")
    return {**control, "outcome": "caught" if code != 0 else "MISSED", "detail": detail}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Prove each N8 gate fails when its defect is reintroduced.")
    parser.add_argument("--list", action="store_true", help="list the controls and exit")
    parser.add_argument("--only", action="append", metavar="NAME",
                        help="run only these controls (repeatable)")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    if args.list:
        for control in CONTROLS:
            print(f"{control['name']:<22} {control['path']:<22} {control['gate']}")
        return 0

    selected = list(CONTROLS)
    if args.only:
        unknown = set(args.only) - {control["name"] for control in CONTROLS}
        if unknown:
            parser.error(f"unknown controls: {', '.join(sorted(unknown))}")
        selected = [c for c in CONTROLS if c["name"] in set(args.only)]

    results = []
    for control in selected:
        outcome = run_control(control)
        results.append(outcome)
        if not args.quiet:
            print(f"{outcome['name']:<22} {outcome['outcome']:<8} {outcome['why']}")
            if outcome["outcome"] != "caught":
                print(f"    gate: {outcome['gate']}\n    {outcome['detail']}")

    caught = sum(1 for result in results if result["outcome"] == "caught")
    stale = [result["name"] for result in results if result["outcome"] == "stale"]
    missed = [result["name"] for result in results if result["outcome"] == "MISSED"]
    print(f"{caught}/{len(results)} controls caught by their gate")
    for name in stale:
        print(f"  stale control (fix the replacement text): {name}")
    for name in missed:
        print(f"  gate did NOT fail: {name}")
    return 1 if missed or stale else 0


if __name__ == "__main__":
    raise SystemExit(main())
