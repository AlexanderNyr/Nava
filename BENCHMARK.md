# Native Execution Microbenchmark

## Measurement protocol

`bench/run.py` separates the phases that a single "compile and run" number
usually mixes together:

| Field | Meaning |
|---|---|
| `frontend_ms` | Nava front end plus C emission (`nava emit-c`). C and C++ have no separate equivalent, so the field is `null` for them. |
| `compile_ms` | Native compilation. For Nava this is the `cc` invocation on the emitted C only; `compile_parts_ms` keeps both phases. |
| `startup_ms` | Median of `--samples` runs with a zero-iteration workload: process start plus trivial work. Compare it with the main workload before attributing differences to arithmetic. |
| `runs_ms` | Raw samples of the main workload **in execution order**. |

Order and repetitions:

- Reference checks run first on 0, 1 and 100 iterations for every program.
- `--warmup` runs (default 1) are discarded before sampling.
- Sample order is rotated by sample index (`workload.run_order`), so no program
  is systematically measured first or last.
- Every program must produce identical output, otherwise the run fails.

`--json PATH` writes a `nava-bench/1` record containing the platform, CPU
count, Python and compiler versions, flags, the warm-up policy, the rotation
log, raw samples, derived statistics and a `failures` list. Fields that would
require a model run are stored as `null` with
`model_measurements.status = "not-measured"`; this script never estimates
them. Recorded runs live in [`bench/results/`](results/).

## Recorded run

```bash
python3 bench/run.py --iterations 5000000 --samples 7 \
  --json bench/results/2026-10-27-linux-x86_64-gcc-14.2.0-o2.json
```

Environment of the record: Linux-6.1.158+-x86_64-with-glibc2.41, x86_64,
2 visible CPUs, Python 3.13.16, `cc`:
cc (Debian 14.2.0-19) 14.2.0, `c++`: c++ (Debian 14.2.0-19) 14.2.0, Python `CPython`;
C11 and C++17 at `-O2`.

| Program | Source bytes | Front end ms* | Compile ms* | Startup ms | Run median ms | Run min-max ms | Stdev ms |
|---|---:|---:|---:|---:|---:|---:|---:|
| Nava | 172 | 65.63 | 57.05 | 1.28 | 25.71 | 25.43-26.30 | 0.29 |
| C++ checked | 769 | - | 59.88 | 1.21 | 24.32 | 24.24-24.53 | 0.10 |
| C++ unchecked | 295 | - | 52.66 | 1.21 | 24.29 | 24.21-24.72 | 0.17 |

\* One measurement per run, in a shared non-isolated host. Run times include
process startup and I/O. `output` was `723413853` for every
program; all nine reference checks passed.

## What this record does and does not show

- On this workload the medians differ by about 6% (25.71 ms for Nava versus
  24.29-24.32 ms for the C++ variants), and the spread of individual samples is
  of the same order as that difference. The honest reading is that the numbers
  are close, not that either side wins.
- Nava pays a front-end cost the C++ programs do not have: about
  66 ms of Python on top of a comparable `cc`
  invocation (57 ms against
  53 ms for unchecked C++).
- Startup is a small fraction of the total here
  (1.28-1.21 ms),
  so the main workload is measured, not just process creation.
- The loop performs the same integer operations on bounded values. Nava checks
  overflow and division errors; the checked C++ version implements similar
  checks with GCC/Clang builtins, but error handling and input are not
  identical, and the unchecked C++ version relies on the workload never
  overflowing. Differences in input handling (`nv_read_i64` with full syntax
  and range validation versus `std::scanf`) are inside these numbers.
- This is one small workload on one machine. It says nothing about other
  algorithms, memory access, data structures, APIs or I/O patterns, and nothing
  at all about model generation speed or accuracy.
- Source bytes are not model tokens. The record stores `source_tokens` as
  `null` unless `--encoding` is passed, and even then only source files are
  tokenized, not prompts.

