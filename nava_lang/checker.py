from __future__ import annotations

from .diagnostics import make, severity_of
from .limits import MAX_NESTING
from .model import (
    Assign, Binary, Boolean, Call, Diagnostic, Expr, ExprStmt, Function, If, Let,
    Number, Program, Return, String, Symbol, Unary, Variable, While,
)

BUILTINS = {"print", "read_i64"}
VALUE_TYPES = {"i64", "bool", "str"}


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
            for param in function.params:
                if param.typ not in VALUE_TYPES:
                    raise make("E0305", "function parameters cannot have type void", param.loc,
                               param.loc.advance(param.name))
                param.symbol = self.declare(param.name, param.typ, param.loc)
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
            typ = self.expression(statement.value)
            name_loc = statement.name_loc or statement.loc
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
            statement.symbol = self.declare(statement.name, typ, name_loc)
        elif isinstance(statement, Assign):
            statement.symbol = self.lookup(statement.name, statement.loc)
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

    def expression(self, expr: Expr) -> str:
        self.enter(expr.loc)
        try:
            return self.expression_inner(expr)
        finally:
            self.leave()

    def expression_inner(self, expr: Expr) -> str:
        if isinstance(expr, Number):
            typ = "i64"
        elif isinstance(expr, String):
            typ = "str"
        elif isinstance(expr, Boolean):
            typ = "bool"
        elif isinstance(expr, Variable):
            expr.symbol = self.lookup(expr.name, expr.loc)
            typ = expr.symbol.typ
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
        for index, (arg, param) in enumerate(zip(expr.args, function.params), start=1):
            actual = self.expression(arg)
            if actual != param.typ:
                raise make("E0327",
                           f"argument {index} of {expr.name} expects {param.typ}, got {actual}",
                           arg.loc)
        expr.target = function
        return function.returns


def check(program: Program) -> Program:
    return Checker(program).check()
