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
        "test_array_tasks_pass_the_evaluator",
        "test_prompt_contains_task_and_language",
    },
    "ArrayFrontendTests": {
        "test_index_rules",
        "test_only_a_variable_is_indexed",
        "test_arrays_cannot_be_copied_or_reassigned",
        "test_arrays_cannot_be_returned",
        "test_borrow_rules",
        "test_an_owned_array_may_be_mutated",
        "test_literal_rules",
        "test_builtin_rules",
        "test_arrays_are_not_scalars",
        "test_array_types_are_restricted",
        "test_a_literal_may_span_lines_inside_the_brackets",
        "test_bracket_diagnostics",
    },
    "ArrayRuntimeTests": {
        "test_literals_len_and_elements",
        "test_mutation_through_a_mutable_borrow_is_visible",
        "test_nested_scopes_loops_and_early_returns",
        "test_zeros_allocation_and_length_checks",
        "test_out_of_range_access_fails_predictably",
        "test_a_failed_write_has_no_side_effect",
        "test_sorting_matches_an_independent_reference",
        "test_sieve_matches_an_independent_reference",
        "test_index_access_goes_through_the_checked_helpers",
    },
    "ArrayMemoryTests": {
        "test_every_normal_path_releases_its_storage",
        "test_release_happens_once_per_iteration",
        "test_an_error_path_ends_the_process_with_the_storage_still_owned",
        "test_arrays_are_memory_safe_under_sanitizers",
        "test_a_disabled_bounds_check_is_caught",
        "test_the_runtime_documents_its_own_limit",
    },
    "ForFrontendTests": {
        "test_break_and_continue_need_a_loop",
        "test_loop_variable_is_read_only",
        "test_iterable_rules",
        "test_loop_variable_scope",
        "test_return_analysis_is_still_conservative",
        "test_dead_code_after_a_jump_is_a_warning",
    },
    "ForRuntimeTests": {
        "test_range_iteration_matches_python",
        "test_element_iteration_matches_python",
        "test_element_iteration_over_a_parameter_and_an_array_variable",
        "test_the_loop_variable_is_a_copy",
        "test_break_and_continue_match_python",
        "test_break_leaves_only_the_innermost_loop",
        "test_continue_in_a_while_loop_rechecks_the_condition",
        "test_return_from_inside_a_loop_still_releases",
        "test_break_and_continue_release_the_iteration_storage",
        "test_range_bounds_are_evaluated_once",
        "test_nested_loops_match_a_python_reference",
        "test_loops_are_memory_safe_under_sanitizers",
        "test_documented_example_output_is_real",
    },
    "FloatFrontendTests": {
        "test_no_implicit_conversion",
        "test_float_is_not_an_integer_type",
        "test_literal_rules",
        "test_conversion_arguments",
        "test_floats_work_where_values_are_expected",
    },
    "FloatRuntimeTests": {
        "test_printing_matches_python_repr",
        "test_printing_random_values_matches_python_repr",
        "test_arithmetic_matches_python",
        "test_exceptional_values_and_nan_comparisons",
        "test_rounding_is_round_to_nearest_even",
        "test_int_to_float_conversion_is_exact_then_rounds",
        "test_float_to_int_truncates_and_refuses_the_rest",
        "test_conversion_has_no_undefined_behaviour",
        "test_float_programs_are_sanitizer_clean",
        "test_read_f64_accepts_only_decimal_forms",
        "test_input_and_output_are_locale_independent",
        "test_documented_float_output_is_real",
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
    "ClassificationTests": {
        "test_thresholds_match_the_plan",
        "test_full_study_is_empirical",
        "test_one_missing_threshold_keeps_it_exploratory",
    },
    "AggregateTests": {
        "test_missing_measurements_are_null_not_zero",
        "test_reported_values_are_summarized",
        "test_bootstrap_is_deterministic_and_contains_the_point",
        "test_empty_input_yields_nulls",
    },
    "ProviderTests": {
        "test_mock_does_not_invent_timings_or_tokens",
        "test_openai_provider_without_a_key_does_not_call_out",
        "test_credentials_are_redacted_from_errors",
        "test_replay_provider_matches_by_prompt_hash",
    },
    "ExtractionTests": {
        "test_tagged_fence_wins",
        "test_untagged_fence_is_used",
        "test_no_fence_uses_the_whole_text",
        "test_foreign_fence_falls_back_without_editing",
    },
    "RecordValidationTests": {
        "test_a_genuine_record_passes",
        "test_mock_labelled_measured_is_rejected",
        "test_empirical_label_below_the_thresholds_is_rejected",
        "test_measured_without_evaluated_attempts_is_rejected",
        "test_require_status_is_enforced",
    },
    "StudyRunTests": {
        "test_end_to_end_mock_study_is_labeled_as_not_measured",
        "test_plan_only_does_not_call_anything",
        "test_repairs_are_counted_separately_from_first_attempts",
        "test_provider_error_is_recorded_and_not_mistaken_for_an_answer",
        "test_study_exit_code_reports_provider_errors",
        "test_model_status_is_not_measured_when_every_call_failed",
        "test_store_raw_keeps_prompts_and_answers",
    },
    "ModuleFrontendTests": {
        "test_a_builtin_cannot_be_reached_through_a_module",
        "test_a_cycle_names_every_module_and_its_location",
        "test_a_local_module_may_not_shadow_a_standard_module",
        "test_a_module_may_import_another_module",
        "test_a_module_must_not_define_main",
        "test_a_qualified_call_without_an_import_is_reported",
        "test_a_standard_module_must_declare_its_version",
        "test_an_import_cannot_escape_the_project_boundary",
        "test_an_in_memory_source_cannot_resolve_an_import",
        "test_an_unqualified_call_into_another_module_names_the_fix",
        "test_an_unused_import_is_a_warning_not_an_error",
        "test_diagnostics_carry_the_path_of_the_file_they_come_from",
        "test_import_must_precede_every_function",
        "test_missing_module_lists_both_search_locations",
        "test_module_imported_twice_is_an_error",
        "test_the_project_root_can_be_moved",
        "test_the_standard_library_version_is_checked",
        "test_unknown_function_in_a_module",
    },
    "ModuleRuntimeTests": {
        "test_check_build_and_run_agree",
        "test_module_fixture_cases",
        "test_the_generated_c_is_one_translation_unit",
        "test_the_multi_file_example_runs",
    },
    "MathTests": {
        "test_abs_of_the_smallest_i64_is_a_runtime_error",
        "test_integer_helpers_match_python",
    },
    "SortTests": {
        "test_sorting_matches_python",
    },
    "TextTests": {
        "test_a_nul_byte_cannot_become_a_str",
        "test_byte_at_is_bounds_checked",
        "test_bytes_and_code_points_match_python",
        "test_chars_len_stops_on_invalid_utf8",
        "test_utf8_validity_matches_pythons_strict_decoder",
    },
    "StdlibContractTests": {
        "test_every_export_is_documented",
        "test_every_standard_module_declares_the_supported_version",
        "test_the_exports_are_what_the_tests_cover",
    },
    "FileCapabilityTests": {
        "test_a_directory_is_not_a_file",
        "test_a_granted_directory_is_readable",
        "test_a_missing_file_says_why",
        "test_a_path_outside_the_grant_is_denied",
        "test_a_program_without_a_grant_is_denied",
        "test_a_symlink_outside_the_grant_is_denied",
        "test_an_empty_file_is_an_empty_string",
        "test_the_grants_are_compiled_in",
        "test_the_per_file_limit_is_enforced",
        "test_the_program_cannot_grant_itself_access",
        "test_the_read_call_limit_is_enforced",
        "test_the_total_read_limit_is_enforced",
        "test_write_needs_its_own_grant",
        "test_write_through_a_symlinked_directory_is_denied",
    },
    "FileCommandLineTests": {
        "test_a_grant_that_is_not_a_directory_is_reported",
        "test_a_missing_grant_directory_is_reported",
    },
    "IsolatedFileTests": {
        "test_a_workdir_is_required_to_exist",
        "test_the_boundary_holds_inside_the_namespace_jail",
        "test_the_isolated_runner_allows_a_granted_file_and_denies_another",
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
    "sort_numbers": 8,
    "sieve_count": 8,
    "mean_abs": 11,
}

#: Candidate languages the evaluator must support, with a labelled arithmetic
#: contract.  `eval/evaluate.py` records the matching label in each report.
REGISTERED_LANGUAGES = ("nava", "c", "cpp")

#: Executable-specification fixture directories and their expected sizes.
REGISTERED_FIXTURE_DIRS = {
    "tests/fixtures/negative": 83,
    "tests/fixtures/positive": 19,
    "tests/fixtures/warnings": 2,
}

#: Multi-file module fixtures: one directory per case, listed in
#: `tests/fixtures/modules.json`.
REGISTERED_MODULE_CASES = 8

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
    "E0352": "tests/test_modules.py: test_missing_module_lists_both_search_locations",
    "E0353": "tests/fixtures/modules.json: cycle",
    "E0355": "tests/fixtures/modules.json: duplicate_import",
    "E0356": "tests/test_modules.py: test_an_import_cannot_escape_the_project_boundary",
    "E0357": "tests/fixtures/modules.json: main_in_module",
    "E0358": "tests/fixtures/modules.json: unqualified",
    "E0359": "tests/fixtures/modules.json: not_imported",
    "E0360": "tests/fixtures/modules.json: shadow",
    "E0361": "tests/test_modules.py: test_the_standard_library_version_is_checked",
    "E0506": "tests/test_files.py: test_a_missing_grant_directory_is_reported",
    "E0507": "tests/test_files.py: test_a_grant_that_is_not_a_directory_is_reported",
    "W0002": "tests/fixtures/modules.json: unused_import",
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
    import test_arrays  # noqa: PLC0415  (import after sys.path setup)
    import test_control  # noqa: PLC0415
    import test_files  # noqa: PLC0415
    import test_float  # noqa: PLC0415
    import test_modules  # noqa: PLC0415
    import test_nava  # noqa: PLC0415
    import test_registry  # noqa: PLC0415
    import test_sandbox  # noqa: PLC0415
    import test_spec  # noqa: PLC0415
    import test_stdlib  # noqa: PLC0415
    import test_study  # noqa: PLC0415

    found: dict[str, set[str]] = {}
    for module in (test_arrays, test_control, test_files, test_float, test_modules,
                   test_nava, test_registry, test_sandbox, test_spec, test_stdlib,
                   test_study):
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
    import test_arrays  # noqa: PLC0415
    import test_control  # noqa: PLC0415
    import test_files  # noqa: PLC0415
    import test_float  # noqa: PLC0415
    import test_modules  # noqa: PLC0415
    import test_stdlib  # noqa: PLC0415
    for module, class_name in ((test_arrays, "ArrayRuntimeTests"),
                               (test_arrays, "ArrayMemoryTests"),
                               (test_control, "ForRuntimeTests"),
                               (test_float, "FloatRuntimeTests"),
                               (test_modules, "ModuleRuntimeTests"),
                               (test_stdlib, "MathTests"),
                               (test_stdlib, "SortTests"),
                               (test_stdlib, "TextTests"),
                               (test_files, "FileCapabilityTests"),
                               (test_files, "FileCommandLineTests"),
                               (test_files, "IsolatedFileTests")):
        class_object = getattr(module, class_name)
        if getattr(class_object, "__unittest_skip__", False):
            problems.append(f"{class_name} is skipped on this host; required tool: cc")
    if not test_arrays.SANITIZERS:
        problems.append(
            "the address/undefined-behaviour sanitizers are unavailable: the array "
            "memory-safety gate would be skipped (install the compiler's sanitizer "
            "runtime; do not treat a skipped memory test as a pass)"
        )
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
    modules_directory = ROOT / "tests/fixtures/modules"
    module_manifest = ROOT / "tests/fixtures/modules.json"
    if not module_manifest.is_file():
        problems.append("tests/fixtures/modules.json: manifest is missing")
    elif not modules_directory.is_dir():
        problems.append("tests/fixtures/modules: fixture directory is missing")
    else:
        cases = json.loads(module_manifest.read_text(encoding="utf-8"))["cases"]
        if len(cases) != REGISTERED_MODULE_CASES:
            problems.append(f"tests/fixtures/modules.json: registered "
                            f"{REGISTERED_MODULE_CASES} cases, found {len(cases)}")
        on_disk = {item.name for item in modules_directory.iterdir() if item.is_dir()}
        listed = {case["dir"] for case in cases}
        for missing in sorted(listed - on_disk):
            problems.append(f"tests/fixtures/modules.json: {missing} has no directory")
        for extra in sorted(on_disk - listed):
            problems.append(f"tests/fixtures/modules.json: {extra} has no expectation")
        for case in cases:
            entry = modules_directory / case["dir"] / case["entry"]
            if not entry.is_file():
                problems.append(f"tests/fixtures/modules/{case['dir']}: "
                                f"{case['entry']} is missing")
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
    fixtures = sum(REGISTERED_FIXTURE_DIRS.values()) + REGISTERED_MODULE_CASES
    print(
        f"OK: {tests} registered tests, {len(REGISTERED_TASKS)} tasks with {cases} cases, "
        f"{fixtures} spec fixtures, {len(CODES)} diagnostic codes, "
        f"tools {', '.join(REQUIRED_TOOLS)} present, languages {', '.join(REGISTERED_LANGUAGES)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