**Conclusion:** native Nava and both C++ variants fall in roughly the same
range on this test. The differences are comparable to the observed spread; we
cannot claim that Nava is faster than C++. Nava ultimately compiles generated
C with the same family of native compilers.

## Compiler performance (phase N8)

`bench/run.py` answers "how fast is the compiled program". Phase N8 asked the
other question first: *where does a build spend its time?* `bench/profile.py`
measures the phases separately on five workloads — the N0 loop, an array
sieve, a three-file module project, an `f64` statistics example and a generated
10 KB program with 60 functions — and `bench/compare.py` classifies the
difference between two records.

The classification rule is explicit and deliberately conservative: a phase is
`improved` or `regressed` only when its median difference exceeds twice the
larger standard deviation **and** is at least 5% of the baseline median.
Everything else is `inconclusive`, which is reported as a result, not hidden.
Sizes are exact counts and are compared exactly; a phase one side never
measured is `missing`, never zero.

### Before and after

Both records were taken on this host in the same session, with 15 in-process
stage samples and 7 process-level samples per row. "Before" is the N7 tree
(`git worktree` at the delivered N7 commit) with the same profiler; "after" is
this tree.

```bash
python3 bench/profile.py --runtime full     --json bench/results/2026-10-27-profile-before.json
python3 bench/profile.py --runtime selected --json bench/results/2026-10-27-profile-after.json
python3 bench/compare.py bench/results/2026-10-27-profile-before.json \
                         bench/results/2026-10-27-profile-after.json \
                         --json bench/results/2026-10-27-n8-comparison.json
```

Medians in ms; `C bytes` is the size of the generated translation unit.

| Workload | Front end | `emit` | `nava emit-c` | C bytes | `cc -O2` | Run |
|---|---|---|---|---|---|---|
| `loop` | 0.631 → **0.407** | 0.043 → 0.052 | 78.03 → **49.97** | 23442 → **4909** | 71.70 → 65.99 | 6.77 → 6.88 |
| `arrays` | 0.830 → 0.884 | 0.056 → 0.096 | 78.10 → **50.56** | 24262 → **8641** | 73.20 → 80.31 | 1.11 → 1.16 |
| `modules` | 7.865 → 8.076 | 0.518 → 0.702 | 87.02 → **55.28** | 35528 → **22129** | 118.07 → 112.63 | 1.30 → 1.32 |
| `float` | 1.337 → 1.334 | 0.090 → 0.159 (**regressed**) | 81.46 → **50.51** | 25323 → **12129** | 105.39 → 102.05 | not run |
| `synthetic` (10 KB) | 13.693 → 15.879 | 1.072 → 1.556 (**regressed**) | not measured | 54394 → **34801** | 147.89 → 146.26 | 1.26 → 1.22 |

Process-level rows that no workload owns: `python3 -c pass` 14.22 → 13.93
(inconclusive), `nava --version` 74.37 → **48.97** (improved, −34.6%).

Verdicts over the whole comparison: 8 improved, 2 regressed, 31 inconclusive,
4 missing. The improvements that hold on every workload are the ones the
change targets: the command-line front end (`nava emit-c`, −35% to −38%) and
the size of the emitted C (−36% to −79%).

### What the changes cost

Two rows are regressions and are left in the table:

- `emit` on `float` (+0.070 ms, 2.5σ) and on `synthetic` (+0.484 ms, 9σ). Both
  come from one new step: the emitted body is scanned for runtime symbols so
  the unreachable helpers can be dropped. On a 172-byte program that scan is
  +0.009 ms (`inconclusive`); on a 34 KB body it is measurable. The scanner
  uses a character class instead of a word-boundary pattern for this reason —
  the first version cost 1.0 ms per build on the same body.
- The selective runtime does **not** make the binary smaller: 16352 bytes
  before and after for `loop`, unchanged for every workload. `-O2` already
  discarded the unused `static inline` helpers. The gain is on the build side —
  less text through the preprocessor and the C parser, and a translation unit a
  reader can inspect.

