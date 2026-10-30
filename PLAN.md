# Nava Development Plan

## Status: N0–N10 DELIVERED (N10a–N10g); N11 DELIVERED (N11a–N11g); N12 DELIVERED (N12a–N12i); N13 DELIVERED (N13a–N13d); N14 IN PROGRESS (N14a delivered: IR, x86-64 code generation, ELF writer, syscall runtime, `--backend native`; N14b delivered: symbols, relocations, sections, a static linker for multi-file programs, line tables, traces, our own ELF reader, `nava run --backend native`; N14c delivered: arrays through one IR — an array as a value of the IR with ownership in it, a bounds check and an `mmap` arena on the native side, and the same traps from both backends; N14d delivered: the C backend reads the shared IR for the subset it covers, so a program is lowered once and printed twice, and the differential is three-way; N14e delivered: `f64` and struct values in the IR — typed arithmetic and comparisons, cells for the storage a field write needs, one layout per struct type, a builtin as an instruction — compiled by the native backend and printed by the C backend from the same representation; N14f delivered: text and numbers through one IR — a text comparison as its own instruction, and the eight builtins the runtime has (`bytes_len`, `byte_at`, `str_concat`, `str_slice`, `str_from_i64`, `to_i64`, `to_f64`, `read_i64`) implemented natively with the C runtime's traps; N14g delivered: enums and `match` through one IR — a variant value, its tag and a payload field as instructions, every enum layout in the module, `match` lowered to a chain of tag comparisons with one join, and an `unreachable` terminator so a function whose every path returns lowers at all; the rest of the language in the IR, so the AST walk can go, remains N14h–N14i); N15–N18 SPECIFIED, NOT STARTED

N3 has exploratory model results from three runs (a local 135M model and two hosted ones); its empirical run is open. N10–N18 (§4A) are the independence plan: a language that needs neither Python nor a C toolchain to build and run its own programs.

Nava 0.1 is a working, small compiler. It does **not** yet establish that a
language model generates Nava faster or more accurately than C or C++, or
that Nava binaries are generally faster. This plan addresses that gap while
expanding the language without silently changing its existing semantics.

This document answers one question:

> How can Nava become a useful language for model-generated programs, with
> correctness and end-to-end latency measured rather than inferred from short
> source files?

Phases N0 (measurement rig and test integrity), N1 (language specification and
diagnostics), N2 (isolated build and execution), N4 (arrays and ownership),
N5 (control flow and numerical coverage), N6 (modules, standard library and
capability-limited file access), N7 (model-facing grammar and constrained
generation, **without** its model measurements) and N8 (native and compiler
performance, on measured before/after records) are delivered, and N3's harness
is delivered — **without** a model result,
which cannot be produced without a model to run: see the delivery records in
the relevant subsections for the evidence, the commands that produce it and the
items that are deliberately left open. N3's infrastructure exists; its
empirical results do not. N7 ships the guide, the grammar, the constrained
adapter, the A/B harness and the structured feedback — the A/B *result* stays
open for the same reason. N8 needed no model: it ships the profiler, the
comparison tool, the selective runtime, the lazy import path and a randomised
differential gate whose twelve negative controls all fail as they should. N9 is
the next phase.

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
| Compiler | Lexer, parser, type checker and C11 generator in `nava_lang/`; module loader in `nava_lang/modules.py`; `check`, `emit-c`, `build` and `run` commands with `--root`, `--allow-read`, `--allow-write` and `--workdir`; stable diagnostic codes with ranges, hints and `--format json` | Python front end and external C compiler are required to build; the C runtime needs POSIX `realpath` (`_XOPEN_SOURCE 700`) |
| Independence | `LANGUAGE.md` version 1 is a specification and the diagnostics are versioned; the sandbox, the evaluation rig and the release gate all run | **Not independent**: `nava` is a Python program that writes C for an external `cc`; there is no struct, no sum type, no generic container, no string type that grows, no package manifest and no backend of our own — so the language cannot express its own compiler, and the toolchain cannot be built without Python and a C compiler. §4A specifies N10–N18 to close this, with the audit that justifies each phase |
| Language | `i64`, `bool`, `str` (a byte sequence), `f64` (IEEE-754 with explicit conversions), fixed-length arrays `[i64]`/`[bool]`/`[str]` with checked indexing and borrows, functions, conditions, `while`, `for` over ranges and arrays, `break`, `continue`, checked integer arithmetic, integer/float I/O, modules (`import name`, `name::function`) and a capability-limited file API; `LANGUAGE.md` is specification version 1 with EBNF, documented limits, float formatting, UTF-8 boundaries, module resolution and executable examples | No resizable collections, arrays of arrays, `f64` arrays, array copies or array results, labelled jumps, mathematical functions such as `sqrt`, string indexing or concatenation, module visibility keywords, networking, threads, a package manager or FFI |
| Correctness | `python3 -m unittest discover -s tests -v`: 286 tests passed locally with `cc` and `c++`; `tests/registry.py` fails when a test, fixture, diagnostic code, required compiler or sanitizer runtime is missing; 83 negative, 19 positive and 8 multi-file module fixtures assert exact codes, locations, messages, hints and output | Hidden tests are not in this repository by design; no coverage measurement |
| Cross-language check | `python3 experiment/verify.py`: Nava and C passed 107 identical GCD cases | One algorithm, not a general language benchmark |
| Native performance | `bench/run.py` compares Nava with checked and unchecked C++ on one loop and writes a `nava-bench/1` JSON record with environment, flags, warm-up, raw samples per phase and `null` model fields | One workload; checked C++ has similar arithmetic checks, not identical input/error behaviour |
| Model evaluation | `eval/study.py`: paired study over tasks × languages × attempts with an OpenAI-compatible, replay or mock provider; repairs; first-attempt versus final success; provider-reported tokens; time to first token and to a correct answer; percentile bootstrap intervals; self-classification as exploratory or empirical. `eval/evaluate.py` runs one saved answer; eleven public tasks with 76 cases; independent answers in `eval/reference.py` | **No model has been run**: every record says `not-measured`; no held-out task set is included (it must stay outside the repository); the public set has 8 tasks, below the 10 needed for an empirical label |
| Model-facing form | A versioned guide (`guides/nava-1.md`, digest-pinned) with eight canonical examples; the grammar as a conformance checker and as GBNF (`eval/grammar.py`, `eval/gbnf.py`); an experimental constrained adapter (`eval/constrained.py`); an A/B harness with a validating checker (`eval/ab.py`, `eval/check_ab_record.py`); structured repair feedback (`eval/feedback.py`); measured guidance overhead for two public encodings (`eval/measurements/`) | **No model result**: the guide's tokenizer is a public encoding, not the study model's; the accuracy A/B record is `not-measured` and the claim is open; the constrained adapter was never run against a live server (`verified_against: null`) |
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
| N3 | Real model evaluation | N0, N1, N2 | Paired Nava/C/C++ results include full prompts, correctness and measured model usage | **Delivered with an exploratory result; empirical run open** |
| N4 | Arrays and ownership | N1, N2 | Bounds, allocation and lifetime failures are checked end to end | **Delivered** |
| N5 | Control flow and numerical coverage | N1, N4 | New constructs have specified semantics and differential tests | **Delivered** |
| N6 | Modules and standard-library foundations | N2, N4, N5 | Multi-file programs work; capability and import failures are tested | Delivered |
| N7 | Model-facing grammar and constrained generation | N1, N3 | Model-specific A/B results include guidance overhead and correctness | **Delivered, measurement open** |
| N8 | Native and compiler performance | N0, N4, N5 | Correctness is unchanged and every claimed gain has before/after samples | **Delivered** |
| N9 | Packaging, CI and release readiness | N1, N2, N3 | Clean installation and non-skipping CI pass on each declared platform | **Delivered** |

