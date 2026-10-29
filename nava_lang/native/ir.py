"""A linear, SSA-shaped IR between the checker and the backends (PLAN.md N14a).

The compiler used to have one backend: `cgen.py` walked the AST and printed C. That
is a fine shape for one backend and a bad shape for two, because the differential
between them would then be a differential between two *lowerings of the AST* rather
than between two *backends of one program*. This module is the thin waist: a small
instruction set with values in SSA form and control flow in blocks, which `lower.py`
produces from a checked program and which every backend consumes.

The shape, in one paragraph. A function is a list of **blocks**; a block has a
label, a list of **block parameters** (values it receives when it is entered, which
is how SSA expresses a join without a phi instruction), and a list of
**instructions**. Every instruction defines exactly one value — its first field —
and values are named `%0`, `%1`, … in the order they are defined, so a dump of the
IR is stable and can be diffed. The last instruction of a block is a **terminator**:
`jump`, `branch` or `ret`. That is the whole language: constants, arithmetic with
checked overflow, comparisons, calls, `print`, and the three terminators.

Two decisions worth stating, because the backends inherit them:

* **Overflow is in the instruction set, not in a flag.** `add`, `sub`, `mul` and
  `neg` are *checked*; a backend that computes a wrapped result has not implemented
  the instruction. There is no unchecked variant, because the language has none.
* **`print` is an instruction, not a call to a builtin.** The string a backend has
  to write is part of the program's behaviour, and hiding it behind a name that
  each backend resolves differently would put the one thing being compared — the
  output — inside two implementations of the same thing.

The IR is also where the native backend says no. Everything `lower` cannot express
is refused with `E0901` and the construct named, so "the native backend does not
implement this yet" is a diagnostic rather than a wrong answer.
"""
from __future__ import annotations

from ..records import field, record

#: Types a value may have in the IR. Floats, arrays, strings-as-values and the
#: owning containers are not here yet: `lower.py` refuses them by name.
VALUE_TYPES = ("i64", "bool")
#: The one type that is a value but not a number: a string literal, printable and
#: nothing else. It has no operations, which is what keeps the native backend small.
STRING_TYPE = "str"


@record()
class Const:
    """`%v = const <value>` — an `i64` or a `bool`; a string is `Str`."""

    value: str
    typ: str
    payload: object


@record()
class Str:
    """`%v = str "<text>"` — a string literal, printable, not comparable."""

    value: str
    text: str


@record()
class Bin:
    """`%v = <op> %a, %b` — checked arithmetic; `op` is add/sub/mul/div/mod."""

    value: str
    op: str
    left: str
    right: str


@record()
class Neg:
    """`%v = neg %a` — checked: `-i64_min` stops the program."""

    value: str
    operand: str


@record()
class Not:
    """`%v = not %a` — logical negation of a `bool`."""

    value: str
    operand: str


@record()
class Cmp:
    """`%v = cmp <op> %a, %b` — `op` is eq/ne/lt/le/gt/ge; the result is a `bool`."""

    value: str
    op: str
    left: str
    right: str


@record()
class Copy:
    """`%v = copy %a` — a block parameter receiving a value is exactly this."""

    value: str
    operand: str


@record()
class Call:
    """`%v = call @name(%args)`, or `call @name(...)` when the function is `void`."""

    value: str
    target: str
    args: list[str]
    returns: str


@record()
class Print:
    """`print %v` — a value and the type that says how to write it."""

    operand: str
    typ: str


@record()
class Jump:
    """`jump ^label(%args)` — the block arguments become the target's parameters."""

    target: str
    args: list[str]


@record()
class Branch:
    """`branch %cond, ^then(%a), ^else(%b)`."""

    condition: str
    then_target: str
    then_args: list[str]
    else_target: str
    else_args: list[str]


@record()
class Ret:
    """`ret %v`, or `ret` when the function returns nothing."""

    operand: str | None


#: The terminators: every block ends with exactly one of these.
TERMINATORS = (Jump, Branch, Ret)


