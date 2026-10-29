"""`nava.lock`: the resolved dependency graph, by digest (PLAN.md N13b).

A manifest says what a project *wants*; the lockfile says exactly what it *got*,
as bytes. Between the two there is one operation, `resolve`, and everything else
in this module is about making its answer checkable by somebody who was not there
when it was produced.

**A package is a directory, and its identity is a digest.** There is no registry
and no network in this compiler: a dependency is vendored — a directory under
`vendor/` holding a `nava.toml` and its `.nv` files — and the digest is computed
from the files, so a package is identified by what it contains rather than by what
it is called. Two packages may share a name in different projects and be different
things; one package whose bytes change is a different package, and the lockfile
says so.

The digest is SHA-256 over a canonical serialisation of the directory, defined
here so that a second implementation can produce the same number:

    for each regular file, in byte order of its relative path:
        "F\0" + relative path + "\0" + decimal byte length + "\0" + bytes
    end marker: "E\0"

Nothing else is in it: no timestamps, no file modes, no absolute paths, no order
from a directory listing. Two checkouts of the same package therefore agree, and a
single flipped byte does not.

**Resolution walks the graph, and every way it can fail has its own code.** A
dependency is looked up in `vendor/` (`E0805` if it is not there), its own
`nava.toml` is read (`E0808` if the directory is not a package), every constraint
placed on a version is checked (`E0806`), a cycle is refused (`E0807`, naming the
cycle), and the answer is sorted by name so that it does not depend on the order in
which the graph was walked.

**The lockfile is checked, not trusted.** `check` recomputes the resolution and
compares it with the file: a package the lockfile names and the project no longer
needs, a package the project needs and the lockfile does not name, a version or a
dependency list that disagrees, are all `E0803` with the difference named; bytes
that no longer hash to the digest they were locked at are `E0804`, which is the one
code for both "the package was edited" and "the digest was edited by hand" —
because the two are distinguished by the message, which names both digests, rather
than by a code that would claim a knowledge the checker does not have.

The lockfile is the same TOML subset as the manifest, read by the same reader:

    [lock]
    format = 1
    project = "demo"
    version = "0.1.0"

    [[package]]
    name = "json"
    version = "1.2.3"
    digest = "sha256:..."
    source = "vendor/json"
    requires = ["util"]

`requires` is sorted by name, and so are the `[[package]]` blocks; the file that
`write` produces is the same bytes for the same tree, which is what makes it worth
committing.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from . import manifest as manifest_module
from .diagnostics import make
from .manifest import Constraint, Version
from .model import Location
from .records import field, record

#: Name of the lockfile, next to `nava.toml`.
LOCK_NAME = "nava.lock"
#: Format version written into `[lock] format`. A reader refuses anything else.
LOCK_FORMAT = 1
#: Directory a package is vendored into, relative to the project root.
VENDOR_DIR = "vendor"
#: Prefix of every digest, so the algorithm is part of the value.
DIGEST_PREFIX = "sha256:"
#: The tables a lockfile may contain; `package` repeats.
LOCK_TABLES = ("lock", "package")
#: Fields of a `[lock]` table and of a `[[package]]` block.
LOCK_FIELDS = {"format": "integer", "project": "string", "version": "string"}
PACKAGE_FIELDS = {"name": "string", "version": "string", "digest": "string",
                  "source": "string", "requires": "array"}


@record()
class Package:
    """One resolved package: what it is called, what it is, and what it needs."""

    name: str
    version: Version
    digest: str
    source: str
    requires: list[str] = field(default_factory=list)

    def line(self) -> str:
        return f"{self.name} {self.version} {self.digest}"


@record()
class Resolution:
    """The answer `resolve` gives: the project, and its packages sorted by name."""

    project: str
    version: Version
    packages: list[Package] = field(default_factory=list)

    def package(self, name: str) -> Package | None:
        for item in self.packages:
            if item.name == name:
                return item
        return None


def digest_of(directory: Path | str) -> str:
    """The content digest of a package directory.

    Regular files only, in byte order of their relative path, each contributing its
    path, its length and its bytes. A symbolic link is refused rather than
    followed: a package whose content depends on where a link points is not
    identified by its own bytes.
    """
    directory = Path(directory)
    entries: list[tuple[bytes, Path]] = []
    for path in directory.rglob("*"):
        if path.is_symlink():
            raise make(
                "E0808",
                f"{directory.name}: {path.relative_to(directory).as_posix()} is a "
                f"symbolic link; a package is identified by its own bytes",
                hint="replace the link with the file it points at",
            )
        if path.is_file():
            entries.append((path.relative_to(directory).as_posix().encode("utf-8"), path))
    entries.sort(key=lambda item: item[0])
    digest = hashlib.sha256()
    for relative, path in entries:
        data = path.read_bytes()
        digest.update(b"F\x00")
        digest.update(relative)
        digest.update(b"\x00")
        digest.update(str(len(data)).encode("ascii"))
        digest.update(b"\x00")
        digest.update(data)
    digest.update(b"E\x00")
    return DIGEST_PREFIX + digest.hexdigest()


def read_package(directory: Path, name: str) -> tuple[Version, list[manifest_module.Dependency]]:
    """Read the `nava.toml` of a vendored package: its version and its needs.

    A package is not a project: it has no entry file and no build table, so the
    rules about the entry (`E0707`) and about the edition agreeing with an entry
    file (`E0706`) do not apply here. What is checked is what a package is: a name
    that matches the directory it is vendored into, a version, an edition this
    compiler has, and dependencies of its own.
    """
    manifest_path = directory / manifest_module.MANIFEST_NAME
    if not manifest_path.is_file():
        raise make(
            "E0808",
            f"{VENDOR_DIR}/{name} is not a package: it has no {manifest_module.MANIFEST_NAME}",
            hint=f"a vendored package is a directory with {manifest_module.MANIFEST_NAME} "
                 f"and its .nv files",
        )
    try:
        text = manifest_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise make("E0808", f"{VENDOR_DIR}/{name}/{manifest_module.MANIFEST_NAME} "
                            f"is not valid UTF-8") from exc
    sections = manifest_module.read_document(
        text, manifest_path, manifest_module.TABLES, name=manifest_module.MANIFEST_NAME)
    package = manifest_module.first_table(sections, "package")
    if package is None:
        raise make("E0808", f"{VENDOR_DIR}/{name}/{manifest_module.MANIFEST_NAME} "
                            f"has no [package] table")
    for section in sections:
        if section.name == "dependencies":
            continue
        for item in section.values:
            if item.name not in manifest_module.FIELDS.get(section.name, {}):
                raise make("E0704", f"unknown field {section.name}.{item.name} in "
                                    f"{VENDOR_DIR}/{name}/{manifest_module.MANIFEST_NAME}",
                           item.loc)
    declared = package.get("name")
    if declared is not None and str(declared) != name:
        raise make(
            "E0808",
            f"{VENDOR_DIR}/{name} declares the name {declared!r}; a package is vendored "
            f"under its own name",
            package.where("name"),
            hint=f"rename the directory to {declared}",
        )
    version_text = package.get("version")
    if version_text is None:
        raise make("E0703", f"{VENDOR_DIR}/{name}/{manifest_module.MANIFEST_NAME} is "
                            f"missing package.version")
    version = manifest_module.parse_version(str(version_text), package.where("version"))
    edition = package.get("edition")
    if edition is not None and int(edition) not in (1, 2):
        raise make("E0706", f"{VENDOR_DIR}/{name}: edition {edition} is not an edition "
                            f"of this compiler", package.where("edition"))
    requires: list[manifest_module.Dependency] = []
    dependencies = manifest_module.first_table(sections, "dependencies")
    if dependencies is not None:
        for item in dependencies.values:
            manifest_module.check_name(item.name, "dependency", item.loc)
            if not isinstance(item.value, str):
                raise make("E0702", f"{VENDOR_DIR}/{name}: dependencies.{item.name} must be "
                                    f"a string holding a version constraint", item.loc)
            requires.append(manifest_module.Dependency(
                item.name, manifest_module.parse_constraint(item.value, item.loc)))
    return version, requires


def _vendored(root: Path, name: str, required_by: str) -> Path:
    directory = root / VENDOR_DIR / name
    if not directory.is_dir():
        raise make(
            "E0805",
            f"{required_by} requires {name!r}, which is not vendored: "
            f"{VENDOR_DIR}/{name} does not exist",
            hint=f"put the package in {VENDOR_DIR}/{name} with its "
                 f"{manifest_module.MANIFEST_NAME}",
        )
    return directory


def resolve(project: manifest_module.Manifest) -> Resolution:
    """Resolve the project's dependencies from what is vendored.

    Every constraint placed on a name is collected while the graph is walked, so a
    version has to satisfy the project and every package that requires it — which
    is the whole point of resolving rather than trusting a list. Nothing is
    fetched: the answer comes from the tree, and a package that is not there is
    `E0805` rather than an attempt to look elsewhere.
    """
    root = project.root
    #: name -> every constraint placed on it, with who placed it.
    constraints: dict[str, list[tuple[Constraint, str]]] = {}
    packages: dict[str, Package] = {}
    stack: list[str] = []

    def place(name: str, constraint: Constraint, requirer: str) -> None:
        constraints.setdefault(name, []).append((constraint, requirer))

    def walk(name: str, required_by: str, constraint: Constraint) -> None:
        place(name, constraint, required_by)
        if name in stack:
            cycle = " -> ".join([*stack[stack.index(name):], name])
            raise make("E0807", f"dependency cycle: {cycle}", None,
                       hint="a package cannot depend on itself, directly or through "
                            "another package")
        if name in packages:
            return
        directory = _vendored(root, name, required_by)
        version, requires = read_package(directory, name)
        stack.append(name)
        for dependency in requires:
            walk(dependency.name, name, dependency.constraint)
        stack.pop()
        packages[name] = Package(name=name, version=version, digest=digest_of(directory),
                                 source=f"{VENDOR_DIR}/{name}",
                                 requires=sorted(item.name for item in requires))

    for dependency in project.dependencies:
        walk(dependency.name, project.name, dependency.constraint)

    for name, placed in sorted(constraints.items()):
        package = packages.get(name)
        if package is None:
            continue
        for constraint, requirer in placed:
            if not constraint.matches(package.version):
                who = "the project" if requirer == project.name else requirer
                raise make(
                    "E0806",
                    f"{name} {package.version} is outside {constraint.text}, required "
                    f"by {who}",
                    hint=f"vendor a version that {who} accepts, or widen the constraint",
                )
    return Resolution(project=project.name, version=project.version,
                      packages=[packages[name] for name in sorted(packages)])


def render(resolution: Resolution) -> str:
    """The lockfile text: same tree in, same bytes out."""
    lines = [
        f"# {LOCK_NAME} — written by `nava lock`, checked by `nava lock --check`.",
        "# Do not edit it: change nava.toml or the vendored package and lock again.",
        "",
        "[lock]",
        f"format = {LOCK_FORMAT}",
        f'project = "{resolution.project}"',
        f'version = "{resolution.version}"',
        "",
    ]
    for package in resolution.packages:
        lines.extend([
            "[[package]]",
            f'name = "{package.name}"',
            f'version = "{package.version}"',
            f'digest = "{package.digest}"',
            f'source = "{package.source}"',
            "requires = [" + ", ".join(f'"{item}"' for item in package.requires) + "]",
            "",
        ])
    return "\n".join(lines).rstrip("\n") + "\n"


def write(resolution: Resolution, path: Path | str) -> Path:
    path = Path(path)
    path.write_text(render(resolution), encoding="utf-8")
    return path


def parse(text: str, path: Path | str = LOCK_NAME) -> Resolution:
    """Read a lockfile; anything outside the format is `E0802`."""
    path = Path(path)
    sections = manifest_module.read_document(text, path, LOCK_TABLES,
                                             repeated=("package",), name=LOCK_NAME)
    header = manifest_module.first_table(sections, "lock")
    if header is None:
        raise make("E0802", f"{LOCK_NAME} has no [lock] table", None,
                   hint=f"write it, or delete the file and run `nava lock`")
    for item in header.values:
        if item.name not in LOCK_FIELDS:
            raise make("E0802", f"unknown field lock.{item.name} in {LOCK_NAME}", item.loc,
                       hint=f"[lock] has {', '.join(sorted(LOCK_FIELDS))}")
        if item.shape != LOCK_FIELDS[item.name]:
            raise make("E0802", f"lock.{item.name} must be a {LOCK_FIELDS[item.name]}, "
                                f"not a {item.shape}", item.loc)
    if header.get("format") != LOCK_FORMAT:
        raise make("E0802", f"{LOCK_NAME} is format {header.get('format')!r}; this "
                            f"compiler writes and reads format {LOCK_FORMAT}",
                   header.where("format"),
                   hint="delete the file and run `nava lock` to write it again")
    project = header.get("project")
    version_text = header.get("version")
    if not isinstance(project, str) or not isinstance(version_text, str):
        raise make("E0802", f"{LOCK_NAME} must name the project and its version",
                   header.loc, hint='[lock] holds format, project and version')
    packages: list[Package] = []
    seen: set[str] = set()
    for section in sections:
        if section.name != "package":
            continue
        for item in section.values:
            if item.name not in PACKAGE_FIELDS:
                raise make("E0802", f"unknown field package.{item.name} in {LOCK_NAME}",
                           item.loc, hint=f"a package block has "
                                         f"{', '.join(sorted(PACKAGE_FIELDS))}")
            if item.shape != PACKAGE_FIELDS[item.name]:
                raise make("E0802", f"package.{item.name} must be a "
                                    f"{PACKAGE_FIELDS[item.name]}, not a {item.shape}",
                           item.loc)
        name = section.get("name")
        version = section.get("version")
        digest = section.get("digest")
        if not isinstance(name, str) or not isinstance(version, str) \
                or not isinstance(digest, str):
            raise make("E0802", f"a [[package]] block in {LOCK_NAME} needs name, "
                                f"version and digest", section.loc)
        manifest_module.check_name(name, "package", section.where("name"))
        if not digest.startswith(DIGEST_PREFIX) or len(digest) != len(DIGEST_PREFIX) + 64:
            raise make("E0802", f"{name}: digest {digest!r} is not "
                                f"{DIGEST_PREFIX} followed by 64 hex digits",
                       section.where("digest"),
                       hint="digests are written by `nava lock`; do not type one")
        if name in seen:
            raise make("E0802", f"{name} appears twice in {LOCK_NAME}",
                       section.where("name"))
        seen.add(name)
        requires = section.get("requires")
        requires = [str(item) for item in requires] if isinstance(requires, list) else []
        if requires != sorted(requires):
            raise make("E0802", f"{name}: requires is not sorted by name: "
                                f"{requires!r}", section.where("requires"),
                       hint="`nava lock` writes it sorted; a hand-edited file is not")
        packages.append(Package(name=name,
                                version=manifest_module.parse_version(
                                    version, section.where("version")),
                                digest=digest,
                                source=str(section.get("source") or f"{VENDOR_DIR}/{name}"),
                                requires=requires))
    packages.sort(key=lambda item: item.name)
    return Resolution(project=project,
                      version=manifest_module.parse_version(version_text,
                                                            header.where("version")),
                      packages=packages)


def read(path: Path | str) -> Resolution:
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise make("E0801", f"no {LOCK_NAME} at {path}", None,
                   hint="run `nava lock` to write it") from exc
    except UnicodeDecodeError as exc:
        raise make("E0802", f"{path} is not valid UTF-8") from exc
    return parse(text, path)


def check(project: manifest_module.Manifest, locked: Resolution) -> None:
    """Compare a lockfile with the project it claims to describe.

    Raises the first disagreement, named: the wrong project, a package the lockfile
    names and the project no longer needs, a package the project needs and the
    lockfile does not name, a version or a dependency list that has moved, or bytes
    that no longer match the digest they were locked at.
    """
    if locked.project != project.name or locked.version != project.version:
        raise make(
            "E0803",
            f"{LOCK_NAME} describes {locked.project} {locked.version}, "
            f"but nava.toml describes {project.name} {project.version}",
            hint="run `nava lock` to write the lockfile for this project",
        )
    wanted = resolve(project)
    for package in locked.packages:
        current = wanted.package(package.name)
        if current is None:
            raise make(
                "E0803",
                f"{LOCK_NAME} names {package.name} {package.version}, which this project "
                f"does not depend on",
                hint="run `nava lock` to drop it, or declare the dependency in nava.toml",
            )
        if current.version != package.version:
            raise make(
                "E0803",
                f"{LOCK_NAME} locks {package.name} {package.version}, but "
                f"{VENDOR_DIR}/{package.name} holds {current.version}",
                hint="run `nava lock` to record the version that is vendored",
            )
        if current.requires != package.requires:
            raise make(
                "E0803",
                f"{LOCK_NAME} says {package.name} requires {package.requires}, but "
                f"{VENDOR_DIR}/{package.name} requires {current.requires}",
                hint="run `nava lock` to record what the package now requires",
            )
        if current.digest != package.digest:
            raise make(
                "E0804",
                f"{package.name} does not match its digest: {LOCK_NAME} locks "
                f"{package.digest}, {VENDOR_DIR}/{package.name} hashes to {current.digest}",
                hint="if the package change was intended, run `nava lock`; otherwise "
                     "restore the package",
            )
    for package in wanted.packages:
        if locked.package(package.name) is None:
            raise make(
                "E0803",
                f"{LOCK_NAME} does not name {package.name} {package.version}, which "
                f"{'the project' if package.name in [item.name for item in project.dependencies] else 'a dependency'} requires",
                hint="run `nava lock` to add it",
            )


def graph(resolution: Resolution, project: manifest_module.Manifest) -> str:
    """The graph, as a tree with children sorted by name: the recorded answer."""
    lines = [f"{resolution.project} {resolution.version}"]
    roots = sorted(item.name for item in project.dependencies)

    def render_children(name: str, prefix: str, seen: tuple[str, ...]) -> None:
        package = resolution.package(name)
        if package is None:
            return
        children = [item for item in package.requires]
        for index, child in enumerate(children):
            last = index == len(children) - 1
            branch = "└── " if last else "├── "
            child_package = resolution.package(child)
            if child_package is None:
                lines.append(f"{prefix}{branch}{child} (missing)")
                continue
            lines.append(f"{prefix}{branch}{child_package.line()}")
            if child in seen:
                continue
            render_children(child, prefix + ("    " if last else "│   "),
                            (*seen, child))

    for index, name in enumerate(roots):
        last = index == len(roots) - 1
        package = resolution.package(name)
        branch = "└── " if last else "├── "
        if package is None:
            lines.append(f"{branch}{name} (missing)")
            continue
        lines.append(f"{branch}{package.line()}")
        render_children(name, "    " if last else "│   ", (name,))
    return "\n".join(lines) + "\n"


def summary_json(resolution: Resolution) -> dict:
    """The same answer as `graph`, shaped for `--format json`."""
    return {
        "project": resolution.project,
        "version": str(resolution.version),
        "packages": [{"name": item.name, "version": str(item.version),
                      "digest": item.digest, "source": item.source,
                      "requires": list(item.requires)}
                     for item in resolution.packages],
    }