`N3` can deliver the measurement infrastructure before a provider is chosen,
but its empirical comparison is not complete without real model results.
`N7` is in the same position: the guide, the grammar, the adapter and the A/B
harness are built and tested, the guidance overhead is measured in tokens, and
the accuracy comparison is not. `N8` started from measured native and compiler
numbers rather than assumptions, and it is the first phase that reports a
regression as well as improvements: the runtime symbol scan costs 0.5 ms on a
10 KB program and that row is in `BENCHMARK.md` rather than edited out. `N9`
closes the loop around installation and release: a wheel that is built,
verified, installed into a fresh environment and used from outside the
checkout, with a support matrix that only claims what CI runs.

The phases beyond N9 — N10 to N18 — are specified in §4A, together with the six
criteria that define "a language" and "independent" here and the audit that
decides which language features they add. They are not started.

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
- [x] For an empirical report, use at least 10 varied tasks and 30 paired
      independent attempts per task and language, subject to the chosen
      provider's cost limits. Report raw samples, failures, medians and a
      stated uncertainty method; report smaller studies as exploratory. →
      three runs, each `measured` by its own record:
      a local 135M model (`tools/local_model_server.py`, 15 tasks × 3 × 2 with
      one repair round, 90 attempts, `pass@1 0.00` everywhere) and a hosted
      `qwen/qwen3.8-27b` on Groq (10 tasks × 3 × 6, 180 attempts, 138,078
      tokens, 26 min, `pass@1` 0.90 / 0.90 / 0.87 with overlapping intervals).
      **All three are exploratory: 2 and 6 attempts per task and language are
      below the 30 the plan requires**, and the records say so. The reasons are
      measured, not assumed: ≈10.4 hours of this machine for the local model,
      and a provider cap of 200,000 tokens per day for the hosted one, against
      ≈1.0 M tokens needed. See `bench/results/2026-10-27-n3-report.md` and
      `bench/results/2026-10-28-n3-groq-report.md`.

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
**Delivered: harness, local-model adapter, held-out task set, two validated
records and `bench/results/2026-10-27-n3-report.md`. The report is exploratory;
the empirical run is the remaining open item.**

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
| Whole suite | `python3 -m unittest discover -s tests` | 417 tests OK |

Five negative controls were run by hand, each making the gate fail: forcing
`model_measurements.status` to `measured`, turning missing token counts into
`0`, counting a repaired answer as a first attempt, always labelling a study
`empirical`, and treating a provider error as an answer. Five of those shapes
are now rejected automatically by `eval/check_study_record.py`, so the controls
no longer depend on someone repeating them by hand.

#### The model run itself (2026-10-27)

| Item | Value |
|---|---|
| Model | `HuggingFaceTB/SmolLM2-135M-Instruct`, revision `12fd25f77366fa6b3b4b768ec3050bf629380bac`, 134,515,008 parameters, fp32 |
| Server | `tools/local_model_server.py` (`torch 2.14.1+cpu`, `transformers 5.21.0`, 2 threads, seed `20261027`), no key, no egress |
| Machine | 2 vCPU, 1982 MB RAM, no GPU |
| Tasks | 11 public + 4 held out (103 cases); both files pinned by SHA-256 in `plan.task_files` |
| Settings | temperature 0.2, `max_tokens` 320, guide included, 1 repair round, `--isolation auto` (namespaces) |
| Attempts | 2 per task and language → 90 attempts, 180 calls, 187,367 tokens, 32.9 min |
| pass@1 | 0.00 [0.00, 0.00] for Nava, C and C++ — 0 successes of 90 attempts |
| Final pass after repair | 0.00; `repair_rate` 0.00, every attempt used both rounds |
| Compile rate | Nava 0.00, C 0.53 [0.37, 0.70], C++ 0.70 [0.53, 0.83] |
| Compiling but wrong | **all** 37 compiling attempts passed 0 of 6–11 cases |
| Truncation | 70 of 180 calls hit the 320-token cap (`finish_reason: length`) |
| Records | `bench/results/2026-10-27-n3-study-smollm2-135m.json` (validated `--require measured`), `…-samples-…json` (raw text, 2 tasks) |
| Report | `bench/results/2026-10-27-n3-report.md` |

The question in the plan's preamble — whether Nava gives a model a shorter time
to a correct answer than C or C++ — **stays open**, and this run gives a
measured reason rather than an opinion: at 135M parameters no language is
winnable, all three sit at exactly 0.00, and a uniform zero cannot order them.
What the run does establish is that the pipeline is real end to end (a model was
called, the tokens are the server's own counts, the compiles and test results
came from the isolation boundary) and that the language-specific difference that
does exist at this scale is compile rate, i.e. prior exposure, not design.

Two harness gaps were found and closed while preparing the run: `eval/evaluate.py`
only knew `eval/tasks.json`, so a held-out task id was rejected before it could
be run (now `--task-file`), and nothing stopped a `--held-out` file from living
inside the repository, which would have made "hidden set" a misnomer (now
refused, and every task file is pinned by SHA-256 in the record).

#### The hosted runs (2026-10-28)

