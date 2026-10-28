# Nava Independence Plan — phases N10–N18

This is the plan for the two claims `PLAN.md` does not make: that Nava is a
language in its own right, and that its toolchain stands on its own — no Python,
no C compiler, no libc.

**Status: N10 in progress — N10a (editions and structs) and N10b (enums and
`match`) delivered; N10c–N10e and N11–N18 specified, not started.** The
document is written so that each phase's gate is a command somebody can run and a
record somebody can check, in the same discipline as `PLAN.md`: a phase is done
when its gate has run and its evidence is recorded, and an unmeasured result stays
`unmeasured`.

The phases are dependency-ordered: §4 holds the table, the milestones and the
per-phase detail, §1 the six criteria that define the two claims, §2 the audit of
the current compiler that decides which language features the phases add, and §3
the decisions.

Phase N9 made Nava installable. It did not make it independent: `nava` is a Python
program that writes C and hands it to `cc`. This document reverses two items that
`PLAN.md` §5 put out of scope — self-hosting and a package manager — and says
exactly how "the language is really a language, and it stands on its own" will be
measured instead of asserted.

## 1. Six criteria, each with a probe

A claim of independence is only worth what its check is. These are the checks;
every one of them is a CI lane by the end of N18.

| # | Criterion | What "done" means — the probe | Phase |
|---|---|---|---|
| C1 | **A specification, not a description** | `LANGUAGE.md` is versioned, complete over the grammar it accepts (every EBNF production appears in the normative conformance set), and states the semantics that matter: evaluation order, overflow, bounds, aliasing, resource limits, and the absence of undefined behaviour. A language change without a spec change and a fixture in the same commit fails the guard. | N10, N17 |
| C2 | **The bootstrap criterion** | A compiler written in Nava compiles itself: stage2 (built by stage1) and stage3 (built by stage2) are byte-identical. This is the operative definition of "the language is complete enough", and it replaces guessing about which features a language "should" have. | N15 |
| C3 | **No external toolchain** | `nava` is one static x86-64 ELF that builds and runs a Nava program on a host with **no `python3`, no `cc`, no `ld`, no libc** — an empty container, kernel only. Checked by running, not by claiming. | N14, N16 |
| C4 | **An ecosystem, not three files** | A standard library with written contracts and doc-tests that execute in CI; packages with a content-addressed lockfile; a formatter; a REPL; generated documentation. | N12, N13, N17 |
| C5 | **Proof on real programs** | The compiler itself, plus at least three non-trivial programs (a JSON parser, a bytecode interpreter, a file-processing tool), built by the native toolchain and measured. Toy programs prove nothing. | N14, N18 |
| C6 | **Evidence discipline survives** | Each gate carries negative controls; `not-measured` never decays into `0`; no CI lane can be green while it compiled nothing (the property the CI fix already enforces for the study and A/B lanes). | every phase, N10–N18 |

C2 is the load-bearing one. "Enough language" stops being a matter of taste the
moment the language has to express a compiler.

## 2. What the compiler needs from the language — a measured audit

The requirement list below is not a wish list: it is an audit of the 5 395 lines,
13 modules and 174 functions under `nava_lang/`, which is the program Nava must be able to
express. Counts are of AST nodes in the current tree, re-measured at the last delivery
(N10b); the method reproduces with `ast.walk` over `nava_lang/*.py`, and
`tests/test_plan.py` recomputes the size and function count so this paragraph
cannot drift from the code.

