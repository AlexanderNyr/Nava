"""Checked AST → IR (PLAN.md N14a).

This is the lowering the native backend consumes, and it is deliberately narrow:
`i64`, `bool`, string literals, arithmetic, comparisons, `if`, `while`,
`for … in a..b`, `break`/`continue`, function calls and `print`. Everything else —
`f64`, arrays, slices, `Vec`, `String`, `HashMap`, `HashSet`, structs, enums,
`match`, `defer`, `?`, module functions, the clock, the filesystem — is refused with
`E0901` **naming the construct**, because the one thing a second backend must not do
is compile a program it does not understand into something that runs.

Two properties matter more than the size of the subset:

* **Evaluation order is the language's.** Operands are lowered left to right, `&&`
  and `||` short-circuit through real blocks, and a call in an operand happens
  before the operand to its right — the same order the C backend produces, because
  the differential compares the two.
* **Mutable locals are SSA.** A local lives in an environment of values; a write
  rebinds it to a fresh value; a join (`if`, loop header, loop exit) passes the live
  values as **block parameters**, which is SSA without phi instructions. A loop's
  back edge — and a `continue`, which is an early back edge — passes the values it
  reached, so the loop needs no special case beyond the parameters.

The lowering is total over what it accepts: there is no "best effort" path and no
silently wrong answer.
"""
from __future__ import annotations

from ..diagnostics import make
from ..model import (
    Assign, Binary, Boolean, Break, Call, Continue, ExprStmt, For, Function, If, Let,
    Number, Program, Range, Return, String, Unary, Variable, While,
)
from . import ir

#: Types `print` can write in the native backend.
PRINTABLE = ("i64", "bool", "str")


class Refused(Exception):
    """Internal: a construct the native backend does not implement, with its name."""

    def __init__(self, what: str, loc=None):
        super().__init__(what)
        self.what = what
        self.loc = loc


def describe(node) -> str:
    """The name of a construct, for a refusal that names what it refused."""
    names = {
        "Float": "a floating-point literal",
        "ArrayLiteral": "an array",
        "Index": "an array index",
        "Slice": "a slice",
        "Match": "a match",
        "Defer": "a defer",
        "Try": "the `?` operator",
        "StructLiteral": "a struct",
        "VariantValue": "an enum variant",
        "Member": "a field access",
        "MemberAssign": "a field write",
        "IndexAssign": "an array write",
        "Range": "a range outside a 'for'",
    }
    name = type(node).__name__
    return names.get(name, f"the construct {name}")


def assigned_names(body) -> set[str]:
    """Every local a statement list assigns, at any depth inside it."""
    names: set[str] = set()
    for statement in body or []:
        if isinstance(statement, Assign):
            names.add(statement.name)
        elif isinstance(statement, Let):
            continue
        elif isinstance(statement, If):
            names |= assigned_names(statement.then_body)
            names |= assigned_names(statement.else_body or [])
        elif isinstance(statement, While):
            names |= assigned_names(statement.body)
        elif isinstance(statement, For):
            names |= assigned_names(statement.body)
            names.add(statement.name)
        elif isinstance(statement, (Break, Continue, Return, ExprStmt)):
            continue
        else:
            raise Refused(describe(statement), getattr(statement, "loc", None))
    return names


class Loop:
    """Where a `break` and a `continue` go, and what a loop carries through them."""

    def __init__(self, exit_label: str, names: list[str], label: str | None = None,
                 back_edge=None):
        self.exit_label = exit_label
        self.names = names
        self.label = label
        #: Called at a `continue` with (block, env) to jump to the loop's next entry.
        self.back_edge = back_edge


