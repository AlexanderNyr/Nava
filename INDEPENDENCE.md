# Nava Independence Plan — phases N10–N18

This is the plan for the two claims `PLAN.md` does not make: that Nava is a
language in its own right, and that its toolchain stands on its own — no Python,
no C compiler, no libc.

**Status: N10 delivered — N10a (editions and structs), N10b (enums and
`match`), N10c (`Option`/`Result` and `?`), N10d (arrays of structs and slices),
N10e (`defer`, labelled loops, `pub`), N10f (monomorphised generics) and N10g
(function values and pointers) are done. N11 is in progress: N11a (strings and
enums as values, arrays of every value), N11b (owning fields, with the copy and
release rules they need), N11c (`Vec(T)`, the growing vector), N11d (`String`, the `str_*` text functions
and the arena), N11e (moves, and the arena region a frame gets back), N11f (a `Vec` or a `String`
as a field, a payload or a type argument) and N11g (an element that owns storage)
are delivered, and N12–N18 are not started.** The
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

The requirement list below is not a wish list: it is an audit of the 9 019 lines,
13 modules and 282 functions under `nava_lang/`, which is the program Nava must be
able to express. Counts are of AST nodes in the current tree, re-measured at the
last delivery (N11g) with `python3 tools/audit_compiler_needs.py`, which prints
this table and states its own definitions; `tests/test_plan.py` recomputes the
size and the function count so this paragraph cannot drift from the code.

| Python construct in the compiler | Count | What Nava needs | Phase |
|---|---|---|---|
| `class` (AST nodes, diagnostics, module records, type descriptors) | 56 | `struct` with named fields; `enum` with data-carrying variants; exhaustive `match` | N10 |
| `raise` / `try` | 287 / 55 | `Result(T, E)` with `?`, and `panic` reserved for broken invariants | N10 |
| dict literal, `.get`, `.items`, `.values`, `.setdefault`, `.keys` | 73 / 63 | `HashMap(K, V)` with iteration and a total `get` returning `Option<V>` | N11 |
| `.append`, `.extend`, `.pop`, `.sort`, `.insert`, `.remove` | 125 | `Vec<T>` with amortised growth, `pop -> Option<T>`, `sort` with a comparator | N11 |
| set literal, `.add`, `.discard`, `.update` | 16 / 14 | `HashSet<T>` | N11 |
| f-strings and `str.format` | 519 | `fmt` with width/alignment/precision specifiers over `i64`, `f64`, `str` | N12 |
| `split`, `join`, `strip`, `startswith`, `endswith`, `replace`, `encode`, `decode` | 103 | `String` (growing UTF-8) and string methods on it and on `str` slices | N11, N12 |
| slice syntax `a[i:j]` | 24 | slice values `[T]` with bounds checks | N10 (closed by N10d) |
| `sorted(...)`, of which `key=` | 5 / 1 | `sort` over a comparator; `sort_by_key` in the library, not in the language | N12 |
| generator expressions, list/set comprehensions | 57 / 40 / 6 | explicit `for` loops; laziness is **not** required | N10 (loops) |
| `lambda` | 2 | a function used as a value: `fn(i64) -> i64` and a call through a variable (N10g; there is still no closure, and the audit row is closed because a call-back needs no `lambda`) | N10 |
| file I/O, `argv`, environment | `cli.py`, `modules.py` | `fs`, `args` with the existing capability model | N12 |
| generators, `await`, async functions | 0 | nothing; Nava is not asked to acquire them | — |

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
| N10 | Structures, sum types, errors, generics | N1, N4, N5 | Edition 2 parses, checks and runs; the §2 audit is closed row by row; the Python oracle and the C backend agree | **Delivered** — N10a–N10g; the §2 audit is closed row by row |
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

**Increments.** N10 is delivered in seven patches, because one patch that changes
the grammar, the checker, the code generator, the specification, the grammar
artefact and the guide at once cannot be reviewed. A phase is still done only
when the whole gate has run.

| Increment | Scope | State |
|---|---|---|
| N10a | `edition 2;`, `struct`, named construction, field read and write, copy semantics | **Delivered** (record below) |
| N10b | `enum` with data-carrying variants, `match`, exhaustiveness | **Delivered** (record below) |
| N10c | `Option(T)`, `Result(T, E)`, `?` | **Delivered** (record below) |
| N10d | Slices `[T]` and range slicing, arrays of structs | **Delivered** (record below) |
| N10e | `defer`, labelled loops, `pub` | **Delivered** (record below) |
| N10f | Monomorphised generics: type parameters on `struct`, `enum` and `fn`, instantiation, inference | **Delivered** (record below) |
| N10g | Function pointers: `fn(T) -> U` as a type, a function as a value, a call through a variable | **Delivered** (record below) |

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

#### Delivery record N10c (2026-10-28)

| Gate item | Probe | Result |
|---|---|---|
| A failure is forwarded, never converted | `tests/test_results.py`: `test_propagation_rules` | `1?` is `E0410`; `?` in a `fn main()` is `E0411` and the message says the function returns void; `Result(bool, i64)` propagating into a function that fails with `str` is `E0411` `forwards i64, but this function fails with str` |
| A failure really returns early | `ResultRuntimeTests.test_a_failure_returns_early_and_reaches_the_caller`, `test_a_chain_stops_at_the_first_failure` | A `print` after `?` does not run on the failure path (`8` and the forwarded message `not positive`, no `1000`); a two-step chain stops inside the second `?` — the program prints `1 2 3 3 1 2 too big`, so `3` never ran |
| The success type may differ | `test_the_success_type_may_differ_from_the_callers`, `test_the_success_value_is_bound_with_its_own_type` | `Result(bool, str)` inside `Result(i64, str)`: compiles, `flag` is a `bool`, and the program prints `1` then `0` |
| `None` is a failure like any other | `test_an_option_propagates_without_a_failure_type`, `test_a_none_propagates_like_an_error` | `Option(bool)` propagates into `Option(i64)` (there is no failure type to compare) and the run prints `10` then `-1` |
| The prelude enums are ordinary enums | `test_the_prelude_enums_are_ordinary_enums` | Printing one is `E0323`; a missing arm is `E0396` `match on Result(i64, str) does not cover Result(i64, str)::Err` — the message names the instantiation, not the bare enum |
| Instantiations are distinct types | `test_two_instances_of_one_prelude_enum_coexist`, `test_a_pattern_must_name_the_same_instantiation` | `Result(i64, str)` and `Result(str, str)` in one module compile and print `3` and `hi`; a pattern naming another instantiation is `E0395` `the value is Result(i64, str)` |
| The shape rules are syntax, the type rules are semantics | `tests/test_grammar.py` `GRAMMAR_ACCEPTS` | `E0412` (arity) and `E0415` (a value as a type argument) accept at the grammar level and are rejected by the parser's symbol pass; `E0413`/`E0414` stay classified with the other semantic codes, so the corpus conformance test still proves the two layers agree |
| The specification moved with the code | `LANGUAGE.md` edition-2 section "`Option`, `Result` and `?`" | The three `?` rules, the parenthesised type arguments and the non-goals are normative; six new diagnostic rows (`E0410`–`E0415`) are documented, and `tests/test_spec.py` fails if a registered code has no row |
| The guide and its measurements moved | `guides/nava-1.md` (digest `84eb3c87…`, 8 238 bytes), `guides/examples/results.nv`, `eval/measurements/overhead-*.json` | New canonical example compiled and run (`8` and `not positive`); overhead re-measured with `tiktoken`, not estimated: `cl100k_base` 2 561 and `o200k_base` 2 567 prompt tokens for the `gcd` task; `guides/examples.json` gained the `results` case |
| No skipped tests | `python3 tests/registry.py`, `python3 -m unittest discover -s tests -q` | registry `OK: 413 registered tests, 11 tasks with 76 cases, 150 spec fixtures, 121 diagnostic codes`; suite **483 tests OK**; on a host without namespaces the isolation check reports `rlimits` and fails honestly rather than skipping |

Negative controls: eight new fixtures — `E0410` (`propagate_not_option.nv`),
`E0411` twice (`propagate_in_void.nv`, `propagate_failure_mismatch.nv`), `E0412`
(`result_wrong_arity.nv`), `E0413` (`propagate_outside_let.nv`), `E0414`
(`generic_user_type.nv`), `E0415` (`result_not_a_type_arg.nv`) and a mismatched
pattern instantiation (`propagate_pattern_mismatch.nv`, `E0395`) — each fails with exactly
its own code, location and message. The one that carries the language claim is
`propagate_failure_mismatch.nv`: a `Result` whose failure type differs is
rejected at compile time, so the caller can never receive an error it did not
declare.

#### Delivery record N10d (2026-10-28)

