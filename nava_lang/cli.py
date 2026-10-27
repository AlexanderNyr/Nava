from __future__ import annotations

import argparse
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import __version__
from .cgen import generate
from .checker import check
from . import modules
from . import sandbox
from .diagnostics import format_human, make, report_json
from .model import Diagnostic, NavaError
from .parser import parse


@dataclass
class Compilation:
    """Result of the front end: generated C and non-fatal diagnostics."""

    code: str
    warnings: list[Diagnostic] = field(default_factory=list)


def compile_source(source: str, path: Path | str | None = None,
                   root: Path | str | None = None,
                   stdlib_root: Path | str | None = None,
                   capabilities: dict | None = None) -> Compilation:
    """Parse, check and emit C. Raises NavaError with a stable code.

    Without `path` the source is a single self-contained file: an `import` has
    no directory to resolve against and is reported as an unresolved module.
    With `path` the entry file is the one at that location, so its directory is
    the project root and its imports are loaded from there.
    """
    program = parse(source, path=str(path) if path is not None else None)
    if program.imports:
        if path is None:
            first = program.imports[0]
            raise make(
                "E0352",
                f"module {first.name!r} cannot be resolved",
                first.name_loc,
                first.name_loc.advance(first.name),
                "an import is resolved against the directory of the file it "
                "appears in; compile a file (nava check FILE), not an in-memory "
                "string",
            )
        program = modules.load(Path(path), root=Path(root) if root else None,
                               stdlib_root=Path(stdlib_root) if stdlib_root else None,
                               entry_program=program)
    check(program)
    return Compilation(generate(program, capabilities), list(program.warnings))


def compile_file(path: Path | str, root: Path | str | None = None,
                 stdlib_root: Path | str | None = None,
                 capabilities: dict | None = None) -> Compilation:
    """Compile the file at `path` together with every module it imports."""
    path = Path(path)
    try:
        source = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise make("E0111", "source is not valid UTF-8",
                   hint="save the file as UTF-8") from exc
    return compile_source(source, path=path, root=root, stdlib_root=stdlib_root,
                          capabilities=capabilities)


def frontend(source: str) -> str:
    """Generated C for `source`. Kept as the stable convenience entry point."""
    return compile_source(source).code


def capability_roots(values, option: str, base: Path | None = None) -> list[str]:
    """Validate `--allow-read`/`--allow-write` directories.

    A relative grant is relative to the program's working directory, which is
    what the runtime resolves it against; `base` is that directory when the
    command already knows it (`run --workdir`). The grant is stored as written.
    """
    roots = []
    for value in values or ():
        path = Path(value)
        if base is not None and not path.is_absolute():
            path = base / path
        if not path.exists():
            raise make("E0506", f"{option} directory does not exist: {value}")
        if not path.is_dir():
            raise make("E0507", f"{option} is not a directory: {value}")
        roots.append(value)
    return roots


def positive_float(value: str) -> float:
    try:
        result = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive number") from exc
    if not (0 < result < float("inf")):
        raise argparse.ArgumentTypeError("must be a positive finite number")
    return result


