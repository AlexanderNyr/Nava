# Changelog

## Unreleased

Phase N9 of `PLAN.md` (packaging, CI and release readiness).

- The project is now licensed under **Apache-2.0** (`LICENSE`, `NOTICE`,
  `license = "Apache-2.0"` plus `license-files` in `pyproject.toml`); the wheel
  ships the license text. The copyright line names "the Nava project
  contributors" because the repository records no legal entity.
- `pyproject.toml` declares the `nava` console script, no runtime dependencies
  and the Nava standard library as package data, so `pip install` yields a
  compiler that can `import math` from any directory.
- `tools/verify_release.py`: version agreement across `pyproject.toml`,
  `nava_lang/__init__.py` and `CHANGELOG.md`, the declared Python 3.10 syntax
  floor, wheel and sdist contents (standard library, console script,
  `License-Expression`, license file, no tests or benchmark records in the
  wheel), a credential scan of the tree and of both archives, and the SHA-256 of
  every artifact.
- `RELEASE.md`: versioning policy, requirements, a support matrix that
  distinguishes *exercised* (Linux x86_64, Python 3.10–3.13, gcc and clang)
  from *expected, not exercised* (macOS, Windows), a seven-step release
  checklist and an explicit list of what a release does not claim.
- CI: the required lane now covers Python 3.11 as well, runs the release gate
  and its tests, and a new `release` lane builds the artifacts, rejects a wheel
  whose standard library was stripped, then installs the wheel into a fresh
  environment and uses it from outside the checkout.
- Tests: `tests/test_release.py` (25). Suite 356 → 381 tests.

Phase N8 of `PLAN.md` (native and compiler performance). Nothing here weakens a
checked operation, and the costs are published next to the gains.

- `bench/profile.py` (`nava-profile/1`) measures a build by phase —
  `python_startup`, `import_and_cli`, `lex`/`parse`/`check`/`emit`,
  `frontend_total`, `cli_emit_c`, `cc`, `binary_startup`, `run` — on five
  workloads. `bench/compare.py` (`nava-comparison/1`) classifies two records
  with a stated rule: a phase is `improved` or `regressed` only when the median
  difference exceeds twice the larger standard deviation and 5% of the
  baseline; everything else is `inconclusive`, sizes are compared exactly and a
  missing phase is `missing`, never zero.
- `nava_lang/runtime.py`: the runtime is split into units and only the
  reachable closure is emitted, so the loop workload's translation unit drops
  from 23442 to 4909 bytes (−79%). `NAVA_FULL_RUNTIME=1` restores the old
  output for comparison. The whole selection is a pure function of the emitted
  body, and `tests/test_runtime.py` checks that the closure is closed.
- The command-line start-up path no longer imports what it does not use:
  `nava_lang/records.py` replaces `dataclasses` for the AST nodes and
  `subprocess`, `tempfile`, `shutil` and `nava_lang.sandbox` are imported
  inside the commands that need them. `nava --version` went from 74.4 to 49.0 ms
  and `nava emit-c` from 78.0 to 50.0 ms on the measured records.
- `tests/test_differential.py`: randomised differential gate — generated
  programs are compared, value by value and error by error, against a model of
  the documented semantics (overflow, division and remainder by zero,
  `INT64_MIN / -1`, truncation, evaluation order, short-circuiting, bounds,
  loop-scoped array release), each arithmetic case compiled twice (builtin and
  `-DNAVA_FORCE_PORTABLE`).
- `bench/negative_controls.py`: twelve deliberately reintroduced defects, each
  required to make its gate fail; 12/12 caught, every file restored and verified
  by SHA-256.
- Tests: `tests/test_runtime.py` (16), `tests/test_records.py` (13),
  `tests/test_bench_compare.py` (22), `tests/test_differential.py` (19).
  Suite 286 → 356 tests.

Phase N7 of `PLAN.md` (model-facing grammar and constrained generation). No
model was called: the accuracy comparison is `not-measured` and the claim
stays open.

- `guides/nava-1.md`: a compact, versioned model guide (`nava-guide/1`) pinned
  by `guides/manifest.json` (version, specification version, SHA-256, bytes,
  characters, lines). Eight canonical programs live in `guides/examples/` with
  their output recorded in `guides/examples.json`; `tests/test_guides.py`
  compiles and runs each one, and checks that the guide names every keyword,
  builtin and standard-library function.