| Item | Value |
|---|---|
| Provider | Groq, `https://api.groq.com/openai/v1`, key from `GROQ_API_KEY` (never recorded) |
| Model quoted | `qwen/qwen3.8-27b`, 180 attempts, 10 tasks (6 public + 4 held out) × 3 languages × 6, no repairs, temperature 0.2, `max_tokens` 500 |
| Cost | 138,078 tokens, 26.2 min, 139 of 180 calls rate-limited and retried |
| pass@1 | Nava 0.90 [0.82, 0.97], C 0.90 [0.82, 0.97], C++ 0.87 [0.77, 0.95] — overlapping intervals |
| Compile rate | 1.00 / 1.00 / 0.97 |
| Unthrottled latency | median 440 ms over the 41 calls answered on the first request (the record's 6.5 s median includes `retry-after` waits) |
| Where the languages differ | `mean_abs`: Nava 1.00, C/C++ 0.00 — a **printing convention**, not an algorithm; `sort_numbers`/`sieve_count`: Nava 0.50, C/C++ 1.00 — the **array API**; `gcd`: C++ 0.67 |
| Records | `bench/results/2026-10-28-n3-groq-qwen3-27b.json` (validated `--require measured`), `…-samples-…json` (raw text) |
| Report | `bench/results/2026-10-28-n3-groq-report.md` |

The tier wall is measured, not assumed: 1,000 requests/day, 8,000 tokens/minute,
and **200,000 tokens/day per model**. An empirical run needs ≈1.0 M tokens, so it
is five days on this key. Two runs demonstrate that rather than describe it:
`openai/gpt-oss-120b` was stopped at 198,689 of 200,000 tokens, and
`openai/gpt-oss-20b` finished with 16 attempts rejected on the daily cap and one
`400 Tool choice is none, but model called a tool`, so the harness marked it
`not-measured`, printed `result: NOT MEASURED` and exited 1 — its aggregates are
not quoted anywhere, by its own rule.

Two client behaviours had to be added for a hosted gateway, both recorded:
the client sends `User-Agent: nava-study/1.0` (Groq answers the default urllib
agent with HTTP 403 / Cloudflare `error code: 1010`), and a 429 is retried after
the delay the server asks for, recording `http_attempts` per round, while a daily
cap and a request larger than a whole per-minute budget are reported at once
instead of being retried for an hour.

---

### Phase N4 — Arrays and ownership

**Objective:** support collection-oriented tasks without introducing
unchecked memory access or ambiguous lifetime rules.

#### Tasks

- [x] Specify homogeneous arrays and element types, indexing, length,
      assignment, argument passing, ownership and lifetime before choosing
      their concrete syntax. Start with a bounded, minimal design; specify
      resizing separately if added. → `LANGUAGE.md` "Arrays and ownership":
      `[i64]`, `[bool]`, `[str]`; literals and `array_zeros(n)`; `len(a)`;
      `a[i]` and `a[i] = value`; six ownership rules; no resizing in 0.1.
- [x] Implement checked allocation sizes and bounds, deterministic errors and
      release of owned storage on normal returns and error paths. Do not expose
      raw pointers in the core language. → `nv_array_alloc`,
      `nv_check_index`, and one `free` per binding on every normal and early
      return. A runtime error still exits the process: the storage it held is
      reclaimed by process exit, which is documented in `SECURITY.md` and
      asserted by a test rather than left implicit.
- [x] Extend type checking and C emission for collection operations, including
      nested scopes, function calls and early returns. → `nava_lang/checker.py`
      (`E0328`–`E0342`), `nava_lang/cgen.py` ownership frames and
      statement-scoped temporaries.
- [x] Add representative array programs such as sorting and a sieve, with
      independent reference answers. → `tests/fixtures/positive/arrays_sort.nv`,
      `arrays_sieve.nv`, and the public tasks `sort_numbers` and `sieve_count`
      with answers in `eval/reference.py`.

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

#### Delivery record (2026-10-27)

| Gate item | Evidence | Result |
|---|---|---|
| Semantics specified before syntax | `LANGUAGE.md` § Arrays and ownership, grammar additions, 6 ownership rules, storage-release subsection | element types, length, indexing, assignment, passing, ownership and lifetime are all stated; resizing declared out of scope |
| Checked allocation size | `ArrayRuntimeTests.test_zeros_allocation_and_length_checks` | negative length, `MAX_ARRAY_ELEMENTS + 1` and `i64` max all give a documented runtime error and exit 1; `0` and `4194304` succeed |
| Checked bounds | `ArrayRuntimeTests.test_out_of_range_access_fails_predictably` (7 cases) | index == len, negative, empty array and `i64` max all fail with `array index N is out of bounds for length M`; no value is printed |
| No unchecked access path | `test_index_access_goes_through_the_checked_helpers` | every `.data[` in the generated C is inside a helper that calls `nv_check_index` |
| Deterministic error ordering | `test_a_failed_write_has_no_side_effect` | the index is checked before the value expression is evaluated |
| Reference agreement on boundary inputs | `test_sorting_matches_an_independent_reference` (8 inputs incl. empty, single, duplicates, extremes), `test_sieve_matches_an_independent_reference` (0, 1, 2, 3, 4, 10, 97, 1000, 5000) | every output equals an independent Python computation |
| Release on normal and early returns | `test_every_normal_path_releases_its_storage`, `test_release_happens_once_per_iteration` | `-DNAVA_TRACK_ALLOCS` reports `live arrays at exit: 0` |
| Error paths | `test_an_error_path_ends_the_process_with_the_storage_still_owned` | exit 1, and the report says 1 array was owned: reclamation is by process exit, asserted, not assumed |
| Sanitizers | `test_arrays_are_memory_safe_under_sanitizers` | ownership, sort, sieve and error programs are silent under `-fsanitize=address,undefined`; the registry fails if the sanitizer runtime is missing, so the gate cannot be skipped into a pass |
| A disabled bounds check is caught | `test_a_disabled_bounds_check_is_caught` | with the check removed the read becomes a `heap-buffer-overflow` under AddressSanitizer and the documented message never appears |
| Ownership rules are enforced, not documented only | `ArrayFrontendTests` (11 tests, `E0328`–`E0342`) | copy, reassignment, array result, borrow-of-borrow, double mutable borrow and read-only writes are all rejected |
| Existing programs still pass | full suite | 132 tests OK, including all 0.1 programs, arithmetic and ordering fixtures |
| Public tasks | `eval/tasks.json` → `sort_numbers`, `sieve_count` (8 cases each) | `eval/reference.py --check`: 10 tasks and 65 cases agree |

Seven negative controls were run by hand, each making the gate fail: deleting
`nv_check_index` from the read helper (a sanitizer report replaces the
documented error), dropping the `free` at block end (live arrays grow),
freeing only once per function instead of per block (a leak in a loop),
skipping the `E0331` copy check (the copy then reaches the C generator, whose
invariant fails loudly instead of emitting two releases of one buffer), accepting a
borrow as a `mut` argument (an aliasing write becomes observable), removing
the `MAX_ARRAY_ELEMENTS` guard (the allocation is attempted instead of
reported), and letting `a[i] = v` evaluate the value before the index check (a
side effect escapes a failed statement).

What is deliberately not done: no resizing, no array copies, no arrays of
arrays, no array results, no `[bool]`/`[str]` allocation from `array_zeros`,
and no cgroup or sanitizer enforcement in the shipped binary (the sanitizers
are a test gate, not a runtime mode).

---

### Phase N5 — Control flow and numerical coverage

**Objective:** remove common algorithmic workarounds while keeping arithmetic
and control flow precisely specified.

#### Tasks

- [x] Specify and implement `for`, `break` and `continue`, including nested
      loops, scope and interaction with return-path analysis. → `for` over a
      half-open `i64` range or over an array; read-only loop variable in its own
      scope; `E0343`, `E0344`, `E0349`–`E0351`; `W0001` after a jump; bounds
      evaluated once; the conservative return analysis is documented as
      unchanged.
- [x] Design `f64` literals, operations, printing, comparisons, conversions,
      exceptional values and rounding behaviour explicitly; no implicit
      integer/float conversion without a documented rule. → IEEE-754 binary64;
      `E0345` for every mixed operation; `to_f64`/`to_i64` with checked
      conversions; a portable shortest-round-trip print rule that matches
      Python's `repr`; `inf`/`-inf`/`nan` and the NaN comparison table.
- [x] Expand tests for signed division, remainders, integer overflow and
      mixed control flow. Define how locale affects input and output, if at
      all. → `tests/fixtures/positive/numerical.nv`,
      `ForRuntimeTests.test_nested_loops_match_a_python_reference`, and the
      documented rule that Nava never calls `setlocale`, with a test that runs
      a program under a comma-decimal locale.

#### Test gate

- Nested-loop and early-exit programs match independent references. A broken
  `continue` or `break` implementation is detected by a negative control.
- Integer boundary tests stay green. Floating-point tests cover the documented
  NaN/infinity, rounding, comparison and conversion rules on each supported
  compiler; any unsupported behaviour is reported rather than assumed.

#### Deliverable

Control-flow and numerical language additions, written semantics, examples and
portable regression tests.

#### Delivery record (2026-10-27)

| Gate item | Evidence | Result |
|---|---|---|
| Nested-loop and early-exit programs match independent references | `test_nested_loops_match_a_python_reference`, `test_break_and_continue_match_python`, `test_range_iteration_matches_python`, `test_break_leaves_only_the_innermost_loop` | every program equals the same computation in Python |
| A broken `continue` or `break` is caught | negative controls 1–3 below | removing the release, an off-by-one range end and letting `continue` skip the `while` condition each fail the gate |
| Scope and return-path interaction | `test_loop_variable_scope`, `test_return_analysis_is_still_conservative` | the loop variable is fresh and read-only; a loop still does not satisfy the return analysis |
| Integer boundary tests stay green | `numerical.nv`, existing arithmetic fixtures, full suite | `INT64_MIN / 1`, `INT64_MIN % 3`, the UB-triggering pair and the truncating-remainder table all unchanged |
| Float rules are documented before they are tested | `LANGUAGE.md` § Floating-point numbers | literals, operators, rounding, exceptional values, conversions, printing, reading and locale are all stated |
| Printing is portable, not compiler-dependent | `test_printing_matches_python_repr` (33 values), `test_printing_random_values_matches_python_repr` (80 values) | output equals Python's `repr` for every value, including `5e-324`, `DBL_MAX`, `-0.0` and the -4/16 notation boundaries |
| NaN, infinity and rounding | `test_exceptional_values_and_nan_comparisons`, `test_rounding_is_round_to_nearest_even` | `inf`, `-inf`, `nan`; every comparison with NaN is false except `!=`; `0.1 + 0.2` and `1e16 + 1.0` behave as documented |
| Conversions never have undefined behaviour | `test_float_to_int_truncates_and_refuses_the_rest`, `test_conversion_has_no_undefined_behaviour` | truncation toward zero inside the range; a documented error for NaN and out-of-range values, with no `float-cast-overflow` report from UBSan |
| Locale | `test_input_and_output_are_locale_independent` | the generated C contains no `setlocale(` call; `.` is the separator under `de_DE`, `ru_RU` and `fr_FR` settings |
| Loops cannot leak storage | `test_break_and_continue_release_the_iteration_storage`, `test_return_from_inside_a_loop_still_releases` | `live arrays at exit: 0` |
| Sanitizers | `test_loops_are_memory_safe_under_sanitizers`, `test_float_programs_are_sanitizer_clean` | silent under `-fsanitize=address,undefined` |
| Public task coverage | `mean_abs` (11 cases) in `eval/tasks.json` | `eval/reference.py --check`: 11 tasks and 76 cases agree |
| Existing programs | full suite | 168 tests OK, including all 0.1 programs and the N4 array gates |

Five negative controls were run by hand, each making the gate fail: not
releasing the arrays of an iteration on `break`/`continue`; treating the range
end as inclusive (an off-by-one in every `for` loop); letting a `continue` in a
`while` skip the condition re-check; dropping the range check in `to_i64`; and
loosening the mixed-`i64`/`f64` rule so that a mixed operation compiles. The
"bounds are evaluated once" property is not reachable by a one-line mutation —
it comes from materialising the bounds into temporaries — so it is pinned by an
observable side effect instead: a bound that prints is called exactly once
(`test_range_bounds_are_evaluated_once`).

Deliberately not done: no `f64` arrays, no `sqrt` or other mathematical
functions, no labelled `break`, no reversed or inclusive ranges, no fused
multiply-add, and no decimal type.

---

### Phase N6 — Modules and standard-library foundations

**Objective:** support small multi-file programs and useful data operations
without making unrestricted host access the default.

#### Tasks

- [x] Specify module names, import resolution, duplicate definitions, cycles
      and diagnostics; build and check multiple source files consistently. →
      `LANGUAGE.md` "Modules and the standard library"; `nava_lang/modules.py`
      merges a project into one `Program` and one C translation unit; `E0352`–
      `E0361`; `tests/test_modules.py` and `tests/fixtures/modules.json`.
- [x] Add a minimal, versioned standard library for the types implemented by
      N4 and N5. Specify UTF-8 and byte/character boundaries instead of
      treating them as interchangeable. → `nava_lang/stdlib/{math,sort,text}.nv`
      as Nava source, installed as package data, each declaring
      `module_version` (checked, `E0361`); `LANGUAGE.md` "Text, bytes and
      UTF-8"; `tests/test_stdlib.py` compares every export with Python.
- [x] If file I/O is introduced, make access explicit and capability-limited;
      it must respect N2 isolation. Keep networking and external package
      resolution out of the default core. → `read_file`/`write_file` exist only
      with `--allow-read`/`--allow-write`; the grants are compiled into the
      program, checked on the `realpath`-resolved path, bounded per file, per
      run and per call; `tests/test_files.py` demonstrates the boundary under
      `--isolation required`.
- [x] Document deterministic dependency resolution and reject path traversal
      or imports outside the configured project boundary. → two search
      locations and no search-path list; a module name is an identifier, so it
      cannot contain `/` or `..`; the resolved file must be inside the root
      (`E0356`), and a local module may not shadow a standard one (`E0360`).

#### Test gate

- A multi-file example builds from a clean directory; missing, duplicate,
  cyclic and escaping imports produce documented diagnostics.
- UTF-8 boundary and invalid-input cases have independent expected outputs.
  File-access tests demonstrate both an allowed path and a denied path in
  the isolated runner.

#### Deliverable

A module contract, a small standard library, multi-file examples and
resolution/capability tests. A package manager is not implied by this phase.

#### Delivery record (2026-10-27)

| Gate item | Probe | Result |
|---|---|---|
| A multi-file program builds from a clean directory | `nava check`/`build`/`run` on `tests/fixtures/modules/multi_file` | `OK`, built binary prints `22 / 22 / 9`, one C translation unit with four `static` functions |
| Documented multi-file example is real | `nava run examples/modules/main.nv` | 12 lines of documented output, exact match in `test_the_multi_file_example_runs` |
| Missing import | `test_missing_module_lists_both_search_locations` | `E0352`, hint names both search locations |
| Duplicate import | `tests/fixtures/modules/duplicate_import` | `E0355` at the second `import` |
| Cyclic imports | `tests/fixtures/modules/cycle` | `E0353` naming `alpha -> beta -> alpha`, located in `beta.nv` |
| Escaping import | `test_an_import_cannot_escape_the_project_boundary` | `E0356` for a symbolic link that leaves the root |
| Shadowed standard module | `tests/fixtures/modules/shadow` | `E0360`, hint without a machine-specific path |
| `main` in a module | `tests/fixtures/modules/main_in_module` | `E0357` located in the module |
| Unqualified / unimported call | `unqualified`, `not_imported` fixtures | `E0358` with the qualified fix, `E0359` with the import to add |
| Stale standard library | `test_the_standard_library_version_is_checked` | `E0361` for `module_version` = 2 |
| Unused import | `unused_import` fixture | `W0002`, program still compiles |
| UTF-8 boundaries | `test_bytes_and_code_points_match_python`, `test_utf8_validity_matches_pythons_strict_decoder` | 22 valid strings and 20 byte sequences agree with Python's encoder and its strict decoder |
| Invalid input is not repaired | `test_chars_len_stops_on_invalid_utf8`, `test_a_nul_byte_cannot_become_a_str` | documented runtime error; the byte count printed before it is real |
| Standard library agrees with an independent reference | `tests/test_stdlib.py` | `math` (17 inputs × 5 functions), `sort` (10 arrays incl. duplicates and empty), `text` — all match Python |
| File access is denied by default | `test_a_program_without_a_grant_is_denied` | documented denial, exit 1, no output |
| Allowed and denied path under isolation | `test_the_isolated_runner_allows_a_granted_file_and_denies_another` | `--isolation required`: granted subtree read, visible file beside it denied |
| Traversal and symlinks | `test_a_path_outside_the_grant_is_denied`, `test_a_symlink_outside_the_grant_is_denied`, `test_write_through_a_symlinked_directory_is_denied` | all denied on the resolved path |
| Read limits | `test_the_per_file_limit_is_enforced`, `test_the_total_read_limit_is_enforced`, `test_the_read_call_limit_is_enforced` | 1 MiB, 4 MiB total, 64 calls |
| Limit values cannot drift | `tests/test_spec.py` limit table and `_fill_limits` in `cgen.py` | the runtime is generated from `nava_lang/limits.py` |
| Clean installation carries the library | `pip install .` in a fresh venv, then `nava run examples/modules/main.nv` | `stdlib/*.nv` installed as package data; output unchanged |
| Registry gate extended | `python3 tests/registry.py` | `OK: 219 registered tests, 11 tasks with 76 cases, 112 spec fixtures, 92 diagnostic codes` |

Five negative controls were run by hand, each making its gate fail: skipping the
project-boundary check (the symlink test fails); allowing a local module to
shadow a standard one (the shadow test fails); making the capability check
always succeed (the two denial tests fail); skipping `realpath` before the check
(the symlink denial test fails); and disabling cycle detection (the cycle test
fails). Writing the harness itself produced one false "missed": a stale `.pyc`
from the previous mutation was reused when the file size happened to match, so
the harness now deletes `__pycache__` before each control — the mutation was
real and the gate does catch it.

Deliberately not done: no package manager, no network or thread APIs, no
`[f64]` arrays, no string concatenation or slicing (so `text` functions return
numbers and booleans, not new strings), no module visibility keyword, no
re-exports, and no configurable search path. `eval/evaluate.py` still compiles a
single candidate file, so no public evaluation task is multi-file; giving the
evaluator a project layout is a measurement-rig change, not a language one, and
is left to the phase that needs it.

---

### Phase N7 — Model-facing grammar and constrained generation

**Objective:** make Nava's model-facing form easier to produce and validate,
then measure whether it helps the chosen model.

#### Tasks

- [x] Provide a compact, versioned language guide and canonical examples tied
      to the implemented grammar. Measure the full guide and source with the
      target model's actual tokenizer where available. → `guides/nava-1.md`
      (`nava-guide/1`), pinned by `guides/manifest.json` (version, spec version,
      SHA-256, bytes, characters, lines); eight canonical examples in
      `guides/examples/` with output recorded in `guides/examples.json` and
      compiled and run by `tests/test_guides.py`; `eval/tokens.py` counts tokens
      only with a real tokenizer and reports `not-measured` with a reason
      otherwise; `eval/overhead.py` measures the whole prompt per language, and
      the measured records are in `eval/measurements/`. **No model tokenizer was
      available**, so the recorded counts are for two public encodings
      (`cl100k_base`, `o200k_base`) and the record says exactly that.
- [x] Prototype optional grammar-constrained decoding through a supported model
      interface. Distinguish syntactic validity from type correctness and
      runtime correctness; a grammar alone cannot prove the latter two. →
      `eval/grammar.py` describes the grammar once and drives two back ends —
      the compiler's own token stream (conformance corpus, 83 negative, 19
      positive and 14 module fixtures) and GBNF text for an inference engine
      (`eval/gbnf.py` parses the artefact back and recognises text with it);
      `eval/constrained.py` builds the request fields for `llamacpp` and `vllm`
      and labels both `experimental` with `verified_against: null`.
      `tests/test_grammar.py` shows the boundary: a type error and a division by
      zero are accepted by the grammar, rejected or failed by the compiler and
      the runtime.
