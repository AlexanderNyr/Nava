from __future__ import annotations

from .lexer import lex
from .model import (
    Assign, Binary, Boolean, Call, Expr, ExprStmt, Function, If, Let,
    NavaError, Number, Param, Program, Return, String, Token, Unary,
    Variable, While,
)

PRECEDENCE = {
    "||": 1, "&&": 2, "==": 3, "!=": 3,
    "<": 4, "<=": 4, ">": 4, ">=": 4,
    "+": 5, "-": 5, "*": 6, "/": 6, "%": 6,
}
TYPES = {"i64", "bool", "str", "void"}
I64_MAX = 2**63 - 1


class Parser:
    def __init__(self, tokens: list[Token]):
        self.tokens = tokens
        self.i = 0

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
            raise NavaError(f"expected {description or repr(kind)}, got {actual}", self.current.loc)
        token = self.current
        self.i += 1
        return token

    def separators(self) -> None:
        while self.at("NL") or self.at(";"):
            self.i += 1

    def end_statement(self) -> None:
        if self.at("NL") or self.at(";"):
            self.separators()
        elif not self.at("}") and not self.at("EOF"):
            raise NavaError("expected a newline, ';', or '}' after statement", self.current.loc)

    def parse_type(self) -> str:
        token = self.expect("IDENT", "a type (i64, bool, str, or void)")
        if token.value not in TYPES:
            raise NavaError(f"unknown type {token.value!r}; use i64, bool, str, or void", token.loc)
        return str(token.value)

    def parse(self) -> Program:
        functions: list[Function] = []
        self.separators()
        while not self.at("EOF"):
            functions.append(self.function())
            self.separators()
        return Program(functions)

    def function(self) -> Function:
        start = self.expect("FN", "'fn'")
        name = self.expect("IDENT", "function name")
        self.expect("(")
        params: list[Param] = []
        while not self.at(")"):
            p = self.expect("IDENT", "parameter name")
            self.expect(":")
            typ = self.parse_type()
            params.append(Param(str(p.value), typ, p.loc))
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
        self.expect("{")
        body = []
        self.separators()
        while not self.at("}"):
            if self.at("EOF"):
                raise NavaError("expected '}' before end of file", self.current.loc)
            body.append(self.statement())
            self.separators()
        self.expect("}")
        return body

    def statement(self):
        token = self.current
        if self.take("LET"):
            name = self.expect("IDENT", "variable name")
            annotation = self.parse_type() if self.take(":") else None
            self.expect("=")
            value = self.expression()
            self.end_statement()
            return Let(token.loc, str(name.value), annotation, value)
        if self.at("IF"):
            return self.if_statement()
        if self.take("WHILE"):
            condition = self.expression()
            return While(token.loc, condition, self.block())
        if self.take("RETURN"):
            value = None if self.current.kind in ("NL", ";", "}", "EOF") else self.expression()
            self.end_statement()
            return Return(token.loc, value)
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
            return Unary(token.loc, "-", self.unary())
        if self.take("!"):
            return Unary(token.loc, "!", self.unary())
        return self.primary()

    def primary(self) -> Expr:
        token = self.current
        if self.take("NUMBER"):
            value = int(token.value)
            if value > I64_MAX:
                raise NavaError("integer literal is outside the i64 range", token.loc)
            return Number(token.loc, value)
        if self.take("STRING"):
            return String(token.loc, str(token.value))
        if self.take("TRUE"):
            return Boolean(token.loc, True)
        if self.take("FALSE"):
            return Boolean(token.loc, False)
        if self.take("IDENT"):
            name = str(token.value)
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
            result = self.expression()
            self.expect(")")
            return result
        raise NavaError("expected an expression", token.loc)


def parse(source: str) -> Program:
    return Parser(lex(source)).parse()
