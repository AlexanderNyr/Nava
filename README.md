# Nava

Nava is an experimental programming language designed for programs generated
by language models. It checks Nava source code, translates it to C11 and builds
a native executable. The project is deliberately small and incremental: the
current compiler is a working prototype, not a production replacement for C or
C++.

The design aims to reduce boilerplate and ambiguity through familiar syntax,
explicit function types, inferred local types and checked arithmetic.

---

## Current status

> **Reality check:** no model has yet been shown to generate Nava faster or
> more accurately than C or C++. A new grammar also costs prompt tokens, and
> existing models already know C and C++. These claims require measurements
> with a specific model, not assumptions based on source length.

### Implemented

- `i64`, `bool` and `str`; mutable locals and parameters with block scope.
- Functions and recursion; `if` / `else if` / `else`, `while` and `return`.
- Arithmetic, comparisons, `!`, `&&` and `||`, with left-to-right evaluation
  and short-circuiting.
- `read_i64()` for standard input and `print(value)` for line-oriented output.
- Checked integer overflow and division errors; failures produce a diagnostic
  instead of relying on C's undefined signed-arithmetic behaviour.
- A C11 backend, a native build command, runnable examples and host-side tests.
- A registry guard that fails when a test, task fixture or required compiler is
  missing, instead of reporting skipped tests as a pass (`tests/registry.py`).
- Stable diagnostic codes with source ranges and hints, plus `--format json`
  for machine-readable diagnostics (`nava_lang/diagnostics.py`).
- An executable specification: every example in [`LANGUAGE.md`](LANGUAGE.md)
  type-checks, 47 invalid fixtures assert the exact code and location, and 12
  positive fixtures assert output and exit status (`tests/test_spec.py`).

### Not implemented

Arrays, collections, `for`, floating-point numbers, GPU computation, file
operations, third-party libraries and a package system are not available in
0.1. Nava is not yet a general-purpose language in the production sense.
See [`LANGUAGE.md`](LANGUAGE.md) for the implemented semantics and limits.

---

## Installation

Nava requires Python 3.10+ and a C11 compiler (`cc`, GCC or Clang). The
compiler itself has no third-party Python dependencies. The commands below
were tested on Linux; macOS with Clang should also work.

Either install the package, which provides the `nava` command:

```bash
python3 -m pip install .          # from the repository root
nava run examples/hello.nv
```

or run the entry script directly without installing anything:

```bash
python3 nava run examples/hello.nv
```

Expected output:

```text
Hello, world!
14
true
```

A slightly larger program:

```text
fn sum(n: i64) -> i64 {
    let total = 0
    let i = 1
    while i <= n {
        total = total + i
        i = i + 1
    }
    return total
}

fn main() {
    print(sum(100))
}
```

Save it as `sum.nv` and run `python3 nava run sum.nv`; it prints `5050`.

### Build and run

```bash
# Parse and check types without invoking the C compiler.
python3 nava check examples/sum.nv

# The same diagnostic as one JSON document, for tools and repair loops.
python3 nava --format json check examples/sum.nv

# Compile and run; optionally limit the program's execution time.
python3 nava run examples/sum.nv --timeout 2

# Build a native executable and run it independently of Python.
python3 nava build examples/gcd.nv -o ./gcd
printf '48 18\n' | ./gcd       # 6

# Inspect the generated C.
python3 nava emit-c examples/gcd.nv -o gcd.c

# Run the compiler, runtime, evaluation, benchmark, specification and registry tests.
python3 -m unittest discover -s tests -v

# Only the executable specification: documented examples, fixtures, limits, fuzzing.
python3 -m unittest tests.test_spec -v

# Fail, rather than skip, when a registered test, task fixture or required
# compiler is missing. CI runs this before the suite.
python3 tests/registry.py
```

`run` compiles on every invocation. Use `build` for repeated execution and
runtime measurements; the resulting binary does not need Python to run. The
default optimization level is `-O2`; choose `--opt 0`, `--opt 2` or `--opt 3`
to change it. Set `CC` or pass `--cc` to select the C compiler.

Diagnostics are `path:line:column: severity[CODE]: message`, with an optional
`hint:` line. Codes are stable and documented in
[`LANGUAGE.md`](LANGUAGE.md#diagnostic-codes); `--format json` prints a single
JSON document with `code`, `severity`, `title`, `message`, `range` and `hint`
instead. Syntax and type errors exit with status 2. Pathologically nested input
is reported as `E0401` rather than crashing the compiler: the documented
nesting limit is 200 (`nava_lang/limits.py`).

---

## Performance: measured and unmeasured

In one local arithmetic-loop microbenchmark with five million iterations, the
median was **28.16 ms** for Nava, **27.87 ms** for checked C++ and **28.09 ms**
for unchecked C++. Each result used seven runs at `-O2` and includes process
startup. The results are comparable on this one workload; they do **not**
establish general superiority. The environment and limitations are recorded in
[`BENCHMARK.md`](BENCHMARK.md).

Model generation speed and accuracy have not been measured against a
specified model. The prompt generator and evaluator in [`eval/`](eval/README.md)
can check saved Nava, C and C++ responses on eight tasks without connecting to
a model API; `eval/reference.py` recomputes every expected answer
independently. Use the actual model's tokenizer and count the complete prompt
as well as the output.

`bench/run.py --json PATH` writes a machine-readable record: environment,
compiler versions, flags, warm-up policy, raw samples per phase and missing
model fields as `null` with an explicit status. Recorded runs live in
`bench/results/`.

A separate, coarse Nava-versus-C authoring experiment is documented in
[`experiment/`](experiment/README.md); its timings include tool latency and
are not model-generation measurements.

---

## Safety

This is a research prototype, **not a sandbox**. Do not execute untrusted
model-generated code without operating-system isolation and CPU, memory,
filesystem and network restrictions. `--timeout` limits the executed
process's time; it is not a security boundary. This is especially important
when evaluating C++, which can access the host system.

---

## Project layout

| Path | Purpose |
|---|---|
| `nava_lang/` | Lexer, parser, type checker, C generator, diagnostics, limits and CLI |
| `examples/` | Runnable Nava programs |
| `tests/` | Compiler, runtime, evaluation and registry tests using `unittest` |
| `tests/registry.py` | Suite and fixture registry guard; fails on missing tests, fixtures, codes or tools |
| `tests/test_spec.py` | Executable specification: documented examples, fixtures, limits, fuzzing |
| `tests/fixtures/` | Negative, positive and warning fixtures with recorded expectations |
| `bench/` | Reproducible comparison with C++; JSON records in `bench/results/` |
| `eval/` | Prompts, tasks, reference answers and tests for saved model responses |
| `experiment/` | Nava-versus-C GCD sources and the authoring-time experiment |
| `.github/workflows/ci.yml` | Required, fail-closed, packaging and benchmark lanes |
| [`pyproject.toml`](pyproject.toml) | Packaging metadata and the `nava` console script |
| [`PLAN.md`](PLAN.md) | Dependency-ordered improvement phases and test gates |
| [`CHANGELOG.md`](CHANGELOG.md) | Changelog |