| Gate item | Probe | Result |
|---|---|---|
| An array of structs copies values | `tests/test_slices.py`: `test_a_struct_element_read_is_a_copy`, `test_an_element_write_replaces_the_whole_struct`, `test_zeroed_struct_arrays_are_all_zero` | `let p = points[0]; p.x = 99` prints `99` then `1` — the element is untouched; `points[1] = p` prints `7` and `8`; `array_zeros(3)` of a struct prints `3`, `0`, `0` |
| A slice is a view, not a copy | `test_a_slice_is_a_view_and_not_a_copy`, `test_a_slice_composes` | `points[0..2]` has `len` 2 while the array still has `len` 2, and `a[1..3][1..2]` (written as two bindings) reads the original element |
| The bounds are checked, and the two failures are distinguishable | `test_a_bad_slice_start_is_reported_as_a_start`, `test_a_bad_slice_end_is_reported_as_an_end` | `slice start -1 is out of range for length 3` and `slice start 4 …`; `slice end 1 is out of range for start 2 and length 3`, `slice end 4 …`, and `slice end 1 … length 0` for an empty array — all exit 1 with no value printed |
| The bounds are evaluated left to right | `test_the_bounds_are_evaluated_left_to_right` | A program that prints from both bound expressions prints `1`, then `2`: the generated C materialises both into temporaries instead of leaving the order to the compiler |
| A borrowed view owns nothing | `test_a_slice_does_not_release_the_storage_it_views` | A `-DNAVA_TRACK_ALLOCS` build of a program with a live slice reports `nava: live arrays at exit: 0`: the array was released once and the view was not released at all |
| A view sees the owner's write, and a slice can be iterated | `test_a_view_sees_a_write_through_the_owning_array`, `test_a_for_loop_can_iterate_a_slice` | Writing `points[1] = changed` after binding a view makes `view[1].x` print `99`; `for point in points[mark(1)..mark(3)]` prints the two bounds once and then `8`, so the header is evaluated once and not per turn |
| The read-only rules are checked | `test_a_slice_is_read_only`, `test_a_slice_cannot_become_a_mutable_borrow`, `test_a_slice_needs_edition_two` | Writing through a slice is `E0422`, passing one to a `mut [T]` parameter is `E0423`, and a slice in an edition-1 file is `E0372` with the edition named |
| The element-type rule is split where it can be decided | `test_an_array_of_enums_is_refused_for_now`, `test_an_unknown_element_name_is_reported_as_an_unknown_type`, `tests/test_grammar.py` | `[Shape]` is `E0424` (an enum has no storage until N11), `[Missing]` is `E0203`, and `[f64]`, `[void]`, `[[i64]]` stay `E0206` — a shape no symbol table could allow. The grammar's element terminal excludes the reserved type words, so the syntax-versus-semantics boundary is the same in `eval/grammar.py` and in the compiler |
| The grammar artefact agrees | `eval/grammar.py`, `python3 -m unittest tests.test_grammar` | `[Point]` (an element name) and `a[i..j]` (an element access with a range) are in both the token grammar and the GBNF; `E0420`–`E0424` are classified as semantic, so the corpus conformance test still proves both layers agree on every fixture |
| The specification moved with the code | `LANGUAGE.md` edition-2 section "Arrays of structs and slices" | Element types, value semantics, the two runtime messages, the four slice rules and the non-goals are normative; five new diagnostic rows (`E0420`–`E0424`) are documented; the stale sentence that said `Option`/`Result` did not exist (left over from N10b) is corrected |
| The guide and its measurements moved | `guides/nava-1.md` (digest `dd35c0c9…`, 9 778 bytes), `guides/examples/slices.nv`, `eval/measurements/overhead-*.json` | New canonical example compiled and run (`10`, `4`, `1`, `1`, `2`, `30`); overhead re-measured with `tiktoken`, not estimated: `cl100k_base` 3 038 and `o200k_base` 3 045 prompt tokens for the `gcd` task; `guides/examples.json` gained the `slices` case |
| No skipped tests | `python3 tests/registry.py`, `python3 -m unittest discover -s tests -q` | registry `OK: 432 registered tests, 11 tasks with 76 cases, 157 spec fixtures, 126 diagnostic codes`; suite **502 tests OK**; the allocation instrumentation test builds its own binary instead of skipping when a flag is unavailable |

Negative controls: five new fixtures — `slice_bound_type.nv` (`E0420`),
`slice_not_an_array.nv` (`E0421`), `slice_read_only.nv` (`E0422`),
`slice_mut_argument.nv` (`E0423`) and `array_of_enum.nv` (`E0424`) — each
asserts its own code, location and message, plus two runtime controls that
assert the exact `slice start`/`slice end` messages and that a failed slice
prints nothing. The one that carries the language claim is
`slice_mut_argument.nv`: a view can be read through and passed on, but it can
never become the writable borrower, so the exclusivity rule from N4 survives
the first construct that hands out a view.

**Increments of N11.** The phase is delivered in seven patches, for the same
reason N10 was: the memory rules, the collections, the string type, the move
rules, the storage rules and the element rules each change the checker, the code
generator, the specification and the guide.

| Increment | Scope | State |
|---|---|---|
| N11a | `str` as a struct field and a variant payload; arrays of enums; `array_zeros` restricted to element types with a zero value | **Delivered** (record below) |
| N11b | Owning fields: an array field and an array payload, with the copy and release rules they need | **Delivered** (record below) |
| N11c | `Vec(T)` with amortised growth, `pop -> Option<T>`, iteration, and the owner/release accounting it needs | **Delivered** (record below) |
| N11d | `String` (growing UTF-8) with the audit's methods and the **arena allocator with its accounting**, deferred from N11c for the reason that record gives; `HashMap`/`HashSet` moved to N12 | **Delivered** (record below) |
| N11e | **Moves**: an owning value crosses a frame boundary by transfer — out of a function as its return value, between bindings, and through an assignment — with use-after-move refused; and the **arena regions** that moves make sound, since a frame can now be told what it may reclaim | **Delivered** (record below) |
| N11f | **Storage**: a `Vec(T)` or a `String` as a struct field, a variant payload and a type argument, held by a **deep copy** so the holder owns its own buffer; the element rule stays a refusal, and moves it to N11g | **Delivered** (record below) |
| N11g | **Elements**: an element of an array or a vector may own storage — copied in, copied out on every read, released with the container — with `E0426`/`E0427` retired and nesting left as a shape rule | **Delivered** (record below) |

`HashMap` and `HashSet` are the one item of N11 that moved: the audit's dict and
set rows are compiler bookkeeping, and the collections belong with the standard
library and the package work of N12 rather than with the memory model. The move
is recorded here so that "N11 delivered" does not quietly mean "N11 minus a
collection type".


#### Delivery record N11g (2026-10-29)

| Gate item | Probe | Result |
|---|---|---|
| An array of owning elements copies the value in | `tests/test_elements.py`: `test_an_array_of_strings_copies_elements_in_and_releases_them` | `[String]` generates `result.data[at] = nv_string_copy(items[at])` in `_init` and `nv_string_free(&array->data[at])` in `_free` |
| A vector copies in, releases the slot and copies on the way out | `test_a_vector_of_strings_copies_an_element_in_on_push_and_set` | the generated `push`, `set` and `copy` each call `nv_string_copy`; `set` releases the slot before the copy, and `_free` walks the elements |
| A read is a copy that someone releases | `test_a_read_of_an_element_is_a_copy_that_the_statement_releases`, `test_a_loop_variable_is_a_copy_released_with_its_turn` | `xs[1]` becomes `nv_string_copy(nv_arr_String_get(...))` released in the same statement, and the loop variable is a copy released inside the loop body, before the array's own release |
| A slot is released before it is overwritten | `test_a_slot_is_released_before_it_is_overwritten` | in the generated `nv_arr_String_set` the release precedes the copy, which is the order that keeps an overwrite from leaking |
| A holder of storage is an element | `test_a_holder_of_storage_is_an_element`, `test_an_enum_element_is_copied_and_released` | `[Bag]` and `Vec(Bag)` call `nv_copy_nv_struct_Bag` and `nv_release_nv_struct_Bag` per element; `Vec(Slot)` does the same through `nv_copy_nv_enum_Slot` |
| The stored copy is independent, proved from both sides | `test_a_stored_string_is_independent_of_its_source` | storing `one` and then pushing `!` into the source prints `one` from the array and `one!` from the source: the copy owns its own bytes |
| A read and a write cost exactly one release each | `test_a_write_to_a_slot_releases_what_it_held` | 20 overwrites of one slot under AddressSanitizer: no double free, no leak, live arrays `0` |
| `vec_pop` moves the element out | `test_a_vector_of_strings_pushes_reads_and_pops` | four pushes, a `Some` pop and a length of `3`, with live arrays `0`: the popped buffer is the caller's, not a copy |
| A turn's copy cannot reach the container | `test_a_loop_copies_each_element` | pushing into the loop variable prints `keep!` twice and leaves the array at `keep` |
| Memory safety of the whole thing | `test_an_array_of_bags_is_released_with_its_elements`, `test_elements_under_the_sanitizers` | 50 rounds of `[make(i)]` plus a stolen copy, and 200 rounds of build → overwrite → `Vec(Slot)` → `match`, AddressSanitizer and UndefinedBehaviorSanitizer clean, live arrays `0` |
| An owning element has no zero value | `test_a_zeroed_slot_is_not_a_value_of_an_owning_element` | `array_zeros(3)` of `[String]` is `E0425`: an all-zero `String` is a null buffer, so an owning element is always built from values |
| The nested-shape rule is a different rule | `test_a_collection_is_still_not_an_element` | `Vec(Vec(i64))`, `Vec([i64])`, `[[i64]]` and `[Vec(i64)]` are `E0206`; the ownership rule changed, the shape rule did not |
| Two codes are retired with their fixtures | `nava_lang/diagnostics.py`, `tests/fixtures/negative.json` | `E0426` and `E0427` are gone from the table (145 → 143 codes), and `array_of_owning_values.nv`, `vector_of_owning_values.nv` and `array_of_strings.nv` are retired with this record instead of being rewritten into something they never asserted |
| The specification and the guide moved with the code | `LANGUAGE.md` (`### Owning values as elements`), `guides/nava-1.md` (`## Edition 2: collections of owning elements`) | the copy-in, copy-out and release rules are normative; the specification example prints `1`, `1`, `x`, `x!` and the guide example prints `1`, `x`, `x!` |
| The overhead record is re-measured, not estimated | `python3 eval/overhead.py --encoding cl100k_base` and `--encoding o200k_base` | prompt 83 136 tokens (`cl100k_base`) and 83 422 (`o200k_base`), summed over the eleven tasks, of which the guide is 82 500 / 82 786 tokens at 25 075 bytes and 715 lines |
| The audit is re-measured, not remembered | `python3 tools/audit_compiler_needs.py` | 9 019 lines, 13 modules, 282 functions under `nava_lang/` after this increment (8 823 / 13 / 274 at N11f) |
| No skipped tests | `python3 tests/registry.py`, `python3 -m pytest -q` | registry `OK: 639 registered tests, 11 tasks with 76 cases, 180 spec fixtures, 143 diagnostic codes`; suite **709 tests OK** (1 202 subtests) |

