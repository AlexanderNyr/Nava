"""`nava.toml`: the manifest of a Nava project (PLAN.md phase N13, increment a).

A project is a directory with a manifest in it. The manifest says what the
project is called, which edition of the language it is written in, where its
entry file is, and which other projects it depends on. Everything else in the
phase — the lockfile, vendoring, digests, reproducible builds — is built on this
file, so this module's job is to read it strictly enough that the later work has
something trustworthy to stand on.

**A subset of TOML, and no more.** Three tables, four value shapes (string,
integer, boolean, array of strings), comments and blank lines. There is no
in-line table, no dotted key, no float, no date: each of those would be a promise
about a format this project does not otherwise use, and a manifest that a reader
cannot predict is worse than a manifest that is verbose. Anything outside the
subset is refused with a code and a position rather than ignored, because a key
that is silently skipped is a setting the author believes is in force.

**A typo is an error.** `edtion = 2` is refused (`E0704`) rather than treated as
an absent field with a default. The rule is the same one the doc-block parser
follows: fields are read literally, so a misspelling is visible.

**Two names are checked against the world, not against the file.** A project may
not be called after a standard-library module, and neither may a dependency: a
package named `math` would shadow `nava_lang/stdlib/math.nv` in every program
that imports it, and the loader already refuses that shadowing inside a build
(`E0360`). Refusing it in the manifest turns a confusing build failure into one
message at the source.

**The edition is checked against the entry file.** A manifest that says
`edition = 2` and an entry file that does not declare `edition 2;` disagree about
which language the project is written in, and only one of them is read by the
compiler. `load` reports the disagreement (`E0706`) instead of letting the
manifest be decoration.

Determinism: a manifest has no timestamps, dependencies are kept in the order
they were declared but reported sorted by name, and `summarize` produces the same
text for the same bytes on every machine — which is what the lockfile will have to
reproduce.
"""
from __future__ import annotations

import re
from pathlib import Path

from .diagnostics import make
from .model import Location
from .parser import EDITIONS, parse
from .records import field, record

#: Name of the manifest file. It sits at the project root.
MANIFEST_NAME = "nava.toml"
#: Longest accepted package name. A name is an identifier, not a sentence.
MAX_NAME_LENGTH = 64
#: Longest accepted version constraint, in bytes. `^1.2.3` is three characters of
#: constraint and three of number; anything past this is not a constraint.
MAX_CONSTRAINT_LENGTH = 64
#: Tables the manifest may contain.
TABLES = ("package", "build", "dependencies")
#: Fields of each table: name -> the value shape it takes.
FIELDS = {
    "package": {"name": "string", "version": "string", "edition": "integer"},
    "build": {"entry": "string"},
}
#: Fields that must be present, by table.
REQUIRED = {"package": ("name", "version", "edition")}
#: The default entry file, relative to the project root.
DEFAULT_ENTRY = "main.nv"
#: The one shape a dependency value may take: a version constraint string.
DEPENDENCY_SHAPE = "string"

#: `name`: a lower-case identifier. Dependencies and the project itself share it,
#: so a name cannot be mistaken for a path or for a module qualifier.
NAME_PATTERN = re.compile(r"[a-z][a-z0-9_]*\Z")
#: `1.2.3`, with no leading zeros and nothing else.
VERSION_PATTERN = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")
#: The operators a constraint may carry.
CONSTRAINT_OPERATORS = ("^", "~", "=")


@record(frozen=True)
class Version:
    """A semantic version: three non-negative numbers and nothing else."""

    major: int
    minor: int
    patch: int

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"

    # `record()` gives field-wise equality and nothing else: the ordering a
    # version needs is written here, once, rather than at each comparison.
    @property
    def key(self) -> tuple[int, int, int]:
        return (self.major, self.minor, self.patch)

    def __lt__(self, other: "Version") -> bool:
        return self.key < other.key

    def __le__(self, other: "Version") -> bool:
        return self.key <= other.key

    def __gt__(self, other: "Version") -> bool:
        return self.key > other.key

    def __ge__(self, other: "Version") -> bool:
        return self.key >= other.key


@record(frozen=True)
class Constraint:
    """A version constraint: an operator and the version it applies to.

    The four forms the language accepts:

    * `1.2.3` or `=1.2.3` — exactly that version;
    * `^1.2.3` — the same major version, from `1.2.3` up to but not including
      `2.0.0`; the compatibility rule of semantic versioning;
    * `~1.2.3` — the same minor version, from `1.2.3` up to but not including
      `1.3.0`;
    * `*` — any version at all.
    """

    operator: str
    version: Version | None
    text: str

    def matches(self, version: Version) -> bool:
        if self.operator == "*":
            return True
        if self.version is None:
            return False
        if self.operator == "=":
            return version == self.version
        if self.operator == "^":
            return (version >= self.version
                    and version.major == self.version.major)
        if self.operator == "~":
            return (version >= self.version
                    and version.major == self.version.major
                    and version.minor == self.version.minor)
        return False


