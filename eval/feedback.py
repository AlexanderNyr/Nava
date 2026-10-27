#!/usr/bin/env python3
"""Structured compiler feedback for repair rounds (PLAN.md phase N7).

A repair round is only worth measuring if the feedback is describable. Instead
of pasting whatever the compiler printed, this module produces one JSON object
with a stable schema and three separate stages:

* `parse` — the candidate is not syntactically valid (`E01xx`, `E02xx`);
* `compile` — it parses but the checker rejects it (`E03xx`, `E04xx`);
* `tests` — it compiles and runs but a test case fails (or it dies at run time).

Keeping the stage explicit is the point: a grammar-constrained decoder can make
`parse` failures disappear while `compile` and `tests` failures remain, and a
record that blurs the three cannot show that. The text is capped, and anything
dropped is counted in `elided` rather than silently lost.

Repairs never count as first-attempt successes: `eval/study.py` records
`first_attempt_passed` from round 0 only, and this module has no say in that.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nava_lang.cli import compile_source  # noqa: E402
from nava_lang.model import NavaError  # noqa: E402

SCHEMA = "nava-feedback/1"
#: Bytes of rendered feedback handed to a model. A repair round costs tokens;
#: a cap keeps that cost from depending on how badly the candidate failed.
MAX_FEEDBACK_BYTES = 2048
MAX_DIAGNOSTICS = 4
MAX_FAILURES = 3


def stage_of(code: str) -> str:
    """Which phase a diagnostic code belongs to."""
    if code.startswith(("E01", "E02")):
        return "parse"
    if code.startswith(("E03", "E04")):
        return "compile"
    if code.startswith(("E05", "E06")):
        return "environment"
    return "compile"


def diagnose(source: str, path: Path | None = None) -> dict:
    """Compile `source` and describe the first rejection in structured form."""
    try:
        compile_source(source, path=path)
    except NavaError as error:
        location = error.loc
        return {
            "stage": stage_of(error.code),
            "codes": [error.code],
            "diagnostics": [{
                "code": error.code,
                "severity": "error",
                "line": None if location is None else location.line,
                "column": None if location is None else location.column,
                "end_column": None if error.end is None else error.end.column,
                "message": error.message,
                "hint": getattr(error, "hint", None),
            }],
        }
    return {"stage": "ok", "codes": [], "diagnostics": []}


def test_failures(report: dict, limit: int = MAX_FAILURES) -> tuple[list[dict], int]:
    """Failing cases from an `eval/evaluate.py` report, and how many were dropped."""
    failures = []
    for index, case in enumerate(report.get("tests", []), 1):
        if case.get("passed"):
            continue
        failures.append({
            "case": case.get("case", index),
            "stdin": case.get("stdin", ""),
            "expected": case.get("expected_stdout", ""),
            "actual": case.get("actual_stdout", ""),
            "exit_code": case.get("exit_code"),
            "reason": case.get("reason"),
        })
    return failures[:limit], max(0, len(failures) - limit)


def build(code: str, report: dict | None = None, *, source_path: Path | None = None,
          limit: int = MAX_FEEDBACK_BYTES) -> tuple[dict, str, str]:
    """A `nava-feedback/1` document, a one-line summary and the text to send.

    The same `code` is inspected twice, on purpose: `report` says whether the
    compiled program passed its tests, while the compiler itself says whether
    the candidate even parses. A round that cannot parse has no test results.
    """
    document: dict = {"schema": SCHEMA, "stage": "tests", "diagnostics": [],
                      "failures": [], "elided": 0}
    if report is None or not report.get("compile", {}).get("ok", True):
        found = diagnose(code, source_path)
        document["stage"] = found["stage"]
        document["diagnostics"] = found["diagnostics"]
        document["elided"] = max(0, len(found["diagnostics"]) - MAX_DIAGNOSTICS)
        summary = ("did not parse" if found["stage"] == "parse"
                   else "did not compile" if found["stage"] == "compile"
                   else "could not be checked")
    else:
        failures, elided = test_failures(report)
        total = len(report.get("tests", []))
        failed = sum(1 for case in report.get("tests", []) if not case.get("passed"))
        document["failures"] = failures
        document["elided"] = elided
        summary = f"failed {failed} of {total} tests"

    text = json.dumps(document, ensure_ascii=False, sort_keys=True)
    while len(text.encode("utf-8")) > limit:
        if document["failures"]:
            document["failures"].pop()
        elif document["diagnostics"]:
            document["diagnostics"] = []
        else:
            document["elided_note"] = "feedback exceeds the size cap"
            break
        document["elided"] += 1
        text = json.dumps(document, ensure_ascii=False, sort_keys=True)
    if len(text.encode("utf-8")) > limit:
        text = json.dumps({"schema": SCHEMA, "stage": document["stage"],
                           "note": "feedback exceeds the size cap"}, ensure_ascii=False)
    return document, summary, text


def describe(document: dict, text: str) -> dict:
    """Metadata for a record: what was fed back, without repeating all of it."""
    import hashlib

    return {
        "schema": document.get("schema"),
        "stage": document.get("stage"),
        "codes": [item.get("code") for item in document.get("diagnostics", [])],
        "diagnostics": len(document.get("diagnostics", [])),
        "failures": len(document.get("failures", [])),
        "elided": document.get("elided", 0),
        "bytes": len(text.encode("utf-8")),
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Show the structured feedback a repair round would receive.")
    parser.add_argument("source", type=Path)
    args = parser.parse_args(argv)
    source = args.source.read_text(encoding="utf-8")
    document, summary, text = build(source, None, source_path=args.source)
    print(f"summary: {summary}")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
