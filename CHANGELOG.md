# Changelog

## Unreleased

Phase N3 of `PLAN.md` (real model evaluation). **No model was run**: this
release adds the harness and the honesty rules, not a result.

- `eval/study.py`: paired study over tasks × languages × attempts. Per attempt
  it records the prompt hash (text only with `--store-raw`), extraction
  provenance, compilation, failing tests, first-attempt versus final success,
  every repair round with its diagnostics, time to a correct answer, wall time,
  time to first token, provider-reported tokens and the isolation backend.
  Aggregates carry percentile bootstrap intervals with a fixed seed.
- `eval/providers.py`: `openai-compatible` (key from an environment variable,
  redacted from errors, never recorded), `replay` (JSONL keyed by prompt
  SHA-256) and `mock` (deterministic, measures nothing).
- Self-classification: a study is `exploratory` below 10 tasks or 30 attempts
  per task and language, with the reasons printed; `empirical` is refused
  otherwise. `model_measurements.status` stays `not-measured` unless a model
  was actually called and every attempt produced a compile-and-test result.
- `--plan-only` prints the predeclared plan (tasks, order policy, request
  parameters, expected scale) without calling anything.
- `eval/prompt.py` refactored into `build_parts`/`build_prompt`: the guide, the
  task and the output instruction are counted separately, so guide overhead is
  recorded rather than assumed.
- `eval/extraction.py`: fenced-block extraction with recorded provenance
  (`tagged-fence`, `untagged-fence`, `whole-text`, `whole-text-fallback`); a
  foreign fence is never silently rewritten.
- `eval/check_study_record.py`: re-derives the claim from a saved record -- the
  schema, the thresholds behind `empirical`, `measured` only for a non-mock run
  with evaluated attempts and no provider errors, a reason for `not-measured`,
  and aggregates that match the attempt list. CI runs it on a mock record and on
  a deliberately unreachable endpoint, so "it ran" can never stand in for
  "it measured".
- `tests/test_study.py` (27 tests): classification thresholds, aggregates with
  missing values, bootstrap determinism, provider error handling, credential
  redaction, replay hits and misses, extraction cases, end-to-end mock study,
  repair accounting, raw storage, and five record-validation rejections.
- `tests/registry.py` tracks the new classes. Tests: 77 -> 104.

## N2 (phase N2)

Phase N2 of `PLAN.md` (isolated build and execution).

- `nava_lang/sandbox.py`: two backends and three policies. `namespaces` uses
  `unshare` for user, mount, PID, network, IPC and UTS namespaces with a
  read-only minimal root (read-only `/usr`, a few `/etc` files, four `/dev`
  nodes, a private 16 MiB `/tmp`, one writable directory at `/work`) and a
  fresh `/proc`. `rlimits` provides process-group cleanup, `RLIMIT_CPU`,
  `RLIMIT_AS`, `RLIMIT_FSIZE`, `RLIMIT_NOFILE`, `RLIMIT_NPROC`, `RLIMIT_CORE`,
  a wall-clock deadline and bounded output capture, and states in every report
  that the filesystem, the network and the process tree are **not** isolated.
- Fail-closed policy: `--isolation {auto,required,off}` on `nava run` and
  `eval/evaluate.py`. `required` refuses to run when namespaces are unavailable
  (exit 2, new diagnostic `E0601`); `auto` warns and labels the gap; `off` is an
  explicit opt-in to the unsafe mode. Backend detection is a real `unshare`
  probe, not a version check.
- Bounded output collection replaces unbounded `capture_output` for untrusted
  candidates: each stream is capped, a program that exceeds the cap is killed,
  and truncation is recorded per test case.
- No surviving children: the whole process group is killed on timeout or
  overflow, then re-checked; a survivor is reported and `nava run` fails with
  `E0602`.
- `nava run` gained `--isolation` and `--memory`; its default is `auto`, so the
  best available isolation is used, with the program's own exit status, `124`
  on timeout and 1 for runtime errors preserved.
- `eval/evaluate.py`: compilation and execution both go through the sandbox,
  compiler paths are resolved to real absolute paths (inside the jail
  `PATH`/`/etc/alternatives` chains do not resolve), and each report records
  `isolation.backend`, `network_denied`, `host_files_hidden`, `gaps` and the
  limits in force.
- `SECURITY.md`: threat model, per-platform support table, what is deliberately
  not protected, and the probe-to-expectation map used by the gate.
- `tests/test_sandbox.py` (15 tests): busy loop, sleeping program, output flood,
  memory exhaustion, `fork`+`sleep` child, writes outside the working directory,
  connection to a local listener, fail-closed `required` in the library and in
  the evaluator, honest labels and CLI integration. Tests: 62 -> 77.
- `tests/registry.py` tracks the new test class; `E0601`/`E0602` are registered
  and documented with the tests that cover them.

## N1 (phase N1)

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
