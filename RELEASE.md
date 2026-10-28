# Release readiness

This file is the contract between the repository and a release. It states what
is supported, what was actually exercised, how to cut a release and what a
release deliberately does not claim. `tools/verify_release.py` checks the parts
of it that can be checked by a machine, and `tests/test_release.py` checks the
checker.

## Versioning

| Thing | Where it lives | Current |
|---|---|---|
| Package version | `pyproject.toml`, `nava_lang/__init__.py` | `0.1.0` |
| Language specification version | `LANGUAGE.md` header, `nava_lang/limits.py` | `1` |
| Standard library version | `nava_lang/stdlib/*.nv` (`module_version()`) | `1` |
| Changelog | `CHANGELOG.md` — `## Unreleased` until a tag exists | unreleased |

The package version follows `MAJOR.MINOR.PATCH`. While the major version is `0`
a minor bump may change the language: the specification version in
`LANGUAGE.md` is the number a program should look at, and a change to it is
always recorded in `CHANGELOG.md`. `tools/verify_release.py` fails when the
package version, `pyproject.toml` and any released changelog section disagree.

## Requirements

- **Python 3.10 or newer.** The compiler is a plain Python package with no
  third-party runtime dependencies. `tests/test_release.py` parses every
  Python file with `ast.parse(..., feature_version=(3, 10))`, which proves the
  syntax floor — it is not a substitute for running the suite on 3.10.
- **A C11 compiler** (`cc`, `gcc` or `clang`) for `nava build` and `nava run`.
  `nava check` and `nava emit-c` work without one. The compiler is not a Python
  dependency, so it cannot be resolved by `pip`; the registry guard
  (`python3 tests/registry.py`) fails instead of skipping the runtime tests when
  it is missing.
- **A POSIX shell and `cc`** for the isolation tests. `nava run` uses the best
  available isolation backend documented in `SECURITY.md`; where namespaces are
  unavailable it falls back according to `--isolation` and never claims the
  stronger mode.
- **Unprivileged user namespaces** for the jail itself. Ubuntu 24.04 restricts
  them by default (AppArmor), so the GitHub runner needs
  `sysctl kernel.apparmor_restrict_unprivileged_userns=0`; the workflow attempts
  it and then *reports* which backend this host has. The isolation gate demands
  the jail where namespaces exist and asserts the documented refusal (`exit 2`,
  `E0601`) where they do not — it never skips, because a skipped test is not a
  pass. Every CI log names the backend in its `isolation backend` step and in
  the registry OK line.

## Support matrix

"Exercised" means a CI lane in `.github/workflows/ci.yml` runs the full suite,
the registry guard and the isolation gate on that combination. "Expected, not
exercised" means the code has no known dependency against it, and nothing in
this repository has run the suite there — it is a statement about intent, not
about evidence, and no bug report is closed on the strength of it.

| Platform | Runner | Python | C compilers | Status |
|---|---|---|---|---|
| Linux x86_64 | `ubuntu-24.04` | 3.10–3.13 | gcc, clang | exercised |
| macOS arm64 | — | 3.10–3.13 | Apple clang | expected, not exercised |
| Windows x86_64 | — | 3.10–3.13 | MSVC, clang | expected, not exercised |

`tools/verify_release.py --check` reads both this table and the CI matrix and
fails if they disagree: a Python version or compiler that CI runs but this
table does not list cannot ship quietly, and a platform cannot be promoted to
"exercised" without a lane.

## Release checklist

Run from a clean checkout of the commit to be released; every step either
produces evidence or stops the release.

1. **Version and changelog.** Bump `version` in `pyproject.toml` and
   `__version__` in `nava_lang/__init__.py`, and turn the top of
   `CHANGELOG.md` from `## Unreleased` into `## <version>` with the date.
2. **Tree is consistent.** `python3 tools/verify_release.py` — metadata,
   Python-floor syntax and credential scan. It must print `OK`.
3. **Gates.** `python3 tests/registry.py`, then
   `python3 -m unittest discover -s tests -q`. No skipped tests: the registry
   guard fails when a required tool or fixture is missing.
4. **Artifacts.** From the repository root, with `build` installed:
   `python3 -m build`. Then `python3 tools/verify_release.py --artifacts dist`,
   which checks that the wheel carries `nava_lang/stdlib/*.nv`, the `nava`
   console script, `Apache-2.0` in `METADATA` and the license text, that the
   sdist carries `LICENSE`, `NOTICE`, `README.md`, `tools/verify_release.py` and
   `tests/registry.py`, and that neither archive contains tests, benchmark
   records, `__pycache__` or credentials. The command prints the SHA-256 of
   every artifact; publish those digests with the files.
5. **Clean-environment install.** Install the wheel into a fresh virtual
   environment *outside* the checkout and use it from a third directory:

   ```bash
   python3 -m venv /tmp/nava-release
   /tmp/nava-release/bin/pip install dist/*.whl
   cd /tmp && printf 'import math\nfn main() { print(math::abs(-7)) }\n' > /tmp/demo.nv
   /tmp/nava-release/bin/nava --version
   /tmp/nava-release/bin/nava check /tmp/demo.nv
   /tmp/nava-release/bin/nava build /tmp/demo.nv -o /tmp/demo && /tmp/demo
   ```

   This is the step that catches a missing `package-data` entry: without it
   `import math` fails after installation even though every source checkout
   passes. CI runs exactly this sequence in the `release` lane.
6. **Evidence.** Copy the digests, the `tools/verify_release.py --json` report
   and the test summary into the release notes. Do not paste a number that was
   not produced by these commands.
7. **Tag and publish.** Tag the reviewed commit, upload the wheel and sdist with
   the digests, and keep the artifacts as built in step 4 — no rebuilds.

## What a release does not claim

- **No model results.** The paired study and the constrained-decoding A/B exist
  as harnesses; every record in this repository says
  `model_measurements.status: not-measured`, and `PLAN.md` keeps those claims
  open.
- **No general performance claim.** `BENCHMARK.md` records one workload on one
  host, with raw samples and an explicit noise rule. The compiler-cost numbers
  there are for the commit that produced them.
- **No general security claim.** `SECURITY.md` states the threat model and the
  gaps the tests assert; isolation is bounded by the host kernel features that
  are present.
- **No reproducibility of timings.** Timings come from a shared host. The
  commands are reproducible; the milliseconds are not.

## Known gaps at `0.1.0`

- `f64` has no arithmetic of its own beyond the documented scalar operations,
  and arrays hold `i64`, `bool` and `str` only — there is no `[f64]`.
- Module imports are file-based (`name.nv` next to the entry file or in the
  standard library); there is no package manager and no versioned dependency
  resolution.
- The compiler emits C11 and calls the system compiler; there is no bundled
  backend and no cross-compilation support.
- Windows and macOS are expected to work and are not exercised; the isolation
  tiers are exercised on Linux only.