class Builder:
    """One function's lowering: its blocks, its values, and the environment."""

    def __init__(self, function: Function):
        self.function = function
        self.blocks: list[ir.Block] = []
        self.params: list[tuple[str, str]] = []
        self.next_value = 0
        self.next_block = 0
        self.loops: list[Loop] = []
        self.labels: dict[str, Loop] = {}
        #: One source line per emitted instruction, in the order the backend emits
        #: them: blocks in order, and the instructions of a block in order.
        self.lines: list[int] = []
        #: Where the function is declared, for its prologue.
        self.decl_line = function.loc.line if getattr(function, "loc", None) else 0
        #: The source position being lowered; every instruction emitted until the
        #: next node is charged to it.
        self.loc = function.loc

    def emit(self, block: ir.Block, instruction) -> None:
        """Append one instruction and the line it came from, in one call.

        Recorded together so the two cannot drift: a line table filled in somewhere
        else is a line table that will one day be filled in wrongly.
        """
        block.instructions.append(instruction)
        self.lines.append(self.loc.line if self.loc is not None else self.decl_line)

    # -- names ---------------------------------------------------------------
    def value(self) -> str:
        name = str(self.next_value)
        self.next_value += 1
        return name

    def block(self, hint: str, params: list[tuple[str, str]] | None = None) -> ir.Block:
        block = ir.Block(f"{hint}{self.next_block}", params or [])
        self.next_block += 1
        self.blocks.append(block)
        return block

    # -- expressions ---------------------------------------------------------
    def expression(self, node, block: ir.Block, env: dict) -> tuple[str, str, ir.Block]:
        """Lower one expression; answer (value, type, the block it ended in)."""
        if getattr(node, "loc", None) is not None:
            self.loc = node.loc
        if isinstance(node, Number):
            value = self.value()
            self.emit(block, ir.Const(value, "i64", int(node.value)))
            return value, "i64", block
        if isinstance(node, Boolean):
            value = self.value()
            self.emit(block, ir.Const(value, "bool", bool(node.value)))
            return value, "bool", block
        if isinstance(node, String):
            value = self.value()
            self.emit(block, ir.Str(value, node.value))
            return value, "str", block
        if isinstance(node, Variable):
            if node.name not in env:
                raise Refused(f"the variable '{node.name}'", node.loc)
            value, typ = env[node.name]
            return value, typ, block
        if isinstance(node, Unary):
            operand, typ, block = self.expression(node.operand, block, env)
            if node.op == "-":
                if typ != "i64":
                    raise Refused(f"unary '-' on a {typ}", node.loc)
                value = self.value()
                self.emit(block, ir.Neg(value, operand))
                return value, "i64", block
            if node.op == "!":
                if typ != "bool":
                    raise Refused(f"'!' on a {typ}", node.loc)
                value = self.value()
                self.emit(block, ir.Not(value, operand))
                return value, "bool", block
            raise Refused(f"the unary operator '{node.op}'", node.loc)
        if isinstance(node, Binary):
            return self.binary(node, block, env)
        if isinstance(node, Call):
            return self.call(node, block, env)
        raise Refused(describe(node), getattr(node, "loc", None))

    def binary(self, node: Binary, block: ir.Block, env: dict):
        if node.op in ("&&", "||"):
            return self.short_circuit(node, block, env)
        left, left_type, block = self.expression(node.left, block, env)
        right, right_type, block = self.expression(node.right, block, env)
        if left_type != right_type:
            raise Refused(f"a '{node.op}' between a {left_type} and a {right_type}",
                          node.loc)
        if node.op in ("+", "-", "*", "/", "%"):
            if left_type != "i64":
                raise Refused(f"'{node.op}' on {left_type}s", node.loc)
            value = self.value()
            opcode = {"+": "add", "-": "sub", "*": "mul", "/": "div", "%": "mod"}[node.op]
            self.emit(block, ir.Bin(value, opcode, left, right))
            return value, "i64", block
        if node.op in ("==", "!=", "<", "<=", ">", ">="):
            if left_type not in ("i64", "bool"):
                raise Refused(f"'{node.op}' on {left_type}s", node.loc)
            value = self.value()
            opcode = {"==": "eq", "!=": "ne", "<": "lt", "<=": "le",
                      ">": "gt", ">=": "ge"}[node.op]
            self.emit(block, ir.Cmp(value, opcode, left, right))
            return value, "bool", block
        raise Refused(f"the operator '{node.op}'", node.loc)

    def short_circuit(self, node: Binary, block: ir.Block, env: dict):
        """`a && b` and `a || b`: a branch, because the language says so."""
        left, left_type, block = self.expression(node.left, block, env)
        if left_type != "bool":
            raise Refused(f"'{node.op}' on a {left_type}", node.loc)
        right_block = self.block("sc_right")
        join = self.block("sc_join", [(self.value(), "bool")])
        result = join.params[0][0]
        if node.op == "&&":
            short = self.bool_constant(block, False)
            self.emit(block,
                ir.Branch(left, right_block.label, [], join.label, [short]))
        else:
            short = self.bool_constant(block, True)
            self.emit(block,
                ir.Branch(left, join.label, [short], right_block.label, []))
        right, right_type, right_block = self.expression(node.right, right_block, env)
        if right_type != "bool":
            raise Refused(f"'{node.op}' on a {right_type}", node.loc)
        self.emit(right_block, ir.Jump(join.label, [right]))
        return result, "bool", join

    def bool_constant(self, block: ir.Block, payload: bool) -> str:
        value = self.value()
        self.emit(block, ir.Const(value, "bool", payload))
        return value

    def call(self, node: Call, block: ir.Block, env: dict):
        """A call to a declared function, wherever it is declared.

        The checker has already resolved `node.target`, so the native backend does not
        repeat the name lookup — and a call it cannot make is refused here, with the
        function named, rather than resolved to something else.
        """
        if node.callee is not None:
            raise Refused("a call through a function value", node.loc)
        if node.target is None:
            if node.name == "print":
                if len(node.args) != 1:
                    raise Refused("print with more than one argument", node.loc)
                operand, typ, block = self.expression(node.args[0], block, env)
                if typ not in PRINTABLE:
                    raise Refused(f"printing a {typ}", node.loc)
                self.emit(block, ir.Print(operand, typ))
                return "", "void", block
            if node.module:
                raise Refused(f"a call into the module '{node.module}'", node.loc)
            raise Refused(f"a call to '{node.name}'", node.loc)
        target = node.target
        if target.type_params or node.type_args is not None:
            raise Refused(f"the generic function '{target.qualified}'", node.loc)
        if target.returns not in ("i64", "bool", "str", "void"):
            raise Refused(
                f"a call to '{target.qualified}', which returns {target.returns}",
                node.loc)
        arguments: list[str] = []
        for argument in node.args:
            value, _typ, block = self.expression(argument, block, env)
            arguments.append(value)
        value = self.value()
        self.emit(block, ir.Call(value, ir.symbol(target), arguments, target.returns))
        return value, target.returns, block

    # -- statements ----------------------------------------------------------
    def statements(self, body, block: ir.Block, env: dict) -> ir.Block:
        for statement in body:
            if block.terminator is not None:
                # Everything after a `return`, `break` or `continue` in the same
                # block is unreachable; the C backend reports it as W0001 and the
                # native backend simply does not emit it.
                break
            block = self.statement(statement, block, env)
        return block

    def statement(self, statement, block: ir.Block, env: dict) -> ir.Block:
        if getattr(statement, "loc", None) is not None:
            self.loc = statement.loc
        if isinstance(statement, Let):
            value, typ, block = self.expression(statement.value, block, env)
            env[statement.name] = (value, typ)
            return block
        if isinstance(statement, Assign):
            if statement.name not in env:
                raise Refused(f"an assignment to '{statement.name}'", statement.loc)
            value, typ, block = self.expression(statement.value, block, env)
            _old, old_type = env[statement.name]
            if typ != old_type:
                raise Refused(f"an assignment of a {typ} to a {old_type}", statement.loc)
            env[statement.name] = (value, typ)
            return block
        if isinstance(statement, ExprStmt):
            _value, _typ, block = self.expression(statement.expr, block, env)
            return block
        if isinstance(statement, If):
            return self.if_statement(statement, block, env)
        if isinstance(statement, While):
            return self.while_statement(statement, block, env)
        if isinstance(statement, For):
            return self.for_statement(statement, block, env)
        if isinstance(statement, Return):
            if statement.value is None:
                self.emit(block, ir.Ret(None))
                return block
            value, _typ, block = self.expression(statement.value, block, env)
            self.emit(block, ir.Ret(value))
            return block
        if isinstance(statement, Break):
            loop = self.find_loop(statement.label, statement.loc)
            self.emit(block,
                ir.Jump(loop.exit_label, [env[name][0] for name in loop.names]))
            return block
        if isinstance(statement, Continue):
            loop = self.find_loop(statement.label, statement.loc)
            if loop.back_edge is None:
                raise Refused("a continue in a loop without a next entry", statement.loc)
            return loop.back_edge(self, block, env)
        raise Refused(describe(statement), getattr(statement, "loc", None))

    def find_loop(self, label: str | None, loc) -> Loop:
        if not self.loops:
            raise Refused("a 'break' or 'continue' outside a loop", loc)
        if label is None:
            return self.loops[-1]
        if label not in self.labels:
            raise Refused(f"the label '{label}'", loc)
        return self.labels[label]

    def if_statement(self, statement: If, block: ir.Block, env: dict) -> ir.Block:
        names = [name for name in sorted(
            assigned_names(statement.then_body) | assigned_names(statement.else_body or []))
            if name in env]
        condition, condition_type, block = self.expression(statement.condition, block, env)
        if condition_type != "bool":
            raise Refused(f"an if on a {condition_type}", statement.loc)
        then_block = self.block("if_then",
                                [(self.value(), env[name][1]) for name in names])
        else_block = (self.block("if_else", [(self.value(), env[name][1]) for name in names])
                      if statement.else_body else None)
        join = self.block("if_join", [(self.value(), env[name][1]) for name in names])
        self.emit(block, ir.Branch(
            condition, then_block.label, [env[name][0] for name in names],
            (else_block or join).label, [env[name][0] for name in names]))
        then_env = {**env, **{name: (then_block.params[index][0], env[name][1])
                              for index, name in enumerate(names)}}
        tail = self.statements(statement.then_body, then_block, then_env)
        if tail.terminator is None:
            self.emit(tail, ir.Jump(join.label,
                                             [then_env[name][0] for name in names]))
        if else_block is not None:
            else_env = {**env, **{name: (else_block.params[index][0], env[name][1])
                                  for index, name in enumerate(names)}}
            tail = self.statements(statement.else_body, else_block, else_env)
            if tail.terminator is None:
                self.emit(tail, ir.Jump(join.label,
                                                 [else_env[name][0] for name in names]))
        for index, name in enumerate(names):
            env[name] = (join.params[index][0], env[name][1])
        return join

    def while_statement(self, statement: While, block: ir.Block, env: dict) -> ir.Block:
        names = [name for name in sorted(assigned_names(statement.body)) if name in env]
        header = self.block("while_head", [(self.value(), env[name][1]) for name in names])
        exit_block = self.block("while_exit", [(self.value(), env[name][1]) for name in names])
        body_block = self.block("while_body")
        self.emit(block, ir.Jump(header.label, [env[name][0] for name in names]))
        header_env = {**env, **{name: (header.params[index][0], env[name][1])
                                for index, name in enumerate(names)}}
        condition, condition_type, header = self.expression(statement.condition, header,
                                                            header_env)
        if condition_type != "bool":
            raise Refused(f"a while on a {condition_type}", statement.loc)
        # The exit edge carries the values the header holds at the test: leaving a
        # loop normally is not a `break`, and the exit block is what the code after the
        # loop reads.
        self.emit(header, ir.Branch(
            condition, body_block.label, [], exit_block.label,
            [header_env[name][0] for name in names]))

        def back_edge(builder, current: ir.Block, current_env: dict) -> ir.Block:
            self.emit(current,
                ir.Jump(header.label, [current_env[name][0] for name in names]))
            return current

        loop = Loop(exit_block.label, names, statement.label, back_edge)
        self.loops.append(loop)
        if statement.label:
            self.labels[statement.label] = loop
        try:
            body_env = dict(header_env)
            tail = self.statements(statement.body, body_block, body_env)
            if tail.terminator is None:
                back_edge(self, tail, body_env)
        finally:
            self.loops.pop()
            if statement.label:
                self.labels.pop(statement.label, None)
        for index, name in enumerate(names):
            env[name] = (exit_block.params[index][0], env[name][1])
        return exit_block

    def for_statement(self, statement: For, block: ir.Block, env: dict) -> ir.Block:
        """`for i in a..b` — the index is a block parameter of the header.

        The header receives the index from the entry block and from every back edge,
        so the loop variable is one SSA value and a `continue` is just an early back
        edge that computes the next index where it stands.
        """
        if not isinstance(statement.iterable, Range):
            raise Refused("a 'for' over anything but a range", statement.loc)
        start, start_type, block = self.expression(statement.iterable.start, block, env)
        end, end_type, block = self.expression(statement.iterable.end, block, env)
        if start_type != "i64" or end_type != "i64":
            raise Refused(f"a range over {start_type}s", statement.loc)
        names = [name for name in sorted(assigned_names(statement.body)) if name in env]
        header = self.block("for_head", [(self.value(), "i64")]
                            + [(self.value(), env[name][1]) for name in names])
        exit_block = self.block("for_exit", [(self.value(), env[name][1]) for name in names])
        body_block = self.block("for_body")
        self.emit(block, ir.Jump(header.label, [start, *[env[name][0] for name in names]]))
        header_env = {**env, statement.name: (header.params[0][0], "i64")}
        for index, name in enumerate(names):
            header_env[name] = (header.params[index + 1][0], env[name][1])
        condition = self.value()
        self.emit(header, ir.Cmp(condition, "lt", header.params[0][0], end))
        self.emit(header, ir.Branch(condition, body_block.label, [],
                                             exit_block.label,
                                             [header_env[name][0] for name in names]))

        def back_edge(builder, current: ir.Block, current_env: dict) -> ir.Block:
            one = builder.value()
            self.emit(current, ir.Const(one, "i64", 1))
            next_index = builder.value()
            self.emit(current, ir.Bin(next_index, "add",
                                               current_env[statement.name][0], one))
            self.emit(current,
                ir.Jump(header.label,
                        [next_index, *[current_env[name][0] for name in names]]))
            return current

        loop = Loop(exit_block.label, names, statement.label, back_edge)
        self.loops.append(loop)
        if statement.label:
            self.labels[statement.label] = loop
        try:
            body_env = dict(header_env)
            tail = self.statements(statement.body, body_block, body_env)
            if tail.terminator is None:
                back_edge(self, tail, body_env)
        finally:
            self.loops.pop()
            if statement.label:
                self.labels.pop(statement.label, None)
        for index, name in enumerate(names):
            env[name] = (exit_block.params[index][0], env[name][1])
        return exit_block


