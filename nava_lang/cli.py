from __future__ import annotations

import argparse
import os
import shlex
import sys
from pathlib import Path

# N8: `subprocess`, `tempfile`, `shutil` and `nava_lang/sandbox.py` are imported
# by the commands that need them instead of at start-up.  None of them is used
# by `check`, `emit-c` or `--version`, and importing them costs more than the
# whole front end of a small program (see bench/profile.py).
from . import __version__, limits
from .cgen import generate
from .checker import check
from . import modules
from .diagnostics import format_human, make, report_json
from .model import Diagnostic, NavaError
from .parser import parse
from .records import field, record


def __getattr__(name: str):
    """Create `cli.sandbox` on first use (PEP 562).

    The sandbox is only needed by `run`, so the module is imported inside that
    command; tests and embedders still reach it as `cli.sandbox`, which would
    otherwise raise `AttributeError`.  Nothing is imported here that the caller
    did not ask for.
    """
    if name == "sandbox":
        from . import sandbox as module
        globals()["sandbox"] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


@record()
class Compilation:
    """Result of the front end: generated C and non-fatal diagnostics."""

    code: str
    warnings: list[Diagnostic] = field(default_factory=list)
    #: Names of the vendored packages a module came from, sorted. A build record
    #: lists these, so "which packages are in this binary" is answerable from the
    #: build rather than from a reading of the sources.
    packages: list[str] = field(default_factory=list)
    #: Module name -> the file its source came from. The build record digests these
    #: files, so it describes what was compiled rather than what is on disk later.
    sources: dict[str, str] = field(default_factory=dict)


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
    packages = sorted(set(getattr(program, "module_packages", {}).values()))
    sources = dict(getattr(program, "sources", {}) or {})
    if not sources and path is not None:
        # A program with no imports is one file, and `modules.load` never ran, so
        # the source map has to be filled here: a build record that cannot name the
        # file it compiled would report an empty source digest and look reproducible
        # for the wrong reason.
        sources = {"": str(path)}
    return Compilation(generate(program, capabilities), list(program.warnings),
                       packages, sources)


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
    command.add_argument("--project", action="store_true",
                         help="compile the entry named by nava.toml, found above the "
                              "working directory or inside --root")
    command.add_argument("--offline", action="store_true",
                         help="resolve packages from vendor/ only; this is the default "
                              "and the only mode that exists, and the flag is accepted "
                              "so a build script can say what it means")
    command.add_argument("--network", action="store_true",
                         help="ask for network access while resolving packages; refused "
                              "with E0812, because there is no registry to reach")
    command.add_argument("--allow-read", action="append", metavar="DIR",
                         help="permit read_file inside DIR (repeatable, no access by "
                              "default)")
    command.add_argument("--allow-write", action="append", metavar="DIR",
                         help="permit write_file inside DIR (repeatable, no access by "
                              "default)")


def document_modules(names: list[str], out: Path | None) -> list[Path]:
    """`nava doc`: the reference generated from the modules' contracts.

    A name is a path to a `.nv` file, or the name of a packaged standard module.
    With no name at all the whole standard library is documented, in the order the
    files are read, so the output is the same on every machine.
    """
    from . import contracts
    from .parser import parse as parse_module

    root = Path(__file__).resolve().parent.parent
    if not names:
        names = sorted(path.stem for path in modules.stdlib_path().glob("*.nv"))
    written: list[Path] = []
    for name in names:
        path = Path(name)
        if not path.is_file():
            path = modules.stdlib_path() / (name + ".nv")
        if not path.is_file():
            raise make("E0508", f"no module named {name!r}: neither a file nor a "
                                f"standard module")
        program = parse_module(path.read_text(encoding="utf-8"), path=str(path))
        try:
            display = path.resolve().relative_to(root).as_posix()
        except ValueError:
            display = Path(name).as_posix()
        text = contracts.render_module(path.stem, Path(display), program,
                                       contracts.module_version(program))
        if out is None:
            sys.stdout.write(text)
            continue
        out.mkdir(parents=True, exist_ok=True)
        target = out / (path.stem + ".md")
        target.write_text(text, encoding="utf-8")
        written.append(target)
    return written


