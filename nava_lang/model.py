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
    #: True for a slice: a view into an array that somebody else owns. It is
    #: never freed, never written through, and never handed on as a `mut` borrow.
    borrowed: bool = False
    #: True once an owning value has been moved out of this binding: the buffer
    #: now belongs to somebody else, so the local is dead for the rest of its
    #: block and every later use is `E0445`.
    moved: bool = False


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
class Slice(Expr):
    """`a[i..j]`: a read-only view of part of an array (edition 2).

    The base is a variable by construction, not by a rule: the parser accepts
    `[` after a name only, so a slice of a temporary cannot be written down and
    cannot outlive the storage it points at.
    """

    array: "Variable"
    start: Expr
    end: Expr


@record()
class Variable(Expr):
    name: str
    symbol: Symbol | None = field(default=None, init=False)
    #: The declared function behind the name when it is used as a value in
    #: edition 2. A variable of the same name wins; see `Checker.find_symbol`.
    function: Function | None = field(default=None, init=False)


@record()
class Call(Expr):
    name: str
    args: list[Expr]
    #: The module named before `::` in `math::abs(x)`, or None for a call that
    #: is resolved in the calling module.
    module: str | None = None
    #: The written type arguments of a generic function, `identity(i64)`, or None.
    type_args: list[str] | None = None
    target: Function | None = field(default=None, init=False)
    #: The variable the call goes through, for `f(3)` where `f` is a value of a
    #: function type (edition 2). None for a call of a declared function.
    callee: "Symbol | None" = field(default=None, init=False)


@record()
class Range(Expr):
    """`a..b`: the iterable of a `for` loop, half-open, bounds evaluated once."""

    start: Expr
    end: Expr


FUNCTION_TYPE = "fn("
VECTOR_TYPE = "Vec("
#: `HashMap(K, V)` and `HashSet(T)`: builtin containers with one owner, like a
#: vector. The key type decides the hash, and the compiler emits one instance per
#: pair of types the program writes out.
MAP_TYPE = "HashMap("
SET_TYPE = "HashSet("
#: The growing text builder. `str` is immutable and lives for the run; `String`
#: is the owner of bytes that a program assembles a piece at a time.
STRING_TYPE = "String"


def is_vec(typ: object) -> bool:
    """`Vec(i64)` is a growing array that owns one buffer and a capacity."""
    return isinstance(typ, str) and typ.startswith(VECTOR_TYPE) and typ.endswith(")")


def is_string(typ: object) -> bool:
    """`String` is a builtin owning type, like a vector: one owner, no copies."""
    return typ == STRING_TYPE


def vec_element(typ: str) -> str:
    """`Vec(Point)` -> `Point`."""
    return typ[len(VECTOR_TYPE):-1]


def is_map(typ: object) -> bool:
    """`HashMap(i64, String)` is a table of values, keyed and owned."""
    return isinstance(typ, str) and typ.startswith(MAP_TYPE) and typ.endswith(")")


def is_set(typ: object) -> bool:
    """`HashSet(str)` is a table of keys with no values."""
    return isinstance(typ, str) and typ.startswith(SET_TYPE) and typ.endswith(")")


def map_parts(typ: str) -> tuple[str, str]:
    """`HashMap(i64, String)` -> (`i64`, `String`), split at the top-level comma."""
    inner = typ[len(MAP_TYPE):-1]
    depth = 0
    for index, character in enumerate(inner):
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
        elif character == "," and depth == 0:
            return inner[:index].strip(), inner[index + 1:].strip()
    raise ValueError(f"not a map type: {typ!r}")


def set_element(typ: str) -> str:
    """`HashSet(str)` -> `str`."""
    return typ[len(SET_TYPE):-1]


def is_owning_container(typ: object) -> bool:
    """Does this type own storage that a binding has to hand on by moving?

    A `Vec(T)`, a `String`, a `HashMap(K, V)` and a `HashSet(T)` each own one
    buffer, so a binding of one of them is its owner: it moves, and it is
    released, exactly like an array.
    """
    return is_vec(typ) or is_string(typ) or is_map(typ) or is_set(typ)


