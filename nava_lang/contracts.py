"""Contracts in a doc block, and the reference they generate (PLAN.md N12b).

A **doc block** is a run of `///` lines directly above a `fn` declaration. The
parser keeps the lines; this module gives them a meaning:

    /// `abs(x)` — the absolute value of `x`.
    ///
    /// pre: none
    /// post: the result is `x` when `x >= 0`, and `-x` otherwise
    /// cost: O(1)
    /// example: math::abs(-7) == 7
    /// error: math::abs(-9223372036854775808) => i64 overflow in unary -

Four kinds of line are recognised:

* prose — everything that is not a field; the first paragraph is the summary;
* `pre:`, `post:`, `cost:` — one line each, the written promise;
* `example:` — statements separated by `;` whose **last** statement is a `bool`
  expression. The guard compiles and runs it and requires it to be `true`.
* `error: <statements> => <message>` — statements that must **stop the program**
  with that text on stderr. A documented error that does not happen is a broken
  contract, not a paragraph, so the guard runs it too.

Nothing here decides whether a contract is *good*. It decides whether one exists,
whether every field is filled, and — with `tests/test_contracts.py` — whether the
examples and the error cases still behave as written.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .model import Function, Number, Program, Return

#: Fields written once per function.
SINGLE_FIELDS = ("pre", "post", "cost")
#: Fields written as often as needed.
REPEATED_FIELDS = ("example", "error")
#: Functions a standard module must declare but does not offer to a program: the
#: loader calls `module_version`, so it is part of the module protocol rather
#: than of the library, and it carries no contract.
EXEMPT = frozenset({"module_version"})
#: A field line: known key, a colon, then the value.
FIELD_RE = re.compile(r"^\s*(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*:\s*(?P<value>.*)$")
#: The separator an `error:` line puts between the statements and the message.
ERROR_ARROW = "=>"


@dataclass(frozen=True)
class Contract:
    """What a doc block promises about one function."""

    summary: str = ""
    pre: str = ""
    post: str = ""
    cost: str = ""
    examples: tuple[str, ...] = ()
    errors: tuple[tuple[str, str], ...] = ()
    #: Field lines seen more than once, so a copy-paste is reported rather than
    #: silently overwritten.
    duplicates: tuple[str, ...] = ()

    def missing(self) -> list[str]:
        """Which required parts are absent, in a stable order."""
        problems = []
        if not self.summary:
            problems.append("no summary line")
        for name in SINGLE_FIELDS:
            if not getattr(self, name):
                problems.append(f"no `{name}:` line")
        if not self.examples:
            problems.append("no `example:` line")
        return problems


def strip_marker(line: str) -> str:
    """A doc line without the single space the marker usually leaves behind."""
    return line[1:] if line.startswith(" ") else line


def parse(lines: list[str] | tuple[str, ...]) -> Contract:
    """Read a doc block. Unknown `key:` lines stay prose, by design.

    A typo such as `pres:` therefore shows up as a *missing* `pre:` rather than as
    an error about an unknown field, which names the thing the author has to add.
    """
    prose: list[str] = []
    single: dict[str, str] = {}
    examples: list[str] = []
    errors: list[tuple[str, str]] = []
    duplicates: list[str] = []
    for raw in lines:
        line = strip_marker(raw)
        match = FIELD_RE.match(line)
        if match is None:
            text = line.strip()
            if text:
                prose.append(text)
            continue
        key, value = match.group("key"), match.group("value").strip()
        if key in SINGLE_FIELDS:
            if key in single:
                duplicates.append(key)
            single[key] = value
        elif key == "example":
            examples.append(value)
        elif key == "error":
            left, arrow, right = value.partition(ERROR_ARROW)
            errors.append((left.strip(), right.strip()) if arrow else (value, ""))
        else:
            text = line.strip()
            if text:
                prose.append(text)
    return Contract(
        summary=" ".join(prose),
        pre=single.get("pre", ""),
        post=single.get("post", ""),
        cost=single.get("cost", ""),
        examples=tuple(examples),
        errors=tuple(errors),
        duplicates=tuple(duplicates),
    )


def problems(contract: Contract, name: str) -> list[str]:
    """Everything wrong with one contract, as sentences naming the function."""
    problems = [f"{name}: {item}" for item in contract.missing()]
    for key in contract.duplicates:
        problems.append(f"{name}: `{key}:` is written more than once")
    for index, (statements, message) in enumerate(contract.errors, start=1):
        if not statements:
            problems.append(f"{name}: error {index} has no statements")
        if not message:
            problems.append(f"{name}: error {index} does not say what the program prints")
    for index, example in enumerate(contract.examples, start=1):
        if not statements_of(example):
            problems.append(f"{name}: example {index} is empty")
    return problems


def statements_of(text: str) -> list[str]:
    """Split a contract line into statements on `;`, respecting string literals.

    Nava separates statements with `;` and never uses one inside an expression,
    so a split is faithful — except inside a string literal, where a `;` is a
    character like any other.
    """
    parts: list[str] = []
    current: list[str] = []
    in_string = False
    escaped = False
    for character in text:
        if in_string:
            current.append(character)
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
            current.append(character)
            continue
        if character == ";":
            parts.append("".join(current).strip())
            current = []
            continue
        current.append(character)
    tail = "".join(current).strip()
    if tail:
        parts.append(tail)
    return [part for part in parts if part]


def is_public(program: Program, function: Function) -> bool:
    """Is this function part of a module's public surface?

    An edition-2 module exports only its `pub` functions, so `pub` decides. An
    edition-1 module exports everything — that is what the loader does — so every
    function is public there, except the one the module protocol owns.
    """
    if function.name in EXEMPT:
        return False
    if getattr(program, "edition", 1) >= 2:
        return function.public
    return True


def contracts_of(program: Program) -> dict[str, Contract]:
    """Every public function of a parsed module, with its contract."""
    return {function.name: parse(function.doc)
            for function in program.functions if is_public(program, function)}


def check_program(program: Program, module: str) -> list[str]:
    """The guard's question, asked of one parsed module."""
    problems: list[str] = []
    for function in program.functions:
        if not is_public(program, function):
            continue
        name = f"{module}::{function.name}" if module else function.name
        problems.extend(problems_of(parse(function.doc), name))
    return problems