Negative controls: `E0425` for a zeroed owning element, `E0206` for four nested
shapes, and `E0203` for an undeclared element name. The behavioural controls that
carry the memory claim are the sanitizer runs and the live-array counter: a shallow
copy would show up as a double free, a missing release as a leak, and a copied-in
value that aliased its source as a wrong number in
`test_a_stored_string_is_independent_of_its_source`.

Deliberately not done in N11g, and why: a **collection inside a collection** —
`[[i64]]`, `[Vec(i64)]`, `Vec(Vec(i64))` — because the storage of a nested
collection is a different representation problem, and `E0206` states the limit
rather than implying it. `HashMap`/`HashSet` stay in N12 with the reason given
there.

#### Delivery record N11f (2026-10-29)

| Gate item | Probe | Result |
|---|---|---|
| A field holds a `Vec` and a `String` | `tests/test_storage.py`: `test_a_field_holds_a_vector_and_a_string` | `struct Bag { items: Vec(i64), text: String }` compiles and runs; the generated C contains `nv_vec_i64_copy(`, `nv_string_copy(`, `static nv_struct_Bag nv_copy_nv_struct_Bag(` and `static void nv_release_nv_struct_Bag(` |
| A stored value is a deep copy, proved from both sides | `StorageRuntimeTests.test_a_stored_vector_is_a_copy_and_the_source_goes_on` | building the bag from a one-element vector and then pushing to the source leaves the bag at 1 element, and pushing to the builder after storing leaves the field at `a`: the copy owns its own buffer and the source is unaffected |
| A field write replaces and releases the old buffer | `test_a_field_write_replaces_and_releases_the_old_buffer` | `bag.items = other` and `bag.text = fresh` release what the fields held, keep deep copies, and the live-array counter is `0` at exit |
| A nested holder copies every level | `test_a_nested_field_copies_both_levels`, `test_a_stored_value_travels_through_functions_and_keeps_its_storage` | `Shelf { bag: Bag, count: i64 }` copies `Bag` through `nv_copy_nv_struct_Bag(value.bag)`; two shelves returned from `build(n)` print `1` and `2`, so each owns its own buffer, with no leak |
| A payload holds one, and an arm binds a copy | `test_a_payload_holds_a_vector_and_a_string`, `StorageRuntimeTests.test_a_payload_binding_is_a_copy_of_its_own` | `Slot::Full(Bag)` and `Slot::Text(String)` compile; growing the source vector after `Slot::Full(bag)` leaves the payload at 1 element and its text at `keep` |
| A type argument may be an owning type | `test_a_type_argument_may_be_an_owning_type`, `StorageRuntimeTests.test_a_type_argument_value_is_independent` | `Pair(String)`, `Pair(Vec(i64))` and `Option(Vec(i64))` compile; the helper is named after the instance (`nv_copy_nv_struct_Pair_String`), and pushing to the source builder after `Pair(String)(first: a, …)` leaves `p.first` at `left` while `a` becomes `left!` |
| The element rule is unchanged and moves up a level | `test_a_holder_of_storage_is_not_an_element` | `[Bag]` is `E0426` and `Vec(Bag)` is `E0427` **because** a `Bag` holds a `Vec` and a `String`: the refusal now follows the fields, not the shape |
| A field is read, never moved out | `test_a_field_is_read_but_never_moved_out`, `test_a_mutating_call_on_a_field_needs_a_local` | `let stolen: Vec(i64) = bag.items` and the `String` form are `E0428`; `vec_push(bag.items, 1)` and `string_push(bag.text, "x")` are `E0444` |
| Memory safety of the copy | `StorageRuntimeTests.test_stored_values_under_the_sanitizers`, `test_a_value_that_owns_fields_is_released_with_its_block` | 200 rounds of build → copy → `Option(Bag)` payload → match, AddressSanitizer and UndefinedBehaviorSanitizer clean with the live-array counter at `0`; 50 rounds of nested holders released by their own block leave nothing behind |
| The specification and the guide moved with the code | `LANGUAGE.md` (`### Owning values inside a value`), `guides/nava-1.md` (`## Edition 2: storing a value that owns storage`) | the deep copy, the borrow-on-read rule, the field write and the element refusal are normative; the specification example prints `1`, `note`, `2`, `1` and the guide example prints `1`, `4`, `2` |
| The overhead record is re-measured, not estimated | `python3 eval/overhead.py --encoding cl100k_base` and `--encoding o200k_base` | prompt 79 022 tokens (`cl100k_base`) and 79 319 (`o200k_base`), summed over the eleven tasks, of which the guide is 78 386 / 78 683 tokens at 23 735 bytes and 673 lines |
| The audit is re-measured, not remembered | `python3 tools/audit_compiler_needs.py` | 8 823 lines, 13 modules, 274 functions under `nava_lang/` after this increment (8 759 / 13 / 273 at N11e) |
| No skipped tests | `python3 tests/registry.py`, `python3 -m pytest -q` | registry `OK: 623 registered tests, 11 tasks with 76 cases, 183 spec fixtures, 145 diagnostic codes`; suite **693 tests OK** (1 202 subtests) |

Negative controls: the four element and move fixtures from N11e still fail as
they should, and `E0428` is now covered by
`discarded_owned_result.nv` — a call that returns a `Vec` and is thrown away —
because `string_field.nv` asserted a refusal this increment turns into a feature
and was retired with the reason recorded here. The behavioural controls that
carry the memory claim are the pair of sanitizer runs and the counter: a shallow
copy would alias one buffer into two owners, and the first doubly-released buffer
would be an AddressSanitizer report rather than a wrong number in the output. The
control that carries the language claim is
`test_a_holder_of_storage_is_not_an_element`: storing a buffer and being an
element are different rules, and the second one did not move.

Deliberately not done in N11f, and why: an **element** that owns storage —
`[String]`, `[Bag]`, `Vec(String)`, `Vec(Vec(i64))` — because an element is read
by value on every access, so the copy would have to be made on every read and
released on every overwrite, together with `for x in v` over owning elements;
that is N11g, and the hints of `E0426` and `E0427` now name it. `HashMap`/`HashSet`
stay in N12 with the reason given there.

#### Delivery record N11e (2026-10-29)