def is_function_type(typ: object) -> bool:
    """`fn(i64) -> i64` is a value: the address of a function with that signature."""
    return isinstance(typ, str) and typ.startswith(FUNCTION_TYPE)


def function_type_parts(typ: str) -> tuple[list[str], str] | None:
    """`fn(i64, [bool]) -> i64` -> `(["i64", "[bool]"], "i64")`.

    The spelling is canonical — the return type is always written — so parsing it
    back is unambiguous: the parameter list ends at the parenthesis that closes
    the one after `fn`, at nesting depth zero.
    """
    if not is_function_type(typ):
        return None
    depth = 0
    params: list[str] = []
    current = ""
    index = len(FUNCTION_TYPE)
    while index < len(typ):
        character = typ[index]
        if character == "(":
            depth += 1
        elif character == ")":
            if depth == 0:
                break
            depth -= 1
        elif character == "," and depth == 0:
            params.append(current.strip())
            current = ""
            index += 1
            continue
        current += character
        index += 1
    tail = typ[index:]
    if not tail.startswith(") -> "):
        return None
    if current.strip():
        params.append(current.strip())
    return params, tail[len(") -> "):]


def function_signature(function: "Function") -> str:
    """The function type of a declaration: `fn(i64, bool) -> i64`."""
    params = ", ".join(param.typ for param in function.params)
    return f"fn({params}) -> {function.returns}"


@record()
class StructLiteral(Expr):
    """`Point(x: 1, y: 2)`: every field named, exactly once.

    The fields are named rather than positional, and the syntax is a call rather
    than `Point { ... }`, because a brace after an expression would be ambiguous
    with the block that follows a condition or a range: `for i in 0..n { }` and
    `if p { }` must keep parsing as statements. Named construction cannot be
    confused with either.
    """

    name: str
    fields: list[tuple[str, Expr]]
    #: The written type of a generic construction, `Pair(i64)`; None otherwise.
    instance: str | None = None
    #: The declared struct, filled in by the checker.
    struct: "Struct | None" = field(default=None, init=False)


@record()
class VariantValue(Expr):
    """`Shape::Circle(3)`: a value of an enum type (edition 2).

    The class name avoids the word `Enum`, which is a Python builtin, and the
    spelling keeps the `::` that modules already use: a variant is always
    qualified by its enum, so a model writing code never has to guess which
    variant names are in scope.
    """

    enum: str
    variant: str
    args: list[Expr]
    enum_loc: Location | None = None
    variant_loc: Location | None = None
    #: The written type when the enum takes type arguments, e.g. `Result(i64, str)`.
    #: None for a plain enum, whose type is just `enum`.
    instance: str | None = None
    #: Declared enum and variant, filled in by the checker.
    declared: "EnumDecl | None" = field(default=None, init=False)
    choice: "Variant | None" = field(default=None, init=False)


@record()
class Try(Expr):
    """`f()?`: propagate the failure variant of an `Option` or a `Result`.

    Only a `let` initializer may apply it, and only inside a function whose
    return type is the same enum with the same failure type. Making it a
    statement-level rule keeps the generated code a straight line: a test, an
    early return, and the success value bound to the new local.
    """

    inner: Expr


@record()
class Member(Expr):
    """`p.x`: a read of one field."""

    base: Expr
    field: str
    field_loc: Location | None = None
    #: The declared field, filled in by the checker.
    owner: "Field | None" = field(default=None, init=False)


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
class Defer(Stmt):
    """`defer { ... }`: a block that runs when the enclosing block is left.

    It runs on the normal path, on `return`, and on a `break`/`continue` that
    exits the block, and it runs before the block releases its arrays, so a
    deferred block can still use them.
    """

    body: list[Stmt]