def problems_of(contract: Contract, name: str) -> list[str]:
    return problems(contract, name)


def module_version(program: Program) -> int:
    """The number the module's `module_version()` returns, or 0 when it has none."""
    for function in program.functions:
        if function.name != "module_version":
            continue
        for statement in function.body:
            if isinstance(statement, Return) and isinstance(statement.value, Number):
                return int(statement.value.value)
    return 0


def signature(function: Function) -> str:
    """`abs(x: i64) -> i64`, as the reference writes it."""
    parameters = ", ".join(f"{param.name}: {param.typ}" for param in function.params)
    return f"{function.name}({parameters}) -> {function.returns}"


def render_module(module: str, source: Path, program: Program, version: int) -> str:
    """The Markdown reference for one module, generated from its contracts."""
    lines = [
        f"# `{module}` — Nava standard library, version {version}",
        "",
        f"Generated by `nava doc` from the doc blocks in `{source.as_posix()}`.",
        "Do not edit this file by hand:",
        "`tests/test_contracts.py` regenerates it and fails when the two disagree.",
        "",
    ]
    for function in program.functions:
        if not is_public(program, function):
            continue
        contract = parse(function.doc)
        lines.append(f"## `{signature(function)}`")
        lines.append("")
        lines.append(contract.summary or "_No summary._")
        lines.append("")
        lines.append("| Contract | |")
        lines.append("|---|---|")
        lines.append(f"| Precondition | {contract.pre or '_missing_'} |")
        lines.append(f"| Postcondition | {contract.post or '_missing_'} |")
        lines.append(f"| Cost | {contract.cost or '_missing_'} |")
        lines.append("")
        for example in contract.examples:
            lines.append("**Example**, which must be true:")
            lines.append("")
            lines.append("```nava")
            for statement in statements_of(example):
                lines.append(statement)
            lines.append("```")
            lines.append("")
        for statements, message in contract.errors:
            lines.append("**Documented failure**, which must happen:")
            lines.append("")
            lines.append("```nava")
            for statement in statements_of(statements):
                lines.append(statement)
            lines.append("```")
            lines.append("")
            lines.append(f"The program stops with `{message}`.")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"
