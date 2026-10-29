"""Module loading, resolution and merging (PLAN.md phase N6).

A Nava module is one UTF-8 file of `.nv` source that contains only functions.
`import name` in the entry file (or in another module) loads `name.nv` and makes
the module's functions callable as `name::function`.

Resolution is deterministic and has no search-path list to configure:

1. `<project root>/<name>.nv`, where the project root is the directory of the
   entry file unless `--root` says otherwise;
2. a **vendored package**, `vendor/<package>/<name>.nv`, for every package the
   project's `nava.lock` names (PLAN.md N13c);
3. the packaged standard library, `<package>/stdlib/<name>.nv`.

The order is the rule, and the two refusals around it are what make the order
worth stating:

* a local file may not shadow a standard module, and neither may a package's file
  (**`E0360`**) — a shadowing file is an error rather than a silent precedence
  rule, because the name in the source would mean one thing to the reader and
  another to the compiler;
* a module may come only from a package **`nava.lock` names**. A `vendor/` tree is
  whatever happens to be on disk; the lockfile is what the project resolved, and a
  module from anywhere else is `E0810` — the gate's sentence "no transitive
  dependency can appear that the lockfile does not name", enforced where it can be
  broken;
* two locked packages may not provide the same module name (**`E0811`**): which of
  the two a name means is not something the compiler should decide by directory
  order.

A module path is rejected when its resolved location (realpath, so symbolic links
are followed) lies outside the directory the module name was resolved in, so an
import cannot reach arbitrary files.

The loader merges every module into one `Program`; the checker resolves names
per module and the C generator emits a single translation unit.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from .diagnostics import make
from .model import Import, NavaError, Number, Program, Return
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


#: A vendored package as the loader sees it: its name and its directory.
Vendored = tuple


def vendored_packages(root: Path, locked: tuple[str, ...] = ()) -> list[tuple[str, Path, bool]]:
    """The packages under `<root>/vendor/`, as (name, directory, is_locked).

    Every directory there is listed, locked or not, because the loader has to be
    able to tell a package the lockfile names from one it does not: a module that a
    stray directory provides is `E0810` rather than "not found", and a message that
    says which package offered it is the one that can be acted on. The order is by
    name, so two machines resolve the same way.
    """
    vendor = root / "vendor"
    if not vendor.is_dir():
        return []
    found: list[tuple[str, Path, bool]] = []
    for directory in sorted(vendor.iterdir(), key=lambda item: item.name.encode("utf-8")):
        if directory.is_dir() and not directory.name.startswith("."):
            found.append((directory.name, directory, directory.name in locked))
    return found


def resolve(name: str, root: Path, stdlib_root: Path, loc,
            packages: list[tuple[str, Path, bool]] | None = None) -> tuple[Path, bool, str]:
    """Find the file of `name`; returns (path, is_standard, package).

    `package` is the name of the vendored package that provided the module, or ""
    for a module of the project or of the standard library.
    """
    local = module_file(root, name)
    standard = module_file(stdlib_root, name)
    packages = packages or []
    offered = [(package, directory / (name + MODULE_SUFFIX), locked)
               for package, directory, locked in packages
               if (directory / (name + MODULE_SUFFIX)).is_file()]
    locked = [item for item in offered if item[2]]
    unlocked = [item for item in offered if not item[2]]
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
    if len(locked) > 1:
        names = " and ".join(f"vendor/{package}" for package, _, _ in locked)
        raise make(
            "E0811",
            f"module {name!r} is provided by two packages: {names}",
            loc,
            loc.advance(name),
            "a module name resolves to one file; rename it in one of the packages",
        )
    if locked and standard_exists:
        package = locked[0][0]
        raise make(
            "E0360",
            f"package {package!r} provides module {name!r}, which is a standard module",
            loc,
            loc.advance(name),
            f"rename the file inside vendor/{package}; the name {name} is provided by "
            f"the standard library",
        )
    if not local_exists and not locked and unlocked:
        package, path, _ = unlocked[0]
        raise make(
            "E0810",
            f"module {name!r} is provided by the package {package!r}, which nava.lock "
            f"does not name",
            loc,
            loc.advance(name),
            f"{path} exists, but the lockfile is what this project resolved: add "
            f'`{package} = "<constraint>"` to nava.toml and run `nava lock`, or '
            f"remove the directory",
        )
    if not local_exists and not locked and not standard_exists:
        # Every location that was searched, whether or not a file is there: a
        # reader who has to guess which paths were considered has been told less
        # than the compiler knows.
        tried = [str(local)]
        tried.extend(str(directory / (name + MODULE_SUFFIX))
                     for _, directory, _ in packages)
        tried.append(str(standard))
        raise make(
            "E0352",
            f"module {name!r} not found",
            loc,
            loc.advance(name),
            f"looked for {' and '.join(tried)}; a module is one file named "
            f"<module>{MODULE_SUFFIX}",
        )
    if local_exists:
        path, is_standard, package = local, False, ""
    elif locked:
        path, is_standard, package = locked[0][1], False, locked[0][0]
    else:
        path, is_standard, package = standard, True, ""
    if path.is_dir():
        raise make("E0352", f"module {name!r} is a directory, not a file",
                   loc, loc.advance(name),
                   f"a module is one file: {name}{MODULE_SUFFIX}")
    boundary = stdlib_root if is_standard else (root if not package else path.parent)
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
    return resolved, is_standard, package


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


def locked_packages(root: Path) -> tuple[str, ...]:
    """The package names `nava.lock` names, after checking it against the tree.

    Three things happen here, and the last is the one that matters most:

    1. no lockfile means no packages — the ordinary case of a program with no
       dependencies;
    2. a lockfile that exists is read, and its package names are the packages the
       loader will search;
    3. when the project also has a `nava.toml`, the lockfile is **checked** against
       the tree before anything is compiled (`E0803` for a graph that moved,
       `E0804` for bytes that no longer match their digest). A build is the place
       where "a tampered package fails the build" has to be true, so the check is
       here rather than only in `nava lock --check`.

    The manifest is read with `from_text`, without the entry-file checks: the
    compiler may be pointed at a file that is not the one `build.entry` names, and
    that is not this function's business.
    """
    from . import lockfile
    from . import manifest as manifest_module

    path = root / lockfile.LOCK_NAME
    if not path.is_file():
        return ()
    locked = lockfile.read(path)
    manifest_path = root / manifest_module.MANIFEST_NAME
    if manifest_path.is_file():
        project = manifest_module.from_text(manifest_path.read_text(encoding="utf-8"),
                                            manifest_path)
        lockfile.check(project, locked)
    return tuple(item.name for item in locked.packages)


def load(entry: Path, root: Optional[Path] = None,
         stdlib_root: Optional[Path] = None,
         entry_program: Optional[Program] = None) -> Program:
    """Load `entry` and every module it imports, merged into one `Program`.

    `entry_program` supplies an already-parsed entry file; the compiler uses it
    when the source text came from memory (and `entry` only locates the root).
    Modules may come from the project, from a package `nava.lock` names, or from
    the standard library, in that order (see `resolve`).
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
    module_packages: dict[str, str] = {}
    order: list[str] = []
    stack: list[str] = []
    packages = vendored_packages(root, locked_packages(root))

    def visit(name: str, path: Path, is_standard: bool, loc=None,
              package: str = "") -> None:
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
        if package:
            module_packages[name] = package
        imports_by_module[name] = list(program.imports)
        order.append(name)
        stack.append(name)
        try:
            for item in program.imports:
                target, standard, provider = resolve(item.name, root, stdlib_root,
                                                     item.name_loc, packages)
                item.standard = standard
                item.package = provider
                visit(item.name, target, standard, item.name_loc, provider)
        finally:
            stack.pop()

    visit(ENTRY_MODULE, entry.resolve(), False, None, "")

    functions = []
    for name in order:
        functions.extend(programs[name].functions)
    merged = Program(functions)
    merged.module_editions = editions
    merged.module_packages = module_packages
    merged.edition = editions.get(ENTRY_MODULE, 1)
    merged.imports = list(imports_by_module.get(ENTRY_MODULE, []))
    merged.imports_by_module = imports_by_module
    merged.sources = sources
    return merged