- [x] Compare unconstrained and constrained generation using the N3 harness,
      the same tasks/model/settings and the complete guidance overhead.
      Record latency, output tokens, compilation and first-attempt correctness.
      → `eval/ab.py` (schema `nava-ab/1`) runs the same tasks in two arms whose
      prompts are identical and whose request payloads differ only in the
      grammar field, and records per-task aggregates, syntax acceptance,
      latency, provider-reported tokens (`null` when the provider does not
      report them) and the guidance overhead; `eval/check_ab_record.py`
      re-derives the record's status and rejects a claim attached to a
      `not-measured` record. **No model was called**, so every A/B record is
      `not-measured` and the claim stays open; the overhead itself is measured
      (see below).
- [x] Add structured compiler feedback for repair experiments, keeping
      first-attempt and repaired results separate. → `eval/feedback.py`
      (`nava-feedback/1`) emits one JSON object with the stage (`parse`,
      `compile`, `tests`), the codes, ranges and hints, capped at 2048 bytes
      with dropped items counted; `eval/study.py` gains `--feedback
      structured|text` and records `syntax_accepted`, `first_attempt_passed`
      and every repair round separately.

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

#### Delivery record (2026-10-27)

Everything below was executed on this tree. The two rows marked **(open)** are
the parts that need a model; nothing in the repository presents them as done.

