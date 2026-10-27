# Native Execution Microbenchmark

## Measurement

On October 27, 2026, the following was run in the current workspace:

```bash
python3 bench/run.py --iterations 5000000 --samples 7
```

Environment: Linux x86-64, Intel Xeon 2.60 GHz, GCC/G++ 14.2.0 and Python
3.13.16; C11 and C++17, with `-O2`. The script compiles each program, checks
several small inputs against a Python reference, rotates execution order and
checks that outputs match. All programs returned `723413853` for the main
workload.

| Program | Source bytes | Compile, ms* | Run median, ms | Run min-max, ms |
|---|---:|---:|---:|---:|
| Nava | 172 | 126.53 | 28.16 | 27.57-28.50 |
| C++ checked | 769 | 364.82 | 27.87 | 27.45-28.38 |
| C++ unchecked | 295 | 53.12 | 28.09 | 28.02-28.51 |

\* Compilation was measured once in a shared, non-isolated environment.
Run times include process startup and I/O. This is one small workload;
results may vary substantially on different hardware or under different
load.

The loop performs the same integer operations on bounded values. Nava checks
for overflow and division errors. The checked C++ version implements similar
arithmetic checks explicitly using GCC/Clang builtins; error handling and input
are not entirely identical. The unchecked C++ version does not check
arithmetic, but the selected workload does not overflow. This comparison says
nothing about workloads with other algorithms, memory access, data structures,
APIs or I/O patterns.

**Conclusion:** native Nava and both C++ variants fall in roughly the same
range on this test. The differences are comparable to the observed spread; we
cannot claim that Nava is faster than C++. Nava ultimately compiles generated
C with the same family of native compilers.

Source bytes are not model tokens. The checked C++ source includes helper
functions that Nava's C generator supplies automatically. This table is not
evidence of faster model generation or better model understanding.

## Reproduce

GCC or Clang with C++17 support is required. Change the iteration count and
sample size as needed:

```bash
python3 bench/run.py --iterations 10000000 --samples 9
```

For optional source-token counts with a compatible encoding, install
`tiktoken` and pass, for example, `--encoding cl100k_base`. This only tokenizes
source files; it does not measure full prompts, model latency or generation
correctness. Different models use different tokenizers. For a model
experiment, see [`eval/README.md`](eval/README.md).
