# Nava Development Plan

## Status: PLANNED — N0–N9 not yet delivered

Nava 0.1 is a working, small compiler. It does **not** yet establish that a
language model generates Nava faster or more accurately than C or C++, or
that Nava binaries are generally faster. This plan addresses that gap while
expanding the language without silently changing its existing semantics.

This document answers one question:

> How can Nava become a useful language for model-generated programs, with
> correctness and end-to-end latency measured rather than inferred from short
> source files?

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
| Compiler | Lexer, parser, type checker and C11 generator in `nava_lang/`; `check`, `emit-c`, `build` and `run` commands | Python front end and external C compiler are required to build |
| Language | `i64`, `bool`, `str`, functions, conditions, `while`, checked arithmetic and integer I/O | No arrays, `for`, floating point, imports, file API or package system |
| Correctness | `python3 -m unittest discover -s tests -v`: 31 tests passed locally with `cc` and `c++` available | Some runtime tests skip when a compiler is absent; no CI gate currently proves the suite actually ran |
| Cross-language check | `python3 experiment/verify.py`: Nava and C passed 107 identical GCD cases | One algorithm, not a general language benchmark |
| Native performance | `bench/run.py` compares Nava with checked and unchecked C++ on one loop | One workload; checked C++ has similar arithmetic checks, not identical input/error behaviour |
| Model evaluation | `eval/prompt.py`, `eval/evaluate.py`, four public tasks and optional tokenizer counts | No model API calls, private held-out suite, measured model response time or measured pass rate |
| Execution safety | Optional `run --timeout`; evaluator has a per-test timeout | Neither is a sandbox; filesystem, network, memory, output and child processes are not isolated |

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
| N0 | Measurement rig and test integrity | None | Baseline is reproducible; missing or disabled tests fail the guard | Planned |
| N1 | Language specification and diagnostics | N0 | Grammar and diagnostics match the implementation, including negative cases | Planned |
| N2 | Isolated build and execution | N0 | Hostile programs cannot escape the documented limits; no silent fallback | Planned |
| N3 | Real model evaluation | N0, N1, N2 | Paired Nava/C/C++ results include full prompts, correctness and measured model usage | Planned |
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

- [ ] Record Python, OS, CPU, compiler versions, compiler flags, workloads and
      raw timing samples with every benchmark result. Separate process startup,
      compilation and native execution.
- [ ] Extend the evaluation harness to support C as well as existing Nava and
      C++ candidates, with identical task contracts and labelled arithmetic
      semantics.
- [ ] Add diverse public tasks with input bounds and independent reference
      outputs. Keep any genuinely held-out tests outside prompts and outside
      the public training/evaluation fixtures.
- [ ] Add a suite/fixture registry guard so an omitted, disabled or missing
      test is visible. CI must fail if its required C or C++ compiler is absent
      rather than treating skipped runtime tests as a passing full suite.
- [ ] Record benchmark distributions, warm-up policy, run order and failures;
      do not replace missing model data with source-length estimates.

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
baseline. C support in the evaluator and the guard are **planned**, not
present today.

---

### Phase N1 — Language specification and diagnostics

**Objective:** model authors and compiler users can rely on one written
contract and get actionable errors without a compiler traceback.

#### Tasks

- [ ] Write a versioned grammar and examples matching `lexer.py` and
      `parser.py`, including newlines, comments, string escapes and reserved
      names. Specify types, scoping, short-circuiting, return analysis, checked
      arithmetic and input/output behaviour.
- [ ] Add stable error categories and source ranges; offer machine-readable
      diagnostics alongside the existing human-readable form.
- [ ] Bound parser and compiler recursion and report malformed or excessively
      deep input as an ordinary diagnostic rather than a Python traceback.
- [ ] Add negative fixtures for type errors, integer boundaries, malformed
      UTF-8/escapes, missing names and invalid function signatures.

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
specification/negative fixtures in `tests/`.

---

### Phase N2 — Isolated build and execution

**Objective:** evaluating untrusted generated code is opt-in, bounded and
isolated; the existing timeout is not misrepresented as a sandbox.

#### Tasks

- [ ] Choose and document a supported isolation mechanism. Isolate **both**
      compilation and execution of untrusted C/C++ and generated Nava
      binaries, using an unprivileged worker and a minimal filesystem view.
- [ ] Restrict CPU time, wall time, memory, output size and process count;
      terminate the complete process group or container on failure or timeout.
- [ ] Deny network by default and limit readable/writable paths. Keep model
      credentials and unrelated host files outside the worker.
- [ ] Fail closed when the requested isolation backend is unavailable. If an
      explicitly unsafe local mode is retained, label it clearly and require
      an explicit opt-in. Document unsupported operating systems.
- [ ] Replace unbounded `capture_output` use for untrusted candidates with
      bounded output collection and clear failure diagnostics.

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
threat-model documentation, and adversarial integration tests. This is not
implemented by the current `--timeout` option.

---

### Phase N3 — Real model evaluation

**Objective:** determine whether Nava improves time to a correct result for a
specific model, rather than inferring it from handwritten programs.

#### Tasks

- [ ] Add an opt-in provider adapter for a user-selected model; retain the
      existing offline saved-response mode. Do not commit credentials, prompts
      containing private data or generated answers without explicit consent.
- [ ] Record exact model and inference settings, complete prompts, response
      and API-reported input/output token counts. Measure time to first token
      when supported and wall time to the final answer. Preserve missing values
      as missing.
- [ ] Compile and test Nava, C and C++ responses inside the N2 isolation
      boundary. Record compilation, first-attempt correctness, each retry and
      the time to a correct answer separately from native execution.
- [ ] Use the same task contract, a predeclared prompt policy, fixed sampling
      settings, randomized or alternated language order and a separate hidden
      test set. Count grammar and system instructions, not only source code.
- [ ] For an empirical report, use at least 10 varied tasks and 30 paired
      independent attempts per task and language, subject to the chosen
      provider's cost limits. Report raw samples, failures, medians and a
      stated uncertainty method; report smaller studies as exploratory.

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