| Gate item | Probe | Result |
|---|---|---|
| An owning value crosses a frame boundary by moving | `tests/test_moves.py`: `test_a_vector_and_a_string_can_be_moved_out_of_a_function`, `MoveRuntimeTests.test_a_returned_vector_is_owned_by_the_caller`, `test_an_early_return_moves_the_value_out_of_a_loop` | `fn make(n: i64) -> Vec(i64)` and `fn label(n: i64) -> String` compile, `let xs: Vec(i64) = make(3)` binds and `vec_len` reads 3 with capacity 4; an early `return` from inside a `while` moves the same buffer, and the live-array counter reads `0` afterwards, so exactly one release happened |
| A move transfers the buffer and copies nothing | `MoveRuntimeTests.test_a_move_copies_no_elements_and_releases_once`, `test_an_assignment_releases_the_buffer_it_replaces`, `test_a_moved_binding_inside_a_nested_block_is_released_by_the_receiver` | 100 pushes, then `let ys: Vec(i64) = xs`: length 100, capacity 128 and element 99 read back after the move, with the live-array counter at `0`; `xs = fill(2)` releases the 40-element buffer it replaces and keeps the 2-element one; a move inside an `if` body is released by the inner block and the outer block frees nothing |
| A moved-from binding is dead | `MoveFrontendTests.test_a_moved_binding_cannot_be_read_passed_or_moved_again` (four subtests), `test_a_moved_string_cannot_be_read_or_returned` | every later use — `print(vec_len(xs))`, passing it, a second move, and even `xs = vec_new()` — is `E0445` naming the binding; the same for a `String`, including in a `return` |
| Only what this function owns can be moved out | `MoveFrontendTests.test_a_borrow_cannot_be_moved_out_of_the_frame`, `test_a_mut_parameter_cannot_be_replaced` | returning a `Vec` or a `String` parameter is `E0446` (the caller owns it), and assigning over a `mut` parameter is `E0446` in both directions |
| An owning result is never discarded | `MoveFrontendTests.test_an_owning_result_must_be_bound` | `make(3)` as a statement is `E0428`: the buffer would have no owner |
| Storing an owning value is still refused | `MoveFrontendTests.test_an_owning_value_is_still_not_storable` | a `Vec(i64)` or `String` field and payload are `E0428`, `Vec(Vec(i64))` is `E0427`: a move crosses a frame, it does not make a buffer storable |
| The arena region is real and measurable | `ArenaRegionTests.test_a_frame_that_builds_text_gets_its_region_back` | 2 000 calls that each build eight pieces of text inside the frame report `nava: arena bytes: 0, chunks: 0` at exit; without a region the figure is the sum of all 2 000 |
| The escape rule is conservative and written down | `MoveFrontendTests.test_a_region_is_emitted_only_where_it_is_sound`, `ArenaRegionTests.test_text_that_can_escape_keeps_its_chunks`, `test_text_written_through_a_mut_parameter_survives_the_call` | the generated C carries `nv_arena_mark nv_region` for a function that returns `i64`, and carries none for one returning `str`, for one with a `mut Vec(str)` parameter, or for `main`; a `str`-returning function's 500 calls leave live bytes behind (the caller reads them) and the `mut Vec(str)` case still returns `p0`, `p2` after the call |
| Memory safety of the transfer | `MoveRuntimeTests.test_moves_and_returns_under_the_sanitizers` | 200 rounds of `label` → move → `grow` → move → reassign of the vector, AddressSanitizer and UndefinedBehaviorSanitizer clean, live-array counter `0` |
| A defect found while building it is closed | `tests/test_text.py`: `test_the_reports_are_printed_once_each` | releasing a region takes the chunk count back to zero, and the exit report was being registered again on the next allocation, so the same report printed twice; registration is now a one-shot flag and the test counts the lines |
| The specification and the guide moved with the code | `LANGUAGE.md` (`### Moving an owning value`), `guides/nava-1.md` (`## Edition 2: moves`) | the three move forms, `E0445`, `E0446`, the discarded-result rule and the region rule are normative; the specification example prints `n=7`, `4`, `4`, `n=9` and the guide example prints `3`, `n=4` |
| The overhead record is re-measured, not estimated | `python3 eval/overhead.py --encoding cl100k_base` and `--encoding o200k_base` | prompt 75 403 tokens (`cl100k_base`) and 75 689 (`o200k_base`), summed over the eleven tasks, of which the guide is 74 767 / 75 053 tokens at 22 612 bytes and 637 lines |
| The audit is re-measured, not remembered | `python3 tools/audit_compiler_needs.py` | 8 759 lines, 13 modules, 273 functions under `nava_lang/` after this increment (8 573 / 13 / 269 at N11d) |
| No skipped tests | `python3 tests/registry.py`, `python3 -m pytest -q` | registry `OK: 605 registered tests, 11 tasks with 76 cases, 183 spec fixtures, 145 diagnostic codes`; suite **675 tests OK** (1 199 subtests) |

Negative controls: four new fixtures — `string_moved_into_another_binding.nv`
(`E0445`), `vector_moved_into_another_binding.nv` (`E0445`),
`string_returned_from_a_borrow.nv` (`E0446`) and
`vector_reassigned_in_a_borrow.nv` (`E0446`) — each asserting its own code,
location, message and hint. Two fixtures were **retired** because the behaviour
they asserted is now legal: `string_copied.nv` and `vector_copied.nv` recorded
"a copy is refused", and this increment turns that copy into a move with its own
refusal (`E0445`) and its own fixture. The behavioural control that carries the
memory claim is the counter plus the sanitizers: a second release of a moved
buffer, a read through the emptied source, or a leaked handle is a report rather
than a number. The control that carries the arena claim is the pair of
`ArenaRegionTests`: the same loop reports zero live bytes when the frame can give
its region back and a growing figure when it cannot, so a rule that reset too
much would show up as a use-after-free under AddressSanitizer in
`test_text_written_through_a_mut_parameter_survives_the_call`.

Deliberately not done in N11e: an owning value in a **field, payload, type
argument or element** (`Vec(String)`, `Vec(Vec(i64))`, `[String]`, a struct or an
enum that holds a `Vec` or a `String`) — that needs deep copy in the storage
itself and is the storage work of N11f, which the hints now name;
`str_from_bool`, `str_chars`, string indexing and iteration over a `String`;
UTF-8-aware slicing; and a move that is *conditional* on a branch — the checker
rejects the second use of a moved binding on any path, which is stricter than
Rust and is stated here rather than discovered by a user.

#### Delivery record N11d (2026-10-28)

| Gate item | Probe | Result |
|---|---|---|
| A program can build text | `tests/test_text.py`: `test_the_constructors_produce_the_text_that_print_writes`, `test_slices_and_searches_are_checked_and_exact`, `test_trim_and_replace_build_the_expected_text` | `str_concat`, `str_from_i64`, `str_from_f64`, `str_slice`, `str_index_of`, `str_starts_with`, `str_ends_with`, `str_trim` and `str_replace` return a `str`; the constructors produce exactly the decimal text `print` writes (`1.0`, `0.5`, `-9223372036854775808`), a slice of `abcdef` is `cdef`, a missing needle is `-1`, and an out-of-range slice prints `nava runtime error: string slice 1..9 is out of bounds for a string of 3 bytes` and exits non-zero with nothing on stdout |
| `String` owns bytes and grows by doubling | `test_the_builder_grows_by_doubling_and_keeps_its_text`, `test_text_built_in_a_loop_and_from_a_parameter_is_stable` | capacity `32 → 64 → 128` while 90 bytes are written, `string_text` is stable after a later push (the finished text is copied into the arena, so the builder may grow under it), and the block that wrote the `let` frees the buffer |
| Text that is split and joined goes through a vector | `test_split_fills_a_vector_and_join_reads_one` | `str_split("a,bb,,ccc", ",", parts)` gives four fields including the empty one, `str_join(parts, "-")` is `a-bb--ccc`, and an empty separator is a runtime error rather than an empty list |
| What is refused is refused once, from one place | `test_a_string_cannot_be_copied_or_reassigned`, `…_returned_a_field_or_a_payload`, `…_an_element_or_a_type_argument`, `test_a_mutating_call_needs_a_writable_string` | copy, reassign, return, field, payload and type argument are `E0428`; `[String]` is `E0426` and `Vec(String)` is `E0427`, both naming the reason; `string_push` through a plain `String` parameter is `E0444`; a `mut String` argument from a borrow is `E0333` |
| The strings are reserved and edition-scoped | `test_the_text_names_are_reserved`, `test_a_string_needs_edition_two` | `fn str_concat(…)` and `let string_new = 1` are `E0301`; `String` in an edition-1 file is `E0372` |
| The arena is the accounting, and it is released | `test_the_arena_is_one_chunk_for_small_text_and_freed_at_exit`, `test_a_large_amount_of_text_uses_several_chunks` | under `-DNAVA_TRACK_ALLOCS` the runtime prints `nava: arena bytes: N, chunks: M`; one chunk serves 8 bytes of text, 20 000 concatenations of 32 bytes need several chunks, and AddressSanitizer reports no leak — every chunk is freed at exit |
| Text and vectors under the sanitizers | `test_text_and_vectors_under_the_sanitizers` | 200 rounds of `str_replace`/`str_concat` over a split vector, with a builder per round, clean under AddressSanitizer and UndefinedBehaviorSanitizer and with the live-array counter at zero |
| A defect from N11c is closed, and pinned | `ArgumentOnceTests` in `tests/test_text.py` | the vector builtins used to evaluate their arguments twice: `vec_push(xs, bump(1))` printed `1` twice, because the dispatch ran *after* the generic argument materialisation. Every vector and text builtin now materialises each argument once, and two tests assert the printed count |
| The specification and the guide moved with the code | `LANGUAGE.md` (`### Strings that grow: String and the text builtins`, the code table), `guides/nava-1.md` (`## Edition 2: String and the text functions`) | The type, the seventeen builtins, the owner and borrow rules, the arena's lifetime and the byte-versus-character rule are normative; `E0428` now covers a `String` and names both fixtures; `tests/test_guides.py` requires every builtin name in the guide, and the digest is re-pinned |
| The overhead record is re-measured, not estimated | `python3 eval/overhead.py --encoding cl100k_base` and `--encoding o200k_base` | prompt 69 562 tokens (`cl100k_base`) and 69 848 (`o200k_base`) for the `gcd` task, of which the guide is 68 926 / 69 212 tokens at 20 848 bytes |
| The audit is re-measured, not remembered | `python3 tools/audit_compiler_needs.py` | 8 573 lines, 13 modules, 269 functions under `nava_lang/` after this increment (8 092 / 13 / 266 at N11c) |
| No skipped tests | `python3 tests/registry.py`, `python3 -m pytest -q` | registry `OK: 584 registered tests, 11 tasks with 76 cases, 181 spec fixtures, 143 diagnostic codes`; suite **654 tests OK** (1 186 subtests) |

