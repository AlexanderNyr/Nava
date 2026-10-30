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

import argparse
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
    #: N12b: doc blocks are a contract, and the guard is what makes the claim
    #: true. `ContractGuardTests` carries the three CI lanes (every public name
    #: documented, every example true, every documented failure real) and the
    #: regeneration check; `ContractNegativeControlTests` proves the lanes fail.
    "DocBlockTests": {
        "test_a_doc_block_attaches_to_the_function_below_it",
        "test_a_blank_line_ends_a_doc_block",
        "test_a_doc_comment_inside_a_body_is_refused",
        "test_a_doc_comment_before_something_that_is_not_a_function_is_refused",
        "test_fields_are_read_and_a_duplicate_is_reported",
        "test_a_miswritten_field_shows_up_as_missing",
        "test_an_error_line_needs_both_halves",
        "test_statements_split_on_semicolons_but_not_inside_strings",
        "test_the_module_protocol_function_is_exempt",
    },
    "ContractGuardTests": {
        "test_every_public_name_has_a_contract",
        "test_every_documented_example_is_true",
        "test_every_documented_failure_happens",
        "test_a_pure_function_is_pure_by_its_body",
        "test_the_purity_claim_is_not_vacuous",
        "test_a_pure_function_answers_the_same_at_every_optimisation_level",
        "test_the_generated_reference_matches_the_tree",
        "test_the_command_writes_what_the_guard_expects",
        "test_an_unknown_module_is_reported",
        # A module that exports nothing passes every other lane by having
        # nothing to check, so it is a lane of its own.
        "test_a_module_with_no_public_function_is_not_a_pass",
    },
    "PurityNegativeControlTests": {
        "test_a_pure_function_that_prints_is_reported",
        "test_a_pure_function_that_calls_an_impure_one_is_reported",
        "test_a_pure_function_that_takes_a_mut_borrow_is_reported",
        "test_a_pure_function_that_writes_through_a_parameter_is_reported",
        "test_a_value_other_than_yes_or_no_is_reported",
        # A local helper without a claim is reported as *unclaimed*, not as
        # impure: the two send the author to different repairs.
        "test_a_pure_function_that_calls_an_unclaimed_helper_is_reported",
        "test_a_helper_that_states_its_own_claim_is_accepted",
    },
    "ContractNegativeControlTests": {
        "test_a_public_function_without_a_contract_is_reported",
        "test_an_example_that_does_not_compile_is_reported",
        "test_an_example_that_is_false_is_reported",
        "test_a_documented_failure_that_does_not_happen_is_reported",
        "test_a_documented_failure_with_the_wrong_message_is_reported",
    },
    #: N12e: `io` is the first module whose public surface is tested in two
    #: places — the resource-free half runs in the self-test, the file half needs
    #: a grant and runs in `tests/test_io.py`, and `TESTED_ELSEWHERE` there is an
    #: explicit list of names rather than a silent gap.
    "RoundTripTests": {
        "test_lines_come_back_as_they_went_in",
        "test_the_file_ends_with_a_newline",
        "test_an_empty_array_writes_an_empty_file",
        "test_only_one_trailing_empty_line_is_dropped",
        "test_lines_are_appended_to_what_the_vector_held",
        "test_append_line_adds_one_line_at_the_end",
        "test_append_line_starts_from_an_empty_file",
    },
    "CapabilityTests": {
        "test_reading_without_a_grant_is_denied",
        "test_a_read_grant_does_not_allow_writing",
        "test_a_write_grant_does_not_allow_reading",
    },
    "FailureTests": {
        "test_a_missing_file_is_reported",
        "test_appending_to_a_missing_file_is_reported",
    },
    #: N12f: `fs` — the text half of the paths runs in the self-test, the three
    #: filesystem questions need a grant and run here (`TESTED_ELSEWHERE`).
    "ExistenceTests": {
        "test_a_file_is_there_and_a_missing_name_is_not",
        "test_a_directory_is_there_too",
        "test_a_path_whose_parent_is_missing_is_simply_not_there",
        "test_a_question_about_a_path_outside_the_grants_is_refused",
        "test_a_question_without_a_grant_is_refused",
    },
    "SizeTests": {
        "test_the_size_is_the_number_of_bytes",
        "test_a_missing_file_stops_the_program",
        "test_a_directory_is_not_a_file",
    },
    "ListingTests": {
        "test_the_names_come_back_in_byte_order",
        "test_dot_and_dot_dot_are_not_entries",
        "test_the_entries_are_appended_to_what_the_vector_held",
        "test_two_listings_agree",
        "test_a_file_where_a_directory_was_expected_is_reported",
        "test_a_missing_directory_is_reported",
    },
    "PathTextTests": {
        "test_the_edges_of_basename_and_dirname",
        "test_join_never_doubles_a_separator",
        "test_extension_and_stem_agree_with_each_other",
        "test_a_path_built_by_join_reads_back_the_same",
    },
    "IsolationTests": {
        "test_asking_about_a_path_outside_the_grant_is_refused_by_the_cli",
    },
    #: N12g: `args` — the pure pair runs in the self-test, the four accessors need
    #: a command line and run here (`TESTED_ELSEWHERE`).
    "ArgumentTests": {
        "test_the_words_arrive_unchanged",
        "test_the_program_name_is_not_an_argument",
        "test_the_builtins_answer_the_same_as_the_module",
        "test_a_position_past_the_end_stops_the_program",
        "test_a_negative_position_stops_the_program",
        "test_the_message_uses_the_singular_for_one_argument",
    },
    "FlagAndValueTests": {
        "test_flag_finds_a_whole_word_and_nothing_shorter",
        "test_value_returns_the_next_word_or_the_fallback",
        "test_a_flag_with_nothing_after_it_has_no_value",
        "test_the_two_layers_agree",
        "test_the_first_occurrence_wins",
    },
    "CommandLineTests": {
        "test_the_program_receives_what_follows_the_double_dash",
        "test_a_word_that_looks_like_an_option_is_still_an_argument",
        "test_an_option_of_nava_after_the_source_belongs_to_nava",
        "test_no_double_dash_means_no_arguments",
    },
    #: N12h: `collections` — every public name of this module runs in the
    #: self-test (none of them needs a file, a grant or a command line), so this
    #: file carries the properties a single call cannot show: appending rather
    #: than replacing, a scratch set that keeps its values, the checked sum and
    #: the stability of the iteration order.
    "ReadingTests": {
        "test_the_sum_is_checked",
        "test_counting_and_containment_agree_with_each_other",
        "test_an_empty_vector_and_an_empty_wanted_list",
    },
    "FillingTests": {
        "test_keys_are_appended_not_replaced",
        "test_elements_are_appended_after_removals",
        "test_the_scratch_set_keeps_its_values",
        "test_a_set_that_already_held_values_counts_them",
    },
    "OrderTests": {
        "test_two_listings_of_one_table_agree",
        "test_the_same_insertions_give_the_same_order",
    },
    #: N12i: `time` — the one module whose subject is not deterministic, so the
    #: file carries what the self-test cannot: the clock compared against the
    #: harness's own `time.time()`, a wall clock that really moves between two
    #: runs of one binary, a monotonic reading that advances while the program
    #: spins, an elapsed time that is not clamped, the checked multiplication, and
    #: the guard that refuses a `pure: yes` claim about a clock.
    "WallClockTests": {
        "test_the_wall_clock_agrees_with_the_harness",
        "test_milliseconds_and_seconds_read_the_same_clock",
        "test_two_runs_of_the_same_binary_see_different_times",
    },
    "MonotonicClockTests": {
        "test_the_monotonic_clock_advances_while_the_program_spins",
        "test_a_mark_in_the_future_gives_a_negative_elapsed_time",
        "test_a_second_mark_is_never_below_the_first",
    },
    "ArithmeticTests": {
        "test_the_conversions_are_the_same_in_two_runs",
        "test_a_second_is_rebuilt_from_its_two_parts",
        "test_multiplication_that_does_not_fit_stops_the_program",
    },
    "GuardTests": {
        "test_a_pure_claim_about_the_clock_is_refused",
        "test_the_clock_builtins_are_not_on_the_pure_list",
        "test_the_module_marks_the_readings_impure_and_the_arithmetic_pure",
        "test_a_wrapper_cannot_be_named_like_the_builtin_it_wraps",
        "test_the_module_reaches_the_clock_under_its_own_names",
    },
    #: N13a: `nava.toml`. The manifest's diagnostics need a manifest next to an
    #: entry file, which no single-file fixture can carry, so this module is where
    #: `E0701`–`E0709` and `E0509` are exercised: strict reading of a TOML subset,
    #: names checked against the standard library, the edition checked against the
    #: entry file, the two commands that use it, and a report that is the same
    #: bytes twice.
    "ParsingTests": {
        "test_a_valid_manifest_is_read_whole",
        "test_comments_blank_lines_and_the_default_entry",
        "test_dependencies_keep_the_order_written_and_report_sorted",
        "test_an_unknown_table_is_refused",
        "test_an_unknown_field_is_refused_and_lists_the_known_ones",
        "test_a_repeated_key_is_refused",
        "test_a_key_before_any_table_is_refused",
        "test_shapes_outside_the_subset_are_refused",
        "test_a_field_of_the_wrong_shape_is_refused",
        "test_a_missing_required_field_is_its_own_code",
    },
    "NameAndVersionTests": {
        "test_a_package_may_not_take_a_standard_module_name",
        "test_a_dependency_may_not_take_one_either",
        "test_a_name_is_a_lower_case_identifier",
        "test_a_version_is_three_numbers",
        "test_constraints_match_what_semantic_versioning_says",
        "test_a_constraint_needs_a_version_after_its_operator",
        "test_an_edition_this_compiler_does_not_have_is_refused",
    },
    "ProjectTests": {
        "test_the_entry_must_exist",
        "test_the_entry_may_not_leave_the_project",
        "test_the_manifest_and_the_entry_must_agree_about_the_edition",
        "test_the_manifest_is_found_in_a_parent_directory",
        "test_no_manifest_anywhere_is_its_own_code",
    },
    "ProjectCommandTests": {
        "test_the_project_command_describes_the_manifest",
        "test_the_json_report_is_byte_identical_between_two_runs",
        "test_a_broken_manifest_is_reported_with_its_code",
        "test_without_a_manifest_the_command_says_where_it_looked",
        "test_the_compiling_commands_take_their_entry_from_the_manifest",
        "test_a_source_and_a_project_together_are_refused",
        "test_neither_a_source_nor_a_project_is_refused",
    },
    #: N13b: `nava.lock`. The phase's package gate is a sentence about trust, so
    #: this module carries the tamper list — one flipped byte in a vendored package,
    #: a digest edited by hand, a version or a dependency list that moved, an entry
    #: nothing needs, an entry that went missing — plus the digest's indifference to
    #: paths and listing order, and a second checkout.
    "DigestTests": {
        "test_the_same_tree_in_two_places_has_one_digest",
        "test_one_flipped_byte_is_a_different_package",
        "test_a_rename_changes_the_digest_as_much_as_a_byte",
        "test_the_order_files_were_written_in_does_not_matter",
        "test_a_symbolic_link_is_refused_rather_than_followed",
        "test_an_empty_package_still_has_a_digest",
    },
    "ResolutionTests": {
        "test_a_transitive_dependency_is_resolved_and_sorted",
        "test_a_package_that_is_not_vendored_is_named_with_who_wanted_it",
        "test_a_version_outside_the_project_constraint_is_refused",
        "test_a_version_outside_a_package_constraint_is_refused_too",
        "test_a_cycle_is_refused_with_the_cycle_written_out",
        "test_a_directory_that_is_not_a_package_is_refused",
        "test_a_package_vendored_under_the_wrong_name_is_refused",
        "test_a_package_may_not_shadow_a_standard_module",
        "test_a_package_with_no_version_is_refused",
    },
    "LockfileFileTests": {
        "test_the_text_is_the_same_bytes_twice",
        "test_a_written_file_reads_back_to_the_same_resolution",
        "test_the_reader_refuses_what_it_did_not_write",
        "test_a_missing_lockfile_is_its_own_code",
    },
    "CheckTests": {
        "test_an_untouched_project_checks_clean",
        "test_one_flipped_byte_in_a_vendored_package_is_e0804",
        "test_a_digest_edited_by_hand_is_e0804_too",
        "test_a_version_that_moved_is_e0803",
        "test_a_dependency_list_that_moved_is_e0803",
        "test_an_entry_nothing_needs_is_e0803",
        "test_an_entry_that_went_missing_is_e0803",
        "test_a_lockfile_for_another_project_is_e0803",
        "test_a_second_checkout_checks_clean",
    },
    "GraphTests": {
        "test_the_graph_names_every_package_with_its_digest",
        "test_the_graph_is_the_same_twice",
    },
    "LockCommandTests": {
        "test_the_command_writes_the_file_and_prints_the_graph",
        "test_the_file_is_the_same_bytes_when_written_twice",
        "test_check_reports_ok_and_then_catches_a_tampered_package",
        "test_the_json_report_carries_every_package",
        "test_checking_without_a_lockfile_says_to_write_one",
    },
    #: N13c: building against vendored packages. Three claims: a package's module is
    #: a module and may import another package's module; the lockfile is the
    #: boundary (an unlocked `vendor/` directory is `E0810`, not a dependency); and a
    #: name means one thing (local wins, `E0360` for the standard library, `E0811`
    #: for two packages). Plus the build record and the refused network mode.
    "PackageModuleTests": {
        "test_a_module_from_a_locked_package_is_importable_and_runs",
        "test_a_package_module_may_import_another_package_module",
        "test_the_build_record_names_the_packages_that_contributed",
        "test_the_json_build_record_carries_the_packages_and_the_network_mode",
        "test_a_project_with_no_packages_records_none",
    },
    "BoundaryTests": {
        "test_a_module_from_a_package_the_lockfile_does_not_name_is_e0810",
        "test_a_module_nobody_provides_lists_where_it_looked",
        "test_a_tampered_package_fails_the_build_and_not_only_the_lock_check",
        "test_a_local_module_wins_over_a_package_module",
        "test_a_package_module_may_not_shadow_a_standard_module",
        "test_two_packages_may_not_provide_the_same_module",
        "test_the_loader_reads_the_lockfile_rather_than_the_directory",
    },
    "NetworkTests": {
        "test_network_is_refused_with_its_own_code",
        "test_network_is_refused_before_anything_is_read",
        "test_offline_is_accepted_and_recorded",
    },
    #: N13d: reproducible builds. Two builds of one source must be one binary; a
    #: second checkout must write the same record bytes and the same executable; the
    #: generated C must be identical across checkouts; `SOURCE_DATE_EPOCH` is the one
    #: clock a build may see and is validated; and a difference is always *named*,
    #: with a toolchain difference listed before the digests it explains.
    "ReproduceTests": {
        "test_two_builds_of_one_source_are_one_binary",
        "test_more_runs_are_compared_with_the_first",
        "test_the_record_carries_every_source_and_no_time_of_its_own",
        "test_a_second_checkout_produces_the_same_record_and_binary",
        "test_the_generated_c_is_the_same_in_two_checkouts",
    },
    "DifferenceTests": {
        "test_a_modified_source_is_named",
        "test_source_date_epoch_is_recorded_and_a_change_to_it_is_named",
        "test_a_different_toolchain_is_a_toolchain_difference_first",
        "test_a_record_that_is_not_a_record_is_refused",
        "test_the_environment_variable_is_validated",
        "test_a_build_refuses_a_broken_epoch_even_without_a_record",
    },
    "CompareTests": {
        "test_equal_records_differ_in_nothing",
        "test_every_difference_is_named_and_the_toolchain_comes_first",
        "test_the_rendered_record_is_stable",
    },
    "SelfTestCoverageTests": {
        "test_the_program_names_every_public_function",
        "test_the_program_tests_every_module",
        "test_the_library_has_public_names_to_test",
        "test_every_name_tested_elsewhere_is_a_real_public_name",
        "test_a_name_tested_elsewhere_is_mentioned_where_it_says",
    },
    "SelfTestRunTests": {
        "test_it_exits_zero_with_the_recorded_output",
        "test_every_line_of_the_report_is_a_passing_test",
        "test_the_verdict_does_not_depend_on_how_it_was_built",
        "test_the_verdict_does_not_depend_on_how_often_it_runs",
    },
    "SelfTestNegativeControlTests": {
        "test_one_wrong_expectation_fails_the_run_and_names_the_test",
        "test_a_failing_expectation_is_the_exit_status",
        "test_the_module_reports_its_own_expectations",
        "test_a_passing_body_reports_nothing",
    },
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
    # --- N3: local model server and held-out task sets ------------------------
    "RateLimitTests": {
        "test_seconds_parses_the_shapes_groq_returns",
        "test_seconds_refuses_to_guess",
        "test_the_delay_prefers_what_the_server_asked_for",
        "test_a_rate_limited_call_is_retried_and_succeeds",
        "test_retries_are_bounded_and_then_reported_as_an_error",
        "test_a_permanent_error_is_not_retried",
        "test_the_request_carries_a_real_user_agent",
        "test_extra_headers_are_sent_but_only_their_names_are_recorded",
        "test_a_daily_quota_is_not_retried",
        "test_a_daily_limit_is_recognised_from_the_message_alone",
        "test_a_requests_own_size_is_not_a_daily_quota",
        "test_a_transient_output_limit_is_retried_not_blocked",
    },
    "IsolationRefusalTests": {
        "test_required_isolation_is_refused_before_anything_is_measured",
        "test_an_evaluator_refusal_is_an_error_not_a_wrong_answer",
    },
    "HeaderOptionTests": {
        "test_headers_are_parsed_in_both_shapes",
        "test_a_malformed_header_is_refused_before_anything_is_called",
        "test_the_key_cannot_be_smuggled_through_a_header",
    },
    "HeldOutSetTests": {
        "test_a_held_out_file_inside_the_repository_is_refused",
        "test_the_plan_records_the_digest_of_every_task_file",
        "test_a_held_out_task_is_evaluated_against_its_own_file",
        "test_an_unknown_task_id_lists_what_the_file_holds",
    },
    "RequestValidationTests": {
        "test_a_valid_request_is_normalised",
        "test_streaming_is_refused_rather_than_faked",
        "test_messages_are_required",
        "test_an_unsupported_role_is_named_in_the_error",
        "test_impossible_sampling_settings_are_refused",
        "test_a_non_object_body_is_refused",
        "test_stop_sequences_are_ignored_not_silently_honoured",
    },
    "ResponseSchemaTests": {
        "test_the_body_matches_the_openai_shape",
        "test_usage_is_the_sum_of_what_was_processed",
        "test_each_response_gets_its_own_id",
    },
    "HttpTests": {
        "test_a_completion_round_trips",
        "test_a_streaming_request_gets_a_400_with_a_reason",
        "test_malformed_json_gets_a_400",
        "test_an_unknown_path_gets_a_404",
        "test_the_models_endpoint_describes_the_weights",
    },
    "LazinessTests": {
        "test_importing_the_server_does_not_import_an_ml_stack",
    },
    "StudyAgainstTheShimTests": {
        "test_a_correct_answer_through_the_shim_is_a_measured_result",
    },
    # --- N9: packaging, release gate and CI honesty ----------------------------
    "MetadataTests": {
        "test_a_platform_cannot_be_promoted_without_evidence",
        "test_a_released_changelog_section_must_match_the_version",
        "test_a_version_mismatch_is_reported",
        "test_an_unknown_status_is_rejected",
        "test_the_ci_matrix_and_the_support_matrix_agree",
        "test_the_license_is_the_one_the_metadata_claims",
        "test_the_repository_declares_one_version_everywhere",
    },
    "SyntaxFloorTests": {
        "test_a_newer_syntax_is_rejected",
        "test_every_file_parses_with_the_declared_python_floor",
    },
    "SecretTests": {
        "test_credential_shapes_are_caught",
        "test_prose_about_secrets_is_not_a_secret",
        "test_the_repository_carries_no_credentials",
    },
    "ArtifactTests": {
        "test_a_complete_pair_of_artifacts_passes",
        "test_a_credential_inside_an_artifact_is_rejected",
        "test_a_wheel_without_the_standard_library_is_rejected",
        "test_an_sdist_without_its_license_is_rejected",
        "test_benchmark_records_never_ship",
        "test_tests_may_ship_in_an_sdist_but_not_in_a_wheel",
        "test_the_digests_are_reported",
    },
    "ReportTests": {
        "test_a_failed_check_makes_the_report_unhappy",
        "test_the_cli_exits_non_zero_on_a_problem",
        "test_the_report_is_machine_readable",
    },
    "DocumentationTests": {
        "test_the_readme_points_at_the_release_document",
        "test_the_release_checklist_names_the_real_commands",
        "test_the_workflow_runs_the_release_gate",
    },
    "ContinuousIntegrationHonestyTests": {
        "test_the_ab_lane_checks_both_arms",
        "test_the_isolation_gate_demands_the_jail_only_where_it_can_run",
        "test_the_runner_is_told_to_allow_user_namespaces",
        "test_the_study_lane_runs_where_it_can_and_proves_it_compiled",
    },
    "RegistryIsolationReportTests": {
        "test_a_host_without_namespaces_says_why",
        "test_strict_mode_fails_when_namespaces_are_missing",
        "test_the_ok_line_names_the_isolation_backend",
    },
    # --- N10e: defer, labelled loops and pub ---------------------------------
    "DeferFrontendTests": {
        "test_a_defer_needs_edition_two",
        "test_a_deferred_block_cannot_leave_itself",
        "test_a_deferred_block_is_a_block",
        "test_a_jump_must_name_a_loop_it_can_reach",
        "test_a_label_must_name_a_loop",
        "test_a_public_function_needs_edition_two",
        "test_labels_need_edition_two",
        "test_only_a_function_can_be_public",
    },
    "DeferRuntimeTests": {
        "test_a_defer_runs_on_break_and_continue",
        "test_a_defer_runs_on_return",
        "test_a_defer_runs_per_iteration",
        "test_a_deferred_block_can_declare_and_release_its_own_array",
        "test_defers_run_last_first_and_before_the_release",
    },
    "LabelledLoopRuntimeTests": {
        "test_a_label_leaves_every_loop_above_the_target",
        "test_a_label_works_on_a_while_loop_and_over_an_array",
        "test_a_labelled_continue_runs_the_increment",
        "test_a_labelled_jump_releases_the_arrays_it_leaves",
        "test_a_nested_loop_may_reuse_a_label_and_the_innermost_wins",
    },
    "PublicFrontendTests": {
        "test_a_private_function_cannot_be_called_from_another_module",
        "test_a_public_function_can_use_a_private_one",
        "test_an_edition_one_module_still_exports_everything",
    },
    # --- N10f: monomorphised generics ----------------------------------------
    "GenericFrontendTests": {
        "test_a_bare_template_is_not_a_type",
        "test_a_generic_enum_template_is_not_a_type_either",
        "test_a_struct_instance_may_hold_a_string",
        "test_a_template_can_be_instantiated_with_a_struct_and_an_enum",
        "test_a_type_argument_list_comes_first",
        "test_a_type_parameter_is_declared_once",
        "test_a_type_parameter_needs_edition_two",
        "test_an_argument_that_cannot_be_inferred",
        "test_an_instantiation_cannot_be_an_array_element",
        "test_an_uninstantiated_template_is_reported_as_unchecked",
        "test_inference_does_not_guess_from_a_conflict",
        "test_the_generated_c_has_one_function_per_instance",
        "test_two_instantiations_are_different_types",
        "test_wrong_number_of_type_arguments",
    },
    "GenericRuntimeTests": {
        "test_a_generic_enum_matches_per_instantiation",
        "test_a_template_instantiates_per_argument_type_at_run_time",
        "test_a_template_may_call_another_template",
        "test_an_instantiated_struct_copies_like_a_struct",
        "test_identity_works_for_every_scalar_type",
        "test_instances_nest",
        "test_the_same_instance_is_generated_once",
    },
    # --- N11a: values that hold text, arrays of every value ------------------
    "ValueFrontendTests": {
        "test_a_string_is_a_field_and_a_payload",
        "test_a_zeroable_element_type_still_works",
        "test_an_array_field_and_an_array_payload_may_be_elements",
        "test_an_enum_is_an_array_element",
        "test_an_undeclared_element_name_is_an_unknown_type",
        "test_array_zeros_needs_a_zero_value",
    },
    "ValueRuntimeTests": {
        "test_a_mutable_array_of_enums_is_written_in_place",
        "test_a_string_field_copies_while_the_string_is_shared",
        "test_a_string_payload_matches",
        "test_a_struct_with_text_travels_through_functions_and_arrays",
        "test_an_array_of_enums_is_written_and_read",
        "test_an_array_of_enums_slices_and_is_passed",
    },
    # --- N11b: fields and payloads that own array storage --------------------
    "TextFrontendTests": {
        "test_a_mutating_call_needs_a_writable_string",
        "test_a_string_may_be_an_element",
        "test_a_string_can_be_a_field_or_a_payload",
        "test_a_string_can_be_a_type_argument",
        "test_a_string_is_returned_by_moving_it",
        "test_a_string_moves_and_a_moved_string_is_dead",
        "test_a_string_is_not_printable_itself",
        "test_a_string_needs_edition_two",
        "test_every_text_function_is_usable_with_its_declared_type",
        "test_the_argument_types_and_arities_are_checked",
        "test_the_generated_c_owns_a_builder_and_uses_the_arena",
        "test_the_text_names_are_reserved",
    },
    "TextRuntimeTests": {
        "test_a_bad_slice_stops_the_program",
        "test_a_byte_outside_the_range_and_a_nul_are_refused_at_run_time",
        "test_a_large_amount_of_text_uses_several_chunks",
        "test_an_empty_separator_is_refused_at_run_time",
        "test_an_empty_vector_allocates_nothing" if False else "test_split_fills_a_vector_and_join_reads_one",
        "test_slices_and_searches_are_checked_and_exact",
        "test_text_and_vectors_under_the_sanitizers",
        "test_text_built_in_a_loop_and_from_a_parameter_is_stable",
        "test_the_arena_is_one_chunk_for_small_text_and_freed_at_exit",
        "test_the_reports_are_printed_once_each",
        "test_the_builder_grows_by_doubling_and_keeps_its_text",
        "test_a_returned_string_is_owned_by_the_caller_and_released",
        "test_the_constructors_produce_the_text_that_print_writes",
        "test_trim_and_replace_build_the_expected_text",
    },
    "ArgumentOnceTests": {
        "test_a_text_argument_with_a_side_effect_runs_once",
        "test_a_vector_argument_with_a_side_effect_runs_once",
    },
    # --- N11e: moves, and the arena region a frame gets ----------------------
    "MoveFrontendTests": {
        "test_a_borrow_cannot_be_moved_out_of_the_frame",
        "test_a_local_moves_into_another_binding_and_into_an_assignment",
        "test_a_moved_binding_cannot_be_read_passed_or_moved_again",
        "test_a_moved_string_cannot_be_read_or_returned",
        "test_a_mut_parameter_cannot_be_replaced",
        "test_a_region_is_emitted_only_where_it_is_sound",
        "test_a_vector_and_a_string_can_be_moved_out_of_a_function",
        "test_an_owning_result_must_be_bound",
        "test_an_owning_value_is_stored_and_may_be_an_element",
        "test_moving_is_a_value_transfer_and_not_a_general_expression",
    },
    "MoveRuntimeTests": {
        "test_a_move_copies_no_elements_and_releases_once",
        "test_a_moved_binding_inside_a_nested_block_is_released_by_the_receiver",
        "test_a_returned_vector_is_owned_by_the_caller",
        "test_an_assignment_releases_the_buffer_it_replaces",
        "test_an_early_return_moves_the_value_out_of_a_loop",
        "test_moves_and_returns_under_the_sanitizers",
    },
    "StorageFrontendTests": {
        "test_a_field_holds_a_vector_and_a_string",
        "test_a_field_is_read_but_never_moved_out",
        "test_a_holder_of_storage_may_be_an_element",
        "test_a_mutating_call_on_a_field_needs_a_local",
        "test_a_nested_field_copies_both_levels",
        "test_a_payload_holds_a_vector_and_a_string",
        "test_a_struct_that_contains_itself_through_a_field_is_still_refused",
        "test_a_type_argument_may_be_an_owning_type",
        "test_an_owning_field_needs_edition_two",
    },
    "StorageRuntimeTests": {
        "test_a_field_write_replaces_and_releases_the_old_buffer",
        "test_a_payload_binding_is_a_copy_of_its_own",
        "test_a_stored_value_travels_through_functions_and_keeps_its_storage",
        "test_a_stored_vector_is_a_copy_and_the_source_goes_on",
        "test_a_type_argument_value_is_independent",
        "test_a_value_that_owns_fields_is_released_with_its_block",
        "test_stored_values_under_the_sanitizers",
    },
    "ElementFrontendTests": {
        "test_a_collection_is_still_not_an_element",
        "test_a_holder_of_storage_is_an_element",
        "test_a_loop_variable_is_a_copy_released_with_its_turn",
        "test_a_read_of_an_element_is_a_copy_that_the_statement_releases",
        "test_a_slot_is_released_before_it_is_overwritten",
        "test_a_vector_of_strings_copies_an_element_in_on_push_and_set",
        "test_a_zeroed_slot_is_not_a_value_of_an_owning_element",
        "test_an_array_of_strings_copies_elements_in_and_releases_them",
        "test_an_enum_element_is_copied_and_released",
        "test_the_honest_message_survives_for_an_unknown_element",
    },
    "ElementRuntimeTests": {
        "test_a_loop_copies_each_element",
        "test_a_stored_string_is_independent_of_its_source",
        "test_a_vector_of_strings_pushes_reads_and_pops",
        "test_a_write_to_a_slot_releases_what_it_held",
        "test_an_array_of_bags_is_released_with_its_elements",
        "test_elements_under_the_sanitizers",
    },
    "TableFrontendTests": {
        "test_a_collection_is_not_a_value_or_an_element",
        "test_a_key_must_be_a_value_with_a_hash",
        "test_a_map_and_a_set_are_not_printable",
        "test_a_map_is_moved_not_copied",
        "test_a_map_loop_needs_a_name",
        "test_a_mutating_call_needs_a_writable_target",
        "test_a_set_uses_the_same_probe_without_a_value",
        "test_a_stored_value_is_copied_in_and_released",
        "test_a_struct_value_is_copied_with_its_own_helpers",
        "test_map_get_is_total_and_returns_an_option",
        "test_one_table_per_instance_is_emitted",
        "test_the_types_must_be_written_out",
    },
    "TableRuntimeTests": {
        "test_a_map_of_structs_copies_in_and_out",
        "test_a_returned_map_keeps_its_keys",
        "test_a_set_deduplicates_owning_elements",
        "test_a_stored_value_is_a_copy",
        "test_a_string_key_is_copied",
        "test_a_struct_field_may_hold_a_map",
        "test_entries_survive_growth_and_removal",
        "test_iteration_visits_every_entry_once",
        "test_tables_under_the_sanitizers",
    },
    "ArenaRegionTests": {
        "test_a_frame_that_builds_text_gets_its_region_back",
        "test_text_that_can_escape_keeps_its_chunks",
        "test_text_written_through_a_mut_parameter_survives_the_call",
    },
    "VectorFrontendTests": {
        "test_a_mut_vector_argument_must_be_created_here",
        "test_a_mut_vector_parameter_may_grow_the_caller_s_vector",
        "test_a_borrowed_vector_cannot_be_reassigned",
        "test_a_moved_vector_leaves_nothing_behind",
        "test_a_vector_can_be_returned_but_not_discarded",
        "test_a_vector_cannot_be_printed_or_assigned_to",
        "test_a_field_holding_a_vector_is_read_but_not_moved_out",
        "test_a_vector_can_be_a_field_or_a_payload",
        "test_a_vector_is_created_in_a_block_and_grown_by_builtins",
        "test_a_vector_of_structs_and_a_vector_of_enums_compile",
        "test_a_collection_is_not_an_element",
        "test_an_element_may_own_storage",
        "test_a_mutating_builtin_needs_a_writable_target",
        "test_the_argument_types_are_checked",
        "test_the_vector_names_are_reserved",
        "test_the_generated_c_holds_a_handle_and_doubles",
        "test_vec_new_needs_an_annotated_element_type",
    },
    "VectorRuntimeTests": {
        "test_a_hundred_pushes_stay_within_twice_the_length",
        "test_a_string_vector_keeps_its_strings",
        "test_a_struct_vector_reads_and_writes_fields",
        "test_an_empty_vector_allocates_nothing",
        "test_an_enum_vector_matches_its_payload",
        "test_an_out_of_bounds_index_stops_the_program",
        "test_a_vector_of_a_generic_instance_and_of_an_option",
        "test_growth_aliasing_and_drain_under_the_sanitizers",
        "test_growth_inside_a_parameter_is_seen_by_the_caller",
        "test_pop_drains_the_vector_and_then_reports_none",
        "test_pop_set_and_push_change_the_caller_s_vector",
        "test_pushes_grow_by_doubling_from_four",
        "test_a_defer_reads_the_vector_before_it_is_released",
        "test_a_float_vector_and_a_bool_vector_round_trip",
        "test_a_vector_declared_in_a_loop_body_is_released_each_turn",
        "test_a_vector_is_released_on_an_early_return",
        "test_set_replaces_one_element_only",
        "test_two_vectors_in_nested_blocks_free_both",
    },
    "OwningFieldFrontendTests": {
        "test_a_nested_owning_field_is_a_value_too",
        "test_an_array_field_and_an_array_payload_are_values",
        "test_an_array_field_can_be_replaced",
        "test_an_array_field_is_read_like_any_other_array",
        "test_an_enum_with_an_owning_payload_is_an_element_too",
        "test_an_owning_value_may_be_an_array_element",
        "test_the_generated_c_copies_and_releases_only_where_needed",
    },
    "OwningFieldRuntimeTests": {
        "test_a_constructed_field_copies_the_source_array",
        "test_a_copied_value_keeps_its_own_storage",
        "test_a_defer_can_read_the_array_a_value_owns",
        "test_a_value_travels_through_functions_and_keeps_its_storage",
        "test_a_write_through_a_parameter_leaves_the_caller_alone",
        "test_the_copies_are_memory_safe_under_the_sanitizers",
        "test_an_enum_payload_is_copied_and_replaced",
        "test_nothing_is_leaked_on_the_normal_path",
        "test_nothing_is_leaked_when_a_block_is_left_early",
    },
    # --- N10g: function values and function pointers -------------------------
    "FunctionValueFrontendTests": {
        "test_a_call_cannot_go_through_a_field",
        "test_a_declared_function_can_be_bound_and_called",
        "test_a_function_type_needs_edition_two",
        "test_a_function_type_takes_no_mut_parameter",
        "test_a_function_value_cannot_be_printed",
        "test_a_function_value_fits_every_value_position",
        "test_a_generic_function_is_not_a_value",
        "test_a_signature_is_part_of_the_type",
        "test_a_template_instantiates_per_signature",
        "test_a_value_carries_its_signature",
        "test_every_part_of_a_function_type_must_be_a_type",
        "test_only_a_function_type_can_be_called",
        "test_the_arity_is_checked_through_the_value",
        "test_the_generated_c_typedefs_each_signature",
        "test_two_function_values_cannot_be_compared",
    },
    "FunctionValueRuntimeTests": {
        "test_a_field_and_a_variant_payload_hold_a_value",
        "test_a_generic_instance_takes_one_copy_per_signature",
        "test_a_nested_signature_is_its_own_type",
        "test_a_value_is_called_through_the_pointer",
        "test_a_value_is_passed_and_returned",
        "test_a_void_function_is_a_value_too",
        "test_an_array_parameter_travels_through_the_value",
        "test_two_values_of_one_signature_compose",
    },
    # --- N10d: arrays of structs and slices ----------------------------------
    "SliceFrontendTests": {
        "test_a_slice_cannot_become_a_mutable_borrow",
        "test_a_slice_composes",
        "test_a_slice_is_read_only",
        "test_a_slice_needs_edition_two",
        "test_an_array_of_enums_is_a_value_array",
        "test_an_unknown_element_name_is_reported_as_an_unknown_type",
        "test_only_an_array_can_be_sliced",
        "test_struct_arrays_keep_the_array_rules",
        "test_the_bounds_must_be_i64",
    },
    "SliceRuntimeTests": {
        "test_a_bad_slice_end_is_reported_as_an_end",
        "test_a_bad_slice_start_is_reported_as_a_start",
        "test_a_slice_does_not_release_the_storage_it_views",
        "test_a_for_loop_can_iterate_a_slice",
        "test_a_slice_is_a_view_and_not_a_copy",
        "test_a_view_sees_a_write_through_the_owning_array",
        "test_a_struct_element_read_is_a_copy",
        "test_an_element_write_replaces_the_whole_struct",
        "test_the_bounds_are_evaluated_left_to_right",
        "test_zeroed_struct_arrays_are_all_zero",
    },
    # --- N10c: Option, Result and ? ------------------------------------------
    "ResultFrontendTests": {
        "test_a_pattern_must_name_the_same_instantiation",
        "test_a_wrong_number_of_type_arguments_is_rejected",
        "test_an_option_propagates_without_a_failure_type",
        "test_an_unknown_type_argument_is_rejected",
        "test_only_the_prelude_takes_type_arguments",
        "test_propagation_rules",
        "test_the_prelude_enum_takes_its_arguments_in_parentheses",
        "test_the_prelude_enums_are_ordinary_enums",
        "test_the_success_type_may_differ_from_the_callers",
    },
    "ResultRuntimeTests": {
        "test_a_chain_stops_at_the_first_failure",
        "test_a_failure_returns_early_and_reaches_the_caller",
        "test_a_none_propagates_like_an_error",
        "test_the_success_value_is_bound_with_its_own_type",
        "test_two_instances_of_one_prelude_enum_coexist",
    },
    # --- N10b: enums and match -----------------------------------------------
    "EnumFrontendTests": {
        "test_a_match_that_does_not_return_everywhere_still_needs_a_return",
        "test_a_struct_and_an_enum_cannot_share_a_name",
        "test_an_enum_cannot_be_printed_or_compared",
        "test_an_enum_needs_edition_two",
        "test_an_exhaustive_match_counts_as_a_return",
        "test_construction_rules",
        "test_declaration_rules",
        "test_enums_nest_in_structs_and_each_other",
        "test_match_rules",
    },
    "EnumRuntimeTests": {
        "test_a_wildcard_arm_catches_the_variants_it_did_not_name",
        "test_arms_are_tried_in_source_order_and_run_once",
        "test_bindings_are_copies",
        "test_every_variant_is_reachable_and_binds_its_fields",
        "test_the_scrutinee_is_evaluated_once",
    },
    # --- N10a: editions and structs -------------------------------------------
    "StructFrontendTests": {
        "test_a_duplicate_struct_is_rejected",
        "test_a_field_may_be_written_inside_a_loop",
        "test_a_function_does_not_take_named_arguments",
        "test_a_struct_cannot_be_printed_or_compared",
        "test_a_struct_may_be_a_parameter_and_a_return_value",
        "test_a_struct_may_not_contain_itself",
        "test_a_struct_must_be_declared_before_use_is_not_required",
        "test_a_struct_needs_edition_two",
        "test_an_unknown_edition_is_named",
        "test_construction_rules",
        "test_edition_one_is_unchanged",
        "test_field_rules",
        "test_member_rules",
        "test_nesting_is_copied_with_the_struct",
        "test_the_edition_declaration_must_come_first",
    },
    "StructRuntimeTests": {
        "test_a_struct_is_copied_not_shared",
        "test_a_struct_parameter_is_a_copy",
        "test_fields_evaluate_left_to_right",
    },
    # --- N10–N18: the plan is itself guarded --------------------------------
    "PhaseRecordTests": {
        "test_a_phase_is_not_called_delivered_without_a_record",
        "test_every_phase_has_an_objective_tasks_gate_and_deliverable",
        "test_the_independence_phases_also_carry_a_negative_control",
        "test_the_status_lines_name_the_last_specified_phase",
        "test_the_independence_plan_is_its_own_document",
    },
    "SpecificationCriteriaTests": {
        "test_decisions_are_numbered_without_gaps",
        "test_every_criterion_names_the_phase_that_proves_it",
        "test_the_audit_numbers_match_the_current_compiler",
        "test_the_feature_audit_names_a_phase_for_each_row",
        "test_the_non_goals_point_at_the_reversal",
        "test_the_six_criteria_exist_and_are_numbered",
    },
    # --- N7: model-facing guide, grammar conformance, A/B and feedback ---------
    "GuideManifestTests": {
        "test_an_edited_guide_without_a_manifest_update_is_reported",
        "test_recorded_digests_and_sizes_match_the_files",
        "test_the_guide_tracks_the_documented_specification_version",
        "test_the_manifest_names_a_current_guide_that_exists",
    },
    "GuideExampleTests": {
        "test_every_example_file_is_reachable_from_a_recorded_entry",
        "test_every_example_is_covered_by_the_grammar_and_the_compiler",
        "test_every_example_runs_and_prints_its_recorded_output",
        "test_the_manifest_lists_the_current_guide",
    },
    "GuideCoverageTests": {
        "test_every_builtin_is_named",
        "test_every_keyword_is_named",
        "test_the_guide_keeps_the_implemented_features_and_drops_the_stale_denials",
        "test_the_guide_lists_every_standard_library_function",
        "test_the_whole_program_examples_in_the_guide_compile",
    },
    "GuidePromptTests": {
        "test_the_c_and_cpp_guides_have_digests_too",
        "test_the_prompt_builder_reports_the_guide_metadata",
        "test_the_prompt_carries_the_versioned_guide_verbatim",
    },
    "GuideOverheadTests": {
        "test_a_record_can_never_contain_an_invented_token_count",
        "test_the_command_line_can_demand_a_measured_overhead",
        "test_the_overhead_covers_every_task_and_language",
        "test_the_overhead_is_the_difference_between_the_prompts",
        "test_the_recorded_measurements_still_describe_the_current_guide",
        "test_without_a_tokenizer_the_overhead_has_no_token_numbers",
    },
    "GuideTokenTests": {
        "test_a_missing_tokenizer_library_is_reported_not_estimated",
        "test_require_measured_fails_when_nothing_was_measured",
        "test_sizes_are_exact_and_tokens_are_null_without_a_tokenizer",
    },
    "GrammarArtefactTests": {
        "test_an_unclosed_string_is_rejected_by_both",
        "test_every_canonical_example_is_recognised_by_the_emitted_text",
        "test_a_left_recursive_grammar_is_refused",
        "test_a_grammar_that_references_a_missing_rule_is_refused",
        "test_gbnf_keyword_matching_is_documented_as_permissive",
        "test_the_artefact_is_written_by_the_command_line",
        "test_the_emitted_gbnf_has_no_structural_problem",
        "test_the_emitted_text_rejects_a_deliberately_malformed_candidate",
        "test_the_validate_command_accepts_the_artefact",
    },
    "CorpusConformanceTests": {
        "test_every_corpus_code_is_classified",
        "test_every_valid_fixture_is_accepted",
        "test_multi_file_module_fixtures_are_accepted",
        "test_negative_fixtures_are_accepted_or_rejected_as_classified",
        "test_the_shipped_examples_are_accepted",
        "test_the_standard_library_is_accepted_with_its_doc_blocks",
    },
    "SyntaxIsNotSemanticsTests": {
        "test_a_runtime_failure_survives_both_the_grammar_and_the_compiler",
        "test_a_type_error_is_accepted_by_the_grammar_and_rejected_by_the_compiler",
        "test_an_overflow_is_accepted_by_both_and_still_fails_at_run_time",
        "test_an_unmatched_bracket_is_rejected_before_types_are_considered",
    },
    "ConstrainedPayloadTests": {
        "test_a_summary_preview_keeps_the_digest_and_drops_the_text",
        "test_an_unknown_interface_is_an_error",
        "test_checking_the_grammar_catches_a_broken_artefact",
        "test_each_interface_puts_the_grammar_in_its_documented_field",
        "test_the_adapter_declares_itself_unverified",
        "test_the_command_line_preview_carries_the_whole_grammar",
    },
    "FeedbackTests": {
        "test_a_syntax_error_is_reported_as_the_parse_stage",
        "test_a_type_error_carries_the_code_and_the_hint",
        "test_a_wrong_answer_is_reported_as_the_tests_stage",
        "test_feedback_is_capped_and_dropped_items_are_counted",
        "test_the_metadata_identifies_the_feedback_without_repeating_it",
    },
    "RepairAccountingTests": {
        "test_a_missing_measurement_is_never_zero",
        "test_structured_feedback_keeps_first_attempt_and_repair_apart",
        "test_text_feedback_still_works_and_is_marked_as_such",
    },
    "ABRecordTests": {
        "test_a_missing_token_count_is_null_with_a_reason",
        "test_a_run_keeps_repairs_out_of_pass_at_1",
        "test_no_claim_is_made_by_the_harness",
        "test_plan_only_announces_both_arms_and_calls_nothing",
        "test_require_measured_is_enforced",
        "test_tampering_is_refused",
        "test_the_arms_differ_only_in_the_request_payload",
        "test_the_grammar_and_guide_digests_are_recorded",
        "test_the_record_passes_its_own_validator",
    },
    "RegistryTests": {
        "test_registered_tests_are_present_and_not_skipped",
        "test_required_tools_are_present",
        "test_task_fixtures_match_registry_and_reference",
        "test_diagnostics_registry_is_complete",
        "test_spec_fixtures_are_registered",
        "test_registry_module_guard_passes",
    },
    #: N14a: the native backend's own gate is a differential — the same program
    #: through two code generators, compared on stdout, stderr and status — plus the
    #: shape of the IR it consumes, the shape of the file it writes, and the refusals
    #: that keep "not implemented" from becoming "wrong answer".
    "NativeDifferentialTests": {
        "test_each_program_prints_the_same_through_both_backends",
        "test_each_trap_is_the_same_message_and_status",
        "test_the_exit_status_is_the_return_value_modulo_256",
    },
    "NativeIRTests": {
        "test_a_branch_and_a_loop_header_pass_their_values_explicitly",
        "test_each_jump_passes_one_value_per_block_parameter",
        "test_every_block_ends_in_exactly_one_terminator",
        "test_every_value_is_used_only_where_its_definition_dominates",
        "test_the_dump_is_stable",
        "test_the_short_circuit_is_a_branch_so_the_right_side_does_not_run",
    },
    "NativeRefusalTests": {
        "test_a_call_with_more_than_six_arguments_is_refused",
        "test_a_construct_is_refused_by_name",
        "test_a_routine_the_runtime_lacks_is_refused_at_code_generation",
        "test_an_unknown_option_is_refused_rather_than_ignored",
        "test_the_c_backend_compiles_every_program_the_native_one_refuses",
    },
    #: N14e: `f64` and struct values. Two claims, both comparisons: the backends agree
    #: on a struct copied through a nested path, and on an `f64` observed through a
    #: comparison. The forms the backend cannot compile are refused by name rather
    #: than approximated, so the boundary is a test and not a comment.
    "NativeStructTests": {
        "test_a_str_field_is_a_string_like_any_other",
        "test_a_struct_is_copied_where_it_is_stored",
        "test_a_struct_value_lives_in_a_cell_and_nowhere_else",
        "test_the_module_states_every_layout_it_lays_out",
    },
    #: N14f: text and input. A `str` comparison is `TextCompare` in the IR — never a
    #: pointer compare — and the four string operations plus `read_i64` are runtime
    #: routines whose traps are the C runtime's words, so the differential covers them.
    "NativeInputTestsOrdering": {
        "test_a_line_printed_before_a_read_comes_first",
    },
    #: N14g: enums and `match`. A tag is a word in the record and a match is a
    #: comparison against the variant's position, so the layout is the module's fact
    #: and the arm is a constant. The last class pins the `unreachable` terminator that
    #: lets a function whose every path returns lower at all.
    "NativeEnumTests": {
        "test_a_match_inside_a_loop_reads_the_value_it_was_given_each_time",
        "test_a_match_through_a_parameter_sees_the_tag_it_was_given",
        "test_a_payload_the_backend_cannot_hold_is_refused_by_name",
        "test_an_enum_and_a_match_agree_with_the_c_backend",
        "test_the_ir_states_the_tag_and_the_layouts",
    },
    "NativeReturnPathTests": {
        "test_a_stmt_after_a_finished_match_is_not_emitted",
        "test_an_if_where_both_halves_return_lowers",
        "test_the_block_after_a_finished_choice_is_stated_unreachable",
    },
    "NativeTextTests": {
        "test_a_byte_index_out_of_bounds_is_the_c_runtime_message",
        "test_a_slice_out_of_bounds_names_three_numbers",
        "test_a_string_comparison_is_text_and_not_storage",
        "test_text_operations_agree_with_the_c_backend",
        "test_the_text_of_a_number_is_the_text_printed_str_alone",
    },
    "NativeInputTests": {
        "test_a_token_is_read_and_converted",
        "test_every_refusal_is_the_c_runtime_message_and_status",
        "test_the_two_extremes_are_read_exactly",
    },
    "NativeFloatTests": {
        "test_a_float_constant_is_its_bits_and_the_dump_says_so",
        "test_an_f64_crosses_a_call_as_its_bits",
        "test_float_arithmetic_and_comparison_agree_with_the_c_backend",
    },
    "NativeELFTests": {
        "test_the_binary_is_marked_executable_and_can_be_run_directly",
        "test_the_file_is_a_static_x86_64_executable",
        "test_the_line_table_names_every_function_of_the_program",
        "test_the_sections_and_the_symbol_table_say_what_was_linked",
        "test_two_builds_of_one_source_are_the_same_bytes",
    },
    "NativeCommandTests": {
        "test_build_needs_no_c_compiler_on_the_path",
        "test_emit_ir_refuses_a_construct_it_cannot_represent",
        "test_emit_ir_states_a_struct_and_an_f64_rather_than_refusing",
        "test_emit_ir_writes_the_same_text_to_stdout_and_to_a_file",
        "test_run_compiles_with_the_native_backend_when_asked",
        "test_run_refuses_the_c_build_options_with_the_native_backend",
        "test_the_json_report_names_the_backend",
    },
    "NativeRuntimeTests": {
        "test_a_bool_prints_as_true_or_false",
        "test_a_string_is_written_byte_for_byte_and_then_a_newline",
        "test_integers_are_written_in_decimal_including_the_extremes",
    },
    #: N14b: whole programs. A unit says what it defines and what it leaves to the
    #: linker; the linker is tested on its own before it is trusted with a program;
    #: two files, two units, one executable; our reader cross-checked against
    #: `readelf` and `nm`; and the trace, whose silence when it is not asked for is
    #: what keeps the two backends comparable.
    "NativeUnitTests": {
        "test_a_function_is_a_global_symbol_and_everything_else_is_private",
        "test_a_reference_every_unit_cannot_resolve_is_a_relocation",
        "test_every_call_is_a_relocation_because_a_call_names_its_callee",
        "test_every_function_records_its_extent_and_its_lines_in_order",
        "test_there_is_one_unit_per_module_and_one_for_the_runtime",
    },
    "NativeLinkerTests": {
        "test_a_missing_entry_point_is_refused",
        "test_an_undefined_symbol_is_refused_by_name",
        "test_the_image_names_its_sections_its_entry_point_and_its_symbols",
        "test_the_line_table_is_the_same_size_in_both_passes",
        "test_two_units_with_one_name_are_refused",
    },
    "NativeModuleTests": {
        "test_a_line_table_names_the_file_each_function_came_from",
        "test_a_program_in_two_files_prints_the_same_through_both_backends",
        "test_each_module_keeps_its_own_symbols",
    },
    "NativeReaderTests": {
        "test_a_truncated_file_is_rejected_rather_than_guessed_at",
        "test_something_that_is_not_an_executable_is_rejected",
        "test_the_reader_and_nm_agree_about_every_symbol",
        "test_the_reader_and_readelf_agree_about_the_header_and_the_segments",
        "test_the_reader_names_the_function_and_the_line_of_an_address",
        "test_the_reader_reads_what_our_writer_wrote_and_nothing_else",
    },
    "NativeTraceTests": {
        "test_a_trace_stops_at_the_bottom_of_the_stack",
        "test_the_variable_adds_a_frame_for_every_caller_from_the_innermost_out",
        "test_without_the_variable_a_trap_prints_exactly_the_message",
    },
    #: N14c: arrays through one IR. The gate is the differential on the same array
    #: programs, plus the three traps (a bounds check, a negative length and the
    #: length limit) whose message and status the two backends share, plus the
    #: ownership the IR states and the one regression this increment found.
    #: N14d: the C backend reads the IR. The gate is three-way — IR path, AST path and
    #: the native backend on one program — plus the boundary in both directions (a
    #: fixture in the subset must be printed from the IR; a fixture outside it must
    #: not be) and the one hole this increment closed, a `str` compared by pointer.
    "CFromIRTests": {
        "test_a_generic_with_one_instantiation_is_printed_from_the_ir",
        "test_a_program_outside_the_subset_still_comes_from_the_ast",
        "test_a_program_the_ir_expresses_but_this_printer_does_not",
        "test_a_str_comparison_prints_as_strcmp_and_never_as_pointer_equality",
        "test_an_enum_is_declared_with_the_name_the_checker_gave_it",
        "test_array_access_goes_through_the_checked_helpers",
        "test_every_function_is_defined_once_under_the_name_the_ast_path_used",
        "test_every_program_in_the_subset_is_printed_from_the_ir",
        "test_the_body_says_it_came_from_the_ir",
        "test_the_escape_hatch_asks_for_the_old_path",
        "test_the_index_is_checked_before_the_value_is_evaluated",
        "test_the_names_in_the_artefact_do_not_depend_on_the_path",
        "test_the_ir_states_the_check_before_the_store",
        "test_the_printer_is_deterministic",
        "test_the_three_paths_agree_on_output_stderr_and_status",
        "test_two_instantiations_are_refused_by_name_rather_than_confused",
    },
    "NativeArrayTests": {
        "test_a_bounds_trap_traces_like_any_other_trap",
        "test_a_temporary_array_is_released_when_its_statement_ends",
        "test_a_while_whose_condition_short_circuits_leaves_the_loop",
        "test_an_array_parameter_is_borrowed_and_never_released",
        "test_every_array_fixture_builds_and_runs_through_both_backends",
        "test_the_block_that_declared_an_array_releases_it_last_declared_first",
        "test_the_ir_states_every_array_operation_the_language_has",
        "test_the_release_instruction_costs_nothing_in_the_native_backend",
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
    "tests/fixtures/negative": 146,
    "tests/fixtures/positive": 32,
    "tests/fixtures/warnings": 3,
}

