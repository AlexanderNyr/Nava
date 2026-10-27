# Nava Development Plan

## Status: N0–N2 DELIVERED; N3 infrastructure delivered, empirical result open; N4–N9 planned

Nava 0.1 is a working, small compiler. It does **not** yet establish that a
language model generates Nava faster or more accurately than C or C++, or
that Nava binaries are generally faster. This plan addresses that gap while
expanding the language without silently changing its existing semantics.

This document answers one question:

> How can Nava become a useful language for model-generated programs, with
> correctness and end-to-end latency measured rather than inferred from short
> source files?

Phases N0 (measurement rig and test integrity), N1 (language specification and
diagnostics) and N2 (isolated build and execution) are delivered, and N3's
harness is delivered — **without** a model result, which cannot be produced
without a model to run: see the
delivery records in the relevant subsections for the evidence, the commands that
produce it and the items that are deliberately left open. N3 is the next
dependency; its infrastructure exists, its empirical results do not.

The phases are dependency-ordered. Each has an objective, concrete tasks, a
test gate and a deliverable. A phase is **done** only when its gate has run
and its evidence is recorded; a checkbox or a benchmark script that has not
run is not evidence.

---

## 1. Where things actually stand

**Baseline:** the local Nava 0.1.0 tree, checked on October 27, 2026. This
workspace has no Git commit identifier; the version and commands below
identify the baseline, not a claim about a published release.

| Area | Present in the tree | Limit or missing evidence |
|---|---|---|
| Compiler | Lexer, parser, type checker and C11 generator in `nava_lang/`; `check`, `emit-c`, `build` and `run` commands; stable diagnostic codes with ranges, hints and `--format json` | Python front end and external C compiler are required to build |
| Language | `i64`, `bool`, `str`, functions, conditions, `while`, checked arithmetic and integer I/O; `LANGUAGE.md` is specification version 1 with EBNF, documented limits and executable examples | No arrays, `for`, floating point, imports, file API or package system |
| Correctness | `python3 -m unittest discover -s tests -v`: 62 tests passed locally with `cc` and `c++`; `tests/registry.py` fails when a test, fixture, diagnostic code or required compiler is missing; 47 negative and 12 positive fixtures assert exact codes, locations and output | Hidden tests are not in this repository by design; no coverage measurement |
| Cross-language check | `python3 experiment/verify.py`: Nava and C passed 107 identical GCD cases | One algorithm, not a general language benchmark |
| Native performance | `bench/run.py` compares Nava with checked and unchecked C++ on one loop and writes a `nava-bench/1` JSON record with environment, flags, warm-up, raw samples per phase and `null` model fields | One workload; checked C++ has similar arithmetic checks, not identical input/error behaviour |
| Model evaluation | `eval/study.py`: paired study over tasks × languages × attempts with an OpenAI-compatible, replay or mock provider; repairs; first-attempt versus final success; provider-reported tokens; time to first token and to a correct answer; percentile bootstrap intervals; self-classification as exploratory or empirical. `eval/evaluate.py` runs one saved answer; eight public tasks with 49 cases; independent answers in `eval/reference.py` | **No model has been run**: every record says `not-measured`; no held-out task set is included (it must stay outside the repository); the public set has 8 tasks, below the 10 needed for an empirical label |
| Execution safety | `nava_lang/sandbox.py`: Linux namespaces (user, mount, PID, network, IPC, UTS) with a read-only minimal root and one writable directory, or a labelled rlimits-only fallback; `--isolation {auto,required,off}`; bounded output; process-group kill with survivor detection | Confinement against a misbehaving program, not against a kernel exploit; no seccomp, no cgroup quota; `rlimits` mode isolates nothing but resource use |

The separate Nava-versus-C authoring experiment in `experiment/README.md`
measured tool-mediated file writes once. It did **not** measure pure model
response time or model token usage and must not be used as a model-speed claim.

---

## 2. Decisions

### D1. Measure time to a correct answer, not characters

Source size, output tokens, total input tokens, time to first token, total
response time, compilation, retries, correctness and native runtime are
separate measurements. Fewer characters do not necessarily mean fewer tokens,
and tokens per second do not equal time to a correct result.

### D2. Keep the comparison equivalent and explicit