def resolve_source_argument(args: argparse.Namespace) -> Path:
    """The entry file a command should compile: the one named, or the project's.

    `--project` reads `nava.toml`, and the entry file and the project root both
    come from it, so module resolution and the build agree about what the project
    is. Naming a source as well is refused rather than guessed at: two answers to
    "which program" is a question the caller did not mean to ask.
    """
    from . import manifest as manifest_module

    if getattr(args, "project", False):
        if args.source:
            raise make("E0509", f"give a source file or --project, not both: "
                                f"{args.source!r} and --project disagree")
        project = manifest_module.resolve(args.root)
        if not args.root:
            args.root = str(project.root)
        return project.entry_path
    if not args.source:
        raise make("E0509", "no source file: name one, or pass --project to compile "
                            "the entry named by nava.toml")
    return Path(args.source)


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
    check_cmd.add_argument("source", nargs="?",
                        help="source file; omit it with --project")
    add_project_options(check_cmd)
    c_cmd = sub.add_parser("emit-c", help="emit portable C11 source")
    c_cmd.add_argument("source", nargs="?",
                        help="source file; omit it with --project")
    c_cmd.add_argument("-o", "--output", help="output path (default: stdout)")
    add_project_options(c_cmd)
    build = sub.add_parser("build", help="compile a native executable")
    build.add_argument("source", nargs="?",
                        help="source file; omit it with --project")
    build.add_argument("-o", "--output", required=True, help="executable path")
    build.add_argument("--cc", help="C compiler command (default: $CC or cc)")
    build.add_argument("--opt", choices=("0", "2", "3"), default="2", help="optimization level (default: 2)")
    build.add_argument("--record", type=Path, metavar="PATH",
                       help="write a build record (JSON) describing what went into "
                            "this binary: source digests, the generated C, the "
                            "toolchain and SOURCE_DATE_EPOCH")
    add_project_options(build)
    doc_cmd = sub.add_parser(
        "doc", help="write the reference generated from the modules' contracts",
        description="Generate reference documentation from the doc blocks of one "
                    "module or a few. With no module named, the whole standard "
                    "library is documented.",
    )
    doc_cmd.add_argument("modules", nargs="*",
                         help="module names or .nv files (default: the standard library)")
    doc_cmd.add_argument("--out", type=Path, metavar="DIR",
                         help="write one Markdown file per module into DIR "
                              "(default: stdout)")
    project_cmd = sub.add_parser(
        "project", help="read and check nava.toml",
        description="Find nava.toml at or above a directory, check it and describe "
                    "the project it declares. Nothing is compiled and nothing is "
                    "downloaded.",
    )
    project_cmd.add_argument("path", nargs="?", help="directory to start from "
                                                    "(default: the working directory)")
    lock_cmd = sub.add_parser(
        "lock", help="resolve the dependencies and write or check nava.lock",
        description="Resolve the project's dependencies from vendor/ and write "
                    "nava.lock, or with --check compare the lockfile with the tree "
                    "and stop at the first disagreement.",
    )
    lock_cmd.add_argument("path", nargs="?", help="directory to start from "
                                                 "(default: the working directory)")
    lock_cmd.add_argument("--check", action="store_true",
                          help="check the lockfile instead of writing it")
    reproduce_cmd = sub.add_parser(
        "reproduce", help="build a program twice and require the same bytes",
        description="Build the same source twice, in two temporary directories, and "
                    "compare the records: the source digests, the generated C, the "
                    "binary, the toolchain and SOURCE_DATE_EPOCH. With --record, the "
                    "first build is additionally compared with a previous record.",
    )
    reproduce_cmd.add_argument("source", help="source file to build")
    reproduce_cmd.add_argument("--runs", type=int, default=2,
                               help="how many builds to compare (default: 2)")
    reproduce_cmd.add_argument("--record", type=Path, metavar="PATH",
                               help="compare the build with a record written earlier")
    reproduce_cmd.add_argument("--cc", help="C compiler command (default: $CC or cc)")
    reproduce_cmd.add_argument("--opt", choices=("0", "2", "3"), default="2",
                               help="optimization level (default: 2)")
    add_project_options(reproduce_cmd)
    run = sub.add_parser(
        "run", help="compile and execute a program",
        description="Compile and run a program. By default the program runs under the best "
                    "available isolation: Linux namespaces when `unshare` works, otherwise "
                    "rlimits only with a warning. `--isolation required` refuses to run "
                    "instead of falling back.",
    )
    run.add_argument("source", nargs="?",
                        help="source file; omit it with --project")
    run.add_argument("--timeout", type=positive_float, help="terminate the program after this many seconds")
    run.add_argument("--cc", help="C compiler command (default: $CC or cc)")
    run.add_argument("--opt", choices=("0", "2", "3"), default="2", help="optimization level (default: 2)")
    run.add_argument("--isolation", choices=limits.ISOLATION_MODES, default="auto",
                     help="isolation policy: auto (default), required, or off (explicitly unsafe)")
    run.add_argument("--memory", type=int, default=256,
                     help="address-space limit for the program in MiB (default: 256)")
    add_project_options(run)
    run.add_argument("--workdir", help="directory the program may write to and run in "
                                       "(default: a temporary directory)")
    # `nargs="*"` rather than REMAINDER: with REMAINDER, `nava run f.nv --timeout 2`
    # would hand `--timeout 2` to the program instead of to `nava`, which is a
    # trap. argparse strips the `--` fence itself, so what arrives here is
    # exactly the words after it.
    run.add_argument("program_args", nargs="*", metavar="-- ARG …",
                     help="everything after `--` is passed to the program, word for "
                          "word, and is what `arg_count`/`arg_at` see")
    return parser