@record()
class Dependency:
    """One line of `[dependencies]`: a package name and its constraint."""

    name: str
    constraint: Constraint


@record()
class Manifest:
    """A validated `nava.toml`, with the paths a build needs."""

    path: Path
    root: Path
    name: str
    version: Version
    edition: int
    entry: str
    dependencies: list[Dependency] = field(default_factory=list)

    @property
    def entry_path(self) -> Path:
        return self.root / self.entry

    def dependency(self, name: str) -> Dependency | None:
        for item in self.dependencies:
            if item.name == name:
                return item
        return None

    def sorted_dependencies(self) -> list[Dependency]:
        """Dependencies in name order, which is the order reports use."""
        return sorted(self.dependencies, key=lambda item: item.name)


def parse_version(text: str, loc: Location | None = None) -> Version:
    """Read `1.2.3`; anything else is `E0705`."""
    if not VERSION_PATTERN.match(text):
        raise make(
            "E0705",
            f"{text!r} is not a version: write MAJOR.MINOR.PATCH, as in 1.2.3",
            loc,
        )
    major, minor, patch = (int(part) for part in text.split("."))
    return Version(major, minor, patch)


def parse_constraint(text: str, loc: Location | None = None) -> Constraint:
    """Read a constraint such as `^1.2.3`, `~1.2`, `=1.2.3`, `1.2.3` or `*`."""
    if len(text.encode("utf-8")) > MAX_CONSTRAINT_LENGTH:
        raise make("E0705", f"version constraint is longer than {MAX_CONSTRAINT_LENGTH} "
                            f"bytes: {text[:32]!r}…", loc)
    if text == "*":
        return Constraint("*", None, text)
    operator = "="
    body = text
    if text[:1] in CONSTRAINT_OPERATORS:
        operator, body = text[0], text[1:]
    if not body:
        raise make("E0705", f"{text!r} is a constraint operator with no version after it",
                   loc, hint="write ^1.2.3, ~1.2.3, =1.2.3, 1.2.3 or *")
    version = parse_version(body, loc)
    return Constraint(operator, version, text)


def _standard_modules() -> set[str]:
    """The names the standard library provides, which no package may take."""
    from . import modules as module_loader

    return {path.stem for path in module_loader.stdlib_path().glob("*.nv")}


def check_name(name: str, what: str, loc: Location | None = None,
               standard: set[str] | None = None) -> None:
    """Validate a package name: shape, length, and freedom from a stdlib clash."""
    if len(name) > MAX_NAME_LENGTH:
        raise make("E0709", f"{what} name is longer than {MAX_NAME_LENGTH} "
                            f"characters: {name!r}", loc)
    if not NAME_PATTERN.match(name):
        raise make(
            "E0709",
            f"{what} name {name!r} is not a name: use lower-case letters, digits "
            f"and '_', starting with a letter",
            loc,
            hint="as in `my_project`, not `My-Project`",
        )
    clash = standard if standard is not None else _standard_modules()
    if name in clash:
        raise make(
            "E0708",
            f"{what} name {name!r} shadows a standard-library module",
            loc,
            hint=f"rename it; the name {name} is provided by the standard library",
        )


def _string(value: str, line: int, path: str) -> tuple[str, int]:
    """Read a basic string starting at `value[0] == '"'`; answer (text, used)."""
    out: list[str] = []
    index = 1
    while index < len(value):
        character = value[index]
        if character == '"':
            return "".join(out), index + 1
        if character == "\\":
            index += 1
            if index >= len(value):
                break
            escape = value[index]
            mapped = {"\\": "\\", '"': '"', "n": "\n", "t": "\t"}.get(escape)
            if mapped is None:
                raise make("E0702", f"invalid string escape '\\{escape}' in nava.toml",
                           Location(line, index, path),
                           hint="the manifest accepts \\\\, \\\", \\n and \\t")
            out.append(mapped)
            index += 1
            continue
        out.append(character)
        index += 1
    raise make("E0702", "unterminated string in nava.toml", Location(line, 1, path),
               hint="close the string with '\"'")


