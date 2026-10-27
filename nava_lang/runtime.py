"""Emit only the C runtime helpers a program actually uses (PLAN.md phase N8).

Every Nava program used to compile together with the complete runtime, so a
five-line program produced a 23 KB translation unit and paid the C compiler for
helpers it could not call: file capabilities, `f64` formatting, three array
types, UTF-8 scanning. The profile in `bench/profile.py` shows that native
compilation of that unit is the dominant build cost, so the runtime is now
selected per program.

The selection is a reachability closure over the runtime *text*:

* the text is split into units at top-level definitions (a function, a typedef,
  a `#define`, a static table).  A chunk that contains a preprocessor
  conditional is kept whole, because splitting it could separate `#if` from
  `#endif`;
* a unit's references are every `nv_`/`NV_` identifier that appears in it;
* the roots are the identifiers the generated program body mentions, plus the
  units that must always be present (the includes) — everything reachable from
  those is emitted, in the original order.

Nothing here changes what a helper *does*: a selected unit is byte-identical to
the unit in the full runtime, and a dropped unit is one no reachable code can
call. The full suite compiles and runs generated C for every fixture, so a
helper that is dropped by mistake shows up as a link error or a wrong answer,
not as a silently different program.
"""
from __future__ import annotations

import re

#: Any runtime symbol: the helpers are `nv_*`, the compile-time limits `NV_*`.
#:
#: The character class is deliberate.  `\b(?:nv|NV)_...` states the intent more
#: plainly but costs a word-boundary test at every offset; on a 34 KB body that
#: was 1.0 ms per build, more than everything this module saves.  The class
#: version is ~2.5x faster and matches a **superset**: an identifier such as
#: `foo_nv_bar` counts as a reference, which keeps one extra runtime unit.  That
#: direction is safe — over-approximating the roots can only emit more of the
#: runtime, never less — and `tests/test_runtime.py` pins both properties.
SYMBOL = re.compile(r"[nN][vV]_[A-Za-z0-9_]+")

_FUNCTION = re.compile(
    r"^static\s+(?:inline\s+)?(?:const\s+)?[A-Za-z_][A-Za-z0-9_]*[\s\*]+"
    r"\**((?:nv|NV)_[A-Za-z0-9_]+)\s*\(")
_STATIC_VALUE = re.compile(
    r"^static\s+(?:const\s+)?[A-Za-z_][A-Za-z0-9_]*[\s\*]+"
    r"\**((?:nv|NV)_[A-Za-z0-9_]+)\s*(?:\[|=|;)")
_MACRO = re.compile(r"^#define\s+((?:nv|NV)_[A-Za-z0-9_]+)")
_TYPEDEF = re.compile(r"^typedef\b.*?\b((?:nv|NV)_[A-Za-z0-9_]+)\s*;")
_CONDITIONAL = re.compile(r"^#\s*(?:if|ifdef|ifndef|elif|else|endif)\b")
_DEFINITION = (_FUNCTION, _STATIC_VALUE, _MACRO, _TYPEDEF)
#: Lines that must always survive: the includes and the capability placeholder,
#: which is where the compiled-in grants are substituted.
_ALWAYS = ("#include", "/* NV_CAPABILITIES */")


class Unit:
    """One emit-or-drop piece of the runtime."""

    __slots__ = ("index", "text", "names", "references", "always")

    def __init__(self, index: int, text: str, names: frozenset, references: frozenset,
                 always: bool):
        self.index = index
        self.text = text
        self.names = names
        self.references = references
        self.always = always

    def describe(self) -> dict:
        return {"index": self.index, "names": sorted(self.names), "bytes": len(self.text),
                "always": self.always}


def _names_in(line: str) -> set[str]:
    names: set[str] = set()
    for pattern in _DEFINITION:
        match = pattern.match(line)
        if match:
            names.add(match.group(1))
    return names