@record()
class Match(Stmt):
    """`match value { Enum::Variant(a) => { ... } _ => { ... } }` (edition 2)."""

    value: Expr
    arms: list["Arm"]


@record()
class Arm:
    """One arm of a `match`: a pattern and a block.

    `variant` is None for the wildcard arm (`_`), which binds nothing.
    """

    loc: Location
    variant: str | None
    bindings: list[str]
    body: list[Stmt]
    #: The instantiated enum the pattern names, e.g. `Result(i64, str)`.
    instance: str | None = None
    #: The declared variant and its binding symbols, filled in by the checker.
    choice: "Variant | None" = field(default=None, init=False)
    symbols: list = field(default_factory=list, init=False)


@record()
class Variant:
    """One variant of an enum: a name and the types it carries."""

    name: str
    types: list[str]
    loc: Location | None = None
    #: Position in the declaration; this is the tag the generated code tests.
    tag: int = 0


@record()
class EnumDecl:
    """`enum Shape { Dot, Circle(i64) }` — edition 2.

    A prelude instantiation such as `Result(i64, str)` is also an `EnumDecl`,
    built by the checker on demand: `loc` is None for those.
    """

    name: str
    variants: list[Variant]
    loc: Location | None = None
    module: str = ""
    cname: str = field(default="", init=False)
    #: Type parameters as written; empty for an ordinary enum and for every
    #: instantiation. A declaration with them is a template, never a type.
    type_params: list[str] = field(default_factory=list)


@record()
class MemberAssign(Stmt):
    """`p.x = value`: write one field of a struct value."""

    target: Member
    value: Expr


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
    #: The name written before the loop (`outer: while ...`), or None.
    label: str | None = None


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
    #: The name written before the loop (`outer: for ...`), or None.
    label: str | None = None
    symbol: Symbol | None = field(default=None, init=False)


@record()
class Break(Stmt):
    #: The loop label this `break` leaves, or None for the innermost loop.
    label: str | None = None


@record()
class Continue(Stmt):
    #: The loop label this `continue` restarts, or None for the innermost loop.
    label: str | None = None


@record()
class Field:
    """One `name: type` line of a struct declaration."""

    name: str
    typ: str
    loc: Location


@record()
class Struct:
    """`struct Point { x: i64, y: i64 }` — edition 2.

    A struct with type parameters (`struct Pair<T> { ... }`) is a *template*: it
    is never a type on its own, and the checker builds one concrete declaration
    per instantiation.
    """

    name: str
    fields: list[Field]
    loc: Location
    #: The module that declares it; "" is the entry file.
    module: str = ""
    cname: str = field(default="", init=False)
    #: Type parameters as written; empty for an ordinary struct.
    type_params: list[str] = field(default_factory=list)


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
    #: True when the declaration says `pub` (edition 2): only such a function is
    #: callable from another module.
    public: bool = False
    #: Type parameters as written; empty for an ordinary function. A declaration
    #: with them is a template, and every call instantiates it.
    type_params: list[str] = field(default_factory=list)

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
    #: Every struct declaration in the translation unit, in source order.
    structs: list["Struct"] = field(default_factory=list)
    #: Every enum declaration in the translation unit, in source order.
    enums: list["EnumDecl"] = field(default_factory=list)
    #: Prelude instantiations the program uses, keyed by their written type
    #: (`Result(i64, str)`); filled in by the checker.
    instances: dict[str, "EnumDecl"] = field(default_factory=dict)
    #: Structs built from a template by the checker, keyed by their written type
    #: (`Pair(i64)`). Like an enum instance, one is an ordinary declaration.
    struct_instances: dict[str, "Struct"] = field(default_factory=dict)
    #: The language edition the entry file declares; 1 when it declares none.
    edition: int = 1
    #: Module name -> the edition its file declares. A module that does not
    #: declare edition 2 exports every function, as it always did.
    module_editions: dict[str, int] = field(default_factory=dict)