| Python construct in the compiler | Count | What Nava needs | Phase |
|---|---|---|---|
| `class` (AST nodes, diagnostics, module records, type descriptors) | 53 | `struct` with named fields; `enum` with data-carrying variants; exhaustive `match` | N10 |
| `raise` / `try` | 208 / 47 | `Result<T, E>` with `?`, and `panic` reserved for broken invariants | N10 |
| dict literal, `.get`, `.items`, `.values`, `.setdefault` | 54 / 30 | `HashMap<K, V>` with iteration and a total `get` returning `Option<V>` | N11 |
| `.append`, `.extend`, `.pop`, `.sort` | 84 | `Vec<T>` with amortised growth, `pop -> Option<T>`, `sort` with a comparator | N11 |
| set literal, `.add` | 11 / 7 | `HashSet<T>` | N11 |
| f-strings and `str.format` | 292 | `fmt` with width/alignment/precision specifiers over `i64`, `f64`, `str` | N12 |
| `split`, `join`, `strip`, `startswith`, `replace`, `encode`, `decode` | 70 | `String` (growing UTF-8) and string methods on it and on `str` slices | N11, N12 |
| slice syntax `a[i:j]` | 17 | slice values `[T]` with bounds checks | N10 |
| `sorted(..., key=...)` | 3 | `sort` over a comparator; `sort_by_key` in the library, not in the language | N12 |
| generator expressions, list/set comprehensions | 36 / 20 / 4 | explicit `for` loops; laziness is **not** required | N10 (loops) |
| `lambda` | 1 | function pointers | N10 |
| file I/O, `argv`, environment | `cli.py`, `modules.py` | `fs`, `args` with the existing capability model | N12 |
| generators, `await`, threads, metaclasses, dynamic attribute lookup | 0 | nothing; Nava is not asked to acquire them | — |

Two consequences follow, and both are recorded as decisions below: Nava does
**not** need closures, laziness, exceptions or a garbage collector to express its
own compiler, and it *does* need structs, sum types, generic containers, strings
and slicing. The audit rows become the N10 checklist: a row is closed only when
a fixture proves the construct is expressible and the corresponding compiler
subsystem is later ported.

## 3. Decisions added by this section

### D7. Editions, not breaks

`LANGUAGE.md` is at version 1. N10 adds
`edition 2`: additive, opt-in per module (`edition 2;` at the top of a file),
with edition 1 frozen and every edition-1 program continuing to compile with
identical diagnostics. A language that breaks its own corpus while claiming to
mature is not independent, it is unreliable.

### D8. The bootstrap criterion decides feature scope

A feature is added to
edition 2 when the compiler-under-construction needs it, and not before. The
audit in §2 is the ledger; "it would be nice" is not an entry.

### D9. Arena first, GC only with evidence

The compiler leaks at will inside
one compilation and exits; the plan therefore uses an arena allocator with an
explicit lifetime and **no** tracing collector. If porting shows a cycle that
cannot be broken, that is a finding to record — and a separate decision — not a
silent reason to build a GC.

### D10. The C backend is a trampoline with a date

`nava_lang/cgen.py` stays a
supported backend until N16 passes, and no phase may regress it before then. From
N15 it prints a deprecation notice; at N16 it is removed in the same patch that
removes the `cc` dependency, with a `CHANGELOG.md` entry and an updated
`RELEASE.md`. The alternative — keeping two backends forever — is how a project
never becomes independent.

### D11. Independence is demonstrated on a bare host

Every C3-style claim is
produced by running in a container without the dependency in question. A
`which cc` check on a developer machine is not evidence.

### D12. The conformance set is normative

From N17, `conformance/` is the
document of record: a row of the specification without a conformance entry fails
the guard, and the two implementations must agree on every entry.

## 4. The phases