Nava, C and C++ receive the same task and input/output contract. Compare
checked arithmetic with checked arithmetic where possible; keep an unchecked
C/C++ baseline separately labelled. Record the full prompts, including Nava's
grammar guide, and the actual compiler flags. Do not hide repairs or retries.

### D3. Correctness and isolation come before automation

Today `eval/evaluate.py` executes arbitrary native code and uses captured
output without a size limit. A timeout is not isolation. Automated model
runs must not execute untrusted responses until a supported sandbox enforces
filesystem, network and resource limits. An unavailable sandbox must not be
silently reported as protection.

### D4. Keep the core explicit and predictable

Document grammar, types, ownership, errors, overflow and evaluation order
before adding features. Model-facing compact forms must remain unambiguous;
no syntax is assumed token-efficient until measured with the actual model's
tokenizer.

### D5. Optimizations need a before, an after and an unchanged contract

Every optimization must retain existing observable semantics and carry a
reproducible correctness gate. Report the machine, compiler, flags, workload,
raw samples and spread. A noisy one-off win or a byte count is not an
optimization receipt.

### D6. Missing evidence stays missing

There is no default model for N3 or N7. If a provider, usage counter, isolated
runner or hidden test set is unavailable, report that fact and leave the
corresponding result unmeasured. Do not manufacture a number or mark a phase
complete by skipping its gate.

---

## 3. Phase summary

| Phase | Subject | Dependencies | Gate criterion | State |
|---|---|---|---|---|
| N0 | Measurement rig and test integrity | None | Baseline is reproducible; missing or disabled tests fail the guard | **Delivered** |
| N1 | Language specification and diagnostics | N0 | Grammar and diagnostics match the implementation, including negative cases | **Delivered** |
| N2 | Isolated build and execution | N0 | Hostile programs cannot escape the documented limits; no silent fallback | **Delivered** |
| N3 | Real model evaluation | N0, N1, N2 | Paired Nava/C/C++ results include full prompts, correctness and measured model usage | **Harness delivered, result open** |
| N4 | Arrays and ownership | N1, N2 | Bounds, allocation and lifetime failures are checked end to end | Planned |
| N5 | Control flow and numerical coverage | N1, N4 | New constructs have specified semantics and differential tests | Planned |
| N6 | Modules and standard-library foundations | N2, N4, N5 | Multi-file programs work; capability and import failures are tested | Planned |
| N7 | Model-facing grammar and constrained generation | N1, N3 | Model-specific A/B results include guidance overhead and correctness | Planned |
| N8 | Native and compiler performance | N0, N4, N5 | Correctness is unchanged and every claimed gain has before/after samples | Planned |
| N9 | Packaging, CI and release readiness | N1, N2, N3 | Clean installation and non-skipping CI pass on each declared platform | Planned |

`N3` can deliver the measurement infrastructure before a provider is chosen,
but its empirical comparison is not complete without real model results.
`N7` and `N8` depend on measurements, not on assuming a particular outcome.

---

## 4. Phase details

### Phase N0 — Measurement rig and test integrity

**Objective:** make the current baseline repeatable and make missing tests
visible before changing the language or claiming a speedup.

#### Tasks

- [x] Record Python, OS, CPU, compiler versions, compiler flags, workloads and
      raw timing samples with every benchmark result. Separate process startup,
      compilation and native execution. → `bench/run.py --json PATH` emits a
      `nava-bench/1` record with `environment` (platform, machine, CPU count,
      Python and compiler versions, flags, isolation note), `frontend_ms`,
      `compile_ms`/`compile_parts_ms`, `startup_ms`, raw `runs_ms`,
      distribution statistics and `failures`.
- [x] Extend the evaluation harness to support C as well as existing Nava and
      C++ candidates, with identical task contracts and labelled arithmetic
      semantics. → `eval/evaluate.py --language {nava,c,cpp}` records
      `candidate.arithmetic` as `checked-i64` or `unchecked-signed`;
      `eval/solutions/gcd.c` checks the lane.
- [x] Add diverse public tasks with input bounds and independent reference
      outputs. Keep any genuinely held-out tests outside prompts and outside
      the public training/evaluation fixtures. → eight tasks / 49 cases,
      bounded inputs, every expected stdout recomputed by
      `eval/reference.py`. Held-out tests stay **outside this repository**.
