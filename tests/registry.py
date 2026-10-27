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
from nava_lang.diagnostics import CODES  # noqa: E402

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
    "SpecDocumentationTests": {
        "test_documented_examples_typecheck",
        "test_referenced_fixtures_exist",
        "test_every_registered_code_is_documented",
        "test_documented_limits_match_the_implementation",
        "test_documented_exit_codes_match_the_cli",
    },
    "NegativeFixtureTests": {
        "test_fixture_files_and_entries_agree",
        "test_each_fixture_fails_with_the_recorded_diagnostic",
        "test_fixtures_cover_every_code_except_documented_exceptions",
        "test_invalid_utf8_source_is_reported",
    },
    "WarningFixtureTests": {
        "test_warning_is_reported_and_is_not_fatal",
    },
    "PositiveFixtureTests": {
        "test_fixture_files_and_entries_agree",
        "test_each_fixture_runs_and_matches_the_recorded_output",
    },
    "DiagnosticFormatTests": {
        "test_human_format_is_path_line_column_code_message",
        "test_json_report_shape_is_stable",
        "test_json_success_report_and_warnings",
        "test_cli_diagnostics_are_registered",
        "test_unregistered_code_is_rejected_by_the_builder",
    },
    "LimitTests": {
        "test_nesting_below_the_limit_is_accepted",
        "test_nesting_above_the_limit_reports_e0401",
        "test_pathological_input_terminates_quickly",
        "test_recursion_safety_net_reports_e0402",
    },
    "FuzzTests": {
        "test_random_token_soup_only_produces_diagnostics",
        "test_mutated_valid_programs_only_produce_diagnostics",
    },
    "SandboxTests": {
        "test_allowed_program_completes",
        "test_infinite_loop_is_stopped",
        "test_wall_clock_limit_stops_an_idle_program",
        "test_excess_output_is_stopped",
        "test_memory_ceiling_stops_allocation",
        "test_child_processes_do_not_survive",
        "test_filesystem_boundary",
        "test_network_boundary",
        "test_required_mode_refuses_without_namespaces",
        "test_evaluator_refuses_required_without_namespaces",
        "test_backend_labels_state_what_is_missing",
        "test_report_records_limits_and_truncation",
        "test_surviving_child_is_reported_by_the_cli",
        "test_cli_run_reports_isolation_and_timeout",
        "test_cli_runtime_error_keeps_its_status",
    },
    "RegistryTests": {
        "test_registered_tests_are_present_and_not_skipped",
        "test_required_tools_are_present",
        "test_task_fixtures_match_registry_and_reference",
        "test_diagnostics_registry_is_complete",
        "test_spec_fixtures_are_registered",
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

#: Executable-specification fixture directories and their expected sizes.
REGISTERED_FIXTURE_DIRS = {
    "tests/fixtures/negative": 47,
    "tests/fixtures/positive": 12,
    "tests/fixtures/warnings": 1,
}

#: Diagnostic codes that no source fixture can produce, with the test that
#: covers them instead. Mirrors `tests/test_spec.py`.
CODES_TESTED_ELSEWHERE = {
    "E0111": "tests/test_spec.py: test_invalid_utf8_source_is_reported",
    "E0402": "tests/test_spec.py: test_recursion_safety_net_reports_e0402",
    "E0501": "tests/test_spec.py: test_cli_diagnostics_are_registered",
    "E0502": "tests/test_spec.py: test_cli_diagnostics_are_registered",
    "E0503": "tests/test_spec.py: test_cli_diagnostics_are_registered",
    "E0504": "tests/test_spec.py: test_cli_diagnostics_are_registered",
    "E0505": "tests/test_spec.py: test_cli_diagnostics_are_registered",
    "E0601": "tests/test_sandbox.py: test_required_mode_refuses_without_namespaces",
    "E0602": "tests/test_sandbox.py: test_surviving_child_is_reported_by_the_cli",
}


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
    import test_sandbox  # noqa: PLC0415
    import test_spec  # noqa: PLC0415

    found: dict[str, set[str]] = {}
    for module in (test_nava, test_registry, test_sandbox, test_spec):
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


def check_fixtures() -> list[str]:
    problems: list[str] = []
    for directory, expected in REGISTERED_FIXTURE_DIRS.items():
        path = ROOT / directory
        if not path.is_dir():
            problems.append(f"{directory}: fixture directory is missing")
            continue
        found = sorted(item.name for item in path.glob("*.nv"))
        if len(found) != expected:
            problems.append(f"{directory}: registered {expected} fixtures, found {len(found)}")
    for manifest, directory in (("tests/fixtures/negative.json", "tests/fixtures/negative"),
                                ("tests/fixtures/positive.json", "tests/fixtures/positive"),
                                ("tests/fixtures/warnings.json", "tests/fixtures/warnings")):
        path = ROOT / manifest
        if not path.is_file():
            problems.append(f"{manifest}: manifest is missing")
            continue
        listed = {case["file"] for case in json.loads(path.read_text(encoding="utf-8"))["cases"]}
        on_disk = {item.name for item in (ROOT / directory).glob("*.nv")}
        for missing in sorted(listed - on_disk):
            problems.append(f"{manifest}: {missing} has no fixture file")
        for extra in sorted(on_disk - listed):
            problems.append(f"{manifest}: {extra} has no expectation")
    return problems


def check_diagnostics() -> list[str]:
    """Every registered code needs a fixture, a documented test or a language entry."""
    problems: list[str] = []
    negative = json.loads((ROOT / "tests/fixtures/negative.json").read_text(encoding="utf-8"))
    warnings = json.loads((ROOT / "tests/fixtures/warnings.json").read_text(encoding="utf-8"))
    covered = {case["code"] for case in negative["cases"]} | {case["code"] for case in warnings["cases"]}
    for code in sorted(set(CODES) - covered - set(CODES_TESTED_ELSEWHERE)):
        problems.append(f"{code} ({CODES[code][1]}): no fixture and no documented test")
    for code in sorted(set(CODES_TESTED_ELSEWHERE) - set(CODES)):
        problems.append(f"{code}: listed as tested elsewhere but not registered in CODES")
    language = (ROOT / "LANGUAGE.md").read_text(encoding="utf-8")
    for code in sorted(CODES):
        if code not in language:
            problems.append(f"{code}: not documented in LANGUAGE.md")
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
    problems.extend(check_fixtures())
    problems.extend(check_diagnostics())
    return problems


def main() -> int:
    problems = verify()
    for problem in problems:
        print(f"registry: {problem}", file=sys.stderr)
    if problems:
        return 1
    tests = sum(len(methods) for methods in REGISTERED_TESTS.values())
    cases = sum(REGISTERED_TASKS.values())
    fixtures = sum(REGISTERED_FIXTURE_DIRS.values())
    print(
        f"OK: {tests} registered tests, {len(REGISTERED_TASKS)} tasks with {cases} cases, "
        f"{fixtures} spec fixtures, {len(CODES)} diagnostic codes, "
        f"tools {', '.join(REQUIRED_TOOLS)} present, languages {', '.join(REGISTERED_LANGUAGES)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
