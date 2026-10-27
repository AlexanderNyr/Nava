# Changelog

## Unreleased

Phase N1 of `PLAN.md` (language specification and diagnostics).

- Stable diagnostic codes: 51 registered codes (`E01xx` lexer, `E02xx` parser,
  `E03xx` semantics, `E04xx` limits, `E05xx` command line, `W00xx` warnings) in
  `nava_lang/diagnostics.py`. An existing code never changes meaning; building
  an unregistered code raises instead of silently printing it.
- Source ranges: `NavaError` and `Diagnostic` carry a start location and an
  exclusive end. Identifier diagnostics point at the identifier, not at the
  statement keywords (`let`, `fn`).
- `--format json`: one JSON document on stdout with `status`, `path` and
  `diagnostics[]` entries containing `code`, `severity`, `title`, `message`,
  `path`, `range` and `hint`. Human output is unchanged apart from the
  `severity[CODE]` prefix and an optional `hint:` line.
- New `W0001` warning for unreachable statements, reported on stderr and in the
  JSON document; the program still compiles and runs.
- `nava_lang/limits.py`: `MAX_NESTING = 200`, `MAX_LITERAL_DIGITS = 19`,
  `I64_MIN`/`I64_MAX` as the single source of truth, enforced by the parser and
  the checker. Pathologically nested input, unary chains and long
  left-associative expression trees now raise `E0401` instead of a Python
  `RecursionError`; the CLI converts any escaped `RecursionError` into `E0402`.
- `LANGUAGE.md` rewritten as specification version 1: EBNF grammar, lexical
  rules, evaluation order, checked arithmetic, exit statuses, warnings,
  documented limits and the complete code table. Its `nava` examples are
  compiled by the test suite.
- `tests/test_spec.py` (23 tests) and `tests/fixtures/`: 47 negative fixtures
  asserting code, line, column and message; 12 positive fixtures asserting
  stdout and exit status; one warning fixture; fuzzing over random token soup
  and mutations of valid programs; documentation/limit/exit-status conformance.
- `tests/registry.py` extended with fixture counts, diagnostic-code coverage
  (every code needs a fixture, a documented test and a `LANGUAGE.md` entry) and
  the new test classes. Tests: 37 -> 62.

## N0 (phase N0)

Phase N0 of `PLAN.md` (measurement rig and test integrity), plus the packaging
parts of N9 that the N0 gate depends on.

- `pyproject.toml`: installable package `nava-lang` with the `nava` console
  script; no third-party runtime dependencies. `nava` is now executable.
- `tests/registry.py`: registry guard for test methods, task fixtures and
  required compilers. A missing test, a changed case count or a host without
  `cc`/`c++` fails the guard; skipped runtime tests are no longer a pass.
- `eval/reference.py`: independent reference implementations for all eight
  public tasks, with `--check` validating every fixture in `tasks.json`.
- `eval/evaluate.py`: C candidate support alongside Nava and C++; reports now
  record `candidate.language`, `candidate.compile_command` and a labelled
  `candidate.arithmetic` (checked-i64 versus unchecked-signed).
- `eval/tasks.json`: four additional public tasks (`is_prime`, `digit_sum`,
  `max_of_three`, `collatz_steps`), each with six cases checked against the
  reference; `eval/solutions/gcd.c` added.
- `bench/run.py`: separated phases (Nava front end, native compilation,
  zero-iteration startup, main runs), warm-up policy, rotated run order,
  standard deviation and a `--json PATH` machine-readable record
  (`nava-bench/1`) with environment, compiler versions and flags. Missing
  model measurements are recorded as `null` with status `not-measured`.
- `bench/results/`: first recorded benchmark run (Linux x86-64, GCC 14.2.0).
- `.github/workflows/ci.yml`: required lane (Python 3.10/3.12/3.13 x GCC/Clang)
  running the registry guard, the full suite, the cross-language check and the
  fixture reference; a lane proving the guard fails without compilers; a
  packaging lane; a benchmark-record lane.
- Tests: 31 -> 37, including the C candidate, the four new tasks, the
  benchmark record schema and four registry-guard tests.
