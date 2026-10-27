#!/usr/bin/env python3
"""Phase-separated profile of the Nava toolchain (PLAN.md phase N8).

`bench/run.py` answers "how fast is the compiled program". This answers the
other question the plan asks first: *where does the build time go?* The phases
are measured separately, because they have different owners and different fixes:

* `python_startup` — the interpreter with no project code (`python3 -c pass`);
* `import_and_cli` — `nava --version`, i.e. startup plus importing the compiler;
* `frontend_total` — lexer, parser, checker and C emission in one process;
* `lex`, `parse`, `check`, `emit` — the same work split into stages;
* `cli_emit_c` — the same front end through the real command line;
* `cc` — the system C compiler on the emitted translation unit;
* `binary_startup` — a zero-iteration run of the built program;
* `run` — the built program on its workload.

Nothing here calls a model and nothing is estimated. A phase that cannot run is
recorded as `null` with a reason, never as `0`.

Two runtime variants are measured on purpose:

    python3 bench/profile.py --runtime full       # the N7 behaviour: whole runtime
    python3 bench/profile.py --runtime selected   # N8: only the reachable units

`--runtime full` sets `NAVA_FULL_RUNTIME=1` for the child processes, so the
"before" side of a comparison is reproducible from the current tree instead of
depending on an old checkout. `bench/compare.py` reads two records and decides
whether a difference is an improvement, a regression or noise.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import platform
import shlex
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nava_lang import __version__ as nava_version  # noqa: E402
from nava_lang import cgen, cli  # noqa: E402

try:  # N8 added selective runtime emission; older trees emit the whole runtime.
    from nava_lang import runtime as runtime_module  # noqa: E402
except ImportError:  # pragma: no cover - only on a tree before N8
    runtime_module = None
from nava_lang.checker import check as check_program  # noqa: E402
from nava_lang.lexer import lex  # noqa: E402
from nava_lang.parser import parse  # noqa: E402

SCHEMA = "nava-profile/1"
#: In-process stages are cheap, so they get more samples than process-level ones.
STAGE_SAMPLES = 15
PROCESS_SAMPLES = 5

WORKLOADS = (
    {"name": "loop", "path": "bench/loop.nv", "stdin": "1000000\n",
     "note": "the N0 workload: one checked arithmetic loop"},
    {"name": "arrays", "path": "tests/fixtures/positive/arrays_sieve.nv",
     "stdin": "200\n", "note": "arrays, bounds checks and a nested loop"},
    {"name": "modules", "path": "examples/modules/main.nv", "stdin": "", "project": True,
     "note": "three files: a local module plus the standard library"},
    {"name": "float", "path": "examples/stats.nv", "stdin": None,
     "note": "f64 formatting and conversions; not run (it takes no input)"},
    {"name": "synthetic", "path": None, "stdin": "1\n",
     "note": "generated: 60 functions, to measure how the front end scales"},
)

SYNTHETIC_FUNCTIONS = 60


def synthetic_source(functions: int = SYNTHETIC_FUNCTIONS) -> str:
    """A deterministic large program: the same shape repeated."""
    parts = ["fn main() {"]
    for index in range(functions):
        parts.append(f"    print(fn_{index}())")
    parts.append("}")
    for index in range(functions):
        parts.append(f"fn fn_{index}() -> i64 {{")
        parts.append("    let total = 0")
        parts.append("    let i = 0")
        parts.append(f"    while i < {index + 3} {{")
        parts.append("        total = total + i * 3")
        parts.append("        i = i + 1")
        parts.append("    }")
        parts.append("    return total")
        parts.append("}")
    return "\n\n".join(parts) + "\n"


def median_ms(samples: list[float]) -> float | None:
    return round(statistics.median(samples), 4) if samples else None


def summarise(samples: list[float]) -> dict:
    if not samples:
        return {"samples": [], "median_ms": None, "min_ms": None, "stdev_ms": None}
    return {
        "samples": [round(value, 4) for value in samples],
        "median_ms": round(statistics.median(samples), 4),
        "min_ms": round(min(samples), 4),
        "stdev_ms": round(statistics.stdev(samples), 4) if len(samples) > 1 else None,
    }


def timed(callable_object, samples: int) -> list[float]:
    values = []
    for _ in range(samples):
        start = time.perf_counter_ns()
        callable_object()
        values.append((time.perf_counter_ns() - start) / 1_000_000)
    return values


def run_process(command: list[str], samples: int, *, stdin: str | None = None,
                env: dict | None = None) -> list[float]:
    values = []
    for _ in range(samples):
        start = time.perf_counter_ns()
        subprocess.run(command, input=stdin, text=True, capture_output=True,
                       timeout=600, env=env)
        values.append((time.perf_counter_ns() - start) / 1_000_000)
    return values


def command_version(command: list[str]) -> str | None:
    try:
        result = subprocess.run(command + ["--version"], capture_output=True, text=True,
                               timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    lines = result.stdout.strip().splitlines()
    return lines[0].strip() if lines else None


def environment(cc: list[str]) -> dict:
    uname = platform.uname()
    return {
        "recorded_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(
            timespec="seconds"),
        "platform": platform.platform(),
        "machine": uname.machine,
        "cpu_count": os.cpu_count(),
        "python": sys.version.split()[0],
        "nava_version": nava_version,
        "cc_command": cc,
        "cc_version": command_version(cc),
        "cc_flags": ["-std=c11", "-O2"],
        "isolation": "none: timings come from a shared, non-isolated host; "
                     "compare medians, not single runs",
    }


def profile_workload(spec: dict, *, cc: list[str], opt: str, temp: Path,
                     runtime_variant: str, env: dict, sample_stages: int,
                     sample_processes: int) -> dict:
    if spec["path"] is None:
        source = synthetic_source()
        label = f"synthetic:{SYNTHETIC_FUNCTIONS} functions"
        path = None
    else:
        path = ROOT / spec["path"]
        source = path.read_text(encoding="utf-8")
        label = spec["path"]

    stages = {}
    if spec.get("project") and path is not None:
        # A multi-file workload cannot be lexed or checked one file at a time:
        # `parse` is the loader (parse every file and merge), `check` and `emit`
        # run on the merged program.
        from nava_lang import modules  # noqa: PLC0415
        loaded = modules.load(path)
        stages["lex"] = {"samples": [], "median_ms": None, "min_ms": None,
                         "stdev_ms": None,
                         "note": "not measured: the workload is several files"}
        stages["parse"] = summarise(timed(lambda: modules.load(path), sample_stages))
        stages["check"] = summarise(timed(lambda: check_program(loaded), sample_stages))
        checked = check_program(modules.load(path))
        stages["emit"] = summarise(timed(lambda: cgen.generate(checked), sample_stages))
    else:
        stages["lex"] = summarise(timed(lambda: lex(source), sample_stages))
        stages["parse"] = summarise(timed(lambda: parse(source), sample_stages))
        parsed = parse(source)
        stages["check"] = summarise(timed(lambda: check_program(parsed), sample_stages))
        checked = check_program(parse(source))
        stages["emit"] = summarise(timed(lambda: cgen.generate(checked), sample_stages))
    if path is None:
        stages["frontend_total"] = summarise(timed(lambda: cli.compile_source(source),
                                                   sample_stages))
        emitted = cli.compile_source(source).code
    else:
        stages["frontend_total"] = summarise(
            timed(lambda: cli.compile_file(path), sample_stages))
        emitted = cli.compile_file(path).code

    record = {
        "workload": spec["name"],
        "source": label,
        "note": spec["note"],
        "source_bytes": len(source.encode("utf-8")),
        "stages_ms": stages,
        "emitted_c_bytes": len(emitted.encode("utf-8")),
        "runtime_units": {
            "variant": runtime_variant,
            "selective": runtime_module is not None,
            "units": (len(runtime_module.split(cgen.RUNTIME))
                      if runtime_module is not None else None),
            "runtime_bytes": (len(cgen.RUNTIME.encode("utf-8"))
                              if runtime_module is not None else None),
            "emitted_bytes": len(emitted.encode("utf-8")),
        },
        "processes_ms": {},
        "binary_bytes": None,
        "output": None,
        "failure": None,
    }
    try:
        c_file = temp / f"{spec['name']}.c"
        binary = temp / f"{spec['name']}.bin"
        if path is None:
            c_file.write_text(emitted, encoding="utf-8")
            record["processes_ms"]["cli_emit_c"] = None
        else:
            record["processes_ms"]["cli_emit_c"] = summarise(run_process(
                [sys.executable, str(ROOT / "nava"), "emit-c", str(path), "-o", str(c_file)],
                sample_processes, env=env))
        record["processes_ms"]["cc"] = summarise(run_process(
            cc + ["-std=c11", f"-{opt}", str(c_file), "-o", str(binary)],
            sample_processes, env=env))
        record["binary_bytes"] = binary.stat().st_size
        if spec["stdin"] is not None:
            record["processes_ms"]["binary_startup"] = summarise(run_process(
                [str(binary)], sample_processes, stdin="0\n", env=env))
            record["processes_ms"]["run"] = summarise(run_process(
                [str(binary)], sample_processes, stdin=spec["stdin"], env=env))
            result = subprocess.run([str(binary)], input=spec["stdin"], text=True,
                                    capture_output=True, timeout=600, env=env)
            record["output"] = result.stdout[-200:]
        else:
            record["processes_ms"]["binary_startup"] = None
            record["processes_ms"]["run"] = None
    except (OSError, subprocess.SubprocessError, RuntimeError) as error:
        record["failure"] = f"{type(error).__name__}: {error}"
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Profile the Nava toolchain phase by phase.",
        epilog="Pair with bench/compare.py: two records in, one classified difference out.")
    parser.add_argument("--runtime", choices=("selected", "full"), default="selected",
                        help="which runtime the C compiler sees: only the reachable units "
                             "(default) or the whole runtime (the pre-N8 behaviour)")
    parser.add_argument("--label", default=None,
                        help="free-form label recorded with the profile")
    parser.add_argument("--cc", default=os.environ.get("CC", "cc"))
    parser.add_argument("--opt", default="O2", choices=("O0", "O1", "O2", "O3", "Os"))
    parser.add_argument("--stage-samples", type=int, default=STAGE_SAMPLES)
    parser.add_argument("--process-samples", type=int, default=PROCESS_SAMPLES)
    parser.add_argument("--workload", action="append", dest="workloads",
                        help="restrict to these workload names (repeatable)")
    parser.add_argument("--json", metavar="PATH", help="write the record here ('-' for stdout)")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)
    if args.stage_samples < 1 or args.process_samples < 1:
        parser.error("sample counts must be positive")

    cc = shlex.split(args.cc)
    selected = list(WORKLOADS)
    if args.workloads:
        unknown = set(args.workloads) - {spec["name"] for spec in WORKLOADS}
        if unknown:
            parser.error(f"unknown workloads: {', '.join(sorted(unknown))}")
        selected = [spec for spec in WORKLOADS if spec["name"] in set(args.workloads)]

    env = dict(os.environ)
    if args.runtime == "full":
        env["NAVA_FULL_RUNTIME"] = "1"
    else:
        env.pop("NAVA_FULL_RUNTIME", None)
    # The in-process stages read the variable too: `cgen.assemble()` decides
    # there whether to emit the whole runtime. Setting it only on the child
    # environment would label the run "full" while measuring the selective path.
    os.environ.clear()
    os.environ.update(env)

    record = {
        "schema": SCHEMA,
        "label": args.label,
        "runtime": args.runtime,
        "environment": environment(cc),
        "configuration": {"opt": args.opt, "stage_samples": args.stage_samples,
                          "process_samples": args.process_samples,
                          "synthetic_functions": SYNTHETIC_FUNCTIONS,
                          "runtime_env": env.get("NAVA_FULL_RUNTIME")},
        "processes": {},
        "workloads": [],
        "failures": [],
        "model_measurements": {
            "status": "not-measured",
            "note": "no model is called; generation time and token usage are not estimated",
        },
    }
    record["processes"]["python_startup"] = summarise(run_process(
        [sys.executable, "-c", "pass"], args.process_samples, env=env))
    record["processes"]["import_and_cli"] = summarise(run_process(
        [sys.executable, str(ROOT / "nava"), "--version"], args.process_samples, env=env))

    with tempfile.TemporaryDirectory(prefix="nava-profile-") as temp:
        temp_path = Path(temp)
        for spec in selected:
            entry = profile_workload(spec, cc=cc, opt=args.opt, temp=temp_path,
                                     runtime_variant=args.runtime, env=env,
                                     sample_stages=args.stage_samples,
                                     sample_processes=args.process_samples)
            record["workloads"].append(entry)
            if entry["failure"]:
                record["failures"].append({"workload": entry["workload"],
                                           "error": entry["failure"]})

    if not args.quiet:
        print(f"profile of nava {nava_version}, runtime={args.runtime}, -{args.opt}")
        print(f"host: {record['environment']['platform']}; python "
              f"{record['environment']['python']}; cc: {record['environment']['cc_version']}")
        print("Medians in ms. Process rows include interpreter startup.")
        header = (f"{'workload':<10} {'src B':>7} {'frontend':>9} {'emit ms':>8} "
                  f"{'cli ms':>8} {'cc ms':>8} {'C bytes':>8} {'bin B':>7} {'run ms':>8}")
        print(header)
        def cell(entry, key):
            value = entry["processes_ms"].get(key)
            if not isinstance(value, dict):
                return "-"
            return f"{value['median_ms']:.2f}" if value["median_ms"] is not None else "-"
        for entry in record["workloads"]:
            frontend = entry["stages_ms"]["frontend_total"]["median_ms"]
            emit = entry["stages_ms"]["emit"]["median_ms"]
            print(f"{entry['workload']:<10} {entry['source_bytes']:>7} {frontend:>9.3f} "
                  f"{emit:>8.3f} {cell(entry, 'cli_emit_c'):>8} {cell(entry, 'cc'):>8} "
                  f"{entry['emitted_c_bytes']:>8} "
                  f"{str(entry['binary_bytes'] or '-'):>7} {cell(entry, 'run'):>8}")
        print(f"python startup {record['processes']['python_startup']['median_ms']:.2f} ms, "
              f"nava --version {record['processes']['import_and_cli']['median_ms']:.2f} ms")
        if record["failures"]:
            for failure in record["failures"]:
                print(f"failed: {failure['workload']}: {failure['error']}", file=sys.stderr)

    if args.json:
        text = json.dumps(record, ensure_ascii=False, indent=2) + "\n"
        if args.json == "-":
            sys.stdout.write(text)
        else:
            path = Path(args.json)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            if not args.quiet:
                print(f"record: {path}")
    return 0 if not record["failures"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
