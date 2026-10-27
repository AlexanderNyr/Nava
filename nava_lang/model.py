from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class Location:
    line: int
    column: int


class NavaError(Exception):
    def __init__(self, message: str, loc: Optional[Location] = None):
        super().__init__(message)
        self.message = message
        self.loc = loc


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