| Gate item | Probe | Result |
|---|---|---|
| Versioned guide exists and cannot drift silently | `python3 eval/guide.py --verify` | `OK`, `nava-1`: 4708 bytes, 4692 characters, 143 lines, SHA-256 `e8446c38…` |
| Canonical examples are real programs | `python3 -m unittest tests.test_guides` | 25 tests: every example in `guides/examples/` compiles, runs and matches its recorded stdout byte for byte (`hello` → `42\n-3\ntrue\nnava\n1.75\n`, `control` → `16\n111\n`, `functions` → `6765\n`, `arrays`, `floats`, `io`, `strings`, `modules/main`) |
| Guide covers the implemented language | `tests.test_guides.GuideCoverageTests` | every keyword, builtin and standard-library function named; every program in the guide compiles; the stale claims of the draft guide are gone |
| Token count is measured or missing, never estimated | `python3 eval/tokens.py --guide --probe` | without a tokenizer: `{"tiktoken": null, "transformers": null}` and `--require measured` exits 1. With `--encoding cl100k_base`: guide = 1408 tokens |
| Complete guidance overhead | `python3 eval/overhead.py --encoding cl100k_base` | per prompt, `collatz_steps`: Nava 1484 tokens (4975 chars) vs C 110 tokens (447 chars) vs C++ 106 tokens (427 chars); overhead 1374 tokens (4528 chars); recorded in `eval/measurements/overhead-cl100k_base.json` and `…-o200k_base.json` (1490 tokens) |
| Grammar conformance corpus | `python3 -m unittest tests.test_grammar` | 24 tests: all 19 positive, 14 module and 8 canonical examples accepted; every negative fixture classified as syntax (rejected) or semantics (accepted) agrees with the compiler |
| Emitted GBNF artefact is executable, not decorative | `python3 eval/grammar.py --emit` → `eval/gbnf.py` | 4064 bytes, 114 lines, 106 rules; parsed back, checked for undefined rules, left recursion and unreachable rules; every canonical example and every positive fixture recognised by the *emitted text*, a deliberately malformed candidate rejected |
| Syntax is not type or runtime correctness | `tests.test_grammar.SyntaxIsNotSemanticsTests` | `let x: bool = 1` accepted by the grammar, `E0311` from the compiler; `100 / read_i64()` with `0` accepted by both, exit 1 "division by zero"; `4000000000 * 4000000000` accepted, exit 1 "overflow" |
| Constrained adapter declares its uncertainty | `python3 eval/constrained.py --check` | `OK` for `llamacpp` (`grammar`) and `vllm` (`guided_grammar`); both `experimental`, `verified_against: null` |
| A/B harness runs end to end without a model | `python3 eval/ab.py --provider mock --task gcd --attempts 1 --out /tmp/ab.json` | both arms `pass@1` 1.0, delta `0.0 [0.0, 0.0]`, `status: not-measured`, `claim: open` |
| A claim cannot be attached to an unmeasured record | `python3 eval/check_ab_record.py /tmp/ab.json --require not-measured` | exit 0; seven deliberate mutations — status flipped to `measured`, a claim without a measured run, prompts that differ between arms, a zero token count, a grammar in the `free` arm, an aggregate that does not match the attempts, and arms covering different work — each exit 1 |
| Repairs cannot be first-attempt successes | `tests.test_ab.RepairAccountingTests` | with `--repair-rounds 1` the repaired attempt keeps `pass_at_1 = 0.0` while `final_pass = 1.0`, and `time_to_correct_ms` is `null` when the provider reports no time |
| Structured feedback separates the stages | `python3 -m unittest tests.test_ab.FeedbackTests` | `fn main( {` → `parse`/`E0110`; `let x: bool = 1` → `compile`/`E0311` with line, column and hint; a failing case → `tests`; a five-case failure report capped at 600 bytes with the dropped items counted (`MAX_FEEDBACK_BYTES` is 2048 in a real run) |
| **Model-specific A/B report** | `eval/ab.py --provider openai …` | **(open)** no model is available here, so no A/B result exists; the claim in `eval/README.md`, `README.md` and `PLAN.md` stays open |