- [x] Add a suite/fixture registry guard so an omitted, disabled or missing
      test is visible. CI must fail if its required C or C++ compiler is absent
      rather than treating skipped runtime tests as a passing full suite. →
      `tests/registry.py` (also the `RegistryTests` class), with five negative
      controls; the CI lane `guard-fails-without-compilers` proves the
      fail-closed behaviour.
- [x] Record benchmark distributions, warm-up policy, run order and failures;
      do not replace missing model data with source-length estimates. →
      `--warmup`, `workload.run_order`, `stdev_ms`, and
      `model_measurements.status = "not-measured"` with `null` values.

#### Test gate

- Current checks pass with the required compilers present:
  `python3 -m unittest discover -s tests -v` and
  `python3 experiment/verify.py`.
- A deliberately wrong GCD result fails the evaluator; removing a registered
  test or required compiler fails the planned registry/CI guard.
- The extended benchmark emits reproducible machine-readable results with
  actual sample counts and unmeasured fields marked as such.

#### Deliverable

`bench/`, `eval/` and `tests/` baseline tools, a registry guard and a recorded
baseline. **Delivered.** See the delivery record below.

#### Delivery record (2026-10-27)

| Gate item | Evidence | Result |
|---|---|---|
| Baseline reproducible | `python3 -m unittest discover -s tests -v` | 37 tests, OK |
| Registry guard present | `python3 tests/registry.py` | `OK: 37 registered tests, 8 tasks with 49 cases, tools cc, c++ present` |
| Missing test is visible | rename a registered test method (NC1) | guard fails: `registered test is missing` |
| Corrupted fixture is visible | change one expected stdout (NC2) | guard fails: `reference gives ...` |
| Removed task is visible | delete `collatz_steps` (NC3) | guard fails: `registered task is missing` |
| Missing compiler fails closed | `PATH=` an empty directory (NC4) | guard fails: `required tools are missing: cc, c++` |
| Unsafe local mode is not a pass | NC4 plus `NAVA_ALLOW_MISSING_TOOLS=1` (NC5) | still exits 1, labelled `NOT a passing gate` |
| Cross-language check | `python3 experiment/verify.py` | Nava and C: 107/107 |
| Fixtures agree with the reference | `python3 eval/reference.py --check` | 8 tasks, 49 cases agree |
| Machine-readable benchmark | `python3 bench/run.py --iterations 5000000 --samples 7 --json ...` | record in `bench/results/`, 9/9 reference checks, `failures: []` |
| Wrong candidate still fails | `eval/evaluate.py` on `fn main() { print(123) }` | exit 1, `all_passed: false` |
| Clean installation | `python3 -m pip install .` in a fresh venv, then `nava --version` | `Nava 0.1.0`; `nava run` works |

Still open after N0, and deliberately not closed here:

1. **Held-out tests** must not exist in this repository (they would leak into
   prompts and fixtures). They are needed for N3 and are tracked there.
2. **Bounded output capture** for untrusted candidates (`capture_output` in
   `eval/evaluate.py` is still unbounded) belongs to N2's isolation work; a
   size limit without a sandbox would suggest protection that is not there.

The five negative controls are reproducible by hand; they are not automated in
CI because each requires mutating the tree. CI covers the same failure mode
that matters in practice — a host without compilers — in its own lane.

---

### Phase N1 — Language specification and diagnostics

**Objective:** model authors and compiler users can rely on one written
contract and get actionable errors without a compiler traceback.

#### Tasks