- `eval/tokens.py` counts tokens only with a real tokenizer and reports
  `not-measured` with a reason otherwise — no estimate is ever substituted.
  `eval/overhead.py` measures the complete prompt per language (guide, task,
  instruction, total) and the difference between languages; two measured
  records are in `eval/measurements/` (`cl100k_base`, `o200k_base`): the Nava
  prompt carries 1374 more tokens than the C prompt for the same task.
- `eval/grammar.py` describes the language once and drives two back ends: the
  compiler's own token stream (conformance corpus) and GBNF text for an
  inference engine. `eval/gbnf.py` parses the emitted GBNF back and recognises
  text with it, so the artefact is executed by the tests instead of trusted.
- `eval/constrained.py`: an optional adapter that builds the grammar fields for
  `llamacpp` and `vllm`, marked `experimental` with `verified_against: null`.
  `eval/ab.py` runs the same tasks in two arms whose prompts are identical and
  whose request payloads differ only in that field, and
  `eval/check_ab_record.py` refuses a claim attached to an unmeasured record.
- `eval/feedback.py`: structured repair feedback (`nava-feedback/1`) with the
  stage kept apart (`parse`, `compile`, `tests`), capped at 2048 bytes, dropped
  items counted. `eval/study.py` gains `--feedback structured|text`,
  `syntax_accepted` per round and repair accounting that cannot turn a repaired
  attempt into a first-attempt success.
- Tests: `tests/test_guides.py` (25), `tests/test_grammar.py` (24),
  `tests/test_ab.py` (17). Suite 219 → 286 tests.

Phase N6 of `PLAN.md` (modules and standard-library foundations).