| Phase | Subject | Dependencies | Gate criterion | State |
|---|---|---|---|---|
| N10 | Structures, sum types, errors, generics | N1, N4, N5 | Edition 2 parses, checks and runs; the §2 audit is closed row by row; the Python oracle and the C backend agree | **In progress** — N10a and N10b delivered, N10c–N10e open |
| N11 | Memory, collections, strings | N10 | Collections and strings are safe under fuzz, leak detection and sanitizers; unsafe patterns are rejected, not tolerated | **Specified** |
| N12 | A standard library with contracts | N11 | Every public name has a contract and a test; documentation examples execute in CI | **Specified** |
| N13 | Projects, packages, reproducibility | N12 | A lockfile reproduces a build on another machine; a tampered package fails the build | **Specified** |
| N14 | Own backend: Nava to x86-64 ELF without `cc` | N10, N11 | Behaviour differential against the C backend on the whole corpus; builds and runs where `cc` and `ld` do not exist | **Specified** |
| N15 | Self-hosting and bootstrap | N10–N14 | A Nava-written compiler compiles itself; stage2 and stage3 are byte-identical; the suite passes under stage2 | **Specified** |
| N16 | Zero dependencies, reproducible delivery | N14, N15 | `nava` builds and runs programs in a kernel-only container; rebuilds are byte-identical; no network syscalls | **Specified** |
| N17 | The language as a contract | N12, N15 | The conformance set is normative and both implementations pass it; editions and deprecation are enforced by the guard | **Specified** |
| N18 | Evidence for the claims | all of the above | Every number traces to a recorded run; anything unmeasured stays `not-measured` | **Specified** |

Milestones, in the order a reader should check them: **M1** the language can
express a compiler (N10–N12); **M2** projects and packages (N13); **M3** no C
toolchain (N14); **M4** self-hosted (N15); **M5** no external dependency at all
(N16); **M6** a contract with users and models (N17); **M7** the claims are
measured (N18).

---

### Phase N10 — Structures, sum types, errors and generics

**Objective:** give the language the data-modelling power its own compiler
needs, as an additive edition, without touching edition 1.

**Tasks**

- `struct` with named fields, nesting, value and reference passing, and the
  borrow rules N4 defined for arrays extended to struct fields.
- `enum` with data-carrying variants; `match` with exhaustiveness checking and no
  fallthrough; `Option<T>` and `Result<T, E>` as library types, not keywords.
- `?` for error propagation, with a documented conversion rule.
- Slices `[T]` and range slicing `a[i..j]` with bounds checks.
- Monomorphised generics: `Vec<T>`, `HashMap<K, V>`, user-defined generic
  structs and functions; explicitly **no** traits, and no type-class inference —
  equality and hashing are built-in interfaces on the primitive types.
- Function pointers, `defer`, labelled loops, per-item module visibility (`pub`).
- `edition 2;` header, with edition 1 frozen.

**Test gate**

- `LANGUAGE.md` gains the edition-2 chapter: every construct with its grammar,
  semantics and at least one executable example; the EBNF and `eval/grammar.py`
  and the GBNF in `eval/gbnf.py` agree, as N7's conformance checker already
  enforces.
- At least 120 new fixtures (positive and negative) with exact diagnostics.
- The §2 audit table is closed row by row: each row names the fixture that
  proves the construct is expressible.
- Differential runs: the Python front end (oracle) and the C backend agree on
  every fixture, byte for byte on stdout and stderr.

**Negative controls:** a non-exhaustive `match` is rejected with a code and a
location; taking a reference to a temporary is rejected; a recursive `struct`
without indirection is rejected with a stated rule; an edition-1 file that uses
an edition-2 construct is rejected with the edition named.

**Deliverable:** the patch that lands edition 2, the spec chapter, the fixtures
and the closed audit.

**Increments.** N10 is delivered in five patches, because one patch that changes
the grammar, the checker, the code generator, the specification, the grammar
artefact and the guide at once cannot be reviewed. A phase is still done only
when the whole gate has run.

| Increment | Scope | State |
|---|---|---|
| N10a | `edition 2;`, `struct`, named construction, field read and write, copy semantics | **Delivered** (record below) |
| N10b | `enum` with data-carrying variants, `match`, exhaustiveness | **Delivered** (record below) |
| N10c | `Option<T>`, `Result<T, E>`, `?` | Open |
| N10d | Slices `[T]` and range slicing, arrays of structs | Open |
| N10e | Monomorphised generics, function pointers, `defer`, labelled loops, `pub` | Open |

