#!/usr/bin/env python3
"""Release-readiness checks for the Nava distribution (PLAN.md phase N9).

The plan asks for three things this script answers with evidence instead of
prose:

* **version and metadata agree** — the package, `pyproject.toml`, the changelog
  and the declared support matrix in `RELEASE.md` must say the same thing, and
  the matrix must describe what CI actually exercises;
* **the declared Python floor is real** — every Python file must parse with
  `ast.parse(..., feature_version=(3, 10))`. This is a syntax check, not a
  substitute for running the suite on 3.10; `RELEASE.md` says so;
* **artifacts are complete and clean** — a built wheel must carry the standard
  library, the console script and the license, the sdist must carry the files a
  reader needs, and neither may carry tests, benchmark records, credentials or
  private keys.

Nothing here is a guess about a remote system: every check reads a local file
or an artifact this machine built.

    python3 tools/verify_release.py                 # metadata + syntax + secrets
    python3 tools/verify_release.py --artifacts dist
    python3 tools/verify_release.py --json /tmp/release.json
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUPPORT_MATRIX = ROOT / "RELEASE.md"
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"

REQUIRED_STDLIB = ("math", "sort", "text")
SKIP_DIRECTORIES = {".git", "__pycache__", ".mypy_cache", ".ruff_cache", "build",
                    "dist", "node_modules", ".venv", ".arena"}

#: Patterns that must never reach a release artifact. Deliberately specific:
#: a generic "password = ..." rule reports documentation, not credentials.
SECRET_PATTERNS = {
    "private key block": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "OpenAI-style key": re.compile(r"\bsk-[A-Za-z0-9]{20,}"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    "AWS access key id": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "Slack token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"),
    "Google API key": re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    "Hugging Face token": re.compile(r"\bhf_[A-Za-z0-9]{30,}"),
}
SECRET_NAMES = re.compile(r"(^\.env($|\.)|\.(pem|key|p12|pfx|keystore)$|^id_rsa$|^\.netrc$|credentials)")


def problems_metadata() -> list[str]:
    """Every place that states a version, a Python floor or a support claim."""
    problems: list[str] = []
    package = _package_version()
    pyproject = _pyproject_version()
    if package != pyproject:
        problems.append(f"nava_lang.__version__ {package!r} != pyproject version {pyproject!r}")

    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    # Only version-shaped headings are release sections; the changelog also
    # keeps per-phase records ("## N4 (phase N4)") that are not versions.
    released = re.findall(r"^## (\d+\.\d+\.\d+)\b.*$", changelog, re.M)
    if not changelog.startswith("# Changelog\n"):
        problems.append("CHANGELOG.md does not start with '# Changelog'")
    if "## Unreleased" not in changelog:
        problems.append("CHANGELOG.md has no '## Unreleased' section")
    for heading in released:
        if heading.strip() != package:
            problems.append(f"CHANGELOG.md has a section for {heading.strip()!r}, "
                            f"which is not the current version {package!r}")

    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    floor = _requires_python(text)
    if floor != "3.10":
        problems.append(f"requires-python is {floor!r}; the supported floor in this "
                        f"release is 3.10")
    if 'dependencies = []' not in text:
        problems.append("pyproject declares runtime dependencies; the compiler has none")
    if 'nava = "nava_lang.cli:main"' not in text:
        problems.append("pyproject does not declare the `nava` console script")
    if 'nava_lang = ["stdlib/*.nv"]' not in text:
        problems.append("pyproject does not install the Nava standard library")
    if 'license = "Apache-2.0"' not in text:
        problems.append("pyproject does not declare the Apache-2.0 license")
    for name in REQUIRED_STDLIB:
        if not (ROOT / "nava_lang" / "stdlib" / f"{name}.nv").is_file():
            problems.append(f"nava_lang/stdlib/{name}.nv is missing")

    matrix = ci_matrix()
    if not matrix["python"]:
        problems.append("could not read the Python matrix from .github/workflows/ci.yml")
    if not matrix["compiler"]:
        problems.append("could not read the compiler matrix from .github/workflows/ci.yml")
    declared = _classifier_pythons(text)
    for version in matrix["python"]:
        if version not in declared:
            problems.append(f"CI exercises Python {version}, but pyproject does not "
                            f"list a classifier for it")
    supported = supported_matrix()
    exercised = [row for row in supported if row["status"] == "exercised"]
    if not exercised:
        problems.append("RELEASE.md declares nothing as exercised")
    for row in exercised:
        for version in matrix["python"]:
            if version not in row["python"]:
                problems.append(f"RELEASE.md says {row['platform']} is exercised, but "
                                f"the CI matrix runs Python {matrix['python']} "
                                f"and the row lists {row['python']}")
        for compiler in matrix["compiler"]:
            if compiler not in row["compiler"]:
                problems.append(f"RELEASE.md says {row['platform']} is exercised, but "
                                f"CI uses {matrix['compiler']} and the row lists "
                                f"{row['compiler']}")
    for row in supported:
        if row["status"] not in {"exercised", "expected, not exercised"}:
            problems.append(f"RELEASE.md row {row['platform']!r} has an unknown status "
                            f"{row['status']!r}")
    return problems


def problems_syntax(floor: tuple[int, int] = (3, 10)) -> list[str]:
    """Every Python file must be syntactically valid for the declared floor."""
    problems: list[str] = []
    for path in _python_files():
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path),
                      feature_version=floor)
        except SyntaxError as error:  # pragma: no cover - only on a real regression
            problems.append(f"{path.relative_to(ROOT)}: {error.msg} (line {error.lineno}); "
                            f"Python {floor[0]}.{floor[1]} cannot parse it")
    return problems


def problems_secrets(paths: list[Path] | None = None) -> list[str]:
    problems: list[str] = []
    for path in paths if paths is not None else [ROOT]:
        if path.is_file():
            problems.extend(_scan_file(path))
            continue
        for child in sorted(path.rglob("*")):
            if not child.is_file() or SKIP_DIRECTORIES & set(child.parts):
                continue
            problems.extend(_scan_file(child))
    return problems


def problems_artifacts(dist: Path) -> list[str]:
    problems: list[str] = []
    wheels = sorted(dist.glob("*.whl"))
    sdists = sorted(dist.glob("*.tar.gz"))
    if len(wheels) != 1:
        problems.append(f"{dist} holds {len(wheels)} wheels, expected exactly 1")
    if len(sdists) != 1:
        problems.append(f"{dist} holds {len(sdists)} sdists, expected exactly 1")
    for wheel in wheels:
        problems.extend(_check_wheel(wheel))
    for sdist in sdists:
        problems.extend(_check_sdist(sdist))
    return problems


def artifact_digests(dist: Path) -> dict:
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(dist.glob("*")) if path.is_file()}


def ci_matrix() -> dict:
    """The Python and compiler versions the required CI lane actually runs."""
    text = WORKFLOW.read_text(encoding="utf-8") if WORKFLOW.is_file() else ""
    pythons = re.search(r"^\s*python:\s*\[([^\]]*)\]", text, re.M)
    compilers = re.search(r"^\s*compiler:\s*\[([^\]]*)\]", text, re.M)
    return {
        "python": [item.strip().strip("\"'") for item in pythons.group(1).split(",")]
        if pythons else [],
        "compiler": [item.strip().strip("\"'") for item in compilers.group(1).split(",")]
        if compilers else [],
    }


def supported_matrix() -> list[dict]:
    """Rows of the support matrix in `RELEASE.md`."""
    if not SUPPORT_MATRIX.is_file():
        return []
    rows: list[dict] = []
    for line in SUPPORT_MATRIX.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|") or set(line.strip()) <= {"|", "-", " "}:
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 5 or cells[0].lower().startswith(("platform", "---")):
            continue
        rows.append({
            "platform": cells[0].strip("`"),
            "runner": cells[1].strip("`"),
            "python": _versions(cells[2]),
            "compiler": [item.strip() for item in cells[3].split(",") if item.strip()],
            "status": cells[4],
        })
    return rows


def _versions(cell: str) -> list[str]:
    """`3.10, 3.11` or `3.10–3.13` expanded into a list of versions."""
    found: list[str] = []
    for item in re.split(r",|\band\b", cell):
        item = item.strip().strip("`")
        span = re.fullmatch(r"(\d+\.\d+)\s*[–-]\s*(\d+\.\d+)", item)
        if span:
            low, high = span.groups()
            major = int(low.split(".")[0])
            for minor in range(int(low.split(".")[1]), int(high.split(".")[1]) + 1):
                found.append(f"{major}.{minor}")
        elif re.fullmatch(r"\d+\.\d+", item):
            found.append(item)
    return found


def _python_files() -> list[Path]:
    files = []
    for path in sorted(ROOT.rglob("*.py")):
        if SKIP_DIRECTORIES & set(path.relative_to(ROOT).parts):
            continue
        files.append(path)
    return files


def _scan_archive(path: Path) -> list[str]:
    """Credentials inside an archive: read the members, not the compressed bytes."""
    problems: list[str] = []
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            for name in archive.namelist():
                problems.extend(_scan_text(f"{path.name}:{name}",
                                           archive.read(name).decode("utf-8", "replace")))
    elif path.name.endswith(".tar.gz"):
        with tarfile.open(path) as archive:
            for member in archive.getmembers():
                if not member.isfile() or member.name.endswith((".nv", ".py", ".md",
                                                                 ".json", ".txt", ".toml")):
                    pass
                handle = archive.extractfile(member)
                if handle is None:
                    continue
                problems.extend(_scan_text(f"{path.name}:{member.name}",
                                           handle.read().decode("utf-8", "replace")))
    return problems


def _scan_text(label: str, text: str) -> list[str]:
    problems = []
    for description, pattern in SECRET_PATTERNS.items():
        if pattern.search(text):
            problems.append(f"{label}: looks like a {description}")
    return problems


def _scan_file(path: Path) -> list[str]:
    problems: list[str] = []
    if SECRET_NAMES.search(path.name) and path.name not in {"credentials.md"}:
        problems.append(f"{path}: a file with a credential-like name")
    try:
        if path.stat().st_size > 2_000_000:
            return problems
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return problems
    return _scan_text(str(path), text)


def _check_wheel(wheel: Path) -> list[str]:
    problems: list[str] = []
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        metadata_name = next((name for name in names
                              if name.endswith(".dist-info/METADATA")), None)
        entry_name = next((name for name in names
                           if name.endswith(".dist-info/entry_points.txt")), None)
        metadata = archive.read(metadata_name).decode() if metadata_name else ""
        entry = archive.read(entry_name).decode() if entry_name else ""
    required = ["nava_lang/cli.py", "nava_lang/__init__.py"]
    required += [f"nava_lang/stdlib/{name}.nv" for name in REQUIRED_STDLIB]
    for name in required:
        if name not in names:
            problems.append(f"{wheel.name}: missing {name}")
    if "nava = nava_lang.cli:main" not in entry:
        problems.append(f"{wheel.name}: the console script is not installed")
    if "License-Expression: Apache-2.0" not in metadata:
        problems.append(f"{wheel.name}: METADATA does not state License-Expression")
    if not any("licenses/LICENSE" in name for name in names):
        problems.append(f"{wheel.name}: the license text is not inside the wheel")
    if "Requires-Python: >=3.10" not in metadata:
        problems.append(f"{wheel.name}: METADATA does not state Requires-Python >=3.10")
    # Optional extras are fine; an unconditional requirement is not, because the
    # compiler is meant to work with nothing but the standard library.
    for line in metadata.splitlines():
        if line.startswith("Requires-Dist:") and 'extra == "' not in line:
            problems.append(f"{wheel.name}: declares a runtime dependency: "
                            f"{line.split(':', 1)[1].strip()}")
    problems.extend(_forbidden(wheel.name, names, allow_tests=False))
    problems.extend(_scan_archive(wheel))
    return problems


def _check_sdist(sdist: Path) -> list[str]:
    problems: list[str] = []
    with tarfile.open(sdist) as archive:
        names = [member.name for member in archive.getmembers()]
    prefixes = sorted({name.split("/")[0] for name in names})
    if len(prefixes) != 1:
        problems.append(f"{sdist.name}: holds more than one top-level directory "
                        f"{prefixes}")
        return problems
    prefix = prefixes[0]
    required = ["LICENSE", "NOTICE", "README.md", "pyproject.toml",
                "tools/verify_release.py", "tests/registry.py"]
    required += [f"nava_lang/stdlib/{name}.nv" for name in REQUIRED_STDLIB]
    for name in required:
        if f"{prefix}/{name}" not in names:
            problems.append(f"{sdist.name}: missing {name}")
    # A source distribution is expected to carry the test suite: that is how a
    # reader runs the gates. A wheel is not.
    problems.extend(_forbidden(sdist.name, names, allow_tests=True))
    problems.extend(_scan_archive(sdist))
    return problems


def _forbidden(label: str, names: list[str], *, allow_tests: bool) -> list[str]:
    problems: list[str] = []
    rules = {
        "a benchmark result record": re.compile(r"(^|/)bench/results/"),
        "a version-control file": re.compile(r"(^|/)\.git/"),
        "a build cache": re.compile(r"__pycache__|\.pyc$"),
        "a credential file": SECRET_NAMES,
    }
    if not allow_tests:
        rules["a test-suite file"] = re.compile(r"(^|/)tests/")
    for name in names:
        for description, pattern in rules.items():
            if pattern.search(name):
                problems.append(f"{label}: contains {description}: {name}")
    return problems


def _package_version() -> str:
    text = (ROOT / "nava_lang" / "__init__.py").read_text(encoding="utf-8")
    found = re.search(r'^__version__ = "([^"]+)"', text, re.M)
    return found.group(1) if found else ""


def _pyproject_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    found = re.search(r'^version = "([^"]+)"', text, re.M)
    return found.group(1) if found else ""


def _requires_python(text: str) -> str:
    found = re.search(r'^requires-python = ">=([^"]+)"', text, re.M)
    return found.group(1) if found else ""


def _classifier_pythons(text: str) -> list[str]:
    return re.findall(r'"Programming Language :: Python :: (\d+\.\d+)"', text)


def run(checks: list[str], dist: Path | None = None) -> dict:
    report: dict = {"schema": "nava-release-check/1", "checks": {}, "problems": [],
                    "artifacts": {}}
    if "metadata" in checks:
        found = problems_metadata()
        report["checks"]["metadata"] = found
        report["problems"].extend(found)
    if "syntax" in checks:
        found = problems_syntax()
        report["checks"]["syntax"] = found
        report["problems"].extend(found)
    if "secrets" in checks:
        found = problems_secrets()
        report["checks"]["secrets"] = found
        report["problems"].extend(found)
    if "artifacts" in checks:
        if dist is None:
            report["problems"].append("--artifacts needs a directory")
        else:
            found = problems_artifacts(dist)
            report["checks"]["artifacts"] = found
            report["problems"].extend(found)
            report["artifacts"] = artifact_digests(dist)
    report["version"] = _package_version()
    report["ok"] = not report["problems"]
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check that this tree is ready to be released.")
    parser.add_argument("--artifacts", type=Path, metavar="DIST",
                        help="also inspect a directory holding the wheel and sdist")
    parser.add_argument("--skip-secrets", action="store_true",
                        help="skip the credential scan (it reads every file)")
    parser.add_argument("--json", metavar="PATH", help="write the report here ('-' for stdout)")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    checks = ["metadata", "syntax"]
    if not args.skip_secrets:
        checks.append("secrets")
    if args.artifacts is not None:
        checks.append("artifacts")
    report = run(checks, args.artifacts)

    if not args.quiet:
        print(f"nava {report['version']}: release checks {', '.join(checks)}")
        for name, found in report["checks"].items():
            print(f"  {name:<10} {'OK' if not found else f'{len(found)} problem(s)'}")
        for problem in report["problems"]:
            print(f"  - {problem}")
        for name, digest in report["artifacts"].items():
            print(f"  artifact {name}  sha256 {digest}")
        print("OK: the tree is consistent with RELEASE.md"
              if report["ok"] else "FAILED: fix the problems above")
    if args.json:
        text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        if args.json == "-":
            sys.stdout.write(text)
        else:
            Path(args.json).parent.mkdir(parents=True, exist_ok=True)
            Path(args.json).write_text(text, encoding="utf-8")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
