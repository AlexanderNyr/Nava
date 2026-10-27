from __future__ import annotations

from .diagnostics import make, severity_of
from .limits import MAX_NESTING
from .model import (
    ArrayLiteral, Assign, Binary, Boolean, Call, Diagnostic, Expr, ExprStmt,
    Function, If, Index, IndexAssign, Let, Number, Program, Return, String,
    Symbol, Unary, Variable, While,
)

BUILTINS = {"print", "read_i64", "len", "array_zeros"}
VALUE_TYPES = {"i64", "bool", "str"}


def is_array(typ: str | None) -> bool:
    """`[i64]`, `[bool]` and `[str]` are the only array types."""
    return bool(typ) and typ.startswith("[") and typ.endswith("]")


def element_of(typ: str) -> str:
    return typ[1:-1]


#: Which expressions create a new owned array. Anything else with an array type
#: would be a copy or a borrow, and neither is allowed as an initializer.
def owns_new_array(value: Expr) -> bool:
    if isinstance(value, ArrayLiteral):
        return True
    return isinstance(value, Call) and value.name == "array_zeros"


class Checker:
    def __init__(self, program: Program):
        self.program = program
        self.functions: dict[str, Function] = {}
        self.scopes: list[dict[str, Symbol]] = []
        self.next_symbol = 0
        self.current_function: Function | None = None
        self.warnings: list[Diagnostic] = []
        self.depth = 0

    def check(self) -> Program:
        for index, function in enumerate(self.program.functions):
            if function.name in BUILTINS:
                raise make("E0301", f"{function.name!r} is a reserved builtin", function.loc,
                           function.loc.advance(function.name),
                           f"{function.name}() is built in and cannot be redefined")
            if function.name in self.functions:
                raise make("E0302", f"duplicate function {function.name!r}", function.loc,
                           function.loc.advance(function.name))
            function.cname = f"nv_fn_{index}"
            self.functions[function.name] = function
        main = self.functions.get("main")
        if main is None:
            raise make("E0303", "program must define fn main()")
        if main.params or main.returns not in ("void", "i64"):
            raise make("E0304", "main must have no parameters and return void or i64", main.loc,
                       main.loc.advance("fn " + main.name))

        for function in self.program.functions:
            self.current_function = function
            self.scopes = [{}]
            if is_array(function.returns):
                raise make(
                    "E0330",
                    f"function {function.name!r} cannot return an array",
                    function.loc,
                    function.loc.advance("fn " + function.name),
                    "an array has exactly one owner; write into a mut [T] parameter instead",
                )
            for param in function.params:
                if param.typ not in VALUE_TYPES and not is_array(param.typ):
                    raise make("E0305", "function parameters cannot have type void", param.loc,
                               param.loc.advance(param.name))
                if param.mutable and not is_array(param.typ):
                    raise make(
                        "E0341",
                        f"'mut' is only allowed on an array parameter, not on {param.typ}",
                        param.loc, param.loc.advance("mut " + param.name),
                        "scalar parameters are already mutable locals inside the function",
                    )
                param.symbol = self.declare(param.name, param.typ, param.loc)
                if is_array(param.typ):
                    # A parameter is a borrow: it owns no storage and its
                    # lifetime is the call, which no Nava program can extend.
                    param.symbol.owned = False
                    param.symbol.mutable = bool(param.mutable)
            self.check_statements(function.body, new_scope=False)
            if function.returns != "void" and not self.always_returns(function.body):
                raise make(
                    "E0306",
                    f"function {function.name!r} must return {function.returns} on every path",
                    function.loc,
                    function.loc.advance("fn " + function.name),
                    "the analysis is conservative: even `while true` does not count as a return",
                )
        self.program.warnings = self.warnings
        return self.program

    def declare(self, name, typ, loc) -> Symbol:
        if name in BUILTINS:
            raise make("E0301", f"{name!r} is a reserved builtin", loc, loc.advance(name))
        if name in self.scopes[-1]:
            raise make("E0307", f"duplicate variable {name!r} in the same scope", loc,
                       loc.advance(name),
                       "shadowing is allowed only in a nested block, not in the same block")
        symbol = Symbol(name, typ, f"nv_v{self.next_symbol}")
        self.next_symbol += 1
        self.scopes[-1][name] = symbol
        return symbol

    def lookup(self, name, loc) -> Symbol:
        for scope in reversed(self.scopes):
            if name in scope:
                return scope[name]
        raise make("E0308", f"unknown variable {name!r}", loc, loc.advance(name),
                   "declare it with `let` in this or an enclosing block before use")

    @staticmethod
    def always_returns(statements) -> bool:
        for statement in statements:
            if isinstance(statement, Return):
                return True
            if (isinstance(statement, If) and statement.else_body is not None
                    and Checker.always_returns(statement.then_body)
                    and Checker.always_returns(statement.else_body)):
                return True
        return False

    def enter(self, loc) -> None:
        """Bound the recursion the checker performs over one expression tree."""
        self.depth += 1
        if self.depth > MAX_NESTING:
            raise make(
                "E0401",
                f"expression nesting is deeper than the supported limit of {MAX_NESTING}",
                loc,
                hint="name intermediate results with `let` or split the work into functions",
            )

    def leave(self) -> None:
        self.depth -= 1

    def warn(self, loc, code: str, message: str) -> None:
        self.warnings.append(Diagnostic(code=code, severity=severity_of(code),
                                        message=message, loc=loc))

    def check_statements(self, statements, *, new_scope: bool) -> None:
        if new_scope:
            self.scopes.append({})
        terminated = False
        for statement in statements:
            if terminated:
                self.warn(statement.loc, "W0001",
                          "unreachable statement: the preceding statement always returns")
            self.check_statement(statement)
            if not terminated and self.guarantees_return(statement):
                terminated = True
        if new_scope:
            self.scopes.pop()

    @staticmethod
    def guarantees_return(statement) -> bool:
        """True when this single statement always returns."""
        if isinstance(statement, Return):
            return True
        return (isinstance(statement, If) and statement.else_body is not None
                and Checker.always_returns(statement.then_body)
                and Checker.always_returns(statement.else_body))

    def check_statement(self, statement) -> None:
        if isinstance(statement, Let):
            name_loc = statement.name_loc or statement.loc
            expected = statement.annotation if is_array(statement.annotation) else None
            typ = self.expression(statement.value, expected=expected)
            if typ == "void":
                raise make("E0309", "a variable cannot contain a void value", name_loc,
                           name_loc.advance(statement.name))
            if statement.annotation is not None:
                if statement.annotation == "void":
                    raise make("E0310", "a variable cannot have type void", name_loc,
                               name_loc.advance(statement.name))
                if statement.annotation != typ:
                    raise make("E0311", f"expected {statement.annotation}, got {typ}",
                               name_loc, name_loc.advance(statement.name),
                               "there are no implicit conversions between i64, bool and str")
            if is_array(typ) and not owns_new_array(statement.value):
                raise make(
                    "E0331",
                    f"arrays cannot be copied; {statement.name!r} would share storage with another array",
                    name_loc, name_loc.advance(statement.name),
                    "create a new array with a literal or array_zeros(n), or pass the existing "
                    "one to a parameter",
                )
            statement.symbol = self.declare(statement.name, typ, name_loc)
        elif isinstance(statement, IndexAssign):
            statement.symbol = self.lookup(statement.name, statement.loc)
            if not is_array(statement.symbol.typ):
                raise make(
                    "E0329",
                    f"only an array can be indexed, but {statement.name!r} is {statement.symbol.typ}",
                    statement.loc, statement.loc.advance(statement.name),
                )
            if not statement.symbol.mutable:
                raise make(
                    "E0336",
                    f"cannot assign to an element of the read-only parameter {statement.name!r}",
                    statement.loc, statement.loc.advance(statement.name),
                    "declare the parameter as `mut [T]` to allow writes",
                )
            index_type = self.expression(statement.index)
            if index_type != "i64":
                raise make("E0328", f"an array index must be i64, got {index_type}",
                           statement.index.loc,
                           hint="integers are not converted; use i64 arithmetic")
            element = element_of(statement.symbol.typ)
            value_type = self.expression(statement.value)
            if value_type != element:
                raise make("E0312",
                           f"cannot assign {value_type} to an element of "
                           f"{statement.symbol.typ} (element type {element})",
                           statement.value.loc)
        elif isinstance(statement, Assign):
            statement.symbol = self.lookup(statement.name, statement.loc)
            if is_array(statement.symbol.typ):
                raise make(
                    "E0332",
                    f"an array variable cannot be reassigned ({statement.name!r} is "
                    f"{statement.symbol.typ})",
                    statement.loc, statement.loc.advance(statement.name),
                    "an array owns its storage for the life of its block; "
                    "write elements with a[i] = value",
                )
            typ = self.expression(statement.value)
            if typ != statement.symbol.typ:
                raise make("E0312", f"cannot assign {typ} to {statement.symbol.typ}",
                           statement.loc, statement.loc.advance(statement.name),
                           "there are no implicit conversions between i64, bool and str")
        elif isinstance(statement, ExprStmt):
            if not isinstance(statement.expr, Call):
                raise make("E0313", "only function calls can be used as statements",
                           statement.loc,
                           hint="remove the expression or use its value, as in `print(x)`")
            self.expression(statement.expr)
        elif isinstance(statement, Return):
            assert self.current_function is not None
            expected = self.current_function.returns
            actual = "void" if statement.value is None else self.expression(statement.value)
            if statement.value is not None and actual == "void":
                raise make("E0314", "a void call cannot be returned as a value", statement.loc)
            if is_array(actual):
                raise make(
                    "E0330",
                    "a function cannot return an array",
                    statement.loc,
                    hint="write into a mut [T] parameter, or return an element",
                )
            if actual != expected:
                raise make("E0315", f"return expects {expected}, got {actual}", statement.loc)
        elif isinstance(statement, If):
            typ = self.expression(statement.condition)
            if typ != "bool":
                raise make("E0316", f"if condition must be bool, got {typ}",
                           statement.condition.loc,
                           hint="integers are not converted to bool; compare explicitly, as in x != 0")
            self.check_statements(statement.then_body, new_scope=True)
            if statement.else_body is not None:
                self.check_statements(statement.else_body, new_scope=True)
        elif isinstance(statement, While):
            typ = self.expression(statement.condition)
            if typ != "bool":
                raise make("E0317", f"while condition must be bool, got {typ}",
                           statement.condition.loc,
                           hint="integers are not converted to bool; compare explicitly, as in x > 0")
            self.check_statements(statement.body, new_scope=True)
        else:
            raise AssertionError(f"unknown statement: {type(statement)}")

    def expression(self, expr: Expr, expected: str | None = None) -> str:
        self.enter(expr.loc)
        try:
            return self.expression_inner(expr, expected)
        finally:
            self.leave()

    def expression_inner(self, expr: Expr, expected: str | None = None) -> str:
        if isinstance(expr, Number):
            typ = "i64"
        elif isinstance(expr, String):
            typ = "str"
        elif isinstance(expr, Boolean):
            typ = "bool"
        elif isinstance(expr, Variable):
            expr.symbol = self.lookup(expr.name, expr.loc)
            typ = expr.symbol.typ
        elif isinstance(expr, ArrayLiteral):
            typ = self.array_literal(expr, expected)
        elif isinstance(expr, Index):
            base = self.expression(expr.array)
            if not is_array(base):
                raise make("E0329", f"only an array can be indexed, but {expr.array.name!r} "
                                    f"is {base}", expr.loc, expr.loc.advance(expr.array.name))
            index_type = self.expression(expr.index)
            if index_type != "i64":
                raise make("E0328", f"an array index must be i64, got {index_type}",
                           expr.index.loc,
                           hint="integers are not converted; use i64 arithmetic")
            typ = element_of(base)
        elif isinstance(expr, Call):
            typ = self.call(expr)
        elif isinstance(expr, Unary):
            actual = self.expression(expr.operand)
            expected = "i64" if expr.op == "-" else "bool"
            if actual != expected:
                raise make("E0318", f"operator {expr.op!r} expects {expected}, got {actual}",
                           expr.loc)
            typ = expected
        elif isinstance(expr, Binary):
            left = self.expression(expr.left)
            right = self.expression(expr.right)
            if expr.op in {"+", "-", "*", "/", "%"}:
                if left != "i64" or right != "i64":
                    raise make("E0319", f"operator {expr.op!r} expects two i64 values", expr.loc)
                typ = "i64"
            elif expr.op in {"<", "<=", ">", ">="}:
                if left != "i64" or right != "i64":
                    raise make("E0319", f"operator {expr.op!r} expects two i64 values", expr.loc)
                typ = "bool"
            elif expr.op in {"==", "!="}:
                if left != right or left not in VALUE_TYPES:
                    raise make("E0320",
                               f"operator {expr.op!r} requires two values of the same type",
                               expr.loc)
                typ = "bool"
            elif expr.op in {"&&", "||"}:
                if left != "bool" or right != "bool":
                    raise make("E0321", f"operator {expr.op!r} expects two bool values", expr.loc)
                typ = "bool"
            else:
                raise AssertionError(f"unknown binary operator: {expr.op}")
        else:
            raise AssertionError(f"unknown expression: {type(expr)}")
        expr.typ = typ
        return typ

    def array_literal(self, expr: ArrayLiteral, expected: str | None) -> str:
        """A literal creates a new array; all items must share one element type."""
        if not expr.items:
            if expected is None or not is_array(expected):
                raise make(
                    "E0335",
                    "an empty array literal needs a type annotation",
                    expr.loc,
                    hint="write `let a: [i64] = []` so the element type is known",
                )
            return expected
        element = None
        for item in expr.items:
            actual = self.expression(item)
            if actual not in VALUE_TYPES:
                raise make("E0334",
                           f"array literal elements must be i64, bool, or str, got {actual}",
                           item.loc)
            if element is None:
                element = actual
            elif actual != element:
                raise make("E0334",
                           f"array literal elements must all have the same type; "
                           f"expected {element}, got {actual}",
                           item.loc,
                           hint="there are no implicit conversions between i64, bool and str")
        assert element is not None
        result = f"[{element}]"
        if expected is not None and expected != result:
            raise make("E0311", f"expected {expected}, got {result}", expr.loc,
                       hint="annotate the literal or make every element the same type")
        return result

    def call(self, expr: Call) -> str:
        if expr.name == "print":
            if len(expr.args) != 1:
                raise make("E0322", "print expects exactly one argument", expr.loc)
            if self.expression(expr.args[0]) not in VALUE_TYPES:
                raise make("E0323", "print expects an i64, bool, or str value", expr.loc)
            return "void"
        if expr.name == "read_i64":
            if expr.args:
                raise make("E0324", "read_i64 expects no arguments", expr.loc)
            return "i64"
        if expr.name == "len":
            if len(expr.args) != 1:
                raise make("E0337", "len expects exactly one argument", expr.loc)
            actual = self.expression(expr.args[0])
            if not is_array(actual):
                raise make("E0338", f"len expects an array, got {actual}", expr.loc)
            return "i64"
        if expr.name == "array_zeros":
            if len(expr.args) != 1:
                raise make("E0339", "array_zeros expects exactly one argument", expr.loc)
            actual = self.expression(expr.args[0])
            if actual != "i64":
                raise make("E0340", f"array_zeros expects an i64 length, got {actual}",
                           expr.args[0].loc)
            return "[i64]"
        function = self.functions.get(expr.name)
        if function is None:
            raise make("E0325", f"unknown function {expr.name!r}", expr.loc,
                       expr.loc.advance(expr.name),
                       "define it with `fn`; functions may be called before their definition")
        if len(expr.args) != len(function.params):
            plural = "" if len(function.params) == 1 else "s"
            raise make(
                "E0326",
                f"{expr.name} expects {len(function.params)} argument{plural}, got {len(expr.args)}",
                expr.loc, expr.loc.advance(expr.name),
            )
        borrowed: dict[str, int] = {}
        for index, (arg, param) in enumerate(zip(expr.args, function.params), start=1):
            # An empty literal cannot be typed on its own; the parameter type is
            # the context. A non-empty literal still has to match on its own, so
            # a mismatch is reported as an argument error (E0327), not E0311.
            expected = param.typ if isinstance(arg, ArrayLiteral) and not arg.items else None
            actual = self.expression(arg, expected=expected)
            if actual != param.typ:
                raise make("E0327",
                           f"argument {index} of {expr.name} expects {param.typ}, got {actual}",
                           arg.loc)
            if not is_array(param.typ) or not param.mutable:
                continue
            # A mutable borrow must be exclusive and must come from an owner:
            # a borrow of a borrow could alias another live mutable view.
            if not isinstance(arg, Variable) or not arg.symbol.owned:
                raise make(
                    "E0333",
                    f"argument {index} of {expr.name} is `mut {param.typ}`, so it must be an "
                    f"owned array variable, not a borrow",
                    arg.loc,
                    hint="element writes are only allowed on an array created in this function",
                )
            if arg.name in borrowed:
                raise make(
                    "E0342",
                    f"{arg.name!r} is passed to two mutable parameters of {expr.name} "
                    f"(arguments {borrowed[arg.name]} and {index})",
                    arg.loc,
                    hint="a mutable borrow must be exclusive: copy into a second array first",
                )
            borrowed[arg.name] = index
        expr.target = function
        return function.returns


def check(program: Program) -> Program:
    return Checker(program).check()