#### Delivery record N10a (2026-10-28)

| Gate item | Probe | Result |
|---|---|---|
| An edition is a contract | `tests/test_structs.py`: `test_edition_one_is_unchanged`, `test_a_struct_needs_edition_two` | Without `edition 2;` a struct is refused with `E0372` and a hint that names the declaration; an edition-1 file compiles to the same C as before |
| Structures are values | `test_a_struct_is_copied_not_shared`, `test_a_struct_parameter_is_a_copy`, `test_nesting_is_copied_with_the_struct` | Running programs print `1 / 99 / 2 / 77` for copy, field write and nesting: a copy does not share storage |
| The rules are checked | 15 frontend tests covering `E0370`–`E0381` and `E0311`, `E0312`, `E0320`, `E0323`, `E0351` | Each code asserted with its location; the fixtures `tests/fixtures/negative/struct_*.nv` and `edition_*.nv` (13 new) fail with exactly the recorded diagnostic |
| The specification moved with the code | `LANGUAGE.md` edition-2 chapter: grammar, declaration, construction, copy, and the documented limits | The chapter is normative: it names what a struct may contain and what it may not, and why |
| The grammar artefact agrees | `eval/grammar.py`, `python3 -m unittest tests.test_grammar` | The token grammar and the GBNF accept every new fixture; `E0203` was re-classified as semantic, the array-shape rule became its own syntactic code `E0206`, and `test_every_corpus_code_is_classified` passes |
| The guide and its measurements moved with the language | `guides/nava-1.md` (edition-2 section, digest `134d81b0…`), `guides/examples/structs.nv`, `eval/measurements/overhead-*.json` re-measured with `tiktoken` | Guide digest re-pinned in `guides/manifest.json`; canonical example compiled and run; overhead re-measured, not estimated: `cl100k_base` 19 864 prompt tokens, `o200k_base` 19 941 for the full task set |
| No skipped tests | `python3 tests/registry.py`, `python3 -m unittest discover -s tests -q` | registry `OK: 385 registered tests, 11 tasks with 76 cases, 127 spec fixtures, 105 diagnostic codes`; suite **455 tests OK** |

Negative controls: a non-exhaustive field list (`E0376`), a duplicated field
(`E0375`), an unknown field (`E0374`), a recursive struct (`E0377`), a `str`
field (`E0378`), a duplicate struct (`E0380`), positional construction
(`E0381`), named arguments to a function (`E0381`), member access on a scalar
(`E0379`) and printing a struct (`E0323`) each fail with their own code — none of
them is accepted and none of them silently degrades to a neighbouring error.

#### Delivery record N10b (2026-10-28)

