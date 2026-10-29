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

from .model import (Assign, Call, Function, IndexAssign, MemberAssign, Number,
                    Program, Return)

#: Fields written once per function.
SINGLE_FIELDS = ("pre", "post", "cost", "pure")
#: The two values `pure:` accepts. A pure function has no effect beyond its
#: result: it prints nothing, reads no input, and writes no storage the caller
#: holds, so two runs with equal arguments answer the same thing.
PURE_VALUES = ("yes", "no")
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
    pure: str = ""
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
    #: The field a prose line continues. A contract wraps because it is prose:
    #: a line that is not a `key:` line continues the field above it, and a line
    #: above every field is the summary.
    last: str | None = None
    for raw in lines:
        line = strip_marker(raw)
        match = FIELD_RE.match(line)
        if match is None:
            text = line.strip()
            if not text:
                continue
            if last is None:
                prose.append(text)
            elif last in SINGLE_FIELDS:
                single[last] = f"{single[last]} {text}".strip()
            elif last == "example":
                examples[-1] = f"{examples[-1]} {text}".strip()
            elif last == "error":
                statements, message = errors[-1]
                errors[-1] = (statements, f"{message} {text}".strip())
            else:
                prose.append(text)
            continue
        key, value = match.group("key"), match.group("value").strip()
        last = key
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
            # An unknown `key:` is prose: a typo shows up as a missing field.
            last = None
            text = line.strip()
            if text:
                prose.append(text)
    return Contract(
        summary=" ".join(prose),
        pre=single.get("pre", ""),
        post=single.get("post", ""),
        cost=single.get("cost", ""),
        pure=single.get("pure", "").lower(),
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
    if contract.pure and contract.pure not in PURE_VALUES:
        problems.append(f"{name}: `pure:` is `yes` or `no`, not {contract.pure!r}")
    return problems


#: Builtins a pure function may call: they read their arguments (and the storage
#: a borrow points at) and answer with a value. Everything else — printing,
#: reading stdin or a file, growing a collection — is an effect.
PURE_BUILTINS = frozenset({
    "len", "array_zeros", "to_i64", "to_f64", "bytes_len", "chars_len", "byte_at",
    "str_concat", "str_from_i64", "str_from_f64", "str_slice", "str_index_of",
    "str_starts_with", "str_ends_with", "str_trim", "str_replace", "str_join",
    "vec_len", "vec_capacity", "vec_get",
    "map_len", "map_contains", "map_get",
    "set_len", "set_contains",
    "string_len", "string_capacity", "string_text",
})


def _nodes(value):
    """Every node inside a value, however deeply the records nest."""
    if isinstance(value, (list, tuple)):
        for item in value:
            yield from _nodes(item)
        return
    if isinstance(value, dict):
        for item in value.values():
            yield from _nodes(item)
        return
    # The AST nodes are `@record()` classes, not dataclasses: `__field_names__`
    # is the list this project's own record helper exposes.
    names = getattr(value, "__field_names__", None)
    if names is not None:
        yield value
        for name in names:
            yield from _nodes(getattr(value, name))


def _written_names(statement) -> list[str]:
    """The names a statement writes to, so purity can refuse a write."""
    if isinstance(statement, Assign):
        return [statement.name]
    if isinstance(statement, IndexAssign):
        return [statement.name]
    if isinstance(statement, MemberAssign):
        target = statement.target
        return [target.name] if hasattr(target, "name") else []
    return []


def purity_problems(program: Program, module: str) -> list[str]:
    """Check what a `pure: yes` claim says against what the body does.

    The claim is checked, not believed: a pure function may not take a `mut`
    parameter (it would be able to write the caller's storage), may not call a
    builtin that prints, reads input or grows a collection, may not call a
    function of its own module that is not itself documented pure, and may not
    write through a parameter. A call into *another* module cannot be judged
    from one parsed module, so it is reported instead of assumed.
    """
    claims: dict[str, str] = {}
    for function in program.functions:
        if function.name in EXEMPT:
            continue
        claims[function.name] = parse(function.doc).pure
    problems: list[str] = []
    for function in program.functions:
        if function.name in EXEMPT or claims.get(function.name) != "yes":
            continue
        label = f"{module}::{function.name}" if module else function.name
        parameters = {param.name for param in function.params}
        for param in function.params:
            if getattr(param, "mutable", False):
                problems.append(
                    f"{label}: claims `pure: yes` but takes `mut {param.typ}`, "
                    f"so it can write the caller's storage")
        for node in _nodes(function.body):
            if isinstance(node, Call):
                name, called = node.name, node.module
                if called not in (None, "", module):
                    problems.append(
                        f"{label}: claims `pure: yes` but calls `{called}::{name}`; "
                        f"purity across modules is not checked, so this cannot be "
                        f"verified — mark the function `pure: no` or call it from "
                        f"the same module")
                elif called is None and name in claims:
                    if claims[name] != "yes":
                        problems.append(
                            f"{label}: claims `pure: yes` but calls `{name}`, "
                            f"which is documented `pure: no`")
                elif name not in PURE_BUILTINS:
                    problems.append(
                        f"{label}: claims `pure: yes` but calls `{name}`, "
                        f"which has an effect")
            elif isinstance(node, (Assign, IndexAssign, MemberAssign)):
                for written in _written_names(node):
                    if written in parameters:
                        problems.append(
                            f"{label}: claims `pure: yes` but writes through "
                            f"the parameter `{written}`")
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
    problems.extend(purity_problems(program, module))
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
        lines.append(f"| Pure | {contract.pure or '_missing_'} |")
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