`cc` time moves in the direction of the change (−8% on `loop`, −5% on
`modules`) but stays `inconclusive` on every workload under the rule above, so
no claim is made for it.

### Isolating the two changes

`--runtime full` reproduces the pre-N8 emission from the current tree
(`NAVA_FULL_RUNTIME=1`), so the effect of the runtime selection can be separated
from the import work:

```bash
python3 bench/profile.py --runtime full --json bench/results/2026-10-27-profile-after-full-runtime.json
python3 bench/compare.py bench/results/2026-10-27-profile-after-full-runtime.json \
                         bench/results/2026-10-27-profile-after.json \
                         --json bench/results/2026-10-27-n8-selective-only-comparison.json
```

That comparison reports 2 improved, 2 regressed, 37 inconclusive: the two
regressions are the same `emit` rows, and the emitted C shrinks by the same
exact byte counts. One of the two "improved" rows (`modules.check`) cannot be
caused by this change — the checker never sees the runtime text — so it is
host noise that the rule classified too generously; `modules.cc` (−9%, a smaller
translation unit) is plausible. Both are reported as they came out.

### Program speed: unchanged, as expected

N8 changed what the C compiler is *given*, not what the program *does*.
`bench/run.py` on the N0 workload:

```bash
python3 bench/run.py --json bench/results/2026-10-27-n8-before-bench.json   # N7 tree
python3 bench/run.py --json bench/results/2026-10-27-n8-after-bench.json    # this tree
```

| Program | Front end ms | `cc` ms | Run median ms (min–max) |
|---|---|---|---|
| Nava, N7 | 79.67 | 71.22 | 27.67 (27.56–28.01) |
| Nava, N8 | **52.43** | 66.11 | 27.59 (26.12–28.05) |
| C++ checked | – | 64.19 | 27.89 (26.88–28.76) |
| C++ unchecked | – | 62.14 | 27.66 (27.45–28.50) |

The front end of a complete build drops by 34% (79.67 → 52.43 ms). Program run
time is unchanged within the spread — 27.67 against 27.59 ms is a 0.3%
difference with a standard deviation of 0.19–0.73 ms — so nothing is claimed
for it. The unchecked C++ variant stays labelled as unchecked: it is only
equivalent while the workload cannot overflow.

### The gate behind these numbers

```bash
python3 -m unittest tests.test_differential -q     # 19 randomised differential tests
python3 bench/negative_controls.py                 # 12/12 defects caught
```

`tests/test_differential.py` generates programs from a fixed seed, computes the
expected result with a small interpreter that implements the documented
semantics (wrapping is an error, `/` truncates toward zero, `INT64_MIN / -1` is
an error, left operand first, `&&`/`||` short-circuit), and requires the
compiled program to agree — value by value, and error by error. Every
arithmetic case is compiled twice: with the compiler's overflow builtins and
with `-DNAVA_FORCE_PORTABLE`, so both branches of each helper are exercised.

`bench/negative_controls.py` then re-introduces twelve known defects — a
dropped overflow check in the builtin and in the portable path, a dropped
multiply, negation, division and remainder check, an unchecked array index, a
disabled array release, right-to-left argument evaluation, an eagerly evaluated
`&&`, an unclosed runtime closure and a broken record factory — and requires
the named test to fail for each. All twelve are caught; the script restores
every file and verifies the restore by SHA-256.

## Reproduce

GCC or Clang with C++17 support is required. Change the iteration count and
sample size as needed:

```bash
python3 bench/run.py --iterations 10000000 --samples 9 --warmup 2 --json /tmp/bench.json
```

For optional source-token counts with a compatible encoding, install
`tiktoken` and pass, for example, `--encoding cl100k_base`. This only tokenizes
source files; it does not measure full prompts, model latency or generation
correctness. Different models use different tokenizers. For a model
experiment, see [`eval/README.md`](eval/README.md).