def _split_chunk(chunk: str) -> list[str]:
    """Split one blank-line-separated chunk into definitions, keeping comments."""
    lines = chunk.splitlines()
    pieces: list[str] = []
    current: list[str] = []
    pending: list[str] = []
    for line in lines:
        starts_definition = bool(line[:1].strip()) and any(
            pattern.match(line) for pattern in _DEFINITION)
        if starts_definition and current and any(part.strip() for part in current):
            pieces.append("\n".join(current + pending))
            pending = []
            current = [line]
        elif starts_definition and not current:
            current = pending + [line]
            pending = []
        elif not current and line.lstrip().startswith(("/*", "*", "//")):
            pending.append(line)
        elif not current:
            pending.append(line)
        else:
            current.append(line)
    if current or pending:
        pieces.append("\n".join(current + pending))
    return [piece for piece in pieces if piece.strip()]


#: One-entry cache keyed by identity: the runtime text is a module constant, so
#: `text is cached_text` is an exact hit and the split runs once per process
#: instead of once per compilation. Splitting a 22 KB string with regular
#: expressions cost ~1.6 ms, which would be more than the whole optimisation
#: saves; with the cache it is a single identity check.
_cached_text: str | None = None
_cached_units: list[Unit] = []


def split(text: str) -> list[Unit]:
    """The runtime as an ordered list of units."""
    global _cached_text, _cached_units
    if text is _cached_text:
        return _cached_units
    units: list[Unit] = []
    for chunk in text.split("\n\n"):
        conditional = any(_CONDITIONAL.match(line) for line in chunk.splitlines())
        pieces = [chunk] if conditional else _split_chunk(chunk)
        for piece in pieces:
            names: set[str] = set()
            for line in piece.splitlines():
                if line[:1].strip():
                    names |= _names_in(line)
            always = any(marker in piece for marker in _ALWAYS)
            units.append(Unit(len(units), piece, frozenset(names),
                              frozenset(SYMBOL.findall(piece)), always))
    merged = _attach_comments(units)
    # Indices must match list positions: the closure walks `units[index]`.
    _cached_text = text
    _cached_units = [Unit(position, unit.text, unit.names, unit.references, unit.always)
                     for position, unit in enumerate(merged)]
    return _cached_units


def _attach_comments(units: list[Unit]) -> list[Unit]:
    """A chunk that is only a section comment moves into the next definition."""
    result: list[Unit] = []
    pending: list[Unit] = []
    def merged_with(unit: Unit, prefix: list[Unit]) -> Unit:
        text = "\n\n".join([part.text for part in prefix] + [unit.text])
        references = frozenset().union(*(part.references for part in prefix),
                                       unit.references)
        return Unit(unit.index, text, unit.names, references, unit.always)
    for unit in units:
        if unit.names or unit.always:
            result.append(merged_with(unit, pending) if pending else unit)
            pending = []
        else:
            pending.append(unit)
    if pending:
        if result:
            previous = result.pop()
            text = "\n\n".join([previous.text] + [part.text for part in pending])
            result.append(Unit(previous.index, text, previous.names,
                               previous.references | frozenset().union(
                                   *(part.references for part in pending)), previous.always))
        else:
            result.extend(Unit(part.index, part.text, part.names, part.references, True)
                          for part in pending)
    return result


def closure(units: list[Unit], used: set[str]) -> list[int]:
    """Indices of the units reachable from `used`, in the original order.

    A reference that no unit defines is left alone: it is either a program
    function (`nv_fn_*`) or a library symbol such as `INT64_C`.
    """
    defined: dict[str, int] = {}
    for unit in units:
        for name in unit.names:
            defined.setdefault(name, unit.index)
    selected: set[int] = {unit.index for unit in units if unit.always}
    selected |= {defined[name] for name in used if name in defined}
    pending = list(selected)
    while pending:
        index = pending.pop()
        for reference in units[index].references:
            target = defined.get(reference)
            if target is not None and target not in selected:
                selected.add(target)
                pending.append(target)
    return sorted(selected)


def select(text: str, used: set[str]) -> str:
    """The runtime text restricted to the units reachable from `used`."""
    units = split(text)
    keep = set(closure(units, used))
    return "\n\n".join(unit.text for unit in units if unit.index in keep) + "\n"


def summary(text: str) -> dict:
    """What the splitter sees, for a profile record or a test."""
    units = split(text)
    return {"units": len(units), "bytes": len(text),
            "named_units": sum(1 for unit in units if unit.names),
            "always_bytes": sum(len(unit.text) for unit in units if unit.always)}
