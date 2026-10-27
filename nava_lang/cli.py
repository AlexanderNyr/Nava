from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import __version__
from .cgen import generate
from .checker import check
from .diagnostics import format_human, make, report_json
from .model import Diagnostic, NavaError
from .parser import parse


@dataclass
class Compilation:
    """Result of the front end: generated C and non-fatal diagnostics."""

    code: str
    warnings: list[Diagnostic] = field(default_factory=list)


def compile_source(source: str) -> Compilation:
    """Parse, check and emit C. Raises NavaError with a stable code."""
    program = parse(source)
    check(program)
    return Compilation(generate(program), list(program.warnings))


def frontend(source: str) -> str:
    """Generated C for `source`. Kept as the stable convenience entry point."""
    return compile_source(source).code


def positive_float(value: str) -> float:
    try:
        result = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive number") from exc
    if not (0 < result < float("inf")):
        raise argparse.ArgumentTypeError("must be a positive finite number")
    return result


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
    c_cmd = sub.add_parser("emit-c", help="emit portable C11 source")
    c_cmd.add_argument("source")
    c_cmd.add_argument("-o", "--output", help="output path (default: stdout)")
    build = sub.add_parser("build", help="compile a native executable")
    build.add_argument("source")
    build.add_argument("-o", "--output", required=True, help="executable path")
    build.add_argument("--cc", help="C compiler command (default: $CC or cc)")
    build.add_argument("--opt", choices=("0", "2", "3"), default="2", help="optimization level (default: 2)")
    run = sub.add_parser("run", help="compile and execute a program")
    run.add_argument("source")
    run.add_argument("--timeout", type=positive_float, help="terminate the program after this many seconds")
    run.add_argument("--cc", help="C compiler command (default: $CC or cc)")
    run.add_argument("--opt", choices=("0", "2", "3"), default="2", help="optimization level (default: 2)")
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
        try:
            compilation = compile_source(source)
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
                try:
                    result = subprocess.run([str(binary)], timeout=args.timeout, check=False)
                except subprocess.TimeoutExpired:
                    print(f"nava: execution exceeded {args.timeout:g} seconds", file=sys.stderr)
                    return 124
                return result.returncode
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