| Gate item | Probe | Result |
|---|---|---|
| An enum is a closed set | `tests/test_enums.py`: `test_declaration_rules`, `test_a_struct_and_an_enum_cannot_share_a_name` | Duplicate enum `E0390`, duplicate variant `E0391`, an enum with no variants `E0396`, a `str` payload `E0399`, an unknown payload type `E0203`; `struct` and `enum` names collide in one namespace |
| A variant carries what it says | fixtures `enums_basic.nv`, `enums_state.nv` and `EnumRuntimeTests` | Running programs print `0 / 12 / 12` for the three variants of `Shape` and `1 / 2 / 15 / -1` for a command enum driving a struct |
| Exhaustiveness is proved | `test_match_rules` plus `tests/fixtures/negative/match_not_exhaustive.nv` | A missing arm is `E0396` and the message names the missing variants (`does not cover Shape::Circle, Shape::Rect`); duplicate arm `E0397`, arm after `_` `E0398`, unqualified pattern `E0392`, wrong binding count `E0394`, `match` on `i64` `E0395` |
| The semantics are observable, so they are tested | `test_the_scrutinee_is_evaluated_once`, `test_bindings_are_copies`, `test_arms_are_tried_in_source_order_and_run_once` | The scrutinee is evaluated once (one printed line), a binding is a copy (`99` then `5`), arms run in source order and once |
| `match` counts as a return | `test_an_exhaustive_match_counts_as_a_return` | `fn area(s: Shape) -> i64` with a `match` and no trailing `return` compiles; the same function with a non-returning arm is `E0306` |
| The specification moved with the code | `LANGUAGE.md` edition-2 section "Enums and `match`" | Grammar, construction, patterns, exhaustiveness and the three observable semantics are written down, together with what edition 2 still does not have |
| The grammar artefact agrees | `eval/grammar.py`, `tests/test_grammar.py` | Enum declarations, variant values with and without fields, `=>` arms and match statements are in both the token grammar and the GBNF; `E0390`–`E0399` are classified as semantic |
| The guide and its measurements moved | `guides/nava-1.md` (digest `8ac08098…`), `guides/examples/enums.nv`, `eval/measurements/overhead-*.json` | New canonical example compiled and run; overhead re-measured with `tiktoken`: `cl100k_base` 2 185 and `o200k_base` 2 191 prompt tokens for the `gcd` task, guide 6 943 bytes |
| No skipped tests | `python3 tests/registry.py`, `python3 -m unittest discover -s tests -q` | registry `OK: 399 registered tests, 11 type tasks with 76 cases, 140 spec fixtures, 115 diagnostic codes`; suite **469 tests OK** |

Negative controls: eleven new fixtures cover every new code, and each asserts the
code, the location and the message. The one that matters for the language claim
is `match_not_exhaustive.nv`: adding a variant to an enum breaks every `match`
that does not cover it, at compile time, with the variant named.

Deliberately not done in N10b: `match` as an expression, guards, literal and
range patterns, matching on `i64` or `str`, `Option`/`Result`, `?`, and generic
enums. Those are N10c and later.
Deliberately not done in N10a, and stated so that it cannot be assumed: no sum
types, no `match`, no `Option`/`Result`, no slices, no generics, no `defer`, no
function pointers, no `pub`, no `str` or array fields, no struct equality and no
printing a struct. A struct is a value with named fields and nothing else.

---

### Phase N11 — Memory, collections and strings

**Objective:** make the containers and strings the compiler needs, with safety
that is checked rather than assumed.

**Tasks**

- `Vec<T>` with amortised growth, `push`, `pop -> Option<T>`, `insert`,
  `remove`, iteration; `HashMap<K, V>` with iteration and a total `get`;
  `HashSet<T>`; a deterministic hash so that records and builds are
  reproducible.
- `String` as a growing UTF-8 sequence with documented boundary rules, and the
  methods the audit lists: `split`, `join`, `strip`, `startswith`, `replace`,
  byte and character views.
- Arena allocator with an explicit lifetime (D9), allocation accounting, and a
  defined out-of-memory behaviour that is the same in every backend.
- Bounds, overflow, initialisation and aliasing checks; the documented statement
  that a well-typed program has no undefined behaviour, and the test that shows
  it.

**Test gate**

- Property and fuzz tests over at least 10^5 randomised operations per
  collection, comparing against the Python oracle.
- Leak detection over the whole fixture corpus, with the arena's accounting as
  the second, independent witness.
- AddressSanitizer and UndefinedBehaviorSanitizer clean on the C backend for the
  full corpus (the N0 rig already builds with sanitizers).
- Recorded allocation and time numbers for `HashMap` and `Vec` growth, with the
  workload and uncertainty, in `BENCHMARK.md`.

**Negative controls:** a double release, a use after release, an off-by-one
write and a hash-map rehash-during-iteration each fail the check that is supposed
to catch them — a safety feature whose negative control passes is not a feature.

**Deliverable:** collections, strings, allocator, sanitizer-clean corpus and the
recorded numbers.

---

### Phase N12 — A standard library with contracts

**Objective:** replace "three files in `nava_lang/stdlib/`" with a library whose
promises are written down and executed.