- [x] Write a versioned grammar and examples matching `lexer.py` and
      `parser.py`, including newlines, comments, string escapes and reserved
      names. Specify types, scoping, short-circuiting, return analysis, checked
      arithmetic and input/output behaviour. → `LANGUAGE.md` is now
      *specification version 1* with an EBNF grammar, lexical rules, evaluation
      order, checked arithmetic, exit statuses and documented limits; its
      ```nava blocks are compiled by the test suite.
- [x] Add stable error categories and source ranges; offer machine-readable
      diagnostics alongside the existing human-readable form. → 51 registered
      codes in `nava_lang/diagnostics.py`, `NavaError.diagnostic` with an
      exclusive end location, and `nava --format json` emitting one JSON
      document with `code`/`severity`/`title`/`message`/`range`/`hint`.
- [x] Bound parser and compiler recursion and report malformed or excessively
      deep input as an ordinary diagnostic rather than a Python traceback. →
      `nava_lang/limits.py` (`MAX_NESTING = 200`) enforced by the parser and the
      checker for parentheses, blocks, unary chains and expression-tree depth;
      `E0401` for the limit, `E0402` as a CLI safety net. 20 000 nested
      parentheses and 20 000-term chains now fail in well under a second.
- [x] Add negative fixtures for type errors, integer boundaries, malformed
      UTF-8/escapes, missing names and invalid function signatures. → 47
      fixtures in `tests/fixtures/negative/` with recorded code, line, column,
      message and hint; invalid UTF-8 is covered by its own test.

#### Test gate

- Every documented syntax example checks and runs where applicable; invalid
  examples fail at the expected source location with a stable error category.
- Fuzzed malformed inputs do not crash the front end or hang within the test
  limits. A deliberately broken diagnostic or accepted invalid fixture makes
  the gate fail.
- Existing evaluation order, integer boundaries, Unicode and short-circuit
  tests remain green.

#### Deliverable

An expanded `LANGUAGE.md`, compiler diagnostics in `nava_lang/` and executable
specification/negative fixtures in `tests/`. **Delivered.** See the delivery
record below.

#### Delivery record (2026-10-27)

| Gate item | Evidence | Result |
|---|---|---|
| Documented examples compile | `python3 -m unittest tests.test_spec -v` | 7 `nava` blocks in `LANGUAGE.md` type-check |
| Documentation matches the code | `test_every_registered_code_is_documented`, `test_documented_limits_match_the_implementation`, `test_documented_exit_codes_match_the_cli` | 51/51 codes documented; limits and exit statuses agree |
| Invalid input fails at the expected location with a stable code | `test_each_fixture_fails_with_the_recorded_diagnostic` | 47/47 fixtures match code, line, column, message and hint |
| Code coverage of diagnostics | `test_fixtures_cover_every_code_except_documented_exceptions` | every code has a fixture or a named test |
| No Python traceback on hostile input | `LimitTests`, `FuzzTests` | deep nesting/20 000-term chain → `E0401` < 1 s; 4 500 fuzz cases → only registered codes |
| Warnings do not break the build | `tests/fixtures/warnings.json` | `W0001` on stderr, program still builds and runs |
| Machine-readable diagnostics | `DiagnosticFormatTests` | stable JSON shape, exit 2 on error, `E0501`–`E0505` covered |
| Registry gate extended | `python3 tests/registry.py` | `OK: 62 registered tests, 8 tasks with 49 cases, 60 spec fixtures, 51 diagnostic codes` |

Seven negative controls were run by hand, each making the gate fail: changing a
code in the implementation, moving a diagnostic location, disabling the nesting
guard, deleting a code from `LANGUAGE.md`, breaking a documented example,
changing a documented limit, and deleting a fixture file.

Left open on purpose: `LANGUAGE.md` describes the language, not the compiler's
performance; `E0402`, `E0501`–`E0505` cannot be produced by a source fixture and
are covered by named tests instead.

---

### Phase N2 — Isolated build and execution

**Objective:** evaluating untrusted generated code is opt-in, bounded and
isolated; the existing timeout is not misrepresented as a sandbox.

#### Tasks

- [x] Choose and document a supported isolation mechanism. Isolate **both**
      compilation and execution of untrusted C/C++ and generated Nava
      binaries, using an unprivileged worker and a minimal filesystem view. →
      `nava_lang/sandbox.py` uses `unshare` (user, mount, PID, network, IPC,
      UTS) with a read-only minimal root built inside the namespaces;
      `eval/evaluate.py` compiles and runs every candidate through it.
      Documented in [`SECURITY.md`](SECURITY.md).
- [x] Restrict CPU time, wall time, memory, output size and process count;
      terminate the complete process group or container on failure or timeout.
      → `RLIMIT_CPU`, `RLIMIT_AS`, `RLIMIT_NPROC`, `RLIMIT_FSIZE`,
      `RLIMIT_NOFILE`, `RLIMIT_CORE`, a wall-clock deadline, per-stream byte
      caps, `killpg` followed by a survivor re-check and `E0602` if anything
      outlives it.
- [x] Deny network by default and limit readable/writable paths. Keep model
      credentials and unrelated host files outside the worker. → a private
      network namespace; the jail has no `/home`; the program sees `/work` as
      its only writable path and a minimal environment without the parent's
      variables.
- [x] Fail closed when the requested isolation backend is unavailable. If an
      explicitly unsafe local mode is retained, label it clearly and require
      an explicit opt-in. Document unsupported operating systems. →
      `--isolation required` raises `SandboxUnavailable`/`E0601` and refuses;
      `auto` warns; `off` is the explicit opt-in; `SECURITY.md` carries the
      per-platform table; detection is a real `unshare` probe.
- [x] Replace unbounded `capture_output` use for untrusted candidates with
      bounded output collection and clear failure diagnostics. →
      `_BoundedReader` caps each stream, kills on overflow, records
      `output_limit_exceeded` and truncation per test case.

#### Test gate

- Controlled test programs attempt an infinite loop, excess output, excess
  memory, a surviving child process, a write outside the allowed directory
  and a connection to a local test listener; each is stopped or denied as
  specified, while an allowed program completes.
- The same tests verify that no test child survives a timeout. Disabling an
  enforced limit makes its negative control fail.
- With the isolation backend unavailable, protected mode refuses execution
  instead of falling back silently. No claim of universal sandbox security is
  made beyond the tested platform and mechanism.

#### Deliverable

An isolated evaluator/build runner, bounded-output handling, platform and
threat-model documentation, and adversarial integration tests. **Delivered.**

#### Delivery record (2026-10-27)

| Gate item | Probe | Result |
|---|---|---|
| Allowed program completes | `test_allowed_program_completes` | exit 0, expected stdout, under both backends |
| Infinite loop | `test_infinite_loop_is_stopped` | stopped by CPU rlimit or the wall clock, < 5 s |
| Idle program (no CPU) | `test_wall_clock_limit_stops_an_idle_program` | wall-clock kill only; `cpu_limit_hit` false |
| Excess output | `test_excess_output_is_stopped` | killed, `output_limit_exceeded`, buffer capped |
| Excess memory | `test_memory_ceiling_stops_allocation` | allocation fails inside the program at `RLIMIT_AS` |
| Surviving child | `test_child_processes_do_not_survive` | no host process matches the probe afterwards |
| Write outside the allowed directory | `test_filesystem_boundary` | `namespaces`: `denied=3`, host file absent; `rlimits`: gap asserted |
| Connection to a local listener | `test_network_boundary` | `namespaces`: `DENIED`; `rlimits`: `CONNECTED`, gap asserted |
| Fail closed | `test_required_mode_refuses_without_namespaces`, `test_evaluator_refuses_required_without_namespaces` | exit 2, `E0601`, `"available": false` |
| Whole suite | `python3 -m unittest discover -s tests` | 77 tests OK |

Five negative controls were run by hand, each making the gate fail: removing
`--net`, removing the timeout kill, letting `required` fall back silently,
bind-mounting the whole host root instead of the minimal one, and disabling the
output cap. `SECURITY.md` records the probe-to-expectation map and the gaps the
tests assert rather than hide.

---

### Phase N3 — Real model evaluation

**Objective:** determine whether Nava improves time to a correct result for a
specific model, rather than inferring it from handwritten programs.

#### Tasks

- [x] Add an opt-in provider adapter for a user-selected model; retain the
      existing offline saved-response mode. Do not commit credentials, prompts
      containing private data or generated answers without explicit consent. →
      `eval/providers.py`: `openai-compatible` (key read from an environment
      variable, redacted from errors and never recorded), `replay` (JSONL keyed
      by prompt SHA-256) and `mock`. Prompts and answers are stored only with
      `--store-raw`.
- [x] Record exact model and inference settings, complete prompts, response
      and API-reported input/output token counts. Measure time to first token
      when supported and wall time to the final answer. Preserve missing values
      as missing. → `plan`, `request`, `prompt_policy`, per-round
      `prompt_sha256`/`response_sha256`, `ttft_ms`, `wall_ms`,
      `prompt_tokens`/`completion_tokens` as `null` when unreported, with
      `tokens_reported` counting how many attempts actually had a number.
- [x] Compile and test Nava, C and C++ responses inside the N2 isolation
      boundary. Record compilation, first-attempt correctness, each retry and
      the time to a correct answer separately from native execution. → every
      attempt runs `eval/evaluate.py --isolation <mode>`; rounds record
      `compile_ok`, `passed`, `tests_passed/total`, `isolation_backend`;
      `first_attempt_passed`, `final_passed`, `repaired`, `rounds_used` and
      `time_to_correct_ms` are separate fields.
- [x] Use the same task contract, a predeclared prompt policy, fixed sampling
      settings, randomized or alternated language order and a separate hidden
      test set. Count grammar and system instructions, not only source code. →
      `--plan-only` prints the predeclared plan; language order rotates per
      attempt; `--held-out FILE` adds tasks that must live outside the
      repository; the guide, system message and instruction are recorded and
      their sizes reported.
- [ ] For an empirical report, use at least 10 varied tasks and 30 paired
      independent attempts per task and language, subject to the chosen
      provider's cost limits. Report raw samples, failures, medians and a
      stated uncertainty method; report smaller studies as exploratory. →
      **the machinery is in place** (classification, bootstrap intervals, raw
      attempts), but the run itself needs a named model and a held-out set:
      **not done, and deliberately not simulated**.

#### Test gate

- Mock-provider tests exercise streaming/non-streaming responses, missing
  counters, API errors, timeouts, malformed code and retry accounting without
  network access or credentials.
- A deliberately wrong but compiling answer fails the correctness gate; a
  missing usage counter is shown as missing, not zero or an estimate.
- The empirical phase is complete only after a named model has been run and
  the prompts, settings, isolation method and reproducible aggregate results
  are recorded. If no model is supplied, the infrastructure may land but the
  speed/accuracy question remains open.

#### Deliverable

An opt-in model-evaluation runner, comparison corpus, machine-readable run
records and a model-specific report when actual measurements exist.
**Harness delivered; no model-specific report exists yet.**

#### Delivery record (2026-10-27)

| Gate item | Evidence | Result |
|---|---|---|
| Mock-provider tests, no network or credentials | `tests/test_study.py` | 27 tests: providers, extraction, aggregates, scale, repairs, raw storage |
| Streaming and non-streaming, missing counters, API errors, timeouts | `test_openai_provider_without_a_key_does_not_call_out`, `test_model_status_is_not_measured_when_every_call_failed`, `responses` with `error`/`error_class` | a failed call is an error, never an empty answer; status stays `not-measured` |
| Malformed code | `test_foreign_fence_falls_back_without_editing` | extraction reports `whole-text-fallback`; compilation fails; nothing is "fixed" silently |
| Retry accounting | `test_repairs_are_counted_separately_from_first_attempts` | first attempt stays failed, `repaired: true`, `rounds_used: 2` |
| A wrong but compiling answer fails the correctness gate | `test_end_to_end_mock_study_is_labeled_as_not_measured` | `sum_to_n` compiles (rate 1.00) and passes 0 of 6 cases |
| Missing counter stays missing | `test_missing_measurements_are_null_not_zero` | `null`, `tokens_reported: {prompt: 0, ...}` |
| Scale is not overstated | `test_thresholds_match_the_plan` and the printed summary | 8 public tasks → `exploratory`, with the reason |
| Credentials | `test_credentials_are_redacted_from_errors` | tokens removed from error text; `describe()` carries no key |
| A saved record cannot overstate itself | `eval/check_study_record.py`, `RecordValidationTests` (5 tests) | re-derives the claim from the attempts; exit 1 on any inconsistency |
| CI runs the harness without a model and without a network | `.github/workflows/ci.yml` job `study-harness` | plan-only, mock record validated as `not-measured`, unreachable endpoint → rc 1 and `not-measured` |
| Whole suite | `python3 -m unittest discover -s tests` | 104 tests OK |

Five negative controls were run by hand, each making the gate fail: forcing
`model_measurements.status` to `measured`, turning missing token counts into
`0`, counting a repaired answer as a first attempt, always labelling a study
`empirical`, and treating a provider error as an answer. Five of those shapes
are now rejected automatically by `eval/check_study_record.py`, so the controls
no longer depend on someone repeating them by hand.

What is missing is a model. Running it needs a provider, a key and a held-out
task file; without those the question in the plan's preamble — whether Nava
gives a model a shorter time to a correct answer than C or C++ — stays open.

---

### Phase N4 — Arrays and ownership

**Objective:** support collection-oriented tasks without introducing
unchecked memory access or ambiguous lifetime rules.

#### Tasks

- [ ] Specify homogeneous arrays and element types, indexing, length,
      assignment, argument passing, ownership and lifetime before choosing
      their concrete syntax. Start with a bounded, minimal design; specify
      resizing separately if added.
- [ ] Implement checked allocation sizes and bounds, deterministic errors and
      release of owned storage on normal returns and error paths. Do not expose
      raw pointers in the core language.
- [ ] Extend type checking and C emission for collection operations, including
      nested scopes, function calls and early returns.
- [ ] Add representative array programs such as sorting and a sieve, with
      independent reference answers.

#### Test gate

- Valid programs match the reference on empty, single-element, duplicate and
  boundary inputs. Negative index, out-of-range access and allocation-size
  overflow fail predictably.
- Lifetime and error-path tests pass under available address/undefined-behaviour
  sanitizers; a deliberately disabled bounds check is caught by the gate.
- All existing 0.1 programs and arithmetic/ordering tests still pass.

#### Deliverable

Specified array/ownership semantics, compiler and runtime support, examples
and memory-safety tests.

---

### Phase N5 — Control flow and numerical coverage

**Objective:** remove common algorithmic workarounds while keeping arithmetic
and control flow precisely specified.

#### Tasks

- [ ] Specify and implement `for`, `break` and `continue`, including nested
      loops, scope and interaction with return-path analysis.
- [ ] Design `f64` literals, operations, printing, comparisons, conversions,
      exceptional values and rounding behaviour explicitly; no implicit
      integer/float conversion without a documented rule.
- [ ] Expand tests for signed division, remainders, integer overflow and
      mixed control flow. Define how locale affects input and output, if at
      all.

#### Test gate

- Nested-loop and early-exit programs match independent references. A broken
  `continue` or `break` implementation is detected by a negative control.
- Integer boundary tests stay green. Floating-point tests cover the documented
  NaN/infinity, rounding, comparison and conversion rules on each supported
  compiler; any unsupported behaviour is reported rather than assumed.

#### Deliverable

Control-flow and numerical language additions, written semantics, examples and
portable regression tests.

---

### Phase N6 — Modules and standard-library foundations

**Objective:** support small multi-file programs and useful data operations
without making unrestricted host access the default.

#### Tasks

- [ ] Specify module names, import resolution, duplicate definitions, cycles
      and diagnostics; build and check multiple source files consistently.
- [ ] Add a minimal, versioned standard library for the types implemented by
      N4 and N5. Specify UTF-8 and byte/character boundaries instead of
      treating them as interchangeable.
- [ ] If file I/O is introduced, make access explicit and capability-limited;
      it must respect N2 isolation. Keep networking and external package
      resolution out of the default core.
- [ ] Document deterministic dependency resolution and reject path traversal
      or imports outside the configured project boundary.

#### Test gate

- A multi-file example builds from a clean directory; missing, duplicate,
  cyclic and escaping imports produce documented diagnostics.
- UTF-8 boundary and invalid-input cases have independent expected outputs.
  File-access tests demonstrate both an allowed path and a denied path in
  the isolated runner.

#### Deliverable

A module contract, a small standard library, multi-file examples and
resolution/capability tests. A package manager is not implied by this phase.

---

### Phase N7 — Model-facing grammar and constrained generation

**Objective:** make Nava's model-facing form easier to produce and validate,
then measure whether it helps the chosen model.

#### Tasks

- [ ] Provide a compact, versioned language guide and canonical examples tied
      to the implemented grammar. Measure the full guide and source with the
      target model's actual tokenizer where available.
- [ ] Prototype optional grammar-constrained decoding through a supported model
      interface. Distinguish syntactic validity from type correctness and
      runtime correctness; a grammar alone cannot prove the latter two.
- [ ] Compare unconstrained and constrained generation using the N3 harness,
      the same tasks/model/settings and the complete guidance overhead.
      Record latency, output tokens, compilation and first-attempt correctness.
- [ ] Add structured compiler feedback for repair experiments, keeping
      first-attempt and repaired results separate.

#### Test gate

- A grammar/implementation conformance corpus includes valid and invalid
  boundary cases; generated candidates are checked by the actual compiler.
- A deliberately malformed candidate is rejected, and repair attempts cannot
  be counted as first-attempt successes.
- A model-specific A/B report exists before claiming that constrained
  decoding or a shorter guide improves speed or accuracy. If measurement is
  unavailable, the feature may be experimental but the claim remains open.

#### Deliverable

Versioned model guides, an optional constrained-generation adapter,
conformance tests and a measured A/B report when a model is available.

---

### Phase N8 — Native and compiler performance

**Objective:** reduce measured costs without weakening checked semantics or
changing the result of existing programs.

#### Tasks

- [ ] Profile frontend, C emission, native compilation, startup and execution
      separately on the N0 workloads; prioritize costs actually observed.
- [ ] Consider constant folding, dead-helper elimination, generated-C quality
      and build reuse only where benchmarks justify them. Cache keys, if a
      cache is added, must include source, compiler identity, flags, platform
      and compiler version; untrusted cache use must follow N2's boundary.
- [ ] Preserve checked overflow, division errors, left-to-right side effects,
      short-circuiting and defined collection lifetimes. Do not introduce C
      undefined behaviour to obtain a faster result.
- [ ] Compare with C and C++ programs under equivalent optimization and
      arithmetic assumptions. Label unchecked variants separately.

#### Test gate

- Existing tests and randomized differential arithmetic/collection tests pass
  before and after each change. Reintroducing a known overflow or evaluation-
  order bug makes the gate fail.
- Each performance claim includes a reproducible before/after run, raw sample
  distribution, compiler and host details, and any code-size or compilation
  trade-off. If a gain is within measurement noise, report it as inconclusive.

#### Deliverable

Measured compiler/backend improvements, regression tests and before/after
benchmark receipts. A direct LLVM backend is a separate decision, not an
assumed prerequisite or guaranteed speedup.

---

### Phase N9 — Packaging, CI and release readiness

**Objective:** another developer can install, test and reproduce the project
without relying on this workspace.

#### Tasks

- [ ] Package the compiler and command-line entry point with explicit Python
      and C-compiler requirements; verify installation in a clean environment.
- [ ] Add CI for the declared Python versions and native compilers. Required
      runtime/evaluation tests must execute, not silently skip, in at least one
      lane; platform support is listed only after it is exercised.
- [ ] Run documentation examples, compiler tests, the N0 registry guard and
      N2 isolation gates in their appropriate CI lanes.
- [ ] Define versioning and release-artifact checks. Choose a project license
      with the owner's approval; do not assume one from another repository.
- [ ] Keep `CHANGELOG.md` empty until an actual release/change entry is
      intentionally added. Do not populate it merely to satisfy this plan.

#### Test gate

- A clean environment installs the package, checks a program, builds a native
  binary and runs it without access to this workspace.
- CI fails when a required compiler, test fixture or isolation prerequisite
  is deliberately removed; optional unsupported platforms are reported as
  such, not presented as passing.
- Release documentation, version and artifacts agree; no keys, credentials or
  unconsented private model responses appear in the release.

#### Deliverable

Installation metadata, declared support matrix, non-skipping CI and a
reproducible release checklist. No release date is committed by this plan.

---

## 5. Definition of done and non-goals

A phase is complete only when its implementation, documentation and test
gates are present and its results are recorded. New behaviour has both a
positive test and a negative control where practical. Performance claims
include the workload and uncertainty; model claims include the model, prompts,
tokens, failures and correctness. Planned files and commands are not current
features until they land.

Out of scope for this first plan: GPU code generation/execution for Nava
programs, self-hosting the compiler, training a new neural model, a default
networked package manager and any blanket promise that Nava will outperform
C/C++ on every workload or model. Model-inference benchmarks may still use a
GPU. These items may be evaluated later against measured needs rather than
treated as prerequisites for a useful 0.1 successor.