def compile_c(code: str, binary: Path, cc: str | None, opt: str) -> bool:
    import subprocess  # imported here, not at start-up (N8)
    import tempfile

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


def tempfile_for_reproduce():
    """A fresh temporary directory for one build of `nava reproduce`."""
    import tempfile

    return tempfile.TemporaryDirectory(prefix="nava-reproduce-")


def print_diagnostic(error: NavaError) -> None:
    """One diagnostic on stderr, with its code and its hint.

    The commands that read a project file rather than a program (`project`, `lock`)
    have no source location to print, so they name the code and keep the hint: a
    reader who has to look up what `E0804` means has been told something, but a
    reader who is told "restore the package" has been helped.
    """
    print(f"nava: error[{error.diagnostic.code}]: {error.diagnostic.message}",
          file=sys.stderr)
    if error.diagnostic.hint:
        print(f"  hint: {error.diagnostic.hint}", file=sys.stderr)


def report_warnings(warnings: list[Diagnostic], path: Path, machine: bool) -> None:
    """Warnings go to stderr in human mode and into the JSON document otherwise."""
    if machine:
        return
    for warning in warnings:
        print(format_human(str(path), warning), file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    machine = args.format == "json"
    if args.command == "doc":
        # `doc` reads modules rather than a program: it needs no `main`, no
        # capabilities and no C compiler, so it returns before any of that.
        try:
            written = document_modules(args.modules, args.out)
            if machine:
                print(report_json("nava doc", "ok", [],
                                  {"written": [str(item) for item in written]}))
            elif not written:
                pass
            else:
                for item in written:
                    print(f"Wrote: {item}")
            return 0
        except NavaError as exc:
            if machine:
                print(report_json("nava doc", "error", [exc.diagnostic]))
            else:
                print(f"nava: {exc.diagnostic.message}", file=sys.stderr)
            return 2
    if args.command == "lock":
        # Like `doc` and `project`, this command reads files that are not a
        # program, so it needs no `main` and no C compiler.
        from . import lockfile as lockfile_module
        from . import manifest as manifest_module

        try:
            project = manifest_module.resolve(args.path)
            if args.check:
                locked = lockfile_module.read(project.root / lockfile_module.LOCK_NAME)
                lockfile_module.check(project, locked)
                if machine:
                    print(report_json(str(project.path), "ok", [],
                                      {"checked": str(project.root / lockfile_module.LOCK_NAME),
                                       **lockfile_module.summary_json(locked)}))
                else:
                    print(f"OK: {lockfile_module.LOCK_NAME} describes "
                          f"{project.name} {project.version} "
                          f"({len(locked.packages)} packages)")
                return 0
            resolution = lockfile_module.resolve(project)
            path = lockfile_module.write(resolution,
                                         project.root / lockfile_module.LOCK_NAME)
            if machine:
                print(report_json(str(project.path), "ok", [],
                                  {"written": str(path),
                                   **lockfile_module.summary_json(resolution)}))
            else:
                sys.stdout.write(lockfile_module.graph(resolution, project))
                print(f"Wrote: {path}")
            return 0
        except NavaError as exc:
            where = str(Path(args.path or ".") / manifest_module.MANIFEST_NAME)
            if machine:
                print(report_json(where, "error", [exc.diagnostic]))
            else:
                print_diagnostic(exc)
            return 2

    if args.command == "project":
        # Like `doc`, the project command reads a file that is not a program, so
        # it needs no `main` and no C compiler.
        from . import manifest as manifest_module

        try:
            found = manifest_module.resolve(args.path)
            if machine:
                print(report_json(str(found.path), "ok", [],
                                  manifest_module.summary_json(found)))
            else:
                sys.stdout.write(manifest_module.summarize(found))
            return 0
        except NavaError as exc:
            where = str(Path(args.path or ".") / manifest_module.MANIFEST_NAME)
            if machine:
                print(report_json(where, "error", [exc.diagnostic]))
            else:
                print_diagnostic(exc)
            return 2

    # `path` is set before the `try` so the error handler has somewhere to point
    # even when the failure is "you did not name a program".
    path = Path(args.source) if args.source else Path(getattr(args, "root", None) or ".")
    try:
        if getattr(args, "network", False):
            raise make(
                "E0812",
                "--network is not available: this compiler resolves packages from "
                "vendor/ and has no registry to reach",
                hint="offline is the only mode; drop --network, or vendor the package",
            )
        if args.command in ("build", "reproduce", "run"):
            # The convention is about builds, so a build reads it even when no
            # record is written: a value that cannot be used is refused up front.
            from . import reproducible

            reproducible.source_date_epoch()
        path = resolve_source_argument(args)
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
        # Everything after `--` belongs to the program, word for word. argparse
        # keeps the `--` itself in a REMAINDER, so it goes first; an argument
        # that begins with `-` is kept, because the program may well want one.
        program_args = list(getattr(args, "program_args", []) or [])
        if program_args[:1] == ["--"]:
            program_args = program_args[1:]
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
                                  {"source_bytes": len(source.encode("utf-8")),
                                   "packages": compilation.packages,
                                   "network": "offline"}))
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
            build = None
            if getattr(args, "record", None):
                from . import reproducible
                build = reproducible.record(compilation.sources, code, output,
                                            compilation.packages, args.cc)
                args.record.parent.mkdir(parents=True, exist_ok=True)
                args.record.write_text(reproducible.render(build), encoding="utf-8")
            extra = {"output": str(output), "packages": compilation.packages,
                     "network": "offline"}
            if build is not None:
                extra["record"] = str(args.record)
                extra["binary_digest"] = build.binary_digest
                extra["c_digest"] = build.c_digest
                extra["source_digest"] = build.source_digest
            if machine:
                print(report_json(str(path), "ok", compilation.warnings, extra))
            else:
                if compilation.packages:
                    print(f"Packages: {', '.join(compilation.packages)} (from nava.lock)")
                print(f"Built: {output}")
                if build is not None:
                    print(f"Record: {args.record} ({build.binary_digest})")
            return 0
        if args.command == "reproduce":
            from . import reproducible

            runs = max(2, int(getattr(args, "runs", 2) or 2))
            records = []
            for number in range(runs):
                with tempfile_for_reproduce() as directory:
                    binary = Path(directory) / f"program-{number}"
                    if not compile_c(code, binary, args.cc, args.opt):
                        return 2
                    records.append(reproducible.record(
                        compilation.sources, code, binary, compilation.packages, args.cc))
            # Every pair is compared with the first, and every difference from every
            # comparison is reported: a build that is reproducible once and not twice
            # is not reproducible.
            differences: list[str] = []
            for other in records[1:]:
                differences.extend(reproducible.compare(records[0], other))
            if getattr(args, "record", None):
                previous = reproducible.parse(args.record.read_text(encoding="utf-8"),
                                              str(args.record))
                differences.extend(reproducible.compare(previous, records[0]))
            digest = records[0].binary_digest
            if differences:
                raise make(
                    "E0814",
                    f"{runs} builds of {path} are not identical:\n  "
                    + "\n  ".join(dict.fromkeys(differences)),
                    hint="a difference in the toolchain explains a difference in the "
                         "digests; pin the compiler, and set SOURCE_DATE_EPOCH if the "
                         "toolchain embeds a time",
                )
            if machine:
                print(report_json(str(path), "ok", compilation.warnings,
                                  {"reproducible": True, "runs": runs,
                                   "binary_digest": digest,
                                   "record": str(args.record) if getattr(args, "record", None) else None}))
            else:
                print(f"reproducible over {runs} builds: {digest}")
            return 0

        if args.command == "run":
            # A run prints the program's own output. The package list goes to
            # stderr only when the caller asked for JSON, so a program's stdout
            # stays exactly what the program wrote.
            import shutil
            import tempfile

            from . import sandbox

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
                sandbox_limits = sandbox.Limits(
                    wall_seconds=args.timeout,
                    cpu_seconds=max(1, int(args.timeout) if args.timeout else 3600),
                    memory_bytes=max(1, args.memory) * 1024 * 1024,
                )
                try:
                    outcome = sandbox.run([str(binary), *program_args], limits=sandbox_limits,
                                          mode=args.isolation,
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
