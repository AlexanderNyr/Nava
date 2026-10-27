# Changelog

## Unreleased

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