def _is_node(value) -> bool:
    """True for an AST node: everything the parser builds lives in `model`."""
    return type(value).__module__ == "nava_lang.model"


def calls_in(body) -> list:
    """Every declared function the body calls, in source order, without duplicates.

    The walk is over the node's own fields, so a construct this backend does not
    implement is still walked — it is the construct, not its callees, that will be
    refused, and refusing it is what keeps the report about the right line.
    """
    found: list = []
    #: A node graph is not a tree: `Call.target` points at the function being called,
    #: whose body contains the call. Visiting each node once is what makes the walk
    #: terminate, and a set of visited ids is cheaper than knowing which fields to
    #: skip.
    seen: set[int] = set()

    def visit(node) -> None:
        if id(node) in seen:
            return
        seen.add(id(node))
        if isinstance(node, Call) and node.target is not None:
            found.append(node.target)
        for value in vars(node).values():
            if isinstance(value, (list, tuple)):
                for item in value:
                    if _is_node(item):
                        visit(item)
            elif _is_node(value):
                visit(value)

    for statement in body:
        visit(statement)
    return found


def reachable(program: Program):
    """`main` and everything it can call, in the order the source declares them.

    A function nothing calls is not compiled, and that is a semantic decision rather
    than an optimisation: a standard-library module is full of functions no program
    uses, and refusing to compile the ones it does use is the point — refusing the
    ones it does not use would make the backend reject programs it can compile.
    """
    main = next((function for function in program.functions
                 if function.name == "main" and not function.module), None)
    if main is None:
        # Unreachable through the compiler: the checker requires `fn main()` before a
        # backend is called (E0303). It is asserted rather than given a diagnostic
        # code, because a code no program can produce is a code nothing tests.
        raise AssertionError("lower() was called on a program without fn main()")
    reached: set[int] = set()
    stack = [main]
    while stack:
        function = stack.pop()
        if id(function) in reached:
            continue
        reached.add(id(function))
        stack.extend(calls_in(function.body))
    # The order the source declares them in, not the order the walk found them: the
    # IR dump and the object file then read like the program does, and two runs of
    # the compiler cannot disagree about it.
    return [function for function in program.functions if id(function) in reached]