def _value(text: str, line: int, path: str, table: str, key: str):
    """Read one value; the shape must be the shape the field declares."""
    text = text.strip()
    if not text:
        raise make("E0702", f"{table}.{key} has no value", Location(line, 1, path))
    if text.startswith('"'):
        value, used = _string(text, line, path)
        if text[used:].strip():
            raise make("E0702", f"unexpected text after the string in {table}.{key}: "
                                f"{text[used:].strip()!r}", Location(line, used + 1, path))
        return value
    if text in ("true", "false"):
        return text == "true"
    if text.startswith("["):
        if not text.endswith("]"):
            raise make("E0702", f"unclosed array in {table}.{key}", Location(line, 1, path))
        inner = text[1:-1].strip()
        items: list[str] = []
        if inner:
            position = 1
            while position <= len(text):
                chunk = text[position:].lstrip()
                if not chunk.startswith('"'):
                    raise make("E0702", f"an array in nava.toml holds strings only: "
                                        f"{table}.{key}", Location(line, position + 1, path))
                item, used = _string(chunk, line, path)
                items.append(item)
                position = len(text) - len(chunk) + used
                rest = text[position:]
                stripped = rest.lstrip()
                if stripped.startswith(","):
                    position += len(rest) - len(stripped) + 1
                    if text[position:].strip() == "]":
                        break
                    continue
                break
            if text[position:].strip() != "]":
                raise make("E0702", f"an array in nava.toml is written "
                                    f"[\"a\", \"b\"]: {table}.{key}",
                           Location(line, 1, path))
        return items
    if text.startswith("{"):
        raise make("E0702", "an inline table is not part of the manifest format",
                   Location(line, 1, path),
                   hint="write the values as keys of their own table")
    if re.fullmatch(r"-?[0-9]+", text):
        return int(text)
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", text):
        raise make("E0702", f"unquoted value {text!r} in nava.toml", Location(line, 1, path),
                   hint="strings are quoted: name = \"value\"")
    raise make("E0702", f"cannot read the value {text!r} in {table}.{key}",
               Location(line, 1, path))


def _without_comment(text: str) -> str:
    """Drop a trailing `# ...` that is not inside a string."""
    in_string = False
    escaped = False
    for index, character in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character == "#":
            return text[:index]
    return text


def _split_key(text: str, line: int, path: str) -> tuple[str, str]:
    """Split `key = value` on the first `=` outside a string."""
    in_string = False
    escaped = False
    for index, character in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character == "=":
            return text[:index].strip(), text[index + 1:]
    raise make("E0702", f"expected `key = value`, got {text.strip()!r}",
               Location(line, 1, path))


def from_text(text: str, path: Path | str = MANIFEST_NAME) -> Manifest:
    """Read a manifest from `text`; `path` is used for the project root."""
    path = Path(path)
    display = str(path)
    values: dict[str, dict[str, object]] = {name: {} for name in TABLES}
    locations: dict[tuple[str, str], Location] = {}
    table = ""
    for number, raw in enumerate(text.splitlines(), start=1):
        line = _without_comment(raw).strip()
        if not line:
            continue
        if line.startswith("["):
            if not line.endswith("]"):
                raise make("E0702", f"unclosed table header {line!r}",
                           Location(number, 1, display))
            name = line[1:-1].strip()
            if name not in TABLES:
                raise make("E0704", f"unknown table [{name}] in {MANIFEST_NAME}",
                           Location(number, 1, display),
                           hint=f"the manifest has {', '.join('[' + t + ']' for t in TABLES)}")
            if name == "dependencies":
                table = "dependencies"
                continue
            table = name
            continue
        if not table:
            raise make("E0702", "a key must follow a table header",
                       Location(number, 1, display),
                       hint="the first line of nava.toml is [package]")
        key, rest = _split_key(line, number, display)
        if not key:
            raise make("E0702", f"a key is missing on line {number}", Location(number, 1, display))
        if table == "dependencies":
            if key in values["dependencies"]:
                raise make("E0702", f"dependency {key!r} is declared twice",
                           Location(number, 1, display))
            values["dependencies"][key] = _value(rest, number, display, table, key)
            locations[(table, key)] = Location(number, 1, display)
            continue
        if key not in FIELDS[table]:
            known = ", ".join(sorted(FIELDS[table]))
            raise make("E0704", f"unknown field {table}.{key} in {MANIFEST_NAME}",
                       Location(number, 1, display),
                       hint=f"[{table}] has {known}")
        if key in values[table]:
            raise make("E0702", f"{table}.{key} is written twice",
                       Location(number, 1, display))
        values[table][key] = _value(rest, number, display, table, key)
        locations[(table, key)] = Location(number, 1, display)

    for table_name, fields in FIELDS.items():
        for key, shape in fields.items():
            if key not in values[table_name]:
                continue
            value = values[table_name][key]
            actual = ("integer" if isinstance(value, int) and not isinstance(value, bool)
                      else "boolean" if isinstance(value, bool)
                      else "array" if isinstance(value, list)
                      else "string")
            if actual != shape:
                raise make("E0702", f"{table_name}.{key} must be a {shape}, not a {actual}",
                           locations.get((table_name, key)))

    for table_name, required in REQUIRED.items():
        for key in required:
            if key not in values[table_name]:
                raise make(
                    "E0703",
                    f"{MANIFEST_NAME} is missing {table_name}.{key}",
                    None,
                    hint=f"[{table_name}] must hold {', '.join(required)}",
                )

    package = values["package"]
    standard = _standard_modules()
    name = str(package["name"])
    check_name(name, "package", locations.get(("package", "name")), standard)
    version = parse_version(str(package["version"]), locations.get(("package", "version")))
    edition = int(package["edition"])  # type: ignore[arg-type]
    if edition not in EDITIONS:
        raise make(
            "E0706",
            f"edition {edition} is not an edition of this compiler: "
            f"it implements {', '.join(str(item) for item in sorted(EDITIONS))}",
            locations.get(("package", "edition")),
        )

    entry = str(values["build"].get("entry", DEFAULT_ENTRY))
    if not entry:
        raise make("E0707", "build.entry is empty", locations.get(("build", "entry")))
    entry_path = Path(entry)
    if entry_path.is_absolute():
        raise make("E0707", f"build.entry is an absolute path: {entry!r}",
                   locations.get(("build", "entry")),
                   hint="name a file inside the project, as in src/main.nv")
    if ".." in entry_path.parts:
        raise make("E0707", f"build.entry leaves the project: {entry!r}",
                   locations.get(("build", "entry")),
                   hint="the entry file lives inside the directory of nava.toml")

    dependencies: list[Dependency] = []
    for dependency_name in values["dependencies"]:
        location = locations.get(("dependencies", dependency_name))
        written = values["dependencies"][dependency_name]
        if not isinstance(written, str):
            raise make(
                "E0702",
                f"dependencies.{dependency_name} must be a string holding a version "
                f"constraint, not a {type(written).__name__}",
                location,
                hint='write json = "^1.2.3"',
            )
        check_name(str(dependency_name), "dependency", location, standard)
        constraint = parse_constraint(str(values["dependencies"][dependency_name]), location)
        dependencies.append(Dependency(str(dependency_name), constraint))

    root = path.parent if str(path) != MANIFEST_NAME else Path(".")
    return Manifest(path=path, root=root, name=name, version=version, edition=edition,
                    entry=entry, dependencies=dependencies)