Negative controls: four new fixtures — `string_copied.nv` (`E0428`),
`string_field.nv` (`E0428` — retired in N11f, where a `String` field became legal; the code now has `discarded_owned_result.nv`), `string_mutated_through_a_borrow.nv` (`E0444`) and
`array_of_strings.nv` (`E0426`) — each asserting its own code, location, message
and hint, and two existing fixtures were updated because the text of two
messages moved with the new rules (`E0426` now names the *move* work of N11e, and
`E0341` names an array, a vector or a String parameter). The behavioural control
that carries the memory claim is the arena accounting plus AddressSanitizer: a
chunk that is never released is a LeakSanitizer report, and the counter printed at
exit says how much text was handed out. The control that carries the language
claim is `string_mutated_through_a_borrow.nv`: a builder is bytes one owner
writes, so a mutating call through a read-only parameter is refused exactly as a
mutating vector call is.

Deliberately not done in N11d: **`HashMap`/`HashSet`** (moved to N12, with the
reason given in the increment table); a `String` or a `Vec` as a field, a payload,
a type argument or a return value, and therefore `Vec(String)` and `[String]` as
storage — the same move work that N11c deferred appears here, because an owning
value that crosses a frame boundary is what a region cannot reclaim; `str_from_bool`,
`str_chars`, string indexing and iteration over a `String`; UTF-8-aware slicing
(slices and lengths are bytes, and `chars_len` plus the `text` standard module
stay the character-level tools).

#### Delivery record N11c (2026-10-28)

| Gate item | Probe | Result |
|---|---|---|
| A vector is a type a program can declare and grow | `tests/test_vec.py`: `test_a_vector_is_created_in_a_block_and_grown_by_builtins` | `let xs: Vec(i64) = vec_new()` then `vec_push`, `vec_set`, `vec_get`, `vec_len`, `vec_capacity`; `Vec(Point)`, `Vec(str)`, `Vec(Shape)` and `Vec(f64)` all compile, and the program prints its first element, its length and its capacity |
| The interface is exactly seven builtins | `LANGUAGE.md` `### Vectors: `Vec(T)``, `VectorFrontendTests.test_the_argument_types_are_checked` | `vec_len`, `vec_capacity`, `vec_push`, `vec_pop`, `vec_get`, `vec_set` plus `vec_new`; a wrong argument is `E0327`, a non-`i64` index `E0328`, a wrong arity `E0326`, `print(v)` `E0323` |
| Growth is amortised, and says so | `test_pushes_grow_by_doubling_from_four`, `test_a_hundred_pushes_stay_within_twice_the_length` | capacity `0 -> 4 -> 8 … -> 128` for 100 pushes; the run prints `5 8 8`, then `100 128 99 50`, so no growth ever leaves capacity above `2 * len` |
| Growth is visible through a `mut Vec(T)` parameter | `test_growth_inside_a_parameter_is_seen_by_the_caller`, `test_a_mut_vector_parameter_may_grow_the_caller_s_vector` | `grow(xs, 40)` then `grow(xs, 90)` from `main` gives the caller `130` elements and `vec_get(xs, 129) == 89` — the handle, not a copy of it, is what the parameter holds |
| `pop` is `Option(T)` and iteration binds the element type | `test_pop_drains_the_vector_and_then_reports_none`, `test_an_enum_vector_matches_its_payload`, `test_a_string_vector_keeps_its_strings` | draining prints `8 1 7 -1` and leaves the capacity at `4`; `for shape in shapes` matches each payload (`0`, `5`); a `Vec(str)` prints `ada`, `grace`, `alan` |
| The owner releases the buffer, once | `test_two_vectors_in_nested_blocks_free_both`, `test_growth_aliasing_and_drain_under_the_sanitizers` | every runtime test builds with `-DNAVA_TRACK_ALLOCS` and reports `nava: live arrays at exit: 0`; the sanitizer test (`grow`, alias, drain, iterate, 5 → 130 pushes) is clean under AddressSanitizer and UndefinedBehaviorSanitizer |
| An empty vector allocates nothing | `test_an_empty_vector_allocates_nothing` | `vec_len`, `vec_capacity` and a `for` over it print `0 0 0`, and stderr is empty — no buffer is allocated until the first push |
| What is refused is refused once, from one place | `test_a_vector_cannot_be_copied`, `test_a_vector_cannot_be_reassigned`, `test_a_vector_cannot_be_returned`, `test_a_vector_cannot_be_a_field_or_a_payload`, `test_an_element_cannot_own_storage`, `test_vec_new_needs_an_annotated_element_type`, `test_a_mutating_builtin_needs_a_writable_target` | copy, reassign, return, field and payload are `E0428`; an owning element is `E0427`; `let xs = vec_new()` is `E0429`; `vec_push` through a plain `Vec(T)` parameter or a non-vector is `E0444` / `E0327` |
| A checked index stops the program, it does not read past the end | `test_an_out_of_bounds_index_stops_the_program` | `vec_get(xs, 4)` on a one-element vector prints `nava runtime error: array index 4 is out of bounds for length 1` and exits non-zero |
| The specification and the guide moved with the code | `LANGUAGE.md` (`### Vectors: `Vec(T)``, the code table), `guides/nava-1.md` (`## Edition 2: `Vec(T)``) | The type, the seven builtins, the owner rule, the doubling, the check semantics and the non-goals are normative; `E0427`, `E0428`, `E0429` and `E0444` have rows and fixtures; the guide's digest is re-pinned (`ec1c1c3e…`, 18 545 bytes) and its example prints `5 8 30 16` |
| The overhead record is re-measured, not estimated | `python3 eval/overhead.py --encoding cl100k_base` and `--encoding o200k_base` | prompt 62 016 tokens (`cl100k_base`) and 62 247 (`o200k_base`) for the `gcd` task, of which the guide is 61 380 / 61 611 tokens at 18 545 bytes; both `eval/measurements/overhead-*.json` records updated |
| The audit is re-measured, not remembered | `python3 tools/audit_compiler_needs.py` | 8 092 lines, 13 modules, 266 functions under `nava_lang/` after this increment (7 692 / 13 / 255 at N11b) |
| No skipped tests | `python3 tests/registry.py`, `python3 -m pytest -q` | registry `OK: 559 registered tests, 11 tasks with 76 cases, 177 spec fixtures, 143 diagnostic codes`; suite **629 tests OK** (1 159 subtests) |

Negative controls: four new fixtures — `vector_of_owning_values.nv` (`E0427`),
`vector_copied.nv` (`E0428`), `vector_new_without_element_type.nv` (`E0429`) and
`vector_mutated_through_a_borrow.nv` (`E0444`) — each asserting its own code,
location and message. The control that carries the memory claim is the leak
counter: a vector whose buffer is never freed leaves a count above zero, and a
vector released twice aborts under the sanitizers. `vector_mutated_through_a_borrow.nv`
is the one that carries the language claim: growth replaces the buffer, so a
vector that could be grown through a read-only borrow would resize storage its
caller still believes it owns — the rule that refuses it is what makes `Vec(T)`
safe without a garbage collector.