**Tasks**

- Modules: `fmt`, `io`, `fs`, `args`, `collections`, `text`, `math`, `sort`,
  `test`, `time`, on top of the capability model N6 established.
- For every public name: a contract (preconditions, postconditions, error
  cases, complexity) in the module's documentation block.
- `nava doc` generates reference documentation from those contracts; examples
  inside them are compiled and executed by the suite.
- `test` module: an assertion and test-runner facility inside the language, so
  Nava programs can test themselves without the Python suite.

**Test gate**

- 100 % of public names carry a contract and at least one test; the guard fails
  on an undocumented or untested public name (the same shape as the existing
  diagnostic-code and fixture guards).
- Every documentation example builds and runs in CI; an example that stops
  compiling fails the lane.
- Determinism: functions documented as pure produce identical results across
  repeated runs and across backends.

**Negative controls:** a public function with no contract fails the guard; a
documentation example that does not compile fails the lane; a documented error
case that cannot be triggered fails the guard.

**Deliverable:** the library, its contracts, generated documentation and the
self-testing facility.

---

### Phase N13 — Projects, packages and reproducibility

**Objective:** a Nava program can be a project with dependencies, offline by
default, and rebuilt identically elsewhere.

**Tasks**

- `nava.toml`: name, edition, dependencies with semantic-version constraints,
  build targets.
- A content-addressed lockfile; vendoring; `--offline` as the default and
  network access as an explicit, recorded opt-in.
- Package identity by digest, not by name; a substituted or corrupted package
  fails the build with a named error.
- Deterministic builds: sorted iteration everywhere it reaches output, a fixed
  timestamp source (`SOURCE_DATE_EPOCH`), recorded toolchain identity.

**Test gate**

- A project with two dependencies builds from its lockfile in a second
  checkout, on a clean host, and the binary is byte-identical.
- Tamper tests: one flipped byte in a vendored package, a wrong digest, a
  version outside the constraint — each fails with its own diagnostic.
- The dependency graph is printed and recorded; no transitive dependency can
  appear that the lockfile does not name.

**Negative controls:** a lockfile edited by hand without the digest fails; a
build with a different toolchain records the difference rather than silently
producing a second hash; a package that shadows a standard-library module is
rejected.

**Deliverable:** the manifest, the lockfile, the vendoring story and the
reproducibility records.

---

### Phase N14 — Own backend: Nava to x86-64 ELF without `cc`

**Objective:** remove the C compiler from the path. Two sub-milestones, because
this is the largest single piece of engineering in the plan.

**Tasks**

*N14a — one translation unit*

- A linear, SSA-shaped IR between the checker and the backends; `cgen.py`
  becomes one lowering of it, the new backend the second (this keeps the
  differential meaningful).
- x86-64 code generation, an instruction encoder written in the project, an ELF
  writer, and a syscall-only runtime: `_start`, `mmap`-backed arena, `write`,
  `read`, `exit`, panic with a symbolised trace.
- Linux x86-64 only, and stated as such: macOS and Windows leave the matrix for
  the native backend and stay "expected, not exercised" for the C backend.

**N14b — whole programs**

- Symbols, relocations, sections, a static linker for multi-file programs, line
  tables for diagnostics and traces, and a read-only `readelf`-equivalent of our
  own used by the tests.

**Test gate**

- Behavioural differential against the C backend over the entire corpus: exit
  code, stdout, stderr and diagnostic text identical.
- Builds and runs on a host where `cc` and `ld` do not exist (D11).
- `ldd` reports "not a dynamic executable"; a syscall whitelist is checked under
  `strace`, including that nothing opens a socket.
- Repeated builds are byte-identical.

**Negative controls:** a program that overflows `i64` behaves identically on both
backends; a deliberately truncated ELF is rejected by our own reader; an attempt
to link a foreign object file is refused with a named error; a program whose
panic message is checked still prints the same trace.

