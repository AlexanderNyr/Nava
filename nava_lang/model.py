from __future__ import annotations

from .records import field, record


class Severity:
    """Diagnostic severities. Errors stop compilation; warnings do not."""

    ERROR = "error"
    WARNING = "warning"


@record(frozen=True)
class Location:
    """A position in one source file.

    `path` is the file the position belongs to and is set by the module loader;
    a single-file compilation leaves it `None` and the caller supplies the path.
    """

    line: int
    column: int
    path: str | None = None

    def advance(self, text: str) -> "Location":
        """End location of `text` when it starts at this location."""
        line, column = self.line, self.column
        for character in text:
            if character == "\n":
                line += 1
                column = 1
            else:
                column += 1
        return Location(line, column, self.path)


@record(frozen=True)
class Diagnostic:
    """A message with a stable code, a source range and an optional hint."""

    code: str
    severity: str
    message: str
    loc: Location | None = None
    end: Location | None = None
    hint: str | None = None


class NavaError(Exception):
    """A compilation error. `code` is a stable identifier from diagnostics.py."""

    def __init__(self, message: str, loc: Location | None = None,
                 code: str = "E0000", end: Location | None = None,
                 hint: str | None = None):
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


@record(frozen=True)
class Token:
    kind: str
    value: object
    loc: Location


@record()
class Symbol:
    name: str
    typ: str
    cname: str
    #: False for a read-only array parameter: its elements cannot be assigned.
    mutable: bool = True
    #: False for any parameter: a borrow never owns storage, so it cannot be
    #: handed on as a mutable borrow.
    owned: bool = True
    #: True for a loop variable: it is rebound by the loop, so assigning to it
    #: is rejected instead of silently desynchronising the iteration.
    readonly: bool = False


@record()
class Expr:
    loc: Location
    typ: str | None = field(default=None, init=False)


@record()
class Number(Expr):
    value: int


@record()
class Float(Expr):
    """An `f64` literal. The value is an IEEE-754 binary64 number."""

    value: float


@record()
class String(Expr):
    value: str


@record()
class Boolean(Expr):
    value: bool


@record()
class ArrayLiteral(Expr):
    """`[a, b, c]`: a new owned array. The element type comes from the items."""

    items: list[Expr]


@record()
class Index(Expr):
    """`a[i]`: a checked read of one element. Only a variable can be indexed."""

    array: "Variable"
    index: Expr


@record()
class Variable(Expr):
    name: str
    symbol: Symbol | None = field(default=None, init=False)


@record()
class Call(Expr):
    name: str
    args: list[Expr]
    #: The module named before `::` in `math::abs(x)`, or None for a call that
    #: is resolved in the calling module.
    module: str | None = None
    target: Function | None = field(default=None, init=False)


@record()
class Range(Expr):
    """`a..b`: the iterable of a `for` loop, half-open, bounds evaluated once."""

    start: Expr
    end: Expr


@record()
class Unary(Expr):
    op: str
    operand: Expr


@record()
class Binary(Expr):
    op: str
    left: Expr
    right: Expr


@record()
class Stmt:
    loc: Location


@record()
class Let(Stmt):
    name: str
    annotation: str | None
    value: Expr
    name_loc: Location | None = None
    symbol: Symbol | None = field(default=None, init=False)


@record()
class Assign(Stmt):
    name: str
    value: Expr
    symbol: Symbol | None = field(default=None, init=False)


@record()
class IndexAssign(Stmt):
    """`a[i] = value`: write one element of a mutable array."""

    name: str
    index: Expr
    value: Expr
    name_loc: Location | None = None
    symbol: Symbol | None = field(default=None, init=False)


@record()
class ExprStmt(Stmt):
    expr: Expr


@record()
class Return(Stmt):
    value: Expr | None


@record()
class If(Stmt):
    condition: Expr
    then_body: list[Stmt]
    else_body: list[Stmt] | None


@record()
class While(Stmt):
    condition: Expr
    body: list[Stmt]


@record()
class For(Stmt):
    """`for name in iterable`: a range or an array.

    The loop variable is a fresh read-only local in the body scope. For an
    array it is a copy of the element; for a range it counts from the start.
    """

    name: str
    iterable: Expr
    body: list[Stmt]
    name_loc: Location | None = None
    symbol: Symbol | None = field(default=None, init=False)


@record()
class Break(Stmt):
    pass


@record()
class Continue(Stmt):
    pass


@record()
class Param:
    name: str
    typ: str
    loc: Location
    #: True for `mut [T]`: a mutable borrow, so element writes are allowed.
    mutable: bool = False
    symbol: Symbol | None = field(default=None, init=False)


@record()
class Import:
    """`import name`: the whole content of another file on disk."""

    name: str
    loc: Location
    name_loc: Location
    #: True when the module comes from the packaged standard library.
    standard: bool = False


@record()
class Function:
    name: str
    params: list[Param]
    returns: str
    body: list[Stmt]
    loc: Location
    #: The module this function belongs to; "" is the entry file.
    module: str = ""
    cname: str = field(default="", init=False)

    @property
    def qualified(self) -> str:
        return f"{self.module}::{self.name}" if self.module else self.name


@record()
class Program:
    """The whole translation unit: every function of every loaded module."""

    functions: list[Function]
    warnings: list[Diagnostic] = field(default_factory=list)
    #: Imports declared in the entry file (module ""), in source order.
    imports: list[Import] = field(default_factory=list)
    #: Imports per module name, for every loaded module.
    imports_by_module: dict[str, list[Import]] = field(default_factory=dict)
    #: Module name -> source path, for diagnostics and for `check --format json`.
    sources: dict[str, str] = field(default_factory=dict)
