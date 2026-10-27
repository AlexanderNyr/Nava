#!/usr/bin/env python3
"""A small GBNF reader and recognizer (PLAN.md phase N7).

The grammar an inference engine receives is text in the GBNF form used by
llama.cpp and compatible servers. A text artifact that only ever gets pasted
into a request body is not tested, so this module parses that text back,
checks it for the mistakes that make a grammar useless (a reference to a rule
that does not exist, an unreachable rule, left recursion) and can recognise a
string against it.

The recognizer is an Earley parser over characters, which handles the
ambiguous, non-deterministic shape of a real grammar. Nullable nonterminals are
supported (see `_Earley`), because whitespace and optional groups are
nullable. Nothing here executes generated code: recognition is syntax only.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

NAME = re.compile(r"[a-zA-Z_][a-zA-Z0-9_-]*")


class GbnfError(ValueError):
    """The text is not GBNF, or uses a feature this reader does not support."""


# ---------------------------------------------------------------------------
# Reading GBNF
# ---------------------------------------------------------------------------

def _skip_inline(text: str, i: int) -> int:
    """Skip spaces, tabs and `#` comments, but not newlines."""
    n = len(text)
    while i < n:
        if text[i] in " \t\r":
            i += 1
        elif text[i] == "#":
            while i < n and text[i] != "\n":
                i += 1
        else:
            break
    return i


def _skip(text: str, i: int) -> int:
    """Skip whitespace of any kind and comments."""
    n = len(text)
    while i < n:
        if text[i] in " \t\r\n":
            i += 1
        elif text[i] == "#":
            while i < n and text[i] != "\n":
                i += 1
        else:
            break
    return i


def _at_rule_start(text: str, i: int) -> bool:
    """True when position `i` begins a new `name ::= ...` definition."""
    j = _skip(text, i)
    match = NAME.match(text, j)
    if not match:
        return False
    j = _skip_inline(text, match.end())
    return text.startswith("::=", j)


def _parse_literal(text: str, i: int) -> tuple[str, int]:
    assert text[i] == '"'
    i += 1
    out: list[str] = []
    n = len(text)
    while True:
        if i >= n:
            raise GbnfError("unterminated string literal")
        ch = text[i]
        if ch == '"':
            return "".join(out), i + 1
        if ch == "\\":
            i += 1
            if i >= n:
                raise GbnfError("dangling escape in a string literal")
            escape = text[i]
            simple = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", '"': '"',
                      "[": "[", "]": "]", "(": "(", ")": ")", "{": "{", "}": "}",
                      "|": "|", ".": ".", "*": "*", "+": "+", "?": "?", "-": "-", "^": "^"}
            if escape in simple:
                out.append(simple[escape])
                i += 1
                continue
            if escape == "x" and re.match(r"[0-9a-fA-F]{2}", text[i + 1:i + 3]):
                out.append(chr(int(text[i + 1:i + 3], 16)))
                i += 3
                continue
            if escape == "u" and re.match(r"[0-9a-fA-F]{4}", text[i + 1:i + 5]):
                out.append(chr(int(text[i + 1:i + 5], 16)))
                i += 5
                continue
            raise GbnfError(f"unsupported escape \\{escape} in a string literal")
        out.append(ch)
        i += 1


def _parse_class(text: str, i: int) -> tuple[dict, int]:
    assert text[i] == "["
    i += 1
    n = len(text)
    negated = False
    if i < n and text[i] == "^":
        negated = True
        i += 1
    chars: set[str] = set()
    ranges: list[tuple[str, str]] = []
    while True:
        if i >= n:
            raise GbnfError("unterminated character class")
        ch = text[i]
        if ch == "]":
            return {"negated": negated, "chars": chars, "ranges": ranges}, i + 1
        if ch == "\\":
            i += 1
            escape = text[i] if i < n else ""
            simple = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", '"': '"',
                      "]": "]", "[": "[", "-": "-", "^": "^", "x": "x", "u": "u"}
            if escape in ("x", "u"):
                width = 2 if escape == "x" else 4
                digits = text[i + 1:i + 1 + width]
                if not re.fullmatch(r"[0-9a-fA-F]+", digits or "") or len(digits) != width:
                    raise GbnfError("malformed hex escape in a character class")
                ch = chr(int(digits, 16))
                i += 1 + width
            elif escape in simple:
                ch = simple[escape]
                i += 1
            else:
                raise GbnfError(f"unsupported escape \\{escape} in a character class")
        else:
            i += 1
        if i + 1 < n and text[i] == "-" and text[i + 1] != "]":
            low = ch
            i += 1
            high = text[i]
            if high == "\\":
                raise GbnfError("escaped range ends are not supported in a character class")
            if ord(high) < ord(low):
                raise GbnfError(f"reversed range {low!r}-{high!r} in a character class")
            ranges.append((low, high))
            i += 1
        else:
            chars.add(ch)


def _parse_item(text: str, i: int) -> tuple[dict, int]:
    i = _skip(text, i)
    if i >= len(text):
        raise GbnfError("expected a grammar item, got end of text")
    ch = text[i]
    if ch == '"':
        value, i = _parse_literal(text, i)
        node: dict = {"kind": "lit", "value": value}
    elif ch == "[":
        value, i = _parse_class(text, i)
        node = {"kind": "class", **value}
    elif ch == ".":
        # GBNF's wildcard: any character except a newline.
        node = {"kind": "class", "negated": True, "chars": frozenset("\n"), "ranges": []}
        i += 1
    elif ch == "(":
        i += 1
        alternatives, i = _parse_alternatives(text, i, closing=")")
        node = {"kind": "group", "alternatives": alternatives}
    elif NAME.match(text, i):
        match = NAME.match(text, i)
        node = {"kind": "ref", "name": match.group(0)}
        i = match.end()
    else:
        raise GbnfError(f"unexpected character {ch!r} in a grammar body")
    i = _skip(text, i)
    if i < len(text) and text[i] in "?*+":
        node = {"kind": {"?": "opt", "*": "star", "+": "plus"}[text[i]], "node": node}
        i += 1
    return node, i


def _parse_sequence(text: str, i: int, closing: str | None) -> tuple[list[dict], int]:
    items: list[dict] = []
    while True:
        if closing is None and _at_rule_start(text, i):
            return items, i
        i = _skip(text, i)
        if closing is None and _at_rule_start(text, i):
            return items, i
        if i >= len(text):
            if closing is not None:
                raise GbnfError(f"expected {closing!r} before end of text")
            return items, i
        if text[i] in "|" or (closing is not None and text[i] == closing):
            return items, i
        if closing is None and text[i] == "\n" and _at_rule_start(text, i):
            return items, i
        if text[i] == ")":
            raise GbnfError("unbalanced ')'")
        item, i = _parse_item(text, i)
        items.append(item)


def _parse_alternatives(text: str, i: int, closing: str | None) -> tuple[list[list[dict]], int]:
    alternatives = []
    while True:
        sequence, i = _parse_sequence(text, i, closing)
        alternatives.append(sequence)
        i = _skip(text, i)
        if i < len(text) and text[i] == "|":
            i += 1
            continue
        if closing is not None:
            if i >= len(text) or text[i] != closing:
                raise GbnfError(f"expected {closing!r}")
            i += 1
        return alternatives, i


def parse(text: str) -> "Grammar":
    """Read a GBNF document. The first rule is the entry rule."""
    rules: dict[str, list[list[dict]]] = {}
    order: list[str] = []
    i = 0
    while True:
        i = _skip(text, i)
        if i >= len(text):
            break
        match = NAME.match(text, i)
        if not match:
            raise GbnfError(f"expected a rule name at offset {i}")
        name = match.group(0)
        i = _skip_inline(text, match.end())
        if not text.startswith("::=", i):
            raise GbnfError(f"expected '::=' after rule {name!r}")
        i += 3
        alternatives, i = _parse_alternatives(text, i, closing=None)
        if name in rules:
            raise GbnfError(f"rule {name!r} is defined twice")
        rules[name] = alternatives
        order.append(name)
    if not rules:
        raise GbnfError("the grammar has no rules")
    return Grammar(rules, order)


# ---------------------------------------------------------------------------
# Compiling the AST into productions
# ---------------------------------------------------------------------------

class Grammar:
    def __init__(self, rules: dict[str, list[list[dict]]], order: list[str]):
        self.rules = rules
        self.order = order
        self.start = order[0]
        self.productions: list[tuple[str, tuple]] = []
        self.references: dict[str, set[str]] = {}
        #: Rules invented for a group or a quantifier; not in the source text.
        self.auxiliary: set[str] = set()
        self._counter = 0
        for name in order:
            self.references[name] = set()
            for alternative in rules[name]:
                self.productions.append((name, self._sequence(alternative, name)))
        self.by_lhs: dict[str, list[int]] = {}
        for index, (lhs, _) in enumerate(self.productions):
            self.by_lhs.setdefault(lhs, []).append(index)

    def _fresh(self, hint: str) -> str:
        self._counter += 1
        name = f"{hint}#{self._counter}"
        self.auxiliary.add(name)
        return name

    def _sequence(self, items: list[dict], owner: str) -> tuple:
        symbols = []
        for item in items:
            kind = item["kind"]
            if kind == "lit":
                if item["value"]:
                    symbols.append(("t", "lit", item["value"]))
                # An empty literal is GBNF's way of writing an empty
                # alternative; it contributes no symbol. Making it a
                # zero-length terminal instead would never advance.
            elif kind == "class":
                symbols.append(("t", "class",
                                (item["negated"], frozenset(item["chars"]),
                                 tuple(item["ranges"]))))
            elif kind == "ref":
                self.references[owner].add(item["name"])
                symbols.append(("n", item["name"]))
            elif kind == "group":
                name = self._fresh("group")
                self.references[owner].add(name)
                self.references.setdefault(name, set())
                for alternative in item["alternatives"]:
                    self.productions.append((name, self._sequence(alternative, name)))
                symbols.append(("n", name))
            elif kind in ("opt", "star", "plus"):
                name = self._fresh(kind)
                self.references[owner].add(name)
                self.references.setdefault(name, set())
                inner = self._sequence([item["node"]], name)
                if kind == "opt":
                    self.productions.append((name, inner))
                    self.productions.append((name, ()))
                elif kind == "star":
                    self.productions.append((name, inner + (("n", name),)))
                    self.productions.append((name, ()))
                else:
                    self.productions.append((name, inner))
                    self.productions.append((name, inner + (("n", name),)))
                symbols.append(("n", name))
            else:  # pragma: no cover - parse() cannot produce this
                raise GbnfError(f"unknown node kind {kind!r}")
        return tuple(symbols)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def nullable_nonterminals(grammar: Grammar) -> set[str]:
    nullable: set[str] = set()
    changed = True
    while changed:
        changed = False
        for lhs, rhs in grammar.productions:
            if lhs in nullable and not rhs:
                continue
            if lhs in nullable:
                continue
            if all(symbol[0] == "n" and symbol[1] in nullable for symbol in rhs):
                nullable.add(lhs)
                changed = True
    return nullable


def validate(grammar: Grammar) -> list[str]:
    """Problems that make a grammar artefact unusable."""
    problems: list[str] = []
    defined = set(grammar.rules) | grammar.auxiliary
    for name, referenced in sorted(grammar.references.items()):
        for target in sorted(referenced - defined):
            problems.append(f"rule {name!r} refers to undefined rule {target!r}")
    # Reachability from the start rule.
    reachable: set[str] = set()
    frontier = [grammar.start]
    while frontier:
        current = frontier.pop()
        if current in reachable:
            continue
        reachable.add(current)
        frontier.extend(grammar.references.get(current, ()))
    for name in grammar.order:
        if name not in reachable:
            problems.append(f"rule {name!r} is unreachable from {grammar.start!r}")
    # Left recursion (through nullable prefixes).
    nullable = nullable_nonterminals(grammar)
    every_rule = {lhs for lhs, _ in grammar.productions}
    left_corner: dict[str, set[str]] = {name: set() for name in every_rule}
    for lhs, rhs in grammar.productions:
        for symbol in rhs:
            if symbol[0] == "n":
                left_corner[lhs].add(symbol[1])
                if symbol[1] in nullable:
                    continue
            break
    for start in sorted(every_rule):
        seen: set[str] = set()
        stack = list(left_corner[start])
        while stack:
            current = stack.pop()
            if current == start:
                problems.append(f"rule {start!r} is left recursive")
                break
            if current in seen:
                continue
            seen.add(current)
            stack.extend(left_corner.get(current, ()))
    return problems


# ---------------------------------------------------------------------------
# Recognition
# ---------------------------------------------------------------------------

def _terminal_matches(symbol: tuple, text: str, position: int) -> int:
    """Length of the match at `position`, or 0 for no match."""
    _, kind, payload = symbol
    if kind == "lit":
        return len(payload) if text.startswith(payload, position) else 0
    negated, chars, ranges = payload
    if position >= len(text):
        return 0
    ch = text[position]
    inside = ch in chars or any(low <= ch <= high for low, high in ranges)
    if inside != negated:
        return 1
    return 0


def earley_accepts(productions: list[tuple[str, tuple]], start: str, length: int,
                   advance) -> bool:
    """Earley recognition over positions 0..length.

    `advance(position, symbol)` returns the next position, or 0 when the symbol
    does not match there; a symbol may consume more than one position (a string
    literal in characters, one token in tokens).
    """
    by_lhs: dict[str, list[int]] = {}
    for index, (lhs, _) in enumerate(productions):
        by_lhs.setdefault(lhs, []).append(index)
    chart: list[set[tuple]] = [set() for _ in range(length + 1)]
    agendas: list[list[tuple]] = [[] for _ in range(length + 1)]
    #: Nonterminals that completed with an empty match at each position; a state
    #: awaiting one of them can advance without consuming input.
    nullable_done: list[set[str]] = [set() for _ in range(length + 1)]

    def add(position: int, state: tuple) -> None:
        if state not in chart[position]:
            chart[position].add(state)
            agendas[position].append(state)

    for index in by_lhs.get(start, []):
        add(0, (index, 0, 0))

    for position in range(length + 1):
        cursor = 0
        while cursor < len(agendas[position]):
            index, dot, origin = agendas[position][cursor]
            cursor += 1
            lhs, rhs = productions[index]
            if dot < len(rhs):
                symbol = rhs[dot]
                if symbol[0] == "n":
                    name = symbol[1]
                    for production in by_lhs.get(name, []):
                        add(position, (production, 0, position))
                    if name in nullable_done[position]:
                        add(position, (index, dot + 1, origin))
                else:
                    nxt = advance(position, symbol)
                    if nxt:
                        add(nxt, (index, dot + 1, origin))
            else:
                if origin == position:
                    nullable_done[position].add(lhs)
                for other_index, other_dot, other_origin in list(chart[origin]):
                    if other_dot >= len(productions[other_index][1]):
                        continue
                    next_symbol = productions[other_index][1][other_dot]
                    if next_symbol[0] == "n" and next_symbol[1] == lhs:
                        add(position, (other_index, other_dot + 1, other_origin))
    return any(dot == len(productions[index][1]) and origin == 0
               for index, dot, origin in chart[length]
               if productions[index][0] == start)


def accepts_text(grammar: Grammar, text: str) -> bool:
    """Recognise `text` against a parsed GBNF document."""
    def advance(position: int, symbol: tuple) -> int:
        length = _terminal_matches(symbol, text, position)
        return position + length if length else 0

    return earley_accepts(grammar.productions, grammar.start, len(text), advance)


def to_dot(grammar: Grammar) -> str:
    """A rule-reference graph, for looking at a grammar by eye."""
    lines = ["digraph gbnf {", "  rankdir=LR;"]
    for name in grammar.order:
        for target in sorted(grammar.references.get(name, ())):
            lines.append(f'  "{name}" -> "{target}";')
    lines.append("}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Validate a grammar artefact, or recognise a file against it."""
    parser = argparse.ArgumentParser(
        description="Read, validate and execute GBNF text.",
        epilog="This is the same reader the tests use on the emitted Nava grammar; it "
               "recognises text, it does not run a model.")
    parser.add_argument("grammar", type=Path, help="GBNF file")
    parser.add_argument("--validate", action="store_true",
                        help="report undefined, unreachable and left-recursive rules")
    parser.add_argument("--accept", type=Path, metavar="FILE",
                        help="report whether FILE is recognised by the grammar")
    parser.add_argument("--dot", action="store_true", help="print the rule graph as DOT")
    args = parser.parse_args(argv)

    try:
        grammar = parse(args.grammar.read_text(encoding="utf-8"))
    except GbnfError as error:
        print(f"gbnf: {error}", file=sys.stderr)
        return 2
    status = 0
    if args.validate:
        problems = validate(grammar)
        for problem in problems:
            print(f"gbnf: {problem}", file=sys.stderr)
        print("OK: the grammar has no undefined, unreachable or left-recursive rules"
              if not problems else f"FAILED: {len(problems)} problem(s)", file=sys.stderr)
        status = status or (1 if problems else 0)
    if args.accept is not None:
        text = args.accept.read_text(encoding="utf-8")
        accepted = accepts_text(grammar, text)
        print(f"{args.accept}: accepted={accepted}")
        status = status or (0 if accepted else 1)
    if args.dot:
        print(to_dot(grammar))
    if not (args.validate or args.accept is not None or args.dot):
        print(f"{args.grammar}: {len(grammar.rules)} rules, start {grammar.start!r}")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