- Modules: `import name` loads `name.nv`, and its functions are called as
  `name::function`. Resolution has exactly two locations — the project root
  (the entry file's directory, or `--root DIR`) and the packaged standard
  library — with no search-path list. A local module may not shadow a standard
  one (`E0360`), a module may not define `main` (`E0357`), an import may not be
  repeated (`E0355`), the graph must be acyclic (`E0353`) and the resolved path
  must stay inside the root after `realpath`, so a symbolic link cannot escape
  (`E0356`). Calling a function of another module without the prefix is `E0358`,
  prefixing a module that was not imported is `E0359`, and an unused import is
  `W0002`. Every module is merged into one `Program` and one C translation unit.
- Versioned standard library in Nava source, installed as package data:
  `math` (`abs`, `min`, `max`, `sign`, `gcd`, `sum`), `sort` (`is_sorted`,
  `insertion_sort`, `binary_search`) and `text` (`is_ascii`, `is_valid_utf8`,
  `code_points`, `sequence_width`, `is_continuation`). Each declares
  `module_version`, and the compiler refuses a copy it was not built against
  (`E0361`).
- Text: a `str` is a NUL-terminated byte sequence. `bytes_len` and `byte_at`
  count and read bytes, `chars_len` counts Unicode code points and stops on
  bytes that are not valid UTF-8, and `read_file` returns bytes unchanged
  instead of replacing them. Source literals are still validated as UTF-8
  (`E0111`, `E0104`, `E0106`, `E0105`).
- Capability-limited file API: `read_file(path)` and `write_file(path, text)`
  are compiled with the grants given at build time
  (`--allow-read DIR`, `--allow-write DIR`) and have none by default. Both
  resolve the argument with `realpath` and accept it only inside a granted
  subtree, so `..`, absolute paths and symbolic links are all covered. Reads
  are bounded at 1048576 bytes per file, 4194304 bytes per run and 64 calls;
  a file containing a NUL byte is refused. `run --workdir DIR` makes the
  sandbox's single writable directory explicit, and the boundary is tested
  under `--isolation required`.
- Command line: `--root`, `--allow-read`, `--allow-write` on `check`,
  `emit-c`, `build` and `run`; `--workdir` on `run`; `E0506`/`E0507` for a
  capability or working directory that is missing or not a directory.
- Diagnostics: `E0352`–`E0361`, `E0506`, `E0507` and `W0002` (13 new codes,
  92 in total).
- Tests: `tests/test_modules.py` (22), `tests/test_stdlib.py` (11),
  `tests/test_files.py` (18) and `tests/fixtures/modules.json` (8 multi-file
  cases: 6 error, 1 warning, 1 run). Suite 168 → 219 tests; fixtures 103 → 112.
- `LANGUAGE.md`: "Modules and the standard library", "Text, bytes and UTF-8",
  "Files and capabilities", the grammar (`import`, `::`), the diagnostic tables
  and the three new limits.


Phase N5 of `PLAN.md` (control flow and numerical coverage).

- `for i in a..b` iterates a half-open `i64` range; `for x in values` iterates
  an array (variable, parameter or literal) by value. Both bounds are evaluated
  once. `break` and `continue` work in `for` and `while`, and release the arrays
  owned by the iteration they leave. The loop variable is a fresh read-only
  local (`E0351`), and a statement after a jump is reported as `W0001`.
- `f64` as IEEE-754 binary64: literals (`1.5`, `1e300`), `+ - * /`, unary `-`,
  the full comparison set with the NaN rules, `to_f64`/`to_i64` with checked
  conversions and no undefined behaviour, `read_f64`, and `print` support.
  There is no implicit `i64`/`f64` conversion (`E0345`, `E0311`), `%` is not
  defined for floats, and `f64` arrays do not exist.
- Documented float printing: the shortest form that round-trips, fixed notation
  for decimal exponents in [-4, 15] and exponent notation outside it, `.0` for
  integral values, and `nan`/`inf`/`-inf`. It matches Python's `repr`, which is
  how the tests check 113 values, including subnormals and `DBL_MAX`.
- Locale: the program never calls `setlocale`, so `.` is always the decimal
  separator; `read_f64` validates tokens itself and rejects `inf`, `nan`,
  hexadecimal floats, commas and trailing garbage.
- Diagnostics: `E0343`–`E0351` (9 new codes, 79 in total).
- `LANGUAGE.md`: sections for `for`/`break`/`continue` and for floating-point
  numbers, grammar, limits (`MAX_FLOAT_DIGITS`), the new builtins and updated
  diagnostic tables. 8 new executable documentation examples.
- Tests: `tests/test_control.py` (19) and `tests/test_float.py` (17), as well
  as `tests/spec_example.py`, which checks that a documented example really
  prints what `LANGUAGE.md` claims. Fixtures: 16 negative, 3 positive and 1
  warning added. Public task `mean_abs`. Tests: 132 -> 168.

Phase N4 of `PLAN.md` (arrays and ownership).

- Arrays: `[i64]`, `[bool]` and `[str]`, created by an array literal or
  `array_zeros(n)`, read with `len(a)`, `a[i]` and written with `a[i] = value`.
  The length is fixed at creation; there is no resizing, copying or nested
  array.
- Ownership: one owner per array, no copies (`E0331`, `E0332`), no array
  results (`E0330`), read-only `[T]` and mutable `mut [T]` parameters that
  cannot outlive the call (`E0333`, `E0336`, `E0342`), and statement-scoped
  temporaries for literals used as arguments.
- Checked runtime: allocation length (negative, above `MAX_ARRAY_ELEMENTS`,
  or unallocatable) and every index are checked, with deterministic
  `nava runtime error:` messages and exit status 1. Release is emitted by the
  compiler: exactly one `free` per binding on the normal path and before every
  `return`. A runtime error releases by process exit, which `SECURITY.md`
  states and a test asserts.
- Diagnostics: `E0112`, `E0113` and `E0328`–`E0342` (17 new codes, 70 in
  total), each with a fixture, a `LANGUAGE.md` table row and a title.
- `LANGUAGE.md`: specification sections for arrays, ownership, storage release
  and the new builtins; grammar, limits (`MAX_ARRAY_ELEMENTS`) and the
  "not implemented" list updated. 13 new executable documentation examples.
- Tests: `tests/test_arrays.py` (27 tests: type rules, runtime behaviour
  against independent references, release accounting with
  `-DNAVA_TRACK_ALLOCS`, sanitizer runs, a deliberately disabled bounds check),
  plus array fixtures (19 negative, 4 positive) and the `sort_numbers` and
  `sieve_count` public tasks with reference answers. Tests: 104 -> 132.
- `tests/registry.py` also fails when the address/undefined-behaviour
  sanitizers are unavailable, so the memory-safety gate cannot pass by being
  skipped.

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
