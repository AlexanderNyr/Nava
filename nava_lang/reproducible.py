"""Build records, and proving that two builds are the same build (PLAN.md N13d).

The phase's gate is one sentence — *a project with two dependencies builds from its
lockfile in a second checkout, on a clean host, and the binary is byte-identical* —
and the honest way to claim it is to make the claim checkable by somebody who was
not there:

* **A build records what went into it.** The digest of every `.nv` file that was
  compiled, the digest of the generated C, the digest of the executable, the
  identity of the C compiler and linker, and the `SOURCE_DATE_EPOCH` the build ran
  with. Two records can then be compared by a script, by CI, or by a person with a
  `diff`, and a difference is a named difference rather than a second hash nobody
  explains.
* **Nothing in the record depends on the machine.** No timestamps of the build, no
  absolute paths (module names are recorded, not locations), no user names, no
  order from a directory listing: maps are written with sorted keys and lists are
  sorted by name. The record of the same tree on two machines is the same bytes,
  or it differs in a field that says why.
* **`SOURCE_DATE_EPOCH` is the one clock a build is allowed to see.** It is read
  from the environment, validated (`E0813` for anything that is not a whole
  non-negative number), passed through to the C compiler — which is what the
  reproducible-builds convention asks for — and recorded, so a build that pinned
  time and a build that did not can be told apart afterwards.

What this module does *not* do is claim that a compiler it has never seen is
deterministic. It records the toolchain, and `compare` reports a toolchain that has
changed as a toolchain difference rather than as a mysterious hash: the negative
control the phase asks for — *a build with a different toolchain records the
difference rather than silently producing a second hash* — is a function here, not a
hope.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys

from .diagnostics import make
from .records import field, record

#: The environment variable the reproducible-builds convention uses to pin time.
EPOCH_VARIABLE = "SOURCE_DATE_EPOCH"
#: Format version of a build record. A reader refuses anything else.
RECORD_FORMAT = 1
#: Prefix of every digest written by this module.
DIGEST_PREFIX = "sha256:"


@record()
class Toolchain:
    """The programs that turned C into a binary, named as they described themselves."""

    cc_command: str
    cc_version: str
    cc_path: str
    ld_version: str
    platform: str
    machine: str

    def as_dict(self) -> dict:
        return {
            "cc_command": self.cc_command,
            "cc_version": self.cc_version,
            "cc_path": self.cc_path,
            "ld_version": self.ld_version,
            "platform": self.platform,
            "machine": self.machine,
        }


@record()
class BuildRecord:
    """One build, described so that another build can be compared with it."""

    source_digest: str
    c_digest: str
    binary_digest: str
    toolchain: Toolchain
    source_date_epoch: int | None
    #: Module name -> digest of its file. Sorted when rendered.
    sources: dict[str, str] = field(default_factory=dict)
    #: Vendored packages that contributed a module, sorted.
    packages: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "format": RECORD_FORMAT,
            "sources": {name: self.sources[name] for name in sorted(self.sources)},
            "source_digest": self.source_digest,
            "c_digest": self.c_digest,
            "binary_digest": self.binary_digest,
            "packages": sorted(self.packages),
            "source_date_epoch": self.source_date_epoch,
            "toolchain": self.toolchain.as_dict(),
        }


def digest_bytes(data: bytes) -> str:
    return DIGEST_PREFIX + hashlib.sha256(data).hexdigest()


def sha256_file(path) -> str:
    with open(path, "rb") as handle:
        return digest_bytes(handle.read())


def source_date_epoch() -> int | None:
    """`SOURCE_DATE_EPOCH`, validated; `None` when it is not set.

    A build that does not pin time is allowed — it is what a developer runs — but a
    value that is not a whole non-negative number is refused, because a half-read
    convention is worse than an absent one: the build would look pinned and not be.
    """
    value = os.environ.get(EPOCH_VARIABLE)
    if value is None or value == "":
        return None
    text = value.strip()
    if not text.lstrip("-").isdigit() or text.startswith("-"):
        raise make(
            "E0813",
            f"{EPOCH_VARIABLE}={value!r} is not a whole non-negative number",
            hint="set it to a Unix timestamp, as in SOURCE_DATE_EPOCH=1700000000",
        )
    return int(text)


def toolchain(cc: str | None = None) -> Toolchain:
    """Ask the C compiler and the linker who they are, and record the answer.

    The version string is the first line the program prints about itself, which is
    the line that changes when the compiler changes. The path is the resolved
    program, so a machine with two compilers of one name records which one ran.
    """
    command = cc or os.environ.get("CC") or "cc"
    parts = command.split()
    program = shutil.which(parts[0]) or parts[0]
    cc_version = _first_line([*parts, "--version"])
    ld_version = _first_line(["ld", "--version"])
    return Toolchain(cc_command=command, cc_version=cc_version, cc_path=program,
                     ld_version=ld_version, platform=sys.platform,
                     machine=platform.machine())


def _first_line(command: list[str]) -> str:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return "(unavailable)"
    for line in (result.stdout or result.stderr or "").splitlines():
        line = line.strip()
        if line:
            return line
    return "(no version)"


def record(sources: dict[str, str], c_code: str, binary_path,
           packages: list[str] | None = None, cc: str | None = None) -> BuildRecord:
    """Describe a finished build.

    `sources` is module name -> file path; the digest of each file is taken here,
    so the record describes the sources that were compiled rather than the ones
    that happen to be on disk afterwards.
    """
    # The entry module's name is the empty string inside the compiler, because it
    # needs no qualifier; a record is read by people, so it is called `entry`.
    digests = {("entry" if name == "" else name): sha256_file(path)
               for name, path in sorted(sources.items())}
    joined = "".join(f"{name}\0{digests[name]}\0" for name in sorted(digests))
    return BuildRecord(
        source_digest=digest_bytes(joined.encode("utf-8")),
        c_digest=digest_bytes(c_code.encode("utf-8")),
        binary_digest=sha256_file(binary_path),
        toolchain=toolchain(cc),
        source_date_epoch=source_date_epoch(),
        sources=digests,
        packages=sorted(packages or []),
    )


def render(build: BuildRecord) -> str:
    """The record as JSON: sorted keys, no time of its own, one trailing newline."""
    return json.dumps(build.as_dict(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def parse(text: str, path: str = "build record") -> BuildRecord:
    """Read a record written by `render`; anything else is refused, not guessed at."""
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise make("E0815", f"{path} is not JSON: {exc}") from exc
    if not isinstance(document, dict) or document.get("format") != RECORD_FORMAT:
        raise make("E0815", f"{path} is not a build record of format {RECORD_FORMAT}")
    try:
        tool = document["toolchain"]
        toolchain_value = Toolchain(
            cc_command=str(tool["cc_command"]), cc_version=str(tool["cc_version"]),
            cc_path=str(tool["cc_path"]), ld_version=str(tool["ld_version"]),
            platform=str(tool["platform"]), machine=str(tool["machine"]))
        return BuildRecord(
            source_digest=str(document["source_digest"]),
            c_digest=str(document["c_digest"]),
            binary_digest=str(document["binary_digest"]),
            toolchain=toolchain_value,
            source_date_epoch=(None if document.get("source_date_epoch") is None
                               else int(document["source_date_epoch"])),
            sources={str(name): str(value)
                     for name, value in dict(document.get("sources") or {}).items()},
            packages=[str(item) for item in document.get("packages") or []],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise make("E0815", f"{path} is missing a field: {exc}") from exc


def compare(left: BuildRecord, right: BuildRecord) -> list[str]:
    """Every difference between two records, in a fixed order, one line each.

    The list is empty when the two builds are the same build. A difference in the
    toolchain is reported as a toolchain difference *first*, before the digests it
    caused, because that is the difference that explains the others.
    """
    differences: list[str] = []
    for name in ("cc_command", "cc_version", "cc_path", "ld_version", "platform",
                 "machine"):
        a, b = getattr(left.toolchain, name), getattr(right.toolchain, name)
        if a != b:
            differences.append(f"toolchain.{name}: {a!r} != {b!r}")
    if left.source_date_epoch != right.source_date_epoch:
        differences.append(f"{EPOCH_VARIABLE}: {left.source_date_epoch} != "
                           f"{right.source_date_epoch}")
    if left.sources != right.sources:
        names = sorted(set(left.sources) | set(right.sources))
        for name in names:
            a, b = left.sources.get(name), right.sources.get(name)
            if a != b:
                differences.append(f"source {name}: {a or '(absent)'} != {b or '(absent)'}")
    for name in ("source_digest", "c_digest", "binary_digest"):
        a, b = getattr(left, name), getattr(right, name)
        if a != b:
            differences.append(f"{name}: {a} != {b}")
    if sorted(left.packages) != sorted(right.packages):
        differences.append(f"packages: {sorted(left.packages)} != {sorted(right.packages)}")
    return differences


def summarize(build: BuildRecord) -> str:
    """A short human description, for a build that wants to say what it recorded."""
    epoch = "unset" if build.source_date_epoch is None else str(build.source_date_epoch)
    return (f"build {build.binary_digest}\n"
            f"  sources {build.source_digest}\n"
            f"  c {build.c_digest}\n"
            f"  toolchain {build.toolchain.cc_version}\n"
            f"  {EPOCH_VARIABLE} {epoch}\n")