Deliberately not done in N11c: an **arena allocator** with its own accounting.
The increment's vectors are allocated with the array helpers, freed by their
owner, and reported by the same leak counter, so the arena is an optimisation of
what already works rather than a prerequisite for it — and a region allocator
needs a rule for storage that outlives its region, which N11b created: an owning
field, payload or `return` value leaves the frame that allocated it, so a
per-frame mark can only be restored once the compiler tracks **moves** rather
than copies. The arena is therefore scheduled with that move work in N11d, and
nothing here claims it;
`vec_insert`/`vec_remove`/shrink, sorting and filtering as builtins (a loop over
two vectors does it); a vector of owning elements (`E0427`, N11d); `String`,
`HashMap` and `HashSet` (N11d).

#### Delivery record N11b (2026-10-28)

| Gate item | Probe | Result |
|---|---|---|
| An array field and an array payload exist | `tests/test_ownership.py`: `test_an_array_field_and_an_array_payload_are_values` | `struct Bag { items: [i64] }` and `enum Shape { Dot, Poly([i64]) }` compile and run; the retired `E0378`/`E0399` walls are gone with the restriction they carried |
| A field array is an array like any other | `test_an_array_field_is_read_like_any_other_array`, `test_an_array_field_can_be_replaced` | `b.items[1]`, `len(b.items)`, `b.items[1..3]`, `for x in b.items` and `b.items = [7, 8, 9]` all work; a `for` loop now accepts an array field as its source |
| A value that owns storage is copied when it is bound | `Value...`→`test_a_constructed_field_copies_the_source_array`, `test_a_copied_value_keeps_its_own_storage` | `let b = Bag(items: src)`, then `src[0] = 99`, leaves `b.items[0]` at `1`; after `let copy = b` and `b.items = [8, 9]`, `copy.items` still holds `1, 2, 3` |
| … including through an enum payload, a nested struct and a function boundary | `test_an_enum_payload_is_copied_and_replaced`, `test_a_value_travels_through_functions_and_keeps_its_storage`, `test_a_nested_owning_field_is_a_value_too` | `s = Shape::Dot` after `let t = s` leaves `t`'s payload intact; a `Bag` returned from a function and read through a parameter keeps its length; `Outer(inner: Inner(xs: [...]))` copies both levels |
| Nothing is leaked, on the normal path or on an early exit | `test_nothing_is_leaked_on_the_normal_path`, `test_nothing_is_leaked_when_a_block_is_left_early`, `test_a_defer_can_read_the_array_a_value_owns` | built with `-DNAVA_TRACK_ALLOCS`, every program reports `nava: live arrays at exit: 0`, including a `return` out of a `while` whose body declares an owning value and a `defer` that reads one |
| Only owning types get helpers | `test_the_generated_c_copies_and_releases_only_where_needed` | `nv_copy_nv_struct_Bag` / `nv_release_nv_struct_Bag` are emitted, `nv_struct_Plain` gets neither |
| What is still refused is refused once, from one place | `test_an_owning_value_cannot_be_an_array_element`, `test_an_enum_with_an_owning_payload_cannot_be_an_element_either`, fixture `array_of_owning_values.nv` | `[Bag]` as a binding, as a parameter, as a literal and as `array_zeros` are all `E0426` from `check_element_type`, and the message names the type and the arena work; the retired `E0378` and `E0399` are gone from the code table |
| The specification and the guide moved with the code | `LANGUAGE.md` (`### Declaring a struct`, `### Arrays of structs and slices`, the code table), `guides/nava-1.md` | The field rule states what is copied and released, `E0426` replaces the two retired rows, `tests/test_spec.py` and `tests/test_guides.py` are green |
| The audit is re-measured, not remembered | `python3 tools/audit_compiler_needs.py` | 7 692 lines, 13 modules, 255 functions under `nava_lang/` after this increment (7 324 / 13 / 236 at N11a) |
| No skipped tests | `python3 tests/registry.py`, `python3 -m pytest -q` | registry `OK: 525 registered tests, 11 tasks with 76 cases, 173 spec fixtures, 139 diagnostic codes`; suite **595 tests OK** (1 135 subtests) |