def lower(program: Program) -> ir.Module:
    """Lower every function `main` can reach, or refuse with `E0901` naming it."""
    functions: list[ir.Function] = []
    for function in reachable(program):
        if function.type_params:
            raise make(
                "E0901",
                f"the native backend does not implement the generic function "
                f"'{function.qualified}'",
                function.loc,
                hint="build with the C backend (the default): `nava build` without "
                     "--backend native",
            )
        builder = Builder(function)
        entry = builder.block("entry")
        env: dict[str, tuple[str, str]] = {}
        for param in function.params:
            if param.typ not in ("i64", "bool"):
                raise make(
                    "E0901",
                    f"the native backend does not implement a parameter of type "
                    f"{param.typ} (in '{function.qualified}')",
                    param.loc,
                    hint="build with the C backend (the default), or pass a number "
                         "or a bool",
                )
            value = builder.value()
            env[param.name] = (value, param.typ)
            builder.params.append((value, param.typ))
        try:
            tail = builder.statements(function.body, entry, env)
            if tail.terminator is None:
                if function.returns == "void":
                    builder.emit(tail, ir.Ret(None))
                else:
                    raise Refused(f"a path in '{function.qualified}' that does not return",
                                  function.loc)
        except Refused as refusal:
            raise make(
                "E0901",
                f"the native backend does not implement {refusal.what} "
                f"(in '{function.qualified}')",
                refusal.loc,
                hint="build with the C backend (the default): `nava build` without "
                     "--backend native",
            ) from None
        functions.append(ir.Function(
            function.name, builder.params, function.returns, builder.blocks,
            module=function.module, lines=builder.lines,
            decl_line=builder.decl_line))
    return ir.Module(functions)