def find(start: Path | str | None = None) -> Path:
    """The manifest at or above `start`, which defaults to the working directory."""
    current = Path(start or ".").resolve()
    if current.is_file():
        current = current.parent
    for candidate in [current, *current.parents]:
        path = candidate / MANIFEST_NAME
        if path.is_file():
            return path
    raise make(
        "E0701",
        f"no {MANIFEST_NAME} in {current} or in any directory above it",
        hint=f"create {MANIFEST_NAME} with a [package] table naming the project",
    )


def read(path: Path | str) -> Manifest:
    """Read and validate the manifest at `path`."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise make("E0701", f"no {MANIFEST_NAME} at {path}") from exc
    except UnicodeDecodeError as exc:
        raise make("E0111", f"{path} is not valid UTF-8",
                   hint="save it as UTF-8") from exc
    manifest = from_text(text, path)
    entry = manifest.entry_path
    if not entry.is_file():
        raise make(
            "E0707",
            f"the entry file named by nava.toml does not exist: {manifest.entry}",
            hint="write it, or point build.entry at a file that exists",
        )
    declared = parse(entry.read_text(encoding="utf-8"), path=str(entry)).edition
    if declared != manifest.edition:
        raise make(
            "E0706",
            f"nava.toml declares edition {manifest.edition}, but {manifest.entry} "
            f"declares edition {declared}",
            hint=("write `edition 2;` on the first line of the entry file"
                  if manifest.edition == 2 else
                  "remove the `edition 2;` line, or set `edition = 2` in nava.toml"),
        )
    return manifest


def resolve(start: Path | str | None = None) -> Manifest:
    """Find the manifest above `start`, read it and validate the project."""
    return read(find(start))


def summarize(manifest: Manifest) -> str:
    """A stable, line-oriented description of the manifest.

    Same bytes in, same text out: dependencies are sorted by name and nothing in
    the output depends on the clock or on the order of a directory listing, which
    is what makes this usable in a recorded build log.
    """
    lines = [
        f"project {manifest.name} {manifest.version}",
        f"edition {manifest.edition}",
        f"entry {manifest.entry}",
    ]
    dependencies = manifest.sorted_dependencies()
    if not dependencies:
        lines.append("dependencies none")
    else:
        lines.append(f"dependencies {len(dependencies)}")
        for item in dependencies:
            lines.append(f"  {item.name} {item.constraint.text}")
    return "\n".join(lines) + "\n"


def summary_json(manifest: Manifest) -> dict:
    """The same information as `summarize`, shaped for `--format json`."""
    return {
        "project": manifest.name,
        "version": str(manifest.version),
        "edition": manifest.edition,
        "entry": manifest.entry,
        "dependencies": {item.name: item.constraint.text
                         for item in manifest.sorted_dependencies()},
    }