**Deliverable:** the IR, the backend, the encoder, the linker, the runtime and
the differential records.

---

### Phase N15 — Self-hosting and bootstrap

**Objective:** Nava compiles Nava.

**Tasks**

- Port the compiler to Nava, subsystem by subsystem, in the order
  lexer → parser → checker → modules → IR → backends, each subsystem landing
  with its parity evidence against the Python implementation on the full fixture
  corpus.
- Bootstrap chain, each stage recorded with its SHA-256: stage0 (the Python
  compiler) builds stage1 (the Nava compiler, via the C backend); stage1 builds
  stage2 (the Nava compiler, via the native backend); stage2 builds stage3.
- The Python compiler is frozen as the oracle at the point stage2 passes the
  suite, and is shipped no longer.

**Test gate**

- stage2 and stage3 are byte-identical (C2).
- The full suite and `tests/registry.py` pass with stage2 as the compiler, with
  no skipped tests and the same negative controls.
- Every fixture produces identical diagnostics — code, location, message, hint —
  under stage2 and under the frozen Python oracle.
- Compiler build time and binary size are recorded with the workload; the
  comparison against `nava_lang/`'s Python numbers is a record, not a claim of
  victory.

**Negative controls:** a compiler built from a deliberately broken source is
rejected by the fixpoint check; a divergence between stage2 and the oracle is
reported as a failure rather than "an acceptable difference"; a stage that fails
to build leaves the previous stage's hashes intact and the lane red.

**Deliverable:** the Nava compiler, the bootstrap script, the recorded hashes and
the fixpoint evidence.

---

### Phase N16 — Zero dependencies, reproducible delivery

**Objective:** C3, demonstrated.

**Tasks**

- One static `nava` binary; installation is a copy.
- `check`, `build` and `run` work in a kernel-only container (no shell, no
  libc, no `cc`, no `python3`); the isolation logic of N2 is reimplemented on
  syscalls and re-tested there.
- A documented chain of trust: which binary is trusted, why, and the hashes of
  every stage (the trusting-trust limit is stated, not hidden).
- Reproducible builds: byte-identical rebuild from the same sources and
  `SOURCE_DATE_EPOCH`; an SBOM; a security review of the syscall surface, the
  sandbox and the linker.
- The C backend is removed here (D10): `cgen.py`, its tests and its
  documentation go with the `cc` dependency, and `CHANGELOG.md` says so.

**Test gate**

- A program is written, compiled, sandboxed and run inside an empty container,
  and its output is recorded.
- Rebuild from scratch is byte-identical; a one-byte change in the source changes
  exactly the expected bytes.
- `strace` shows no sockets; the sandbox's own negative controls from N2 still
  fail as they should under the new runtime.
- `RELEASE.md` and the support matrix describe the single binary and the single
  supported platform honestly.

**Negative controls:** a container that still has `cc` is *not* accepted as
evidence of C3 — the check asserts the absence of the dependency before it runs
the build; a build with a network syscall is rejected; a non-reproducible build
fails the lane instead of being noted in prose.

**Deliverable:** the static binary, the bare-host evidence, the chain of trust
and the removal of the C backend.

---

### Phase N17 — The language as a contract

**Objective:** C1, C4 and the part of "a language" that is about everyone who is
not the author.

**Tasks**

- `conformance/`: the normative set, one entry per normative statement, keyed to
  a specification anchor; the specification names its entries and the entries
  name the specification.
- Editions and deprecation: an edition is frozen when released; removal requires
  a policy, a warning period and a migration note.
- `nava fmt` (idempotent, with the idempotence as a test), `nava repl`, and a
  minimal language server for diagnostics and completion — the tools a user
  notices the absence of first.
- Migration: edition 1 to edition 2 tooling that rewrites what can be rewritten
  mechanically and refuses, with a reason, what cannot.

**Test gate**

- Both implementations (the frozen Python oracle and the self-hosted compiler)
  pass the conformance set; a divergence is a release blocker.
