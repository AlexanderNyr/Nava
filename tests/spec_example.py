"""Helpers for checking the examples in `LANGUAGE.md` (phases N1, N4, N5).

A documented example is only worth its salt if its claimed output is real, so
these helpers extract a fenced `nava` block and the `Output:` sentence that
follows it, and the tests compare program output against the documentation.
"""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANGUAGE = ROOT / "LANGUAGE.md"

#: A fenced block plus the paragraph that follows it.
BLOCK = re.compile(r"```nava\n(.*?)```\n(.*?)(?:\n\n|\Z)", re.DOTALL)


def documented_example(marker: str) -> tuple[str, list[str]]:
    """The example whose source contains `marker`, and its documented output.

    Returns the program text and the list of lines the documentation claims it
    prints. A block without an `Output:` sentence returns an empty list; the
    caller decides whether that is acceptable.
    """
    text = LANGUAGE.read_text(encoding="utf-8")
    for source, following in BLOCK.findall(text):
        if marker not in source:
            continue
        claim = re.search(r"Output: (.*?)(?:\. See|\.\s|\.$)", following, re.DOTALL)
        if claim is None:
            return source, []
        return source, re.findall(r"`([^`]*)`", claim.group(1))
    raise AssertionError(f"no documented example contains {marker!r}")


def documented_output(marker: str) -> tuple[str, str]:
    """The example and its documented output joined into newline-terminated lines."""
    source, lines = documented_example(marker)
    if not lines:
        raise AssertionError(f"the example containing {marker!r} documents no output")
    return source, "".join(f"{line}\n" for line in lines)
