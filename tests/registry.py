#!/usr/bin/env python3
"""Suite and fixture registry guard (PLAN.md phase N0).

The point of this module is that a *missing* test is visible.  `unittest`
reports a skipped runtime test as a pass, and deleting a test simply shrinks
the suite without any signal.  Here the expected set of tests, tasks and
fixtures is written down explicitly; any addition, removal or skip is compared
against it and reported as a failure.

Run it directly:

    python3 tests/registry.py

or through the test suite (`tests/test_registry.py`).  Set
``NAVA_ALLOW_MISSING_TOOLS=1`` only to inspect a machine without a C/C++
compiler: the guard then reports the missing tools as an explicit, unsafe
local mode instead of silently passing.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.reference import REFERENCE, check as check_reference  # noqa: E402

#: Tools that the runtime, evaluation and benchmark gates require.  A missing
#: tool must fail the guard: skipped runtime tests are not a passing suite.
REQUIRED_TOOLS = ("cc", "c++")

#: Registered test methods per `unittest` class in `tests/test_nava.py`.
REGISTERED_TESTS = {
    "FrontendTests": {
        "test_requires_main",
        "test_main_signature",
        "test_duplicate_function",
        "test_duplicate_variable_or_parameter",
        "test_variable_scope",
        "test_assignment_must_match_type",
        "test_conditions_require_bool",
        "test_operators_require_types",
        "test_call_validation",
        "test_void_values_cannot_be_used",
        "test_every_return_path_required",
        "test_invalid_literals_and_strings",
        "test_reserved_and_unknown_types",
        "test_expression_statement_must_be_a_call",
        "test_newlines_comments_and_semicolons",
    },
    "RuntimeTests": {
        "test_examples",
        "test_precedence_division_and_remainder",
        "test_shadowing_and_initializer_scope",
        "test_functions_and_left_to_right_order",
        "test_short_circuit",
        "test_runtime_error_precedes_later_side_effect",
        "test_while_condition_re_evaluated",
        "test_strings_unicode_and_equality",
        "test_read_i64_boundaries_and_invalid_input",
        "test_checked_arithmetic_errors",
        "test_portable_c_fallback_multiplication",
        "test_main_exit_code",
        "test_cli_build_check_emit_and_timeout",
    },
    "EvaluationTests": {
        "test_public_gcd_samples_pass_and_bad_candidate_fails",
        "test_c_candidate_passes_and_reports_labelled_semantics",
        "test_other_public_tasks",
        "test_prompt_contains_task_and_language",
    },
    "BenchTests": {
        "test_json_record_has_environment_samples_and_missing_model_fields",
    },
    "RegistryTests": {
        "test_registered_tests_are_present_and_not_skipped",
        "test_required_tools_are_present",
        "test_task_fixtures_match_registry_and_reference",
        "test_registry_module_guard_passes",
    },
}

#: Registered public tasks and their expected number of cases.  A task that
#: disappears, loses a case or changes its count fails the guard.
REGISTERED_TASKS = {
    "gcd": 7,
    "sum_to_n": 6,
    "factorial": 6,
    "fibonacci": 6,
    "is_prime": 6,
    "digit_sum": 6,
    "max_of_three": 6,
    "collatz_steps": 6,
}

#: Candidate languages the evaluator must support, with a labelled arithmetic
#: contract.  `eval/evaluate.py` records the matching label in each report.
REGISTERED_LANGUAGES = ("nava", "c", "cpp")


def tasks_path() -> Path:
    return ROOT / "eval/tasks.json"


def load_tasks() -> list[dict]:
    return json.loads(tasks_path().read_text(encoding="utf-8"))


def allow_missing_tools() -> bool:
    return os.environ.get("NAVA_ALLOW_MISSING_TOOLS") == "1"


def missing_tools() -> list[str]:
    return [tool for tool in REQUIRED_TOOLS if shutil.which(tool) is None]


def discovered_tests() -> dict[str, set[str]]:
    """Test-method names per class, imported from the real test module."""
    if str(ROOT / "tests") not in sys.path:
        sys.path.insert(0, str(ROOT / "tests"))
    import test_nava  # noqa: PLC0415  (import after sys.path setup)
    import test_registry  # noqa: PLC0415

    found: dict[str, set[str]] = {}
    for module in (test_nava, test_registry):
        for name, value in vars(module).items():
            if isinstance(value, type) and issubclass(value, unittest.TestCase) and name != "TestCase":
                if name in found:
                    continue
                found[name] = {method for method in dir(value) if method.startswith("test_")}
    return found


def check_registered_tests() -> list[str]:
    problems: list[str] = []
    found = discovered_tests()
    for class_name, expected in REGISTERED_TESTS.items():
        actual = found.get(class_name)
        if actual is None:
            problems.append(f"test class {class_name} is missing from tests/test_nava.py")
            continue
        for missing in sorted(expected - actual):
            problems.append(f"{class_name}.{missing}: registered test is missing")
        for extra in sorted(actual - expected):
            problems.append(f"{class_name}.{extra}: test is not registered in tests/registry.py")
    for class_name in sorted(set(found) - set(REGISTERED_TESTS)):
        problems.append(f"test class {class_name} is not registered in tests/registry.py")
    return problems


def check_skip_state() -> list[str]:
    """A registered runtime/evaluation test must not be skipped on this host."""
    problems: list[str] = []
    if str(ROOT / "tests") not in sys.path:
        sys.path.insert(0, str(ROOT / "tests"))
    import test_nava  # noqa: PLC0415

    if test_nava.CC is None:
        problems.append("tests/test_nava.py found no 'cc': the runtime tests would be skipped")
    for class_name in ("RuntimeTests", "EvaluationTests"):
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(getattr(test_nava, class_name))
        for case in iterate(suite):
            if case.__class__.__name__ != "_SubTest":
                continue
        if not suite.countTestCases():
            problems.append(f"{class_name} loaded zero test cases")
    # `unittest.skipUnless` on the class is evaluated when the suite runs; the
    # loader keeps the class, so compare the class attribute directly.
    for class_name, required in (("RuntimeTests", "cc"), ("EvaluationTests", "cc")):
        class_object = getattr(test_nava, class_name)
        if getattr(class_object, "__unittest_skip__", False):
            problems.append(f"{class_name} is skipped on this host; required tool: {required}")
    return problems


def iterate(suite):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from iterate(test)
        else:
            yield test


def check_tasks() -> list[str]:
    problems: list[str] = []
    tasks = load_tasks()
    by_id = {task.get("id"): task for task in tasks}
    for task_id, cases in REGISTERED_TASKS.items():
        task = by_id.get(task_id)
        if task is None:
            problems.append(f"task {task_id}: registered task is missing from eval/tasks.json")
            continue
        if len(task.get("tests", [])) != cases:
            problems.append(
                f"task {task_id}: registered {cases} cases, tasks.json has {len(task.get('tests', []))}"
            )
        if not task.get("description"):
            problems.append(f"task {task_id}: description is missing")
    for task_id in sorted(set(by_id) - set(REGISTERED_TASKS)):
        problems.append(f"task {task_id}: not registered in tests/registry.py")
    for task_id in sorted(set(REGISTERED_TASKS) - set(REFERENCE)):
        problems.append(f"task {task_id}: no independent reference implementation")
    problems.extend(f"reference: {problem}" for problem in check_reference(tasks_path()))
    return problems


def check_languages() -> list[str]:
    problems: list[str] = []
    source = (ROOT / "eval/evaluate.py").read_text(encoding="utf-8")
    for language in REGISTERED_LANGUAGES:
        if f'"{language}"' not in source:
            problems.append(f"eval/evaluate.py does not mention the registered language {language!r}")
    if "ARITHMETIC" not in source:
        problems.append("eval/evaluate.py must record labelled arithmetic semantics")
    return problems


def verify() -> list[str]:
    """Return every problem found; an empty list means the gate passes."""
    problems: list[str] = []
    missing = missing_tools()
    if missing:
        message = f"required tools are missing: {', '.join(missing)}"
        if allow_missing_tools():
            problems.append(
                message + " (NAVA_ALLOW_MISSING_TOOLS=1: an explicit, unsafe local mode; "
                "this is NOT a passing gate)"
            )
        else:
            problems.append(message + " (install gcc/clang; do not treat skipped tests as a pass)")
    else:
        problems.extend(check_skip_state())
    problems.extend(check_registered_tests())
    problems.extend(check_tasks())
    problems.extend(check_languages())
    return problems


def main() -> int:
    problems = verify()
    for problem in problems:
        print(f"registry: {problem}", file=sys.stderr)
    if problems:
        return 1
    tests = sum(len(methods) for methods in REGISTERED_TESTS.values())
    cases = sum(REGISTERED_TASKS.values())
    print(
        f"OK: {tests} registered tests, {len(REGISTERED_TASKS)} tasks with {cases} cases, "
        f"tools {', '.join(REQUIRED_TOOLS)} present, languages {', '.join(REGISTERED_LANGUAGES)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
