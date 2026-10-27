from __future__ import annotations

from .model import (
    Assign, Binary, Boolean, Call, Expr, ExprStmt, Function, If, Let,
    NavaError, Number, Program, Return, String, Symbol, Unary, Variable, While,
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

    def check(self) -> Program:
        for index, function in enumerate(self.program.functions):
            if function.name in BUILTINS:
                raise NavaError(f"{function.name!r} is a reserved builtin", function.loc)
            if function.name in self.functions:
                raise NavaError(f"duplicate function {function.name!r}", function.loc)
            function.cname = f"nv_fn_{index}"
            self.functions[function.name] = function
        main = self.functions.get("main")
        if main is None:
            raise NavaError("program must define fn main()")
        if main.params or main.returns not in ("void", "i64"):
            raise NavaError("main must have no parameters and return void or i64", main.loc)

        for function in self.program.functions:
            self.current_function = function
            self.scopes = [{}]
            for param in function.params:
                if param.typ not in VALUE_TYPES:
                    raise NavaError("function parameters cannot have type void", param.loc)
                param.symbol = self.declare(param.name, param.typ, param.loc)
            self.check_statements(function.body, new_scope=False)
            if function.returns != "void" and not self.always_returns(function.body):
                raise NavaError(
                    f"function {function.name!r} must return {function.returns} on every path",
                    function.loc,
                )
        return self.program

    def declare(self, name, typ, loc) -> Symbol:
        if name in BUILTINS:
            raise NavaError(f"{name!r} is a reserved builtin", loc)
        if name in self.scopes[-1]:
            raise NavaError(f"duplicate variable {name!r} in the same scope", loc)
        symbol = Symbol(name, typ, f"nv_v{self.next_symbol}")
        self.next_symbol += 1
        self.scopes[-1][name] = symbol
        return symbol

    def lookup(self, name, loc) -> Symbol:
        for scope in reversed(self.scopes):
            if name in scope:
                return scope[name]
        raise NavaError(f"unknown variable {name!r}", loc)

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

    def check_statements(self, statements, *, new_scope: bool) -> None:
        if new_scope:
            self.scopes.append({})
        for statement in statements:
            self.check_statement(statement)
        if new_scope:
            self.scopes.pop()

    def check_statement(self, statement) -> None:
        if isinstance(statement, Let):
            typ = self.expression(statement.value)
            if typ == "void":
                raise NavaError("a variable cannot contain a void value", statement.loc)
            if statement.annotation is not None:
                if statement.annotation == "void":
                    raise NavaError("a variable cannot have type void", statement.loc)
                if statement.annotation != typ:
                    raise NavaError(f"expected {statement.annotation}, got {typ}", statement.loc)
            statement.symbol = self.declare(statement.name, typ, statement.loc)
        elif isinstance(statement, Assign):
            statement.symbol = self.lookup(statement.name, statement.loc)
            typ = self.expression(statement.value)
            if typ != statement.symbol.typ:
                raise NavaError(f"cannot assign {typ} to {statement.symbol.typ}", statement.loc)
        elif isinstance(statement, ExprStmt):
            if not isinstance(statement.expr, Call):
                raise NavaError("only function calls can be used as statements", statement.loc)
            self.expression(statement.expr)
        elif isinstance(statement, Return):
            assert self.current_function is not None
            expected = self.current_function.returns
            actual = "void" if statement.value is None else self.expression(statement.value)
            if statement.value is not None and actual == "void":
                raise NavaError("a void call cannot be returned as a value", statement.loc)
            if actual != expected:
                raise NavaError(f"return expects {expected}, got {actual}", statement.loc)
        elif isinstance(statement, If):
            typ = self.expression(statement.condition)
            if typ != "bool":
                raise NavaError(f"if condition must be bool, got {typ}", statement.condition.loc)
            self.check_statements(statement.then_body, new_scope=True)
            if statement.else_body is not None:
                self.check_statements(statement.else_body, new_scope=True)
        elif isinstance(statement, While):
            typ = self.expression(statement.condition)
            if typ != "bool":
                raise NavaError(f"while condition must be bool, got {typ}", statement.condition.loc)
            self.check_statements(statement.body, new_scope=True)
        else:
            raise AssertionError(f"unknown statement: {type(statement)}")

    def expression(self, expr: Expr) -> str:
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
                raise NavaError(f"operator {expr.op!r} expects {expected}, got {actual}", expr.loc)
            typ = expected
        elif isinstance(expr, Binary):
            left = self.expression(expr.left)
            right = self.expression(expr.right)
            if expr.op in {"+", "-", "*", "/", "%"}:
                if left != "i64" or right != "i64":
                    raise NavaError(f"operator {expr.op!r} expects two i64 values", expr.loc)
                typ = "i64"
            elif expr.op in {"<", "<=", ">", ">="}:
                if left != "i64" or right != "i64":
                    raise NavaError(f"operator {expr.op!r} expects two i64 values", expr.loc)
                typ = "bool"
            elif expr.op in {"==", "!="}:
                if left != right or left not in VALUE_TYPES:
                    raise NavaError(f"operator {expr.op!r} requires two values of the same type", expr.loc)
                typ = "bool"
            elif expr.op in {"&&", "||"}:
                if left != "bool" or right != "bool":
                    raise NavaError(f"operator {expr.op!r} expects two bool values", expr.loc)
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
                raise NavaError("print expects exactly one argument", expr.loc)
            if self.expression(expr.args[0]) not in VALUE_TYPES:
                raise NavaError("print expects an i64, bool, or str value", expr.loc)
            return "void"
        if expr.name == "read_i64":
            if expr.args:
                raise NavaError("read_i64 expects no arguments", expr.loc)
            return "i64"
        function = self.functions.get(expr.name)
        if function is None:
            raise NavaError(f"unknown function {expr.name!r}", expr.loc)
        if len(expr.args) != len(function.params):
            raise NavaError(
                f"{expr.name} expects {len(function.params)} arguments, got {len(expr.args)}", expr.loc
            )
        for index, (arg, param) in enumerate(zip(expr.args, function.params), start=1):
            actual = self.expression(arg)
            if actual != param.typ:
                raise NavaError(f"argument {index} of {expr.name} expects {param.typ}, got {actual}", arg.loc)
        expr.target = function
        return function.returns


def check(program: Program) -> Program:
    return Checker(program).check()