def add_project_options(command: argparse.ArgumentParser) -> None:
    """Options shared by every command that compiles a project."""
    command.add_argument("--root", help="project root for module resolution "
                                        "(default: the directory of the source file)")
    command.add_argument("--allow-read", action="append", metavar="DIR",
                         help="permit read_file inside DIR (repeatable, no access by "
                              "default)")
    command.add_argument("--allow-write", action="append", metavar="DIR",
                         help="permit write_file inside DIR (repeatable, no access by "
                              "default)")


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nava",
        description="Nava: checked code compiled through C11",
        epilog="Diagnostic codes are documented in LANGUAGE.md; use --format json for "
               "machine-readable diagnostics.",
    )
    parser.add_argument("--version", action="version", version=f"Nava {__version__}")
    parser.add_argument(
        "--format", choices=("human", "json"), default="human",
        help="diagnostic output format; json writes one JSON document to stdout "
             "(default: human)",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    check_cmd = sub.add_parser("check", help="parse and type-check a source file")
    check_cmd.add_argument("source")
    add_project_options(check_cmd)
    c_cmd = sub.add_parser("emit-c", help="emit portable C11 source")
    c_cmd.add_argument("source")
    c_cmd.add_argument("-o", "--output", help="output path (default: stdout)")
    add_project_options(c_cmd)
    build = sub.add_parser("build", help="compile a native executable")
    build.add_argument("source")
    build.add_argument("-o", "--output", required=True, help="executable path")
    build.add_argument("--cc", help="C compiler command (default: $CC or cc)")
    build.add_argument("--opt", choices=("0", "2", "3"), default="2", help="optimization level (default: 2)")
    add_project_options(build)
    run = sub.add_parser(
        "run", help="compile and execute a program",
        description="Compile and run a program. By default the program runs under the best "
                    "available isolation: Linux namespaces when `unshare` works, otherwise "
                    "rlimits only with a warning. `--isolation required` refuses to run "
                    "instead of falling back.",
    )
    run.add_argument("source")
    run.add_argument("--timeout", type=positive_float, help="terminate the program after this many seconds")
    run.add_argument("--cc", help="C compiler command (default: $CC or cc)")
    run.add_argument("--opt", choices=("0", "2", "3"), default="2", help="optimization level (default: 2)")
    run.add_argument("--isolation", choices=sandbox.MODES, default="auto",
                     help="isolation policy: auto (default), required, or off (explicitly unsafe)")
    run.add_argument("--memory", type=int, default=256,
                     help="address-space limit for the program in MiB (default: 256)")
    add_project_options(run)
    run.add_argument("--workdir", help="directory the program may write to and run in "
                                       "(default: a temporary directory)")
    return parser


def compile_c(code: str, binary: Path, cc: str | None, opt: str) -> bool:
    try:
        command = shlex.split(cc or os.environ.get("CC") or "cc")
    except ValueError as exc:
        raise make("E0501", f"invalid C compiler command: {exc}") from exc
    if not command:
        raise make("E0502", "empty C compiler command")
    with tempfile.TemporaryDirectory(prefix="nava-cc-") as temp:
        c_file = Path(temp) / "program.c"
        c_file.write_text(code, encoding="utf-8")
        try:
            result = subprocess.run(command + ["-std=c11", f"-O{opt}", str(c_file), "-o", str(binary)], check=False)
        except FileNotFoundError as exc:
            raise make(
                "E0503", f"C compiler {command[0]!r} not found; install gcc/clang or set CC",
                hint="set CC or pass --cc to choose the compiler",
            ) from exc
        if result.returncode != 0:
            print("nava: C compiler failed", file=sys.stderr)
            return False
    return True


def report_warnings(warnings: list[Diagnostic], path: Path, machine: bool) -> None:
    """Warnings go to stderr in human mode and into the JSON document otherwise."""
    if machine:
        return
    for warning in warnings:
        print(format_human(str(path), warning), file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    path = Path(args.source)
    machine = args.format == "json"
    try:
        try:
            source = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise make("E0111", "source is not valid UTF-8",
                       hint="save the file as UTF-8") from exc
        workdir = None
        if getattr(args, "workdir", None):
            candidate = Path(args.workdir)
            if not candidate.exists():
                raise make("E0506", f"--workdir directory does not exist: {args.workdir}")
            if not candidate.is_dir():
                raise make("E0507", f"--workdir is not a directory: {args.workdir}")
            workdir = candidate.resolve()
        capabilities = {"read": [], "write": []}
        if getattr(args, "allow_read", None):
            capabilities["read"] = capability_roots(args.allow_read, "--allow-read", workdir)
        if getattr(args, "allow_write", None):
            capabilities["write"] = capability_roots(args.allow_write, "--allow-write", workdir)
        try:
            compilation = compile_source(source, path=path, root=args.root,
                                         capabilities=capabilities)
        except RecursionError as exc:
            # The parser and checker enforce MAX_NESTING, so this is a safety
            # net: report it as a diagnostic, never as a Python traceback.
            raise make("E0402", "the front end reached its recursion limit",
                       hint="the input is too deeply nested; simplify it") from exc
        code = compilation.code
        report_warnings(compilation.warnings, path, machine)
        if args.command == "check":
            if machine:
                print(report_json(str(path), "ok", compilation.warnings,
                                  {"source_bytes": len(source.encode("utf-8"))}))
            else:
                print(f"OK: {path}")
            return 0
        if args.command == "emit-c":
            if args.output:
                output = Path(args.output)
                if output.resolve() == path.resolve():
                    raise make("E0504", "output must be different from the source file")
                output.write_text(code, encoding="utf-8")
                if machine:
                    print(report_json(str(path), "ok", compilation.warnings,
                                      {"output": str(output)}))
            else:
                if machine:
                    sys.stdout.write(code)
                    print(report_json(str(path), "ok", compilation.warnings), file=sys.stderr)
                else:
                    sys.stdout.write(code)
            return 0
        if args.command == "build":
            output = Path(args.output).resolve()
            if output == path.resolve():
                raise make("E0504", "output must be different from the source file")
            if not output.parent.is_dir():
                raise make("E0505", f"output directory does not exist: {output.parent}")
            if not compile_c(code, output, args.cc, args.opt):
                return 2
            if machine:
                print(report_json(str(path), "ok", compilation.warnings,
                                  {"output": str(output)}))
            else:
                print(f"Built: {output}")
            return 0
        if args.command == "run":
            with tempfile.TemporaryDirectory(prefix="nava-run-") as temp:
                binary = Path(temp) / "program"
                if not compile_c(code, binary, args.cc, args.opt):
                    return 2
                if workdir is not None:
                    # The sandbox exposes exactly one writable directory; with
                    # --workdir it is the caller's, so the program runs there.
                    staged = workdir / ".nava-program"
                    try:
                        shutil.copy2(binary, staged)
                        staged.chmod(0o755)
                    except OSError as exc:
                        raise make("E0507", f"cannot place the program in {workdir}: {exc}") from exc
                    binary = staged
                limits = sandbox.Limits(
                    wall_seconds=args.timeout,
                    cpu_seconds=max(1, int(args.timeout) if args.timeout else 3600),
                    memory_bytes=max(1, args.memory) * 1024 * 1024,
                )
                try:
                    outcome = sandbox.run([str(binary)], limits=limits, mode=args.isolation,
                                          workdir=workdir or Path(temp), capture=False)
                except sandbox.SandboxUnavailable as exc:
                    raise make("E0601", str(exc)) from exc
                for note in outcome.notes:
                    print(f"nava: {note}", file=sys.stderr)
                if outcome.timed_out:
                    print(f"nava: execution exceeded {args.timeout:g} seconds", file=sys.stderr)
                    return 124
                if outcome.survivors:
                    raise make("E0602", "child processes survived termination of the program")
                return outcome.exit_code if outcome.exit_code is not None else 2
        raise AssertionError("unknown command")
    except NavaError as exc:
        if machine:
            print(report_json(str(path), "error", [exc.diagnostic]))
        else:
            print(format_human(str(path), exc.diagnostic), file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"nava: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nnava: interrupted", file=sys.stderr)
        return 130