- Every normative statement in `LANGUAGE.md` links to a conformance entry, and
  the guard fails on a statement without one.
- `fmt` is idempotent over the whole corpus, and a format that changes meaning is
  rejected by the parser afterwards.
- A REPL session referencing only standard-library modules replays identically.

**Negative controls:** deleting a conformance entry fails the guard; a
specification edit without an entry fails the guard; a formatter that changes
semantics fails the corpus differential.

**Deliverable:** the conformance set, the edition and deprecation policy, the
formatter, the REPL, the language server and the migration tool.

---

### Phase N18 — Evidence for the claims

**Objective:** the project's own thesis — that a model writes Nava quickly and
correctly — re-measured on the independent toolchain rather than on the Python
prototype.

**Tasks**

- Re-run the N3 study, the N7 A/B and the N8 benchmarks against the native
  toolchain: compiler latency, binary run time, time to a correct answer.
- Update `guides/nava-1.md` for edition 2 and re-pin its digest; regenerate the
  GBNF and the conformance checker so constrained generation still matches the
  grammar it claims to constrain.
- Keep the `not-measured` discipline: a lane without a provider records that, and
  no number is inferred from a neighbouring one.

**Test gate**

- Every number in `BENCHMARK.md`, `eval/measurements/` and the phase records
  traces to a run with a hash, a command and an environment; a number that cannot
  be reproduced is removed, not annotated.
- Each CI lane asserts that it compiled and tested what it claims (the property
  the CI fix already enforces).
- Nothing in the release notes states a comparison the records do not contain.

**Negative controls:** a record whose attempt count is zero fails its lane; a
record claiming `measured` without model call records fails the checker.

**Deliverable:** the re-measured records, the regenerated guide artefacts and an
honest summary that separates measured from unmeasured.

---

## 5. Risks, and what this plan refuses to promise

| Risk | Why it is real | Mitigation written into the plan |
|---|---|---|
| **R1. N14 and N15 are simply large** | The current compiler is 4 394 Python lines and 141 functions; the port is an estimate, not a measurement, and will grow. | Two milestones inside N14; the port proceeds subsystem by subsystem with parity evidence; no date is promised, only gates. |
| **R2. The native backend will be slower than C at first** | A first-cut code generator with no register allocator loses to `gcc -O2`. | The gate is behavioural equivalence, not speed; regressions are recorded in `BENCHMARK.md` rather than hidden; optimisation becomes its own phase if the numbers justify it. |
| **R3. No garbage collector may not be enough** | Python's compiler relies on reference counting to reclaim cycles that an arena would retain. | D9: arena first, and a recorded finding if a cycle cannot be broken; a collector would be a separate, measured decision. |
| **R4. A hand-written linker is a new class of bug** | Relocations and sections fail silently by producing a plausible binary. | Differential against the C backend, our own reader as a second witness, an external `readelf`/`file` as a third, and a fuzz corpus of malformed ELF inputs. |
| **R5. The syscall runtime is the security surface** | N2's isolation guarantee now depends on code that did not exist before. | N2's negative controls are re-run under the new runtime; the security review is a gate item in N16, not a paragraph in `SECURITY.md`. |
| **R6. New features can make generation worse** | Edition 2 adds syntax a model must learn; the project's whole point is that a model writes the language. | Every phase that changes the language regenerates the guide, the digest, the grammar and the GBNF, and the study harness measures on the current edition. |

Still out of scope, stated so that it is not smuggled in later: macOS and
Windows for the native backend; GPU execution; a networked package registry as a
default; threads and concurrency; FFI to C or any other language; a tracing
garbage collector; training a model. Each would be a new plan with its own gates,
not an appendix to this one.

"Independent" will be reported in exactly the terms of §1: which criteria
passed, on which host, in which run. Until N16's container evidence exists, Nava
is a language with an independent *specification* and a dependent *toolchain*,
and the plan says so.