Negative control: `tests/fixtures/negative/array_of_owning_values.nv` (`E0426`)
— the one shape a struct with an array cannot take. The controls that carry the
claim are the two runtime tests that would pass under aliasing by accident and
fail under copying: `test_a_constructed_field_copies_the_source_array` (write to
the source afterwards) and `test_a_copied_value_keeps_its_own_storage` (replace
one binding's array and read the other). The leak counter is the third: a shallow
copy released twice aborts, and a copy released never leaves a count above zero.

Deliberately not done in N11b: an array of owning values (`E0426`, the arena work
of N11d), freeing on assignment elision — every boundary copy is made, none is
elided — a struct field or payload assignment from a slice (the checker's array
rules are unchanged), and `String`/`Vec`/hash collections (N11c, N11d).

#### Delivery record N11a (2026-10-28)

| Gate item | Probe | Result |
|---|---|---|
| A string is a value that can live inside another value | `tests/test_values.py`: `test_a_string_is_a_field_and_a_payload` | `struct Person { name: str, age: i64 }` and `enum Shape { Label(str) }` are accepted in edition 2 and run: `ada`, `36`, `3`, then `0`, `3`, `hi` |
| Copying a value copies the container, not the immortal string | `ValueRuntimeTests.test_a_string_field_copies_while_the_string_is_shared` | `copy.age = 37` leaves `ada.age` at `36` while both names point at `Ada`: the struct is copied, the string is shared because nothing writes or frees it |
| A struct with text travels like any value | `test_a_struct_with_text_travels_through_functions_and_arrays` | Constructed, returned from a function, stored in `[Person]` and iterated; prints `a`, `2`, `b`, `3` |
| An enum is an array element | `test_an_enum_is_an_array_element`, `ValueRuntimeTests.test_an_array_of_enums_is_written_and_read`, `test_an_array_of_enums_slices_and_is_passed`, `test_a_mutable_array_of_enums_is_written_in_place` | `[Shape]` literal, `array_zeros`, element write, `for`, slice and `mut [Shape]` all work; a `str` payload matches and prints |
| What still owns storage is still refused | `test_an_array_field_and_an_array_payload_are_still_refused`, fixtures `struct_field_array.nv`, `enum_variant_array.nv` | An array field is `E0378` and an array payload `E0399`, both naming the type and pointing at the memory work |
| `array_zeros` hands out only values | `test_array_zeros_needs_a_zero_value`, fixture `array_zeros_str.nv` | `[str]`, a struct with a `str` field, a struct with a function field and an enum whose first variant carries `str` are all `E0425`, and the message names the field or the variant; `bool`, a struct of scalars, `Shape` and `Tag { Plain, Named(str) }` still zero |
| A hole that existed before is closed | `nava check` used to accept `let names: [str] = array_zeros(2)` and the program segfaulted on `print(names[0])` | The same program is now `E0425` at compile time; the fixture is that program |
| The grammar artefact agrees | `eval/grammar.py`, `tests/test_grammar.py` | The element-type production accepts a declared name and refuses the reserved words, unchanged; `E0425` is classified as semantic (the shape is fine, the element type is not), and `E0424` — the retired enum wall — is gone from both the code table and the classification |
| The specification moved with the code | `LANGUAGE.md`, sections "Declaring a struct" and "Arrays of structs and slices" | The field rule now states why a `str` field is safe (immutable, never freed) and why an array field is not (one owner); a runnable `Person` example prints `36`, `37`, `Ada`; the element rule says "a value with no owner"; `array_zeros` documents the zero-value rule. `E0424` is retired with its reason recorded |
| No skipped tests | `python3 tests/registry.py`, `python3 -m pytest -q` | registry `OK: 509 registered tests, 11 tasks with 76 cases, 174 spec fixtures, 140 diagnostic codes`; suite **579 tests OK** (1135 subtests); 12 new tests in `tests/test_values.py` and three new negative fixtures (two replacing the fixtures whose restriction was lifted) |
| The audit is re-measured, not remembered | `python3 tools/audit_compiler_needs.py` | 7 661 lines, 13 modules, 252 functions under `nava_lang/` after this increment |

Negative controls: `array_zeros_str.nv` (`E0425`), `struct_field_array.nv`
(`E0378`) and `enum_variant_array.nv` (`E0399`). The control that carries the
claim is `array_zeros_str.nv`: it is the program that used to type-check and
crash, and a compiler that keeps its old zeroing rule fails the test rather than
the program. `tests/test_values.py::test_array_zeros_needs_a_zero_value` covers
the four shapes of the same mistake — a bare `str`, a struct containing one, a
function value, and an enum whose zeroed tag would carry text.

Deliberately not done in N11a: an array field or payload (an owner inside a
value, N11b), `Vec<T>`, `String` and the hash collections (N11c, N11d), strings
that are freed at all (every `str` lives for the whole run in this increment,
which is why a field is safe), and arrays of `f64` or of function values.

#### Delivery record N10g (2026-10-28)

| Gate item | Probe | Result |
|---|---|---|
| A function type is a type, and a value has it | `tests/test_functions.py`: `test_a_declared_function_can_be_bound_and_called`, `test_a_function_value_fits_every_value_position` | `let f = double` infers `fn(i64) -> i64`; a signature is accepted in a `let` annotation, a parameter, a result, a struct field, a variant payload, `Option(fn(i64) -> i64)` and a generic call, and all seven run |
| A call through a value is checked before it runs | `test_a_value_carries_its_signature`, `test_the_arity_is_checked_through_the_value`, `test_a_signature_is_part_of_the_type` | `f(true)` is `E0327` "argument 1 of f expects i64, got bool"; `f(1, 2)` is `E0326`; assigning `fn(i64) -> bool` to a `fn(i64) -> i64` variable is `E0312` |
| The C keeps the type instead of erasing it | `test_the_generated_c_typedefs_each_signature` | Two distinct signatures over four bindings produce exactly two `typedef`s — one per signature, not one per binding, and no `void *` anywhere; a third repeat of the first signature adds nothing |
| A signature is a value type like any other | `FunctionValueRuntimeTests.test_a_value_is_called_through_the_pointer`, `test_a_function_value_is_passed_and_returned`, `test_two_values_of_one_signature_compose` | A value passed, returned and called prints `42`, `20`, `12`; two values of one signature compose to `8` and `7`; a `void` function is a value (`1`, `1`); an array parameter travels through a value (`6`, `6`) |
| Monomorphisation and function values meet correctly | `test_a_template_instantiates_per_signature`, `test_a_generic_instance_takes_one_copy_per_signature` | `apply<T>(f: fn(T) -> T, x: T)` compiles once per signature: two definitions for `i64`/`bool` on top of `main`, `double` and `flip`; running it prints `8`, `false`, `10` |
| A nested signature is its own type, emitted in the right order | `test_a_nested_signature_is_its_own_type` | `fn(fn(i64) -> i64, i64) -> i64` is a separate `typedef` emitted after the inner one, and `apply2(twice)` prints `12` |
| What cannot be done is refused with a code and a hint | `test_only_a_function_type_can_be_called`, `test_a_generic_function_is_not_a_value`, `test_a_call_cannot_go_through_a_field`, `test_two_function_values_cannot_be_compared`, `test_a_function_type_takes_no_mut_parameter`, `test_a_function_value_cannot_be_printed` | `E0439` names the type that is not a function and shows the annotation to write; `E0437` refuses a generic function as a value; `E0441` shows `let f = h.op`; `E0442` refuses `==`; `E0443` refuses `mut` in a signature; `print(f)` is `E0323` |
| The grammar artefact agrees with the parser | `eval/grammar.py`, `tests/test_grammar.py` | A function type is a `type` production (`fn` `(` types `)` returns) in both back ends. `E0439` and `E0442` are classified as semantic — a call shape and a comparison the grammar cannot refuse; `E0441` and `E0443` as syntactic, because a call target is a name and `mut` belongs to a declaration |
| The specification moved with the code | `LANGUAGE.md`, section "Function values" | Normative: the spelling, the address-not-closure semantics, the value positions, static checking of the call, the `typedef` in the generated C, and six stated restrictions; four new diagnostic rows (`E0439`, `E0441`–`E0443`) |
| The guide and its measurements moved | `guides/nava-1.md` (digest `0ab04681…`, 15 157 bytes, 456 lines), `guides/examples/fnvalues.nv`, `eval/measurements/overhead-*.json` | New canonical example compiled and run (`42`, `10`, `12`); overhead re-measured: `cl100k_base` 50 917 prompt and 50 270 guide tokens, `o200k_base` 51 126 and 50 479, over the eleven tasks; `guides/examples.json` gained the `fnvalues` case; rule 15 added |
| No skipped tests | `python3 tests/registry.py`, `python3 -m pytest -q` | registry `OK: 497 registered tests, 11 tasks with 76 cases, 174 spec fixtures, 140 diagnostic codes`; suite **567 tests OK**; 23 new tests in `tests/test_functions.py`, four new negative fixtures, and `FunctionValueRuntimeTests` added to the guard that a skipped runtime test is not a pass |
| The audit is re-measured, not remembered | `python3 tools/audit_compiler_needs.py` | 7 269 lines, 13 modules, 235 functions under `nava_lang/` after this increment; `tests/test_plan.py` recomputes them |

Negative controls: four new single-file fixtures — `call_through_non_function.nv`
(`E0439`), `call_through_field.nv` (`E0441`), `compare_function_values.nv`
(`E0442`) and `mutable_in_function_type.nv` (`E0443`). The control that carries
the claim is the emitted-C one: a compiler that represented a function value as
`void *`, or that emitted one `typedef` per binding, would produce a different
count, and `test_the_generated_c_typedefs_each_signature` reads the C rather
than the intent. The second is `test_a_generic_instance_takes_one_copy_per_signature`:
a template instantiated with a function-typed parameter must produce one copy
per signature, which only a monomorphising compiler can do.

The increment closes the last N10 row of the §2 audit (`lambda`: a call-back no
longer needs a closure, because a function is a value with a checked signature).

Deliberately not done in N10g: closures and function literals, a value spelling
for a module-qualified name (`math::abs` is callable, not bindable), a call
through a field or any other expression (`E0441`), comparison and printing of
function values (`E0442`, `E0323`), `mut` in a signature (`E0443`), arrays of
function values, and a generic function as a value (`E0437`). Those are N11 and
later, and each is stated in `LANGUAGE.md` rather than implied.

#### Delivery record N10f (2026-10-28)

| Gate item | Probe | Result |
|---|---|---|
| Generics are monomorphisation, not a runtime representation | `tests/test_generics.py`: `test_the_generated_c_has_one_function_per_instance`, `test_the_same_instance_is_generated_once` | The emitted C contains one definition per distinct instantiation and none for the template: two `identity(i64)` calls and one `identity(bool)` call produce three definitions, and `nv_fn_4` — the next index the template would have taken — is absent |
| An instantiation is an ordinary declaration | `GenericRuntimeTests.test_an_instantiated_struct_copies_like_a_struct`, `test_instances_nest`, `test_a_generic_enum_matches_per_instantiation` | `swap(p)` returns a copy, and writing `q.first = 99` leaves `p.first` at `1`; `Pair(Pair(i64))` prints `2` then `3`; a `Box(T)` matched per instantiation prints `5`, `0`, then `true` |
| Two instantiations are two types | `GenericFrontendTests.test_two_instantiations_are_different_types` | Assigning `Pair(i64)` to `Pair(bool)` is `E0311` with the instance names in the message: `expected Pair(bool), got Pair(i64)` |
| A template body is checked per instantiation | `test_a_template_instantiates_per_argument_type_at_run_time`, `test_a_template_may_call_another_template` | `same<T>` compiles twice and compares `i64`, `bool` and `str` with the operators of each; a template calling another template instantiates both, printing `2` and `false` |
| A bare template is refused everywhere it can be written | `test_a_bare_template_is_not_a_type`, `test_a_generic_enum_template_is_not_a_type_either` | An annotation, a parameter, a field, a construction without arguments and a call without arguments are all `E0437` with the instantiated spelling in the hint |
| Inference, and its limit | `test_an_argument_that_cannot_be_inferred`, `test_inference_does_not_guess_from_a_conflict`, `test_wrong_number_of_type_arguments` | `zero()` with no parameter that mentions `T` is `E0436` and the hint shows `zero(i64)(...)`; `both(1, true)` binds `T = i64` from the first argument and reports `E0327` on the second; `Pair(i64, i64)` is `E0412` |
| A generic body that is never instantiated is not silently verified | `test_an_uninstantiated_template_is_reported_as_unchecked`, `tests/fixtures/warnings/unused_template.nv` | Compilation succeeds with `W0003` and a message that says the body is not type-checked; the fixture is registered in `tests/fixtures/warnings.json` and still builds and runs |
| The restrictions are the existing rules, not new ones | `test_a_struct_instance_cannot_own_a_string`, `test_an_instantiation_cannot_be_an_array_element` | `Pair(str)` is `E0378` — a struct field cannot own a string — and `[Pair(i64)]` is `E0206` from the parser, exactly as `[f64]` is |
| The grammar artefact agrees | `eval/grammar.py`, `tests/test_grammar.py` | `type_parameters` after a declared name, and a value-position instantiation (`generic_type` followed by `(`) are in both back ends; `E0435`–`E0438` are classified as semantic, because a grammar without a symbol table cannot tell `identity(i64)(7)` from `double(i64)(21)` |
| The specification moved with the code | `LANGUAGE.md`, section "Generic declarations" | Normative: the two bracket kinds, instantiation as a type, inference and its limit, per-instantiation checking, and six stated restrictions; four new diagnostic rows (`E0435`–`E0438`) and the `W0003` warning |
| The guide and its measurements moved | `guides/nava-1.md` (digest `2c782cc1…`, 13 519 bytes, 411 lines), `guides/examples/generics.nv`, `eval/measurements/overhead-*.json` | New canonical example compiled and run (`2`, `1`, `9`, `2.5`); overhead re-measured: `cl100k_base` 4 085 and `o200k_base` 4 101 guide tokens per task, 45 582 and 45 758 prompt tokens over the eleven tasks; `guides/examples.json` gained the `generics` case; rule 14 added |
| A template crosses a module boundary | `tests/fixtures/modules/generic_module` (case in `modules.json`) | `pub fn pick<T>` and `pub fn through<T>` are called as `pair::pick(3, 4)` from another module, and print `3`, `42`, `true`; a `pub` template that calls a private one of its own module keeps working. A generic *struct* cannot cross the boundary, because no struct name crosses it — an existing rule, now stated in `LANGUAGE.md` |
| No skipped tests | `python3 tests/registry.py`, `python3 -m unittest discover -s tests -q` | registry `OK: 474 registered tests, 11 tasks with 76 cases, 170 spec fixtures, 136 diagnostic codes`; suite **544 tests OK**; 21 new tests in `tests/test_generics.py`, four new negative fixtures, one warning fixture and one module fixture |
| The audit is re-measured, not remembered | `python3 tools/audit_compiler_needs.py` | 6 968 lines, 13 modules, 221 functions; `tests/test_plan.py` recomputes the size and function count and fails when they drift. The tool is new in this increment because the row counts had been ad-hoc: the table now names its own method |

Negative controls: four new single-file fixtures — `duplicate_type_param.nv`
(`E0435`), `cannot_infer_type_argument.nv` (`E0436`), `generic_used_as_type.nv`
(`E0437`) and `type_args_on_plain_function.nv` (`E0438`) — plus the warning
fixture `unused_template.nv` (`W0003`). The control that carries the claim is the
emitted-C count: a compiler that had implemented generics as a runtime
polymorphism, or that emitted the template itself, would define a different
number of functions, and the test reads the C rather than the intent.

The increment is narrower than the plan's description of N10f, which bundled
monomorphised generics with function pointers. Generics change the parser, the
checker, the code generator, the grammar artefact and the guide; function
pointers are a separate type and a separate call path, so they moved to **N10g**
and the split is in the table above rather than smoothed over.

Deliberately not done in N10f: function pointers (`fn(T) -> U` as a type, a
function as a value), bounds on a type parameter, generic arrays, an instantiation
as an array element type, generic containers (they are N11's, and they need the
ownership work first), `match` as an expression, and template-level checking of a
body that no call instantiates — that last one is reported, not hidden, as `W0003`.

Deliberately not done in N10e: `defer` on a bare statement, a deferred block that
can fail (nothing inside one may leave it), `defer` interacting with `?` by
design, generic parameters of any kind, function pointers, and `pub` on structs
or enums. Those are N10f and later.

#### Delivery record N10e (2026-10-28)

| Gate item | Probe | Result |
|---|---|---|
| A deferred block runs on every way out | `tests/test_defers.py`: `test_a_defer_runs_on_return`, `test_a_defer_runs_on_break_and_continue`, `test_a_defer_runs_per_iteration` | `return` prints `-1`, then the caller sees `7`; a `continue` and a `break` each run the block; a `defer` in a loop body runs once per turn (`0 1 2`, then `9`) |
| Defers nest and see the block's arrays | `test_defers_run_last_first_and_before_the_release` | Two defers print `3` then `3` after the body printed `0`: last-first, and `len(values)` is still readable, so the release happens after them |
| A deferred block reads variables when it runs | `test_a_defer_runs_on_break_and_continue` | `defer { print(100 + i) }` prints `101` and `102` for the same registration, because `i` changed before the block left — the documented semantics, not an accident of code generation |
| A deferred block cannot leave itself | `test_a_deferred_block_cannot_leave_itself` | `return`, `break`, `continue` and `?` inside a `defer` are all `E0430` with a message that names the construct; `defer` without a block is `E0201` |
| A label reaches its loop and stops there | `LabelledLoopRuntimeTests.test_a_label_leaves_every_loop_above_the_target`, `test_a_labelled_continue_runs_the_increment`, `test_a_nested_loop_may_reuse_a_label_and_the_innermost_wins` | A `continue outer` from the inner loop restarts the outer one and the total is `4` (an increment skipped would hang); a `break outer` leaves both loops and prints `0` then `9` |
| A labelled jump releases what it leaves | `test_a_labelled_jump_releases_the_arrays_it_leaves` | Two arrays per turn, one of them in the inner block, freed on the labelled `continue`: the program prints `4` twice and the suite's allocation instrumentation reports no leak elsewhere |
| Labels are checked | `test_a_label_must_name_a_loop`, `test_a_jump_must_name_a_loop_it_can_reach`, `test_labels_need_edition_two` | `outer: let n = 1` is `E0432` (the parser refuses a label in front of anything but `while`/`for`), `break middle` is `E0431`, and a label in an edition-1 file is `E0372` |
| `pub` decides what a module exports | `PublicFrontendTests.test_a_private_function_cannot_be_called_from_another_module`, `test_a_public_function_can_use_a_private_one` | `tools::hidden` is `E0433` with the fix in the hint; a `pub` function that calls a private one of its own module runs and prints `8`, `22` |
| Edition 1 is not changed by `pub` | `test_an_edition_one_module_still_exports_everything` | The edition-1 `multi_file` fixture still compiles and runs with no `pub` anywhere: visibility is decided per module by the edition its file declares |
| The grammar artefact agrees | `eval/grammar.py`, `tests/test_grammar.py` | `pub` as a function prefix, `label:` before a loop, `break`/`continue` with a label and `defer` + block are in both the token grammar and the GBNF; `E0432`/`E0434` are shapes the grammar also refuses, `E0430`/`E0431`/`E0433` are classified as semantic |
| The specification moved with the code | `LANGUAGE.md` edition-2 section "`defer`, labelled loops and `pub`" | The five deferred-block rules, the label rules, the module contract and the edition-1 compatibility are normative, with five new diagnostic rows (`E0430`–`E0434`) |
| The guide and its measurements moved | `guides/nava-1.md` (digest `e9c0e19d…`, 11 410 bytes), `guides/examples/control2.nv`, `eval/measurements/overhead-*.json` | New canonical example compiled and run (`1`, `0`, `101`, `1`, `102`, `-1`); overhead re-measured: `cl100k_base` 3 515 and `o200k_base` 3 527 prompt tokens; `guides/examples.json` gained the `control2` case |
| No skipped tests | `python3 tests/registry.py`, `python3 -m unittest discover -s tests -q` | registry `OK: 453 registered tests, 11 tasks with 76 cases, 164 spec fixtures, 131 diagnostic codes`; suite **523 tests OK**; two new module fixtures (`public_module`, `private_module`) carry a runtime case and an `E0433` case |

Negative controls: four new single-file fixtures — `defer_returns.nv` (`E0430`),
`label_unknown.nv` (`E0431`), `label_not_a_loop.nv` (`E0432`) and
`pub_not_a_function.nv` (`E0434`) — plus a module fixture `private_module`
(`E0433`) and its positive twin `public_module`. The control that carries the
claim is the pair of module fixtures: the same function is reachable from
outside when it says `pub` and invisible when it does not, while an edition-1
module keeps its released behaviour.

The increment is deliberately narrower than the phase description: the plan's
N10e bundled monomorphised generics and function pointers with these three
features. A single patch that changes type substitution, code generation, the
grammar and the guide for five unrelated features is exactly what the
"delivered in increments" paragraph exists to prevent, so the generic work moved
to **N10f** and the split is recorded in the table above rather than smoothed
over.

Deliberately not done in N10e: `defer` on a bare statement, a deferred block that
can fail (nothing inside one may leave it), `defer` interacting with `?` by
design, generic parameters of any kind, function pointers, and `pub` on structs
or enums. Those are N10f and later.

Deliberately not done in N10d: arrays of enums (their payloads have no ownership
story until N11), a field write straight into an array element, slices as values
of a struct field or a return type (an array still cannot be returned), string
slicing by character, generic containers, function pointers, `defer`, labelled
loops and `pub`. Those are N10e and later.

Deliberately not done in N10c: `match` as an expression, guards, generic
declarations of the user's own (`Option`/`Result` are two fixed templates, not a
generic facility), `Vec`/`HashMap`, chained `?` inside a larger expression, any
conversion between failure types, and owned payloads — `str` and array payloads
remain refused until N11 gives them an ownership story. Those are N10d, N10e and
later.

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
| **R1. N14 and N15 are simply large** | The current compiler is 9 019 Python lines and 282 functions (`tools/audit_compiler_needs.py`); the port is an estimate, not a measurement, and will grow — it grew from 4 394 lines and 141 functions in the first edition-2 phase alone. | Two milestones inside N14; the port proceeds subsystem by subsystem with parity evidence; no date is promised, only gates. |
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
