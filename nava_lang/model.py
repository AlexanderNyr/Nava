from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


class Severity:
    """Diagnostic severities. Errors stop compilation; warnings do not."""

    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class Location:
    line: int
    column: int

    def advance(self, text: str) -> "Location":
        """End location of `text` when it starts at this location."""
        line, column = self.line, self.column
        for character in text:
            if character == "\n":
                line += 1
                column = 1
            else:
                column += 1
        return Location(line, column)


@dataclass(frozen=True)
class Diagnostic:
    """A message with a stable code, a source range and an optional hint."""

    code: str
    severity: str
    message: str
    loc: Optional[Location] = None
    end: Optional[Location] = None
    hint: Optional[str] = None


class NavaError(Exception):
    """A compilation error. `code` is a stable identifier from diagnostics.py."""

    def __init__(self, message: str, loc: Optional[Location] = None,
                 code: str = "E0000", end: Optional[Location] = None,
                 hint: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.loc = loc
        self.code = code
        self.end = end
        self.hint = hint

    @property
    def diagnostic(self) -> Diagnostic:
        return Diagnostic(
            code=self.code,
            severity=Severity.ERROR,
            message=self.message,
            loc=self.loc,
            end=self.end,
            hint=self.hint,
        )


@dataclass(frozen=True)
class Token:
    kind: str
    value: object
    loc: Location


@dataclass
class Symbol:
    name: str
    typ: str
    cname: str


@dataclass
class Expr:
    loc: Location
    typ: Optional[str] = field(default=None, init=False)


@dataclass
class Number(Expr):
    value: int


@dataclass
class String(Expr):
    value: str


@dataclass
class Boolean(Expr):
    value: bool


@dataclass
class Variable(Expr):
    name: str
    symbol: Optional[Symbol] = field(default=None, init=False)


@dataclass
class Call(Expr):
    name: str
    args: list[Expr]
    target: Optional[Function] = field(default=None, init=False)


@dataclass
class Unary(Expr):
    op: str
    operand: Expr


@dataclass
class Binary(Expr):
    op: str
    left: Expr
    right: Expr


@dataclass
class Stmt:
    loc: Location


@dataclass
class Let(Stmt):
    name: str
    annotation: Optional[str]
    value: Expr
    name_loc: Optional[Location] = None
    symbol: Optional[Symbol] = field(default=None, init=False)


@dataclass
class Assign(Stmt):
    name: str
    value: Expr
    symbol: Optional[Symbol] = field(default=None, init=False)


@dataclass
class ExprStmt(Stmt):
    expr: Expr


@dataclass
class Return(Stmt):
    value: Optional[Expr]


@dataclass
class If(Stmt):
    condition: Expr
    then_body: list[Stmt]
    else_body: Optional[list[Stmt]]


@dataclass
class While(Stmt):
    condition: Expr
    body: list[Stmt]


@dataclass
class Param:
    name: str
    typ: str
    loc: Location
    symbol: Optional[Symbol] = field(default=None, init=False)


@dataclass
class Function:
    name: str
    params: list[Param]
    returns: str
    body: list[Stmt]
    loc: Location
    cname: str = field(default="", init=False)


@dataclass
class Program:
    functions: list[Function]
    warnings: list[Diagnostic] = field(default_factory=list)