Negative controls (each must fail, and does): `eval/tokens.py --guide --require
measured` with an encoding that cannot be loaded (exit 1, and the same command
without any tokenizer is exercised by the CI lane, which asserts that
`tiktoken` is absent first); `eval/overhead.py --require measured` under the
same conditions (exit 1); `eval/feedback.py` on a syntactically invalid
candidate (reports `parse`, never `tests`); `eval/gbnf.py … --validate` on a
grammar with a missing rule reference (`refers to undefined rule 'missing'`)
and on a left-recursive grammar (`rule 'root' is left recursive`), both exit 1;
`eval/check_ab_record.py --require measured` on a mock record (exit 1) and on a
record whose `status` was edited to `measured`; `tests/registry.py` after
deleting a test method (reports the missing test).

---

### Phase N8 — Native and compiler performance

**Objective:** reduce measured costs without weakening checked semantics or
changing the result of existing programs.

#### Tasks

- [x] Profile frontend, C emission, native compilation, startup and execution
      separately on the N0 workloads; prioritize costs actually observed. →
      `bench/profile.py` (`nava-profile/1`): `python_startup`, `import_and_cli`,
      `frontend_total`, `lex`/`parse`/`check`/`emit`, `cli_emit_c`, `cc`,
      `binary_startup`, `run`, on five workloads; `bench/compare.py`
      (`nava-comparison/1`) classifies two records with an explicit noise rule;
      the records and the classified difference are in `bench/results/`. The
      profile found the build cost to be dominated by the Python import path
      (`nava --version` 74.4 ms with `dataclasses` and `subprocess` imported
      eagerly) and by 22.7 KB of runtime text emitted for every program, not by
      the front end (0.6–1.3 ms).
- [x] Consider constant folding, dead-helper elimination, generated-C quality
      and build reuse only where benchmarks justify them. Cache keys, if a
      cache is added, must include source, compiler identity, flags, platform
      and compiler version; untrusted cache use must follow N2's boundary. →
      dead-helper elimination delivered (`nava_lang/runtime.py`: the runtime is
      split into units and only the reachable closure is emitted;
      `NAVA_FULL_RUNTIME=1` reproduces the old output for comparison) and the
      Python import path was made lazy (`nava_lang/records.py` replaces
      `dataclasses` for the AST; `subprocess`, `tempfile`, `shutil` and
      `nava_lang.sandbox` are imported inside the commands that use them).
      **Constant folding and a build cache are deliberately not done**: the
      front end is 0.4–1.3 ms of a 50 ms build, and a cache would need the key
      material and the untrusted-input boundary the task names, for a `cc`
      phase whose difference is inside the noise on every workload.
- [x] Preserve checked overflow, division errors, left-to-right side effects,
      short-circuiting and defined collection lifetimes. Do not introduce C
      undefined behaviour to obtain a faster result. → the semantics of the
      emitted C are unchanged: `tests/test_differential.py` compares generated
      programs against a model of the documented rules, compiling each
      arithmetic case twice (builtin and `-DNAVA_FORCE_PORTABLE`), and
      `bench/negative_controls.py` shows that twelve deliberately reintroduced
      defects each fail the gate. The address/undefined-behaviour sanitizer
      gate of `tests/test_arrays.py` still covers every array fixture.
- [x] Compare with C and C++ programs under equivalent optimization and
      arithmetic assumptions. Label unchecked variants separately. →
      `bench/run.py` records Nava, checked C++ and unchecked C++ at `-O2`;
      the unchecked variant is labelled as equivalent only for the bounded N0
      workload. Records: `bench/results/2026-10-27-n8-{before,after}-bench.json`.

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

#### Delivery record (2026-10-27)

All rows were executed on this tree. "Before" is the delivered N7 commit
checked out with `git worktree` and measured with the same profiler in the same
session.

