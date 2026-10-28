"""Module loading, resolution and merging (PLAN.md phase N6).

A Nava module is one UTF-8 file of `.nv` source that contains only functions.
`import name` in the entry file (or in another module) loads `name.nv` and makes
the module's functions callable as `name::function`.

Resolution is deterministic and has no search-path list to configure:

1. `<project root>/<name>.nv`, where the project root is the directory of the
   entry file unless `--root` says otherwise;
2. the packaged standard library, `<package>/stdlib/<name>.nv`.

A local file may not shadow a standard module; that is an error rather than a
silent precedence rule.  A module path is rejected when its resolved location
(realpath, so symbolic links are followed) lies outside the directory the module
name was resolved in, so an import cannot reach arbitrary files.

The loader merges every module into one `Program`; the checker resolves names
per module and the C generator emits a single translation unit.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from .diagnostics import make
from .model import Import, Number, Program, Return
from .parser import parse

MODULE_SUFFIX = ".nv"
#: Version of the packaged standard library.  Every standard module declares
#: `fn module_version() -> i64` and the loader refuses a module that declares a
#: different number, so a stale copy cannot be used silently.
STDLIB_VERSION = 1
#: Name of the entry module; its functions are visible without qualification.
ENTRY_MODULE = ""


def stdlib_path() -> Path:
    """The standard library shipped with this compiler."""
    return Path(__file__).resolve().parent / "stdlib"


def module_file(root: Path, name: str) -> Path:
    return root / (name + MODULE_SUFFIX)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise make("E0111", "module source is not valid UTF-8",
                   hint=f"save {path.name} as UTF-8") from exc


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def resolve(name: str, root: Path, stdlib_root: Path, loc) -> tuple[Path, bool]:
    """Find the file of `name`; returns (path, is_standard)."""
    local = module_file(root, name)
    standard = module_file(stdlib_root, name)
    local_exists = local.exists()
    standard_exists = standard.exists()
    if local_exists and standard_exists:
        raise make(
            "E0360",
            f"local module {name!r} shadows a standard module",
            loc,
            loc.advance(name),
            f"rename the file; the name {name} is provided by the standard library",
        )
    if not local_exists and not standard_exists:
        raise make(
            "E0352",
            f"module {name!r} not found",
            loc,
            loc.advance(name),
            f"looked for {local} and {standard}; a module is one file named "
            f"<module>{MODULE_SUFFIX}",
        )
    path, is_standard = (local, False) if local_exists else (standard, True)
    if path.is_dir():
        raise make("E0352", f"module {name!r} is a directory, not a file",
                   loc, loc.advance(name),
                   f"a module is one file: {name}{MODULE_SUFFIX}")
    boundary = stdlib_root if is_standard else root
    resolved = path.resolve()
    if not _inside(resolved, boundary.resolve()):
        raise make(
            "E0356",
            f"module {name!r} resolves outside the project boundary",
            loc,
            loc.advance(name),
            f"{resolved} is not inside {boundary}; an import may not escape the "
            f"directory the module name resolves in",
        )
    return resolved, is_standard


def check_exported(name: str, program: Program, path: Path) -> None:
    """Two rules that only apply to a module that is not the entry file."""
    for function in program.functions:
        if function.name == "main":
            raise make(
                "E0357",
                f"module {name!r} must not define fn main()",
                function.loc,
                function.loc.advance("fn main"),
                "only the entry file may define main; rename the function",
            )


def check_version(name: str, program: Program, stdlib_root: Path) -> None:
    """A standard module must declare the library version it belongs to."""
    version = None
    for function in program.functions:
        if function.name == "module_version":
            version = function
            break
    if version is None:
        raise make(
            "E0361",
            f"standard module {name!r} does not declare fn module_version()",
            hint=f"every module in {stdlib_root} declares the library version it "
                 f"implements; this compiler supports version {STDLIB_VERSION}",
        )
    if version.returns != "i64" or version.params:
        raise make(
            "E0361",
            f"standard module {name!r} declares an invalid module_version",
            version.loc,
            version.loc.advance("fn module_version"),
            "declare it as `fn module_version() -> i64` returning the version",
        )
    declared = None
    if (len(version.body) == 1 and isinstance(version.body[0], Return)
            and isinstance(version.body[0].value, Number)):
        declared = int(version.body[0].value.value)
    if declared != STDLIB_VERSION:
        found = "a computed value" if declared is None else f"version {declared}"
        raise make(
            "E0361",
            f"standard module {name!r} declares {found}",
            version.loc,
            version.loc.advance("fn module_version"),
            f"this compiler implements standard library version {STDLIB_VERSION}; "
            f"replace the module with the copy that ships with it",
        )


def load(entry: Path, root: Optional[Path] = None,
         stdlib_root: Optional[Path] = None,
         entry_program: Optional[Program] = None) -> Program:
    """Load `entry` and every module it imports, merged into one `Program`.

    `entry_program` supplies an already-parsed entry file; the compiler uses it
    when the source text came from memory (and `entry` only locates the root).
    """
    entry = Path(entry)
    root = Path(root).resolve() if root is not None else entry.resolve().parent
    stdlib_root = Path(stdlib_root).resolve() if stdlib_root is not None else stdlib_path()
    if not root.is_dir():
        raise make("E0505", f"project root does not exist: {root}")

    programs: dict[str, Program] = {}
    sources: dict[str, str] = {}
    editions: dict[str, int] = {}
    imports_by_module: dict[str, list[Import]] = {}
    order: list[str] = []
    stack: list[str] = []

    def visit(name: str, path: Path, is_standard: bool, loc=None) -> None:
        if name in stack:
            cycle = " -> ".join(stack[stack.index(name):] + [name])
            raise make(
                "E0353",
                f"modules import each other in a cycle: {cycle}",
                loc,
                loc.advance(name) if loc is not None else None,
                "a module graph must be acyclic; move the shared function "
                "into a third module",
            )
        if name in programs:
            return
        programs[name] = Program([])  # marks it as visited before recursing
        if name == ENTRY_MODULE and entry_program is not None:
            program = entry_program
        else:
            program = parse(_read(path), path=str(path))
        program.sources = {}
        for function in program.functions:
            function.module = name
        seen: set[str] = set()
        for item in program.imports:
            if item.name in seen:
                raise make(
                    "E0355",
                    f"module {item.name!r} is imported twice",
                    item.name_loc,
                    item.name_loc.advance(item.name),
                    "one import is enough; the name is visible for the whole file",
                )
            seen.add(item.name)
        if name != ENTRY_MODULE:
            check_exported(name, program, path)
        if is_standard:
            check_version(name, program, stdlib_root)
        editions[name] = getattr(program, "edition", 1)
        programs[name] = program
        sources[name] = str(path)
        imports_by_module[name] = list(program.imports)
        order.append(name)
        stack.append(name)
        try:
            for item in program.imports:
                target, standard = resolve(item.name, root, stdlib_root, item.name_loc)
                item.standard = standard
                visit(item.name, target, standard, item.name_loc)
        finally:
            stack.pop()

    visit(ENTRY_MODULE, entry.resolve(), False)

    functions = []
    for name in order:
        functions.extend(programs[name].functions)
    merged = Program(functions)
    merged.module_editions = editions
    merged.edition = editions.get(ENTRY_MODULE, 1)
    merged.imports = list(imports_by_module.get(ENTRY_MODULE, []))
    merged.imports_by_module = imports_by_module
    merged.sources = sources
    return merged
