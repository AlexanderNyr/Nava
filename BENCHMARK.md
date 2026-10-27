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