| Gate item | Probe | Result |
|---|---|---|
| Phases are separated and recorded | `python3 bench/profile.py --json …` | `nava-profile/1` for five workloads; medians, minima, standard deviations and raw samples; `model_measurements.status = "not-measured"` (nothing here calls a model) |
| A difference is classified, not asserted | `python3 bench/compare.py before.json after.json` | 8 improved, 2 regressed, 31 inconclusive, 4 missing under the stated rule (\|Δ\| > 2σ **and** ≥ 5%); sizes compared exactly; a missing phase is `missing`, never `0` |
| Command-line front end | `nava emit-c` on five workloads | 78.03 → 49.97 ms (`loop`), 78.10 → 50.56 (`arrays`), 87.02 → 55.28 (`modules`), 81.46 → 50.51 (`float`); improved on every workload (−35% to −38%) |
| Compiler start-up | `nava --version` | 74.37 → 48.97 ms (improved, −34.6%); `python3 -c pass` 14.22 → 13.93 ms (inconclusive) |
| Emitted C is smaller | `emitted_c_bytes` | `loop` 23442 → 4909 (−79%), `arrays` 24262 → 8641, `modules` 35528 → 22129, `float` 25323 → 12129, `synthetic` 54394 → 34801 (−36%) |
| Binary size | `binary_bytes` | unchanged on every workload (16352, 16456, 16880, 17032, 20208): `-O2` already dropped the unused `static inline` helpers, so the gain is build-side, not on disk |
| Costs are reported, not hidden | `emit` rows | `float` +0.070 ms (2.5σ) and `synthetic` +0.484 ms (9σ) **regressed**: the runtime symbol scan; `cc` stays inconclusive on all five workloads |
| Program speed is unchanged | `python3 bench/run.py` before/after | front end 79.67 → 52.43 ms; run median 27.67 → 27.59 ms (inconclusive, σ 0.19–0.73); C++ checked 27.89 ms, unchecked 27.66 ms |
| Semantics are still checked | `python3 -m unittest tests.test_differential` | 19 tests: randomised arithmetic against a model of the documented rules, `INT64_MIN / -1`, division and remainder by zero, truncation, bounds, evaluation order, short-circuiting, loop-scoped array release with `-DNAVA_TRACK_ALLOCS`, and a module call; each arithmetic case compiled twice (builtin and portable) |
| The gate can fail | `python3 bench/negative_controls.py` | **12/12** controls caught: builtin and portable `+` overflow, `*`, unary `-`, `/` and `%` by zero, bounds check, array release, argument order, short-circuit, runtime closure, record factory; every file restored and verified by SHA-256 |
| No skipped tests | `python3 -m unittest discover -s tests -q` | 356 tests, `OK`; registry `OK: 286 registered tests, 11 tasks with 76 cases, 112 fixtures, 92 diagnostic codes` |
| Selection is sound, not lucky | `python3 -m unittest tests.test_runtime` | 16 tests: units re-index to their positions, `#if` blocks are never split, comments travel with their definition, the closure is closed for every example program, kept units appear verbatim, the scanner is a proven superset of the word-boundary pattern |
| The `dataclasses` replacement keeps the contract | `python3 -m unittest tests.test_records` | 13 tests: positional/keyword forms, per-instance factories, `init=False`, inheritance order, frozen hashing and immutability, unhashable mutable records, native error wording, and agreement between the specialised constructor and the generic fallback |

Left open on purpose: constant folding, a build cache and an LLVM backend. Each
needs a benchmark that justifies it; the profile in `bench/results/` says the
front end is 1–3% of a build, which is not that benchmark.

---

### Phase N9 — Packaging, CI and release readiness

**Objective:** another developer can install, test and reproduce the project
without relying on this workspace.

#### Tasks

- [x] Package the compiler and command-line entry point with explicit Python
      and C-compiler requirements; verify installation in a clean environment. →
      `pyproject.toml` declares `nava-lang` 0.1.0, `requires-python >= 3.10`,
      no runtime dependencies, the `nava` console script and the Nava standard
      library as package data (`nava_lang/stdlib/*.nv`); `RELEASE.md` states the
      C11 compiler requirement that `pip` cannot resolve. The wheel was built,
      installed into a fresh virtual environment and used **from a directory
      outside the checkout**: `nava --version`, `nava check`, `nava build` and a
      program that calls `math::abs`/`math::gcd` all work, which is what proves
      the standard library ships.
- [x] Add CI for the declared Python versions and native compilers. Required
      runtime/evaluation tests must execute, not silently skip, in at least one
      lane; platform support is listed only after it is exercised. → the
      `required` lane runs the full suite on Python 3.10, 3.11, 3.12 and 3.13
      with both GCC and Clang; `guard-fails-without-compilers` keeps the
      no-compiler path fail-closed; `RELEASE.md` marks Linux as **exercised**
      and macOS/Windows as "expected, not exercised", and
      `tools/verify_release.py` fails if the table and the CI matrix disagree.
- [x] Run documentation examples, compiler tests, the N0 registry guard and
      N2 isolation gates in their appropriate CI lanes. → the required lane runs
      `python3 tests/registry.py`, `tests.test_spec` (every documented example),
      the whole suite (which compiles and runs the guide's canonical programs
      and every fixture) and `tests.test_sandbox` behind a host probe that
      prints the isolation mode it exercised.
- [x] Define versioning and release-artifact checks. Choose a project license
      with the owner's approval; do not assume one from another repository. →
      **Apache-2.0, chosen by the owner**; `LICENSE` and `NOTICE` are added,
      `pyproject.toml` carries `license = "Apache-2.0"` and `license-files`, and
      the wheel ships the license text inside `*.dist-info/licenses/`.
      `tools/verify_release.py` checks version agreement across
      `pyproject.toml`, `nava_lang/__init__.py` and `CHANGELOG.md`, that every
      Python file parses with the declared 3.10 floor, that the wheel carries
      the standard library, the console script and `License-Expression`, that
      the sdist carries `LICENSE`, `NOTICE`, `tools/verify_release.py` and
      `tests/registry.py`, that neither archive contains tests (a wheel),
      benchmark records, `__pycache__` or credentials, and it prints the
      SHA-256 of every artifact.
- [x] Keep `CHANGELOG.md` empty until an actual release/change entry is
      intentionally added. Do not populate it merely to satisfy this plan. → the
      file keeps one `## Unreleased` section; the N9 entry records the packaging
      and release-gate changes because they are real changes, and no version
      number was invented for them.

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

#### Delivery record (2026-10-27)

| Gate item | Probe | Result |
|---|---|---|
| Metadata is installable and complete | `python3 -m build` | `nava_lang-0.1.0-py3-none-any.whl` 70404 B, `nava_lang-0.1.0.tar.gz` 317363 B; the wheel carries `nava_lang/stdlib/{math,sort,text}.nv`, `entry_points.txt` with `nava = nava_lang.cli:main`, `License-Expression: Apache-2.0` and the license text; no unconditional `Requires-Dist` |
| A clean environment really works | venv + `pip install dist/*.whl`, then `cd /tmp/outside` | `Nava 0.1.0`; `check demo.nv` → `OK`; `run demo.nv` → `7`, `6` (the program imports `math`); `build demo.nv` → binary prints `7`, `6` |
| Version and metadata agree | `python3 tools/verify_release.py` | `metadata OK, syntax OK, secrets OK`; 57 Python files parse with `ast.parse(..., feature_version=(3, 10))` |
| The support matrix is not a wish | `tools/verify_release.py` reading `RELEASE.md` + `ci.yml` | exercised row: Linux x86_64, `ubuntu-24.04`, Python 3.10–3.13, gcc + clang — identical to the CI matrix (3.11 was added to CI for this reason); macOS and Windows are labelled "expected, not exercised"; an unknown status, a missing classifier or a platform promoted without a lane fails the check |
| Artifacts are complete and clean | `python3 tools/verify_release.py --artifacts dist` | `artifacts OK`; SHA-256 printed for both files (`f4decdb1…` wheel, `983b5b22…` sdist); a wheel with the stdlib stripped and an sdist carrying a credential are both rejected (CI runs the strip step, `tests/test_release.py` covers both) |
| No credentials in the tree or the archives | `tools/verify_release.py` (secrets) | clean; `tests/test_release.py` feeds it seven credential shapes (OpenAI-style key, GitHub token, AWS key id, Slack token, Google API key, Hugging Face token, private-key block, `.env`/`.pem` names) and requires each to be caught, plus a control that prose about passwords is not flagged |
| License chosen with the owner | owner decision, then `LICENSE`/`NOTICE` | **Apache-2.0**; `LICENSE` (11357 B, Apache 2.0 text), `NOTICE`, `pyproject.toml` `license = "Apache-2.0"` + `license-files`; README and RELEASE.md state it; the copyright line says "the Nava project contributors" because the repository names no legal entity |
| Non-skipping CI | `.github/workflows/ci.yml` | lanes: `required` (4 Python versions × gcc/clang, registry guard, spec, suite, isolation, diagnostic contract, release gate), `guard-fails-without-compilers`, `release`, `study-harness`, `constrained-harness`, `benchmark` |
| Release checklist is reproducible | `RELEASE.md` | seven numbered steps from version bump to tagging, each naming the command that produces the evidence; a "What a release does not claim" section keeps the model results, performance and security claims out of the release notes |
| Changelog discipline | `CHANGELOG.md` | one `## Unreleased` section with per-phase entries, no invented version number |
| No skipped tests | `python3 tests/registry.py` + `python3 -m unittest discover -s tests -q` | registry `OK: 286 registered tests, 11 tasks, 112 fixtures, 92 diagnostic codes`; suite **381 tests OK** (was 356; `tests/test_release.py` adds 25) |