@record()
class Block:
    """A basic block: a label, the values it receives, and what it does."""

    label: str
    params: list[tuple[str, str]] = field(default_factory=list)
    instructions: list = field(default_factory=list)

    @property
    def terminator(self):
        for instruction in reversed(self.instructions):
            if isinstance(instruction, TERMINATORS):
                return instruction
        return None


@record()
class Function:
    """One function of the program, in IR."""

    name: str
    params: list[tuple[str, str]]
    returns: str
    blocks: list[Block] = field(default_factory=list)

    def value_count(self) -> int:
        """How many values this function defines, block parameters included."""
        total = 0
        for block in self.blocks:
            total += len(block.params)
            total += sum(1 for instruction in block.instructions
                         if not isinstance(instruction, TERMINATORS))
            total += sum(1 for instruction in block.instructions
                         if isinstance(instruction, Branch))
        return total


@record()
class Module:
    """A whole program: its functions in call order, and how it was lowered."""

    functions: list[Function]
    #: The entry function's name; always `main`.
    entry: str = "main"

    def function(self, name: str) -> Function | None:
        for item in self.functions:
            if item.name == name:
                return item
        return None


def dump(module: Module) -> str:
    """A stable textual form of the IR, for `nava emit-ir` and for diffing."""
    lines: list[str] = []
    for function in module.functions:
        params = ", ".join(f"{value}: {typ}" for value, typ in function.params)
        lines.append(f"fn @{function.name}({params}) -> {function.returns}:")
        for block in function.blocks:
            arguments = ", ".join(f"{value}: {typ}" for value, typ in block.params)
            header = f"  ^{block.label}"
            if arguments:
                header += f"({arguments})"
            lines.append(header + ":")
            for instruction in block.instructions:
                lines.append(f"    {_instruction_text(instruction)}")
    return "\n".join(lines) + "\n"


def _instruction_text(instruction) -> str:  # noqa: C901 - one branch per instruction
    if isinstance(instruction, Const):
        text = f"true" if instruction.typ == "bool" and instruction.payload else \
               ("false" if instruction.typ == "bool" else str(instruction.payload))
        return f"%{instruction.value} = const {instruction.typ} {text}"
    if isinstance(instruction, Str):
        escaped = instruction.text.replace("\\", "\\\\").replace('"', '\\"')
        escaped = escaped.replace("\n", "\\n")
        return f'%{instruction.value} = str "{escaped}"'
    if isinstance(instruction, Bin):
        return f"%{instruction.value} = {instruction.op} %{instruction.left}, %{instruction.right}"
    if isinstance(instruction, Neg):
        return f"%{instruction.value} = neg %{instruction.operand}"
    if isinstance(instruction, Not):
        return f"%{instruction.value} = not %{instruction.operand}"
    if isinstance(instruction, Cmp):
        return f"%{instruction.value} = cmp {instruction.op} %{instruction.left}, %{instruction.right}"
    if isinstance(instruction, Copy):
        return f"%{instruction.value} = copy %{instruction.operand}"
    if isinstance(instruction, Call):
        arguments = ", ".join(f"%{item}" for item in instruction.args)
        left = f"%{instruction.value} = " if instruction.returns != "void" else ""
        return f"{left}call @{instruction.target}({arguments})"
    if isinstance(instruction, Print):
        return f"print {instruction.typ} %{instruction.operand}"
    if isinstance(instruction, Jump):
        return f"jump ^{instruction.target}({_arguments(instruction.args)})"
    if isinstance(instruction, Branch):
        return (f"branch %{instruction.condition}, "
                f"^{instruction.then_target}({_arguments(instruction.then_args)}), "
                f"^{instruction.else_target}({_arguments(instruction.else_args)})")
    if isinstance(instruction, Ret):
        return "ret" if instruction.operand is None else f"ret %{instruction.operand}"
    raise AssertionError(f"unknown IR instruction {instruction!r}")


def _arguments(values: list[str]) -> str:
    return ", ".join(f"%{value}" for value in values)
