from __future__ import annotations

from .diagnostics import make
from .lexer import lex
from .limits import I64_MAX, MAX_NESTING
from .model import (
    ArrayLiteral, Assign, Binary, Boolean, Break, Call, Continue, Expr, ExprStmt,
    Float, For, Function, If, Import, Index, IndexAssign, Let, Number, Param,
    Program, Range, Return, String, Token, Unary, Variable, While,
)

PRECEDENCE = {
    "||": 1, "&&": 2, "==": 3, "!=": 3,
    "<": 4, "<=": 4, ">": 4, ">=": 4,
    "+": 5, "-": 5, "*": 6, "/": 6, "%": 6,
}
TYPES = {"i64", "bool", "str", "void", "f64"}
#: Types an array may contain. There are no arrays of arrays and no arrays of
#: `void`; both are rejected in the parser because they are syntax-level shapes.
ELEMENT_TYPES = {"i64", "bool", "str"}


class Parser:
    def __init__(self, tokens: list[Token]):
        self.tokens = tokens
        self.i = 0
        self.depth = 0

    def enter(self, loc) -> None:
        self.depth += 1
        if self.depth > MAX_NESTING:
            raise make(
                "E0401",
                f"nesting is deeper than the supported limit of {MAX_NESTING}",
                loc,
                hint="simplify the expression or split it into named functions",
            )

    def leave(self) -> None:
        self.depth -= 1

    @property
    def current(self) -> Token:
        return self.tokens[self.i]

    def at(self, kind: str) -> bool:
        return self.current.kind == kind

    def take(self, kind: str) -> Token | None:
        if self.at(kind):
            token = self.current
            self.i += 1
            return token
        return None

    def expect(self, kind: str, description: str | None = None) -> Token:
        if not self.at(kind):
            actual = "end of file" if self.at("EOF") else repr(self.current.value)
            text = "" if self.at("EOF") else str(self.current.value)
            raise make(
                "E0201",
                f"expected {description or repr(kind)}, got {actual}",
                self.current.loc,
                self.current.loc.advance(text),
            )
        token = self.current
        self.i += 1
        return token

    def separators(self) -> None:
        while self.at("NL") or self.at(";"):
            self.i += 1

    def end_statement(self) -> None:
        if self.at(".."):
            raise make("E0350", "a range a..b is only allowed directly after `in`",
                       self.current.loc,
                       hint="write `for i in a..b`, or use a while loop with `i < b`")
        if self.at("NL") or self.at(";"):
            self.separators()
        elif not self.at("}") and not self.at("EOF"):
            raise make("E0202", "expected a newline, ';', or '}' after statement",
                       self.current.loc, hint="end the statement with a newline or ';'")

    def parse_type(self) -> str:
        if self.at("["):
            opening = self.expect("[")
            if self.at("["):
                raise make("E0203", "an array cannot contain another array",
                           self.current.loc, self.current.loc.advance("["),
                           "nested collections are not part of Nava 0.1")
            element = self.expect("IDENT", "an element type (i64, bool, or str)")
            if element.value not in ELEMENT_TYPES:
                raise make("E0203",
                           f"an array element type must be i64, bool, or str, got {element.value!r}",
                           element.loc, element.loc.advance(str(element.value)))
            self.expect("]", "']'")
            return f"[{element.value}]"
        token = self.expect("IDENT", "a type (i64, bool, str, void, or f64)")
        if token.value not in TYPES:
            # The message text is part of the fixture contract: array types are
            # documented in LANGUAGE.md and the hint names the scalar set only.
            raise make("E0203", f"unknown type {token.value!r}; use i64, bool, str, or void",
                       token.loc, token.loc.advance(str(token.value)))
        return str(token.value)

    def parse(self) -> Program:
        functions: list[Function] = []
        imports: list[Import] = []
        self.separators()
        while not self.at("EOF"):
            if self.at("IMPORT"):
                if functions:
                    raise make(
                        "E0354",
                        "an import must come before every function definition",
                        self.current.loc,
                        self.current.loc.advance("import"),
                        "move the import statements to the top of the file",
                    )
                imports.append(self.import_statement())
            else:
                functions.append(self.function())
            self.separators()
        return Program(functions, imports=imports)

    def import_statement(self) -> Import:
        start = self.expect("IMPORT")
        name = self.expect("IDENT", "a module name")
        if self.current.kind == "::":
            raise make(
                "E0201",
                "expected a module name, got '::'",
                self.current.loc,
                self.current.loc.advance("::"),
                "an import names one module: write `import math`, then call math::abs(x)",
            )
        self.end_statement()
        return Import(str(name.value), start.loc, name.loc)

    def function(self) -> Function:
        start = self.expect("FN", "'fn'")
        name = self.expect("IDENT", "function name")
        self.expect("(")
        params: list[Param] = []
        while not self.at(")"):
            p = self.expect("IDENT", "parameter name")
            self.expect(":")
            mutable = self.take("MUT") is not None
            typ = self.parse_type()
            params.append(Param(str(p.value), typ, p.loc, mutable))
            if not self.take(","):
                break
        self.expect(")")
        returns = "void"
        if self.take("->"):
            returns = self.parse_type()
        body = self.block()
        return Function(str(name.value), params, returns, body, start.loc)

    def block(self) -> list:
        self.separators()
        opening = self.expect("{")
        self.enter(opening.loc)
        try:
            body = []
            self.separators()
            while not self.at("}"):
                if self.at("EOF"):
                    raise make("E0204", "expected '}' before end of file", self.current.loc,
                               hint=f"the block opened at line {opening.loc.line} is not closed")
                body.append(self.statement())
                self.separators()
            self.expect("}")
        finally:
            self.leave()
        return body

    def statement(self):
        token = self.current
        if self.take("LET"):
            name = self.expect("IDENT", "variable name")
            annotation = self.parse_type() if self.take(":") else None
            self.expect("=")
            value = self.expression()
            self.end_statement()
            return Let(token.loc, str(name.value), annotation, value, name.loc)
        if self.at("IF"):
            return self.if_statement()
        if self.take("WHILE"):
            condition = self.expression()
            return While(token.loc, condition, self.block())
        if self.take("FOR"):
            name = self.expect("IDENT", "loop variable name")
            self.expect("IN", "'in'")
            iterable = self.expression()
            if self.take(".."):
                # `a..b` is half-open; both bounds are evaluated once, before
                # the first iteration.
                iterable = Range(iterable.loc, iterable, self.expression())
            return For(token.loc, str(name.value), iterable, self.block(), name.loc)
        if self.take("BREAK"):
            self.end_statement()
            return Break(token.loc)
        if self.take("CONTINUE"):
            self.end_statement()
            return Continue(token.loc)
        if self.take("RETURN"):
            value = None if self.current.kind in ("NL", ";", "}", "EOF") else self.expression()
            self.end_statement()
            return Return(token.loc, value)
        if self.at("IDENT") and self.tokens[self.i + 1].kind == "[":
            # `a[i] = value` is an element assignment; `a[i]` alone is not a
            # statement, and the message names the rule instead of the token.
            name = self.expect("IDENT")
            self.expect("[")
            index = self.expression()
            self.expect("]")
            if not self.take("="):
                raise make("E0313", "only function calls can be used as statements",
                           token.loc,
                           hint=f"assign to the element, as in {name.value}[i] = value")
            value = self.expression()
            self.end_statement()
            return IndexAssign(token.loc, str(name.value), index, value, name.loc)
        if self.at("IDENT") and self.tokens[self.i + 1].kind == "=":
            name = self.expect("IDENT")
            self.expect("=")
            value = self.expression()
            self.end_statement()
            return Assign(name.loc, str(name.value), value)
        expr = self.expression()
        self.end_statement()
        return ExprStmt(token.loc, expr)

    def if_statement(self) -> If:
        start = self.expect("IF")
        condition = self.expression()
        then_body = self.block()
        self.separators()
        else_body = None
        if self.take("ELSE"):
            if self.at("IF"):
                else_body = [self.if_statement()]
            else:
                else_body = self.block()
        return If(start.loc, condition, then_body, else_body)

    def expression(self, min_prec: int = 1) -> Expr:
        left = self.unary()
        while self.current.kind in PRECEDENCE and PRECEDENCE[self.current.kind] >= min_prec:
            op = self.current
            self.i += 1
            right = self.expression(PRECEDENCE[op.kind] + 1)
            left = Binary(op.loc, op.kind, left, right)
        return left

    def unary(self) -> Expr:
        token = self.current
        if self.take("-"):
            # Permit the one negative value whose positive counterpart does not fit i64.
            if self.at("NUMBER") and self.current.value == 2**63:
                self.i += 1
                return Number(token.loc, -(2**63))
            self.enter(token.loc)
            try:
                return Unary(token.loc, "-", self.unary())
            finally:
                self.leave()
        if self.take("!"):
            self.enter(token.loc)
            try:
                return Unary(token.loc, "!", self.unary())
            finally:
                self.leave()
        return self.primary()

    def primary(self) -> Expr:
        token = self.current
        if self.take("NUMBER"):
            value = int(token.value)
            if value > I64_MAX:
                raise make("E0108", "integer literal is outside the i64 range", token.loc,
                           token.loc.advance(str(token.value)),
                           "i64 holds -9223372036854775808 to 9223372036854775807")
            return Number(token.loc, value)
        if self.take("FLOAT"):
            return Float(token.loc, float(token.value))
        if self.take("STRING"):
            return String(token.loc, str(token.value))
        if self.take("TRUE"):
            return Boolean(token.loc, True)
        if self.take("FALSE"):
            return Boolean(token.loc, False)
        if self.take("["):
            self.enter(token.loc)
            try:
                items: list[Expr] = []
                while not self.at("]"):
                    if self.at("EOF"):
                        raise make("E0113", "unclosed '['", token.loc,
                                   hint="close the array literal with ']'")
                    item = self.expression()
                    if isinstance(item, ArrayLiteral):
                        raise make("E0203", "an array cannot contain another array",
                                   item.loc)
                    items.append(item)
                    if not self.take(","):
                        break
                self.expect("]", "']'")
            finally:
                self.leave()
            return ArrayLiteral(token.loc, items)
        if self.take("IDENT"):
            name = str(token.value)
            if self.take("::"):
                # `module::function(...)`: a qualified call into another module.
                target = self.expect("IDENT", "a function name after '::'")
                if not self.take("("):
                    raise make(
                        "E0201",
                        "expected '(' after a qualified function name",
                        self.current.loc,
                        hint="a module exposes functions only: write math::abs(x)",
                    )
                args = []
                while not self.at(")"):
                    args.append(self.expression())
                    if not self.take(","):
                        break
                self.expect(")")
                return Call(token.loc, str(target.value), args, module=name)
            if self.take("["):
                index = self.expression()
                self.expect("]", "']'")
                return Index(token.loc, Variable(token.loc, name), index)
            if self.take("("):
                args = []
                while not self.at(")"):
                    args.append(self.expression())
                    if not self.take(","):
                        break
                self.expect(")")
                return Call(token.loc, name, args)
            return Variable(token.loc, name)
        if self.take("("):
            self.enter(token.loc)
            try:
                result = self.expression()
                self.expect(")")
                return result
            finally:
                self.leave()
        raise make("E0205", "expected an expression", token.loc)


def parse(source: str, path: str | None = None) -> Program:
    """Parse one file. `path` is recorded in every location for multi-file use."""
    return Parser(lex(source, path)).parse()