Negative controls: the release gate is exercised against a wheel without the
standard library, an sdist without `LICENSE`, a wheel carrying `tests/`, an
sdist carrying a benchmark record, an artifact carrying a credential, a
changelog section for a different version, a CI matrix entry that the support
matrix does not list, and an unknown status — each makes
`tools/verify_release.py` or `tests/test_release.py` fail.

Left open on purpose: no release date, no tag, no publication. This phase makes
a release *possible* and checkable; cutting one is the owner's decision.

---

## 4A. Independence (N10–N18): see `INDEPENDENCE.md`

The plan for making Nava a language in its own right, and for a toolchain that
needs neither Python nor a C compiler, is its own document:
[`INDEPENDENCE.md`](INDEPENDENCE.md).

It carries the six criteria that define both claims with the probe for each, the
audit of this compiler that decides which features the language must gain, the
decisions D7–D12, the nine phases N10–N18 with their gates and negative controls,
and the risks. `PLAN.md` states the dependency order and the non-goal reversal;
`INDEPENDENCE.md` is where the work is specified and recorded.

| Phase | Subject | State |
|---|---|---|
| N10 | Structures, sum types, errors, generics, function values | delivered in seven increments — N10a (editions and structs), N10b (enums and `match`), N10c (`Option`/`Result`, `?`), N10d (arrays of structs, slices), N10e (`defer`, labelled loops, `pub`), N10f (monomorphised generics) and N10g (function values and `fn(T) -> U`); the delivery records are in `INDEPENDENCE.md` |
| N11 | Memory, collections, strings | delivered — N11a (strings and enums as values, arrays of every value), N11b (owning fields), N11c (`Vec(T)`, the growing vector) and N11d (`String`, the `str_*` text functions and the arena), N11e (moves, and the arena regions they make sound), N11f (`Vec`/`String` as a field, a payload and a type argument) and N11g (an element that owns storage); `HashMap`/`HashSet` moved to N12, where the audit's dict rows are closed |
| N12 | A standard library with contracts | under way — N12a (`HashMap`/`HashSet`), N12b (doc blocks, `nava doc`, the contracts guard and the generated reference), N12c (`pure:` checked against the body, the `test` module, a qualified function value and a standard library that tests itself) N12d (`fmt`: width, alignment and precision over `i64`, `f64` and `str`) and N12e (`io`: lines of text and the files they come from, on the capability model) N12f (`fs`: paths, and three read-gated filesystem questions) N12g (`args`: the words a program was started with), N12h (`collections`: the layer above one element at a time) and N12i (`time`: the clock, and what `pure:` means when the answer moves) are delivered; every module of the phase now has its contract, its examples and its tests, and determinism across backends is what remains |
| N13 | Projects, packages, reproducibility | specified, not started |
| N14 | Own backend: Nava to x86-64 ELF without `cc` | in progress — N14a (the IR, the encoder, the ELF writer, the syscall runtime and a behavioural differential against the C backend), N14b (whole programs: units, symbols, relocations, sections, a static linker for multi-file programs, line tables, a symbolised trace, our own ELF reader and `nava run --backend native`) and N14c (arrays through one IR: literals, `array_zeros`, checked reads and writes, `len`, ownership as a `release` instruction, an `mmap` arena and the C runtime's three array traps) delivered; N14d (the C backend as a lowering of the shared IR: one representation, two backends, a three-way differential, and the `str`-comparison hole closed) delivered; N14e (`f64` and struct values through one IR: typed `add`/`cmp`, cells, struct literals and field writes, layouts in the module, a builtin as an instruction, SSE arithmetic and comparison on the native side and struct `typedef`s on the C side) delivered; N14f (text and numbers through one IR: `TextCompare`, and the eight builtins the runtime has — `bytes_len`, `byte_at`, `str_concat`, `str_slice`, `str_from_i64`, `to_i64`, `to_f64`, `read_i64` — implemented natively with the C runtime's words for every trap) delivered; N14g (enums and `match` through one IR: `variant`/`tag`/`payload` instructions, the enum layouts in the module, a `match` as a chain of tag comparisons against the checker's numbering, and an `unreachable` terminator for the block a finished choice leaves dead) delivered; N14h–N14i (slices and `String`/`Vec`, the collections, `defer` and `?`, and the shortest round-trip text of an `f64` — the one conversion that needs exact decimal arithmetic — so that `cgen.py`'s AST walk can be deleted) specified |
| N15 | Self-hosting and bootstrap | specified, not started |
| N16 | Zero dependencies, reproducible delivery | specified, not started |
| N17 | The language as a contract | specified, not started |
| N18 | Evidence for the claims | specified, not started |

## 5. Definition of done and non-goals

A phase is complete only when its implementation, documentation and test
gates are present and its results are recorded. New behaviour has both a
positive test and a negative control where practical. Performance claims
include the workload and uncertainty; model claims include the model, prompts,
tokens, failures and correctness. Planned files and commands are not current
features until they land.

Out of scope for this plan: GPU code generation/execution for Nava programs,
training a new neural model, a default networked package registry, threads and
concurrency, FFI to C or any other language, a tracing garbage collector, and any
blanket promise that Nava will outperform C/C++ on every workload or model.
Model-inference benchmarks may still use a GPU. These items may be evaluated
later against measured needs rather than treated as prerequisites for a useful
successor.

Two items that the first version of this section excluded are now **in** scope,
in §4A: self-hosting the compiler (N15) and a package manager (N13, offline and
content-addressed by default — a networked registry stays out). Reversing a
non-goal is a decision, not a detail: it is recorded here, it is justified by the
audit in §4A.2, and it carries the criteria in §4A.1 so that "the language is
independent" is a checkable statement with a host, a command and a run behind it
rather than a slogan. Until N16's bare-container evidence exists, the honest
description is: an independent specification with a dependent toolchain.
