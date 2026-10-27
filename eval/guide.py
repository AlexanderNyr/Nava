#!/usr/bin/env python3
"""Versioned, model-facing language guides (PLAN.md phase N7).

A guide is a text file under `guides/` plus an entry in `guides/manifest.json`
that pins its version, the specification version it describes and its SHA-256
digest. Pinning matters: a study record or an A/B report cites the digest, so a
later edit to the guide cannot silently change what "the model saw" means.

Nothing here calls a model and nothing here counts tokens; `eval/tokens.py`
does that, and reports `null` when no tokenizer is installed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
GUIDES = ROOT / "guides"
MANIFEST = GUIDES / "manifest.json"
EXAMPLES = GUIDES / "examples.json"


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def size_of(text: str) -> dict:
    """Bytes, characters and lines: cheap facts that need no tokenizer."""
    return {"bytes": len(text.encode("utf-8")), "chars": len(text), "lines": text.count("\n")}


@dataclass(frozen=True)
class Guide:
    name: str
    path: Path
    text: str
    version: int
    spec_version: int
    digest: str

    @property
    def size(self) -> dict:
        return size_of(self.text)

    def describe(self) -> dict:
        return {"name": self.name, "file": str(self.path.relative_to(ROOT)),
                "version": self.version, "spec_version": self.spec_version,
                "sha256": self.digest, **self.size}


def load_manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def load(name: str | None = None) -> Guide:
    manifest = load_manifest()
    name = name or manifest["current"]
    if name not in manifest["guides"]:
        raise SystemExit(f"unknown guide {name!r}; known: {', '.join(sorted(manifest['guides']))}")
    entry = manifest["guides"][name]
    path = GUIDES / entry["file"]
    text = path.read_text(encoding="utf-8")
    return Guide(name=name, path=path, text=text, version=int(entry["version"]),
                 spec_version=int(entry["spec_version"]), digest=sha256(text))


def verify() -> list[str]:
    """Every recorded digest, size and version must still describe the file."""
    problems: list[str] = []
    manifest = load_manifest()
    for name, entry in sorted(manifest["guides"].items()):
        path = GUIDES / entry["file"]
        if not path.is_file():
            problems.append(f"{name}: {entry['file']} is missing")
            continue
        text = path.read_text(encoding="utf-8")
        if sha256(text) != entry["sha256"]:
            problems.append(f"{name}: sha256 does not match the manifest; "
                            "edit the guide and update guides/manifest.json deliberately")
        for key, value in size_of(text).items():
            if entry.get(key) != value:
                problems.append(f"{name}: {key} is {value}, the manifest says {entry.get(key)}")
        if name not in text:
            problems.append(f"{name}: the guide does not name its own version ({name})")
    return problems


def load_examples() -> dict:
    return json.loads(EXAMPLES.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Print, or verify, the versioned model-facing guide.",
        epilog="The digest in guides/manifest.json is what an evaluation record cites.")
    parser.add_argument("--guide", help="guide name (default: the current one)")
    parser.add_argument("--print", dest="print_text", action="store_true",
                        help="print the guide text itself")
    parser.add_argument("--verify", action="store_true",
                        help="recompute every digest and size recorded in the manifest")
    parser.add_argument("--examples", action="store_true",
                        help="list the canonical examples with their recorded output")
    args = parser.parse_args(argv)
    if args.verify:
        problems = verify()
        for problem in problems:
            print(f"guide check: {problem}", file=sys.stderr)
        print("OK: guides/manifest.json matches the guide files" if not problems
              else f"FAILED: {len(problems)} problem(s)", file=sys.stderr)
        return 1 if problems else 0
    if args.examples:
        document = load_examples()
        for case in document["examples"]:
            print(f"{case['id']:10s} {case['entry']:22s} {len(case['stdout'])} bytes of output")
        return 0
    guide = load(args.guide)
    if args.print_text:
        print(guide.text, end="")
    else:
        print(json.dumps(guide.describe(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