#: Multi-file module fixtures: one directory per case, listed in
#: `tests/fixtures/modules.json`.
REGISTERED_MODULE_CASES = 11

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
    "E0433": "tests/fixtures/modules.json: private_module",
    "E0355": "tests/fixtures/modules.json: duplicate_import",
    "E0356": "tests/test_modules.py: test_an_import_cannot_escape_the_project_boundary",
    "E0357": "tests/fixtures/modules.json: main_in_module",
    "E0358": "tests/fixtures/modules.json: unqualified",
    "E0359": "tests/fixtures/modules.json: not_imported",
    "E0360": "tests/fixtures/modules.json: shadow",
    "E0361": "tests/test_modules.py: test_the_standard_library_version_is_checked",
    "E0506": "tests/test_files.py: test_a_missing_grant_directory_is_reported",
    "E0507": "tests/test_files.py: test_a_grant_that_is_not_a_directory_is_reported",
    # `nava doc` is the only command that reads modules rather than a program,
    # so its one diagnostic is covered by the contracts guard.
    "E0508": "tests/test_contracts.py: test_an_unknown_module_is_reported",
    # The manifest's diagnostics need a `nava.toml` beside an entry file, which a
    # single-file fixture cannot hold: they are covered by tests/test_manifest.py.
    "E0509": "tests/test_manifest.py: CommandTests",
    "E0701": "tests/test_manifest.py: test_no_manifest_anywhere_is_its_own_code",
    "E0702": "tests/test_manifest.py: ParsingTests",
    "E0703": "tests/test_manifest.py: test_a_missing_required_field_is_its_own_code",
    "E0704": "tests/test_manifest.py: test_an_unknown_table_is_refused",
    "E0705": "tests/test_manifest.py: test_a_version_is_three_numbers",
    "E0706": "tests/test_manifest.py: test_the_manifest_and_the_entry_must_agree_about_the_edition",
    "E0707": "tests/test_manifest.py: test_the_entry_may_not_leave_the_project",
    "E0708": "tests/test_manifest.py: test_a_package_may_not_take_a_standard_module_name",
    "E0709": "tests/test_manifest.py: test_a_name_is_a_lower_case_identifier",
    "E0801": "tests/test_lockfile.py: test_a_missing_lockfile_is_its_own_code",
    "E0802": "tests/test_lockfile.py: test_the_reader_refuses_what_it_did_not_write",
    "E0803": "tests/test_lockfile.py: test_an_entry_nothing_needs_is_e0803",
    "E0804": "tests/test_lockfile.py: test_one_flipped_byte_in_a_vendored_package_is_e0804",
    "E0805": "tests/test_lockfile.py: test_a_package_that_is_not_vendored_is_named_with_who_wanted_it",
    "E0806": "tests/test_lockfile.py: test_a_version_outside_the_project_constraint_is_refused",
    "E0807": "tests/test_lockfile.py: test_a_cycle_is_refused_with_the_cycle_written_out",
    "E0808": "tests/test_lockfile.py: test_a_directory_that_is_not_a_package_is_refused",
    "E0810": "tests/test_packages.py: test_a_module_from_a_package_the_lockfile_does_not_name_is_e0810",
    "E0811": "tests/test_packages.py: test_two_packages_may_not_provide_the_same_module",
    "E0812": "tests/test_packages.py: test_network_is_refused_with_its_own_code",
    "E0813": "tests/test_reproducible.py: test_the_environment_variable_is_validated",
    "E0814": "tests/test_reproducible.py: test_a_modified_source_is_named",
    "E0815": "tests/test_reproducible.py: test_a_record_that_is_not_a_record_is_refused",
    # The native backend is a second code generator, so its refusals are produced by
    # compiling a *valid* program through it: no source fixture can expect them,
    # because the same fixture compiles through the C backend.
    "E0901": "tests/test_native.py: test_a_construct_is_refused_by_name",
    "E0903": "tests/test_native.py: test_an_unknown_option_is_refused_rather_than_ignored",
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
    import test_ab  # noqa: PLC0415  (import after sys.path setup)
    import test_arrays  # noqa: PLC0415
    import test_contracts  # noqa: PLC0415
    import test_control  # noqa: PLC0415
    import test_defers  # noqa: PLC0415
    import test_enums  # noqa: PLC0415
    import test_files  # noqa: PLC0415
    import test_float  # noqa: PLC0415
    import test_functions  # noqa: PLC0415
    import test_generics  # noqa: PLC0415
    import test_grammar  # noqa: PLC0415
    import test_guides  # noqa: PLC0415
    import test_local_model_server  # noqa: PLC0415
    import test_modules  # noqa: PLC0415
    import test_nava  # noqa: PLC0415
    import test_ownership  # noqa: PLC0415
    import test_plan  # noqa: PLC0415
    import test_registry  # noqa: PLC0415
    import test_self_test  # noqa: PLC0415
    import test_release  # noqa: PLC0415
    import test_results  # noqa: PLC0415
    import test_slices  # noqa: PLC0415
    import test_sandbox  # noqa: PLC0415
    import test_spec  # noqa: PLC0415
    import test_structs  # noqa: PLC0415
    import test_stdlib  # noqa: PLC0415
    import test_study  # noqa: PLC0415
    import test_values  # noqa: PLC0415
    import test_vec  # noqa: PLC0415
    import test_text  # noqa: PLC0415
    import test_moves  # noqa: PLC0415
    import test_elements  # noqa: PLC0415
    import test_storage  # noqa: PLC0415
    import test_self_test  # noqa: PLC0415
    import test_tables  # noqa: PLC0415
    import test_io  # noqa: PLC0415
    import test_fs  # noqa: PLC0415
    import test_args  # noqa: PLC0415
    import test_collections  # noqa: PLC0415
    import test_time  # noqa: PLC0415
    import test_manifest  # noqa: PLC0415
    import test_lockfile  # noqa: PLC0415
    import test_packages  # noqa: PLC0415
    import test_reproducible  # noqa: PLC0415
    import test_native  # noqa: PLC0415
    import test_cgen_ir  # noqa: PLC0415

    found: dict[str, set[str]] = {}
    for module in (test_ab, test_arrays, test_contracts, test_control, test_defers, test_enums,
                   test_files,
                   test_float, test_functions, test_generics, test_grammar, test_guides,
                   test_local_model_server,
                   test_elements, test_moves, test_modules, test_nava, test_ownership,
                   test_plan,
                   test_io, test_fs, test_args, test_collections, test_time,
                   test_manifest, test_lockfile, test_packages, test_reproducible,
                   test_registry, test_release, test_results, test_self_test, test_sandbox,
                   test_slices,
                   test_spec, test_storage, test_structs, test_stdlib, test_study,
                   test_tables, test_text, test_values, test_vec, test_native,
                   test_cgen_ir):
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
    import test_contracts  # noqa: PLC0415
    import test_control  # noqa: PLC0415
    import test_files  # noqa: PLC0415
    import test_float  # noqa: PLC0415
    import test_functions  # noqa: PLC0415
    import test_generics  # noqa: PLC0415
    import test_modules  # noqa: PLC0415
    import test_ownership  # noqa: PLC0415
    import test_stdlib  # noqa: PLC0415
    import test_values  # noqa: PLC0415
    import test_vec  # noqa: PLC0415
    import test_text  # noqa: PLC0415
    import test_moves  # noqa: PLC0415
    import test_elements  # noqa: PLC0415
    import test_storage  # noqa: PLC0415
    import test_self_test  # noqa: PLC0415
    import test_tables  # noqa: PLC0415
    import test_io  # noqa: PLC0415
    import test_fs  # noqa: PLC0415
    import test_args  # noqa: PLC0415
    import test_collections  # noqa: PLC0415
    import test_time  # noqa: PLC0415
    import test_manifest  # noqa: PLC0415
    import test_lockfile  # noqa: PLC0415
    import test_packages  # noqa: PLC0415
    import test_reproducible  # noqa: PLC0415
    import test_native  # noqa: PLC0415
    import test_cgen_ir  # noqa: PLC0415
    for module, class_name in ((test_cgen_ir, "CFromIRTests"),
                               (test_native, "NativeDifferentialTests"),
                               (test_native, "NativeArrayTests"),
                               (test_native, "NativeStructTests"),
                               (test_native, "NativeFloatTests"),
                               (test_native, "NativeTextTests"),
                               (test_native, "NativeInputTests"),
                               (test_native, "NativeInputTestsOrdering"),
                               (test_native, "NativeEnumTests"),
                               (test_native, "NativeReturnPathTests"),
                               (test_native, "NativeRuntimeTests"),
                               (test_native, "NativeUnitTests"),
                               (test_native, "NativeLinkerTests"),
                               (test_native, "NativeModuleTests"),
                               (test_native, "NativeReaderTests"),
                               (test_native, "NativeTraceTests"),
                               (test_reproducible, "ReproduceTests"),
                               (test_reproducible, "DifferenceTests"),
                               (test_reproducible, "CompareTests"),
                               (test_packages, "PackageModuleTests"),
                               (test_packages, "BoundaryTests"),
                               (test_packages, "NetworkTests"),
                               (test_lockfile, "DigestTests"),
                               (test_lockfile, "ResolutionTests"),
                               (test_lockfile, "LockfileFileTests"),
                               (test_lockfile, "CheckTests"),
                               (test_lockfile, "GraphTests"),
                               (test_lockfile, "LockCommandTests"),
                               (test_manifest, "ParsingTests"),
                               (test_manifest, "NameAndVersionTests"),
                               (test_manifest, "ProjectTests"),
                               (test_manifest, "ProjectCommandTests"),
                               (test_time, "WallClockTests"),
                               (test_time, "MonotonicClockTests"),
                               (test_time, "ArithmeticTests"),
                               (test_time, "GuardTests"),
                               (test_collections, "ReadingTests"),
                               (test_collections, "FillingTests"),
                               (test_collections, "OrderTests"),
                               (test_args, "ArgumentTests"),
                               (test_args, "FlagAndValueTests"),
                               (test_args, "CommandLineTests"),
                               (test_fs, "ExistenceTests"),
                               (test_fs, "SizeTests"),
                               (test_fs, "ListingTests"),
                               (test_fs, "PathTextTests"),
                               (test_fs, "IsolationTests"),
                               (test_io, "RoundTripTests"),
                               (test_io, "CapabilityTests"),
                               (test_io, "FailureTests"),
                               (test_self_test, "SelfTestRunTests"),
                               (test_self_test, "SelfTestNegativeControlTests"),
                               (test_contracts, "ContractGuardTests"),
                               (test_contracts, "ContractNegativeControlTests"),
                               (test_arrays, "ArrayRuntimeTests"),
                               (test_arrays, "ArrayMemoryTests"),
                               (test_control, "ForRuntimeTests"),
                               (test_float, "FloatRuntimeTests"),
                               (test_modules, "ModuleRuntimeTests"),
                               (test_stdlib, "MathTests"),
                               (test_stdlib, "SortTests"),
                               (test_stdlib, "TextTests"),
                               (test_functions, "FunctionValueRuntimeTests"),
                               (test_values, "ValueRuntimeTests"),
                               (test_ownership, "OwningFieldRuntimeTests"),
                               (test_vec, "VectorRuntimeTests"),
                               (test_text, "TextRuntimeTests"),
                               (test_text, "ArgumentOnceTests"),
                               (test_moves, "MoveRuntimeTests"),
                               (test_moves, "ArenaRegionTests"),
                               (test_storage, "StorageRuntimeTests"),
                               (test_elements, "ElementRuntimeTests"),
                               (test_tables, "TableRuntimeTests"),
                               (test_generics, "GenericRuntimeTests"),
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


def isolation_state() -> dict:
    """Which isolation backend this host can actually run programs under.

    The registry is the project's "nothing goes missing quietly" mechanism, so
    it reports this instead of leaving it implicit: a run under `rlimits` tests
    the documented gap, not the jail, and a reader of the log must be able to
    tell which one happened.
    """
    from nava_lang import sandbox  # noqa: PLC0415
    available = sandbox.namespaces_available()
    return {
        "backend": "namespaces" if available else "rlimits",
        "namespaces_available": available,
        "reason": None if available else sandbox.namespaces_probe()["reason"],
    }


def verify(require_namespaces: bool = False) -> list[str]:
    """Return every problem found; an empty list means the gate passes."""
    problems: list[str] = []
    state = isolation_state()
    if require_namespaces and not state["namespaces_available"]:
        problems.append(
            "the namespace backend is required but unavailable on this host: "
            f"{state['reason']} (the jail tests would silently assert the refusal path "
            "instead of testing isolation; run where unprivileged user namespaces are "
            "permitted, e.g. `sudo sysctl -w "
            "kernel.apparmor_restrict_unprivileged_userns=0`)"
        )
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


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Suite and fixture registry guard.",
        epilog="By default a host without user namespaces is reported, not failed: the "
               "suite asserts the documented refusal there. --require-namespaces turns "
               "that into a failure, for a CI lane that claims to test the jail.",
    )
    parser.add_argument("--require-namespaces", action="store_true",
                        help="fail unless the namespace backend can run here")
    args = parser.parse_args(argv)
    problems = verify(require_namespaces=args.require_namespaces)
    for problem in problems:
        print(f"registry: {problem}", file=sys.stderr)
    if problems:
        return 1
    tests = sum(len(methods) for methods in REGISTERED_TESTS.values())
    cases = sum(REGISTERED_TASKS.values())
    fixtures = sum(REGISTERED_FIXTURE_DIRS.values()) + REGISTERED_MODULE_CASES
    state = isolation_state()
    isolation = f"isolation {state['backend']}"
    if not state["namespaces_available"]:
        isolation += f" (no namespaces: {state['reason']})"
    print(
        f"OK: {tests} registered tests, {len(REGISTERED_TASKS)} tasks with {cases} cases, "
        f"{fixtures} spec fixtures, {len(CODES)} diagnostic codes, "
        f"tools {', '.join(REQUIRED_TOOLS)} present, languages {', '.join(REGISTERED_LANGUAGES)}, "
        f"{isolation}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
