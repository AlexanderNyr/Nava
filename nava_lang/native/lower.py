"""Checked AST → IR (PLAN.md N14a).

This is the lowering the native backend consumes, and it is deliberately narrow:
`i64`, `bool`, `f64`, string literals, arrays of `i64`/`bool`/`str`, arithmetic,
comparisons, `if`, `while`, `for … in a..b`, `for … in array`,
`break`/`continue`, function calls, a function taken as a value, `print`, slices,
`Vec`, `HashMap`, `HashSet`, structs, enums, `match`, `defer`, `?` and module
functions. Everything else — `String`, the clock, the filesystem and the builtins
whose only implementation is the C runtime's (`str_split`, `read_f64`, `arg_*`,
`chars_len`) — is refused with `E0901` **naming the construct**, because the one
thing a second backend must not do is compile a program it does not understand
into something that runs.

Arrays arrived with N14c and bring the first thing in this lowering that is not an
SSA value: **ownership**. An array is a handle, and a handle has one owner; the
owner is the binding a block declared, and the block gives it back through
`release` when it is left — on the normal path, on `return`, and on a
`break`/`continue` that leaves the block, innermost first. Nothing about that is a
backend's decision: the C backend turns `release` into `free`, the native one
turns it into nothing at all, and both are implementing the same instruction.

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

A **function value** is one word — the address of the function's code — and
nothing more: there are no closures, so a function's own name is all a value has to
carry. `funcref` names the function, an indirect `call` runs it, and both name the
same link-time symbol a direct call does, so a function reached through a value and
the same function called by name are one address. A function the program only ever
passes along — never calls by name — is still reachable, because the call that will
run it is a call through a register, and that call names no one.

The lowering is total over what it accepts: there is no "best effort" path and no
silently wrong answer.
"""
from __future__ import annotations

from ..diagnostics import make
from ..model import (
    ArrayLiteral, Arm, Assign, Binary, Boolean, Break, Call, Continue, Defer, EnumDecl,
    ExprStmt, Float, For, Function, If, Index, IndexAssign, Let, Match, Member,
    MemberAssign, Number, Program, Range, Return, Slice, String, StructLiteral, Try,
    Unary, Variable, Variant, VariantValue, While,
)
from ..checker import BUILTINS
from ..model import (is_function_type, is_map, is_set, is_vec, map_parts,
                     set_element, vec_element)
from . import ir

#: Types `print` can write in the native backend.
#: What `print` accepts. An `f64` is here because the language prints one and the IR
#: says so; the native backend refuses it where it would have to write the digits,
#: which is the shortest round-tripping decimal form and needs a conversion this
#: backend does not have yet.
PRINTABLE = ("i64", "bool", "str", "f64")
#: The payload types an enum variant may carry here. A struct payload would be a
#: struct value inside an arena record, which is the copy question structs already
#: answer in their own layout; it arrives when the copy does.
PAYLOAD_TYPES = ("i64", "bool", "str", "f64")
#: The element types an array may hold here. A `Vec`, a struct or a slice is a
#: different representation, and each arrives with its own increment.
ELEMENT_TYPES = ("i64", "bool", "str")


def struct_layouts(program: Program) -> dict[str, tuple]:
    """Every struct type of the program: its name and its fields in declaration order.

    A generic struct is one declaration per instantiation, and the written type
    (`Pair(i64)`) is the name a value of it is spelled with, so both the declared name
    and the written one answer the same layout.
    """
    layouts: dict[str, tuple] = {}
    declarations = list(program.structs) + list(program.struct_instances.values())
    for struct in declarations:
        layouts[struct.name] = tuple((item.name, item.typ) for item in struct.fields)
    for written, struct in program.struct_instances.items():
        layouts[written] = tuple((item.name, item.typ) for item in struct.fields)
    return layouts


def enum_layouts(program: Program) -> dict[str, tuple]:
    """Every enum type the program constructs: its variants and what each carries.

    The written type is the key (`Shape`, `Result(i64, str)`), because that is what a
    value's type is spelled as; a variant's position in the tuple is its tag. The
    declarations come from the nodes themselves — a construction names its enum — so
    the lowering does not have to guess which enums a program uses.
    """
    layouts: dict[str, tuple] = {}
    for decl in program.enums:
        layouts[decl.name] = tuple((item.name, tuple(item.types))
                                   for item in decl.variants)
    for node in walk(program.functions):
        if isinstance(node, VariantValue) and node.declared is not None:
            key = node.instance or node.declared.name
            layouts[key] = tuple((item.name, tuple(item.types))
                                 for item in node.declared.variants)
    # The checker instantiates a prelude enum for a builtin that answers one —
    # `map_get` and `vec_pop` answer `Option(V)` — and no `VariantValue` node is
    # written for it, so the instances are what puts the layout in the module. The
    # tags are the checker's, so `Some` is 0 and `None` is 1 here because they are
    # there, not because this backend assumes it.
    for key, decl in program.instances.items():
        layouts.setdefault(key, tuple((item.name, tuple(item.types))
                                      for item in decl.variants))
    return layouts


def walk(value, depth: int = 0):
    """Every node under `value`, in source order.

    A small walker over the typed AST: records hold lists and tuples of children, and
    a `str` has no children, which is what keeps the walk from reading types as nodes.
    """
    if depth > 60 or isinstance(value, (str, bytes, int, float, bool, type(None))):
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            yield from walk(item, depth + 1)
        return
    fields = getattr(value, "__dict__", None)
    if fields is None:
        return
    yield value
    for item in list(fields.values()):
        yield from walk(item, depth + 1)


def is_array(typ: object) -> bool:
    """Is this type a fixed-length array, `[T]`?"""
    return isinstance(typ, str) and typ.startswith("[") and typ.endswith("]")


def element_of(typ: str) -> str:
    """`[i64]` -> `i64`."""
    return typ[1:-1].strip()


def container_element(typ: str) -> str:
    """The element type of an array or a `Vec`, or the type itself.

    A `Vec` and an array differ in what they are (a vector grows and owns a
    capacity, an array cannot) but not in what one element is: one word. So the two
    share every instruction that reads or writes an element, and the type is what
    tells a backend which helper to call.
    """
    if is_vec(typ):
        return vec_element(typ)
    return element_of(typ)


def is_sequence(typ: object) -> bool:
    """An array or a `Vec`: the two things a `for` walks by index.

    Not the same set as what can be *indexed*, which is an array alone (`E0329`),
    nor as what answers a `len`, which is an array and a table (`E0338`).
    """
    return is_array(typ) or is_vec(typ)


def is_table(typ: object) -> bool:
    """A `HashMap` or a `HashSet`: a value you walk by position, not by index."""
    return is_map(typ) or is_set(typ)


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
        elif isinstance(statement, Defer):
            # A `defer` assigns nothing the loop carries: its body runs where the block
            # is left. Whether it *may* assign is decided where it is lowered, so that
            # the refusal is the same inside a loop as outside one.
            continue
        elif isinstance(statement, Match):
            # An arm assigns through the same join as an `if` half, so a `match` inside
            # a loop body contributes the names its arms assign to the loop's frame.
            for arm in statement.arms:
                names |= assigned_names(arm.body)
        elif isinstance(statement, (Break, Continue, Return, ExprStmt, IndexAssign,
                                    MemberAssign)):
            # Nothing here binds a name: an element write changes storage that some
            # other binding already owns, so there is nothing to carry across a join.
            continue
        else:
            raise Refused(describe(statement), getattr(statement, "loc", None))
    return names


class Loop:
    """Where a `break` and a `continue` go, and what a loop carries through them."""

    def __init__(self, exit_label: str, names: list[str], label: str | None = None,
                 back_edge=None, frame: int = 0):
        self.exit_label = exit_label
        self.names = names
        self.label = label
        #: Called at a `continue` with (block, env) to jump to the loop's next entry.
        self.back_edge = back_edge
        #: The body's ownership frame: `break` and `continue` release down to it.
        self.frame = frame


def check_instantiations(functions) -> None:
    """Refuse a generic function with more than one instantiation, by name.

    An instantiation carries no type argument in its name, so `identity(i64)` and
    `identity(bool)` are one symbol. Compiling both would either link two bodies into
    one name or fail at link time for a reason the user did not cause, and a wrong
    answer is worse than a refusal. A template with a single instantiation is fine and
    is compiled: there is exactly one body to name.
    """
    seen: dict[tuple[str, str], object] = {}
    for function in functions:
        key = (function.module, function.name)
        if key in seen:
            raise make(
                "E0901",
                f"the native backend does not implement more than one instantiation "
                f"of the generic function '{function.name}' (in '{function.qualified}')",
                function.loc,
                hint="build with the C backend (the default): `nava build` without "
                     "--backend native",
            )
        seen[key] = function


#: The scalar types the IR names directly. Anything else is an aggregate: an array
#: (`[T]`), a struct, an enum, a container or a function value.
SCALARS = ("i64", "bool", "f64", "str")
#: The field types a struct may hold here. A field that owns storage (`String`, `Vec`,
#: a map, a set) needs a copy that knows how to copy its storage, which is a different
#: instruction; a struct of words is the increment that says fields are positions.
FIELD_TYPES = ("i64", "bool", "f64", "str")


class Builder:
    """One function's lowering: its blocks, its values, its cells, its environment."""

    def __init__(self, function: Function, structs: dict | None = None,
                 enums: dict | None = None):
        self.function = function
        #: The struct types of the program and their fields, in declaration order: a
        #: variable of one of these lives in a slot, because a field assignment writes
        #: into storage, and a field name is a position in the layout.
        self.structs = dict(structs or {})
        #: The enum types of the program: variant names in declaration order, and the
        #: types each carries. The position of a variant is its tag, so this is also
        #: what a `match` compares.
        self.enums = dict(enums or {})
        #: The cells this function declares, in declaration order.
        self.slots: list[ir.Slot] = []
        #: Variable name -> slot name, for the variables that live in a slot.
        self.cells: dict[str, str] = {}
        #: The types of those variables, so a slot can be named in a refusal.
        self.cell_types: dict[str, str] = {}
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
        #: One entry per enclosing block, innermost last: the arrays that block
        #: owns, in the order it declared them.
        self.frames: list[list[tuple[str, str]]] = []
        #: Arrays built inside one statement: nobody binds them, so the statement
        #: gives them back when it ends.
        self.temporaries: list[tuple[str, str]] = []
        #: One entry per enclosing block, innermost last: the `defer` bodies that block
        #: registered, in the order it registered them. They are *bodies*, not
        #: instructions: a deferred block reads the values its scope holds when it
        #: runs, so it is lowered at every exit rather than once where it is written.
        self.defers: list[list[list]] = []

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

    def declare_slot(self, name: str, typ: str) -> str:
        """Give a variable storage: a cell of `typ`, named once per function."""
        slot = f"c{len(self.slots)}"
        self.slots.append(ir.Slot(slot, typ))
        self.cells[name] = slot
        self.cell_types[name] = typ
        return slot

    def is_struct(self, typ: object) -> bool:
        return isinstance(typ, str) and typ in self.structs

    def layout(self, typ: str, loc) -> tuple:
        """The fields of a struct type, refusing one whose fields are not words."""
        fields = self.structs.get(typ)
        if fields is None:
            raise Refused(f"the type '{typ}'", loc)
        for name, field_type in fields:
            if field_type not in FIELD_TYPES and not self.is_struct(field_type):
                raise Refused(
                    f"a field '{name}' of type {field_type} in '{typ}': the native "
                    f"backend holds a struct of numbers, bools, strings and structs",
                    loc)
        return fields

    def is_enum(self, typ: object) -> bool:
        return isinstance(typ, str) and typ in self.enums

    def enum_layout(self, typ: str, loc) -> tuple:
        """The variants of an enum type, refusing one the lowering was not given.

        An enum reached only through a parameter or a return value, and never
        constructed anywhere in the program, has no layout to state — and a backend
        that guessed one would be reading words it had not been told about.
        """
        variants = self.enums.get(typ)
        if variants is None:
            raise Refused(f"the enum type '{typ}'", loc)
        for name, types in variants:
            for payload_type in types:
                if payload_type not in PAYLOAD_TYPES:
                    raise Refused(
                        f"a payload of type {payload_type} in '{typ}::{name}': the "
                        f"native backend holds a payload that is a number, a bool or a "
                        f"string", loc)
        return variants

    def load_variable(self, name: str, block: ir.Block) -> tuple[str, str] | None:
        """`a` where `a` lives in a cell: the value it holds now."""
        slot = self.cells.get(name)
        if slot is None:
            return None
        typ = self.cell_types[name]
        value = self.value()
        self.emit(block, ir.SlotLoad(value, typ, slot))
        return value, typ

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
            loaded = self.load_variable(node.name, block)
            if loaded is not None:
                return loaded[0], loaded[1], block
            if node.name not in env:
                if node.function is not None:
                    return self.function_value(node, block)
                raise Refused(f"the variable '{node.name}'", node.loc)
            value, typ = env[node.name]
            return value, typ, block
        if isinstance(node, Float):
            value = self.value()
            self.emit(block, ir.Const(value, "f64", float(node.value)))
            return value, "f64", block
        if isinstance(node, Slice):
            return self.slice_value(node, block, env)
        if isinstance(node, StructLiteral):
            return self.struct_literal(node, block, env)
        if isinstance(node, VariantValue):
            return self.variant_value(node, block, env)
        if isinstance(node, Member):
            base, base_type, block = self.expression(node.base, block, env)
            self.layout(base_type, node.loc)
            value = self.value()
            self.emit(block, ir.Field(value, node.typ, base, node.field, base_type))
            return value, node.typ, block
        if isinstance(node, Unary):
            operand, typ, block = self.expression(node.operand, block, env)
            if node.op == "-":
                if typ not in ("i64", "f64"):
                    raise Refused(f"unary '-' on a {typ}", node.loc)
                value = self.value()
                self.emit(block, ir.Neg(value, operand, typ))
                return value, typ, block
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
        if isinstance(node, ArrayLiteral):
            return self.array_literal(node, block, env, owned=False)
        if isinstance(node, Index):
            return self.index(node, block, env)
        raise Refused(describe(node), getattr(node, "loc", None))

    # -- arrays --------------------------------------------------------------
    def check_element(self, typ: str, loc) -> None:
        """An element is one word here: a scalar, or a struct that is a record.

        A struct element is the address of its record, because a struct *is* a record on
        this backend — so the array copies a pointer where C copies the struct, and the
        value semantics are kept where the language can observe them (a read gives a
        value that goes into a cell, and a cell copies).
        """
        if is_vec(typ):
            element = vec_element(typ)
            if element not in ELEMENT_TYPES and element != "f64":
                # A vector of structs would need the copy an element of an array of
                # structs already has, and a vector that reallocates on push would
                # have to copy every element with it; that is its own increment.
                raise Refused(f"a 'Vec({element})'", loc)
            return
        if is_map(typ) or is_set(typ):
            key = map_parts(typ)[0] if is_map(typ) else set_element(typ)
            if key not in ("i64", "str"):
                raise Refused(f"a '{typ}' keyed by a {key}", loc)
            if is_map(typ) and map_parts(typ)[1] not in ELEMENT_TYPES + ("f64",):
                raise Refused(f"a '{typ}' holding a {map_parts(typ)[1]}", loc)
            return
        if not is_array(typ):
            raise Refused(f"the array type '{typ}'", loc)
        element = element_of(typ)
        if element in ELEMENT_TYPES:
            return
        if self.is_struct(element):
            # `layout` refuses a struct whose fields are not words, and that refusal is
            # the one that matters here: an element has to be copyable by copying its
            # words on both backends, and a struct holding an array is not.
            self.layout(element, loc)
            return
        raise Refused(f"an array of {element}", loc)

    def array_literal(self, node: ArrayLiteral, block: ir.Block, env: dict,
                      owned: bool):
        """`[a, b, c]` — a new array, and the question of who gives it back.

        Inside a `let` the binding owns it; anywhere else the enclosing statement
        does, and it is released when that statement ends. `owned` says which,
        because the difference is one entry in one list and getting it wrong is a
        leak or a double release.
        """
        typ = node.typ
        self.check_element(typ, node.loc)
        items: list[str] = []
        for item in node.items:
            value, item_type, block = self.expression(item, block, env)
            if item_type != element_of(typ):
                raise Refused(f"a {item_type} in an array of {element_of(typ)}",
                              node.loc)
            items.append(value)
        value = self.value()
        self.emit(block, ir.ArrayLiteral(value, typ, items))
        self.take(value, typ, owned)
        return value, typ, block

    def slice_value(self, node: Slice, block: ir.Block, env: dict):
        """`a[i..j]`: the bounds, then one instruction that answers a view.

        The bounds are lowered left to right, before the view exists, which is the
        language's order; the range itself is checked by the instruction, because the
        checker proved only that the two bounds are `i64`.
        """
        loaded = self.load_variable(node.array.name, block)
        if loaded is not None:
            base, base_type = loaded
        elif node.array.name in env:
            base, base_type = env[node.array.name]
        else:
            raise Refused(f"the variable '{node.array.name}'", node.loc)
        self.check_element(base_type, node.loc)
        start, start_type, block = self.expression(node.start, block, env)
        if start_type != "i64":
            raise Refused(f"a slice start of type {start_type}", node.loc)
        end, end_type, block = self.expression(node.end, block, env)
        if end_type != "i64":
            raise Refused(f"a slice end of type {end_type}", node.loc)
        value = self.value()
        self.emit(block, ir.Slice(value, base_type, base, start, end))
        return value, base_type, block

    def variant_value(self, node: VariantValue, block: ir.Block, env: dict):
        """`Shape::Circle(2)`, in the order the arguments are written.

        The arguments are lowered left to right before the value exists, which is the
        language's evaluation order; a call in the second argument happens after a call
        in the first, on both backends.
        """
        declared = node.declared
        if declared is None:
            raise Refused(f"the variant '{node.enum}::{node.variant}'", node.loc)
        typ = node.instance or declared.name
        self.enum_layout(typ, node.loc)
        values: list[str] = []
        types: list[str] = []
        for argument in node.args:
            value, argument_type, block = self.expression(argument, block, env)
            values.append(value)
            types.append(argument_type)
        value = self.value()
        tag = node.choice.tag if node.choice is not None else -1
        self.emit(block, ir.Variant(value, typ, node.variant, tag, tuple(values),
                                    tuple(types)))
        return value, typ, block

    def struct_literal(self, node: StructLiteral, block: ir.Block, env: dict):
        """`Point(x: 1, y: 2)` — every field named, in the order written."""
        self.layout(node.typ, node.loc)
        fields: list[tuple[str, str]] = []
        for name, expression in node.fields:
            value, _typ, block = self.expression(expression, block, env)
            fields.append((name, value))
        value = self.value()
        self.emit(block, ir.StructNew(value, node.typ, fields))
        return value, node.typ, block

    def array_zeros(self, node: Call, block: ir.Block, env: dict, owned: bool):
        """`array_zeros(n)` — a zeroed array of `n` elements."""
        typ = node.typ
        self.check_element(typ, node.loc)
        if len(node.args) != 1:
            raise Refused("an array_zeros without one argument", node.loc)
        count, count_type, block = self.expression(node.args[0], block, env)
        if count_type != "i64":
            raise Refused(f"array_zeros of a {count_type}", node.loc)
        value = self.value()
        self.emit(block, ir.ArrayZeros(value, typ, count))
        self.take(value, typ, owned)
        return value, typ, block

    def index(self, node: Index, block: ir.Block, env: dict):
        """`a[i]` — a checked read of one element."""
        name = node.array.name
        if name not in env:
            raise Refused(f"the variable '{name}'", node.loc)
        base, base_type = env[name]
        self.check_element(base_type, node.loc)
        index, index_type, block = self.expression(node.index, block, env)
        if index_type != "i64":
            raise Refused(f"an index of type {index_type}", node.loc)
        element = element_of(base_type)
        value = self.value()
        self.emit(block, ir.Index(value, element, base, index))
        return value, element, block

    def take(self, value: str, typ: str, owned: bool) -> None:
        """Register a new array as owned by the block, or by the statement."""
        if owned:
            self.frames[-1].append((value, typ))
        else:
            self.temporaries.append((value, typ))

    # -- ownership -----------------------------------------------------------
    def body(self, statements, block: ir.Block, env: dict) -> ir.Block:
        """Lower one block, then give back what it owned.

        The frame is popped before the caller sees the tail block, so a `return`
        inside the body releases it explicitly and the release here is only the
        path that fell out of the block.
        """
        self.frames.append([])
        self.defers.append([])
        try:
            tail = self.statements(statements, block, env)
            if tail.terminator is None:
                tail = self.run_defers(tail, len(self.frames) - 1, env)
                self.release_frame(tail, len(self.frames) - 1)
        finally:
            self.frames.pop()
            self.defers.pop()
        return tail

    def run_defers(self, block: ir.Block, index: int, env: dict) -> ir.Block:
        """Lower one frame's `defer` bodies where the block is left, last first.

        A deferred block is lowered *at the exit point*, not where it is written, which
        is why `defer { print(i) }` in a loop prints the value `i` has when the iteration
        ends. Each exit path gets its own copy — the same thing C does — and the copies
        run before the frame releases the arrays it owns, so a deferred block can still
        use them.
        """
        for body in reversed(self.defers[index]):
            block = self.body(body, block, dict(env))
        return block

    def release_frame(self, block: ir.Block, index: int) -> None:
        """Emit `release` for one frame, last declared first."""
        for value, typ in reversed(self.frames[index]):
            self.emit(block, ir.Release(typ, value))

    def leave(self, block: ir.Block, down_to: int, env: dict | None = None) -> None:
        """Leave every block from the innermost down to `down_to`, inclusive.

        A block is left in the language's order: its `defer` bodies, last registered
        first, and then the storage it owns — so a deferred block still has the arrays
        its scope declared.
        """
        for index in range(len(self.frames) - 1, down_to - 1, -1):
            if env is not None:
                block = self.run_defers(block, index, env)
            self.release_frame(block, index)

    def release_temporaries(self, block: ir.Block) -> ir.Block:
        for value, typ in reversed(self.temporaries):
            self.emit(block, ir.Release(typ, value))
        self.temporaries = []
        return block

    def binary(self, node: Binary, block: ir.Block, env: dict):
        if node.op in ("&&", "||"):
            return self.short_circuit(node, block, env)
        left, left_type, block = self.expression(node.left, block, env)
        right, right_type, block = self.expression(node.right, block, env)
        if left_type != right_type:
            raise Refused(f"a '{node.op}' between a {left_type} and a {right_type}",
                          node.loc)
        if node.op in ("+", "-", "*", "/", "%"):
            if left_type not in ("i64", "f64"):
                raise Refused(f"'{node.op}' on {left_type}s", node.loc)
            value = self.value()
            opcode = {"+": "add", "-": "sub", "*": "mul", "/": "div", "%": "mod"}[node.op]
            self.emit(block, ir.Bin(value, opcode, left, right, left_type))
            return value, left_type, block
        if node.op in ("==", "!=", "<", "<=", ">", ">="):
            if left_type not in ("i64", "bool", "f64", "str"):
                raise Refused(f"'{node.op}' on {left_type}s", node.loc)
            value = self.value()
            opcode = {"==": "eq", "!=": "ne", "<": "lt", "<=": "le",
                      ">": "gt", ">=": "ge"}[node.op]
            if left_type == "str":
                # Two strings compare by their text. That is a different question from
                # where they are, so it is a different instruction.
                self.emit(block, ir.TextCompare(value, opcode, left, right))
                return value, "bool", block
            self.emit(block, ir.Cmp(value, opcode, left, right, left_type))
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

    def note_function_type(self, typ: str, loc) -> None:
        """A function value is one word here; a closure would not be.

        The checker has already decided that a call through a value is well typed;
        this is the lowering's own control, and it is what would have to change
        first if a value ever came to carry an environment with it.
        """
        if not is_function_type(typ):
            raise Refused(f"a call through a value of type '{typ}'", loc)

    def function_value(self, node: Variable, block: ir.Block):
        """A declared function named as a value: the address of its code.

        The checker has already decided which function the name means, so this is
        not a lookup — it is the one instruction that says "the address of that
        function", which is a thing only a backend can know.
        """
        target = node.function
        if target.type_params or getattr(target, "type_args", None):
            raise Refused(f"the generic function '{target.qualified}' as a value",
                          node.loc)
        value = self.value()
        self.emit(block, ir.FuncRef(value, ir.symbol(target), node.typ))
        return value, node.typ, block

    def call(self, node: Call, block: ir.Block, env: dict):
        """A call to a declared function, wherever it is declared.

        The checker has already resolved `node.target`, so the native backend does not
        repeat the name lookup — and a call it cannot make is refused here, with the
        function named, rather than resolved to something else.
        """
        if node.callee is not None:
            # A call through a value: the arguments are lowered the same way and
            # in the same order, and only the callee changes — from a name the
            # checker resolved to a word the program is holding.
            if node.callee.name not in env:
                raise Refused(f"a call through the variable '{node.callee.name}'",
                              node.loc)
            callee, callee_type = env[node.callee.name]
            self.note_function_type(callee_type, node.loc)
            arguments: list[str] = []
            for argument in node.args:
                value, _typ, block = self.expression(argument, block, env)
                arguments.append(value)
            result = self.value()
            self.emit(block, ir.Call(result, "", arguments, node.typ or "void",
                                     callee=callee))
            return result, node.typ or "void", block
        if node.target is None:
            if not node.module and node.name == "array_zeros":
                return self.array_zeros(node, block, env, owned=False)
            if not node.module and node.name == "len":
                if len(node.args) != 1:
                    raise Refused("a len without one argument", node.loc)
                base, base_type, block = self.expression(node.args[0], block, env)
                if not (is_array(base_type) or is_table(base_type)):
                    raise Refused(f"len of a {base_type}", node.loc)
                value = self.value()
                self.emit(block, ir.Length(value, base))
                return value, "i64", block
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
            if node.name in BUILTINS:
                # A builtin is an instruction, not a call: the language defines it,
                # and which runtime implements it is the backend's business.
                arguments: list[str] = []
                for argument in node.args:
                    value, _typ, block = self.expression(argument, block, env)
                    arguments.append(value)
                value = self.value()
                self.emit(block, ir.Builtin(value, node.typ or "void", node.name,
                                            arguments))
                return value, node.typ or "void", block
            raise Refused(f"a call to '{node.name}'", node.loc)
        target = node.target
        if target.type_params or node.type_args is not None:
            raise Refused(f"the generic function '{target.qualified}'", node.loc)
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
            outer, self.temporaries = self.temporaries, []
            block = self.statement(statement, block, env)
            if block.terminator is None:
                block = self.release_temporaries(block)
            self.temporaries = outer
        return block

    def statement(self, statement, block: ir.Block, env: dict) -> ir.Block:
        if getattr(statement, "loc", None) is not None:
            self.loc = statement.loc
        if isinstance(statement, Defer):
            # A deferred body runs where the block is left, so an assignment inside it
            # would land after the values it changed had already been carried out of the
            # scope. The C backend accepts one; refusing it here is honest, and the
            # refusal names it.
            if assigned_names(statement.body):
                raise Refused("a 'defer' whose body assigns a variable", statement.loc)
            # Kept, not emitted: the frame that registered it runs it on every path
            # out of this block, which is why nothing is emitted here.
            self.defers[-1].append(statement.body)
            return block
        if isinstance(statement, Let) and isinstance(statement.value, Try):
            return self.propagate_let(statement, block, env)
        if isinstance(statement, Let):
            declared = getattr(statement.symbol, "typ", None)
            if is_array(declared) and not getattr(statement.symbol, "borrowed", False):
                # An array binding is its owner. The checker admits only a literal
                # or `array_zeros` here, so the binding cannot be a second name for
                # somebody else's storage.
                if isinstance(statement.value, ArrayLiteral):
                    value, typ, block = self.array_literal(statement.value, block, env,
                                                           owned=True)
                elif (isinstance(statement.value, Call)
                      and statement.value.target is None
                      and not statement.value.module
                      and statement.value.name == "array_zeros"):
                    value, typ, block = self.array_zeros(statement.value, block, env,
                                                         owned=True)
                else:
                    raise Refused("an array binding that is not a literal or "
                                  "array_zeros", statement.loc)
                env[statement.name] = (value, typ)
                return block
            value, typ, block = self.expression(statement.value, block, env)
            if self.is_struct(typ):
                # A struct is a value with fields that can be written, so the
                # variable that holds one is storage rather than a name for a value.
                slot = self.declare_slot(statement.name, typ)
                self.emit(block, ir.SlotStore(slot, value))
                return block
            env[statement.name] = (value, typ)
            return block
        if isinstance(statement, Assign):
            slot = self.cells.get(statement.name)
            if slot is not None:
                value, _typ, block = self.expression(statement.value, block, env)
                self.emit(block, ir.SlotStore(slot, value))
                return block
            if statement.name not in env:
                raise Refused(f"an assignment to '{statement.name}'", statement.loc)
            value, typ, block = self.expression(statement.value, block, env)
            _old, old_type = env[statement.name]
            if typ != old_type:
                raise Refused(f"an assignment of a {typ} to a {old_type}", statement.loc)
            env[statement.name] = (value, typ)
            return block
        if isinstance(statement, MemberAssign):
            # The root must be a variable in storage: a write to a field of a temporary
            # is a write nobody could read, and the checker refuses it before this.
            path: list[str] = []
            node = statement.target
            while isinstance(node, Member):
                path.append(node.field)
                node = node.base
            if not isinstance(node, Variable):
                raise Refused(
                    "a field assignment to something that is not a variable",
                    statement.loc)
            slot = self.cells.get(node.name)
            if slot is None:
                raise Refused(f"a field assignment to '{node.name}', which is not held "
                              f"in storage", statement.loc)
            path.reverse()
            typ = self.cell_types[node.name]
            for step in path:
                self.layout(typ, statement.loc)
                typ = dict(self.structs[typ])[step]
            value, _typ, block = self.expression(statement.value, block, env)
            self.emit(block, ir.FieldStore(slot, tuple(path), value))
            return block
        if isinstance(statement, IndexAssign):
            if statement.name not in env:
                raise Refused(f"the variable '{statement.name}'", statement.loc)
            base, base_type = env[statement.name]
            self.check_element(base_type, statement.loc)
            index, index_type, block = self.expression(statement.index, block, env)
            if index_type != "i64":
                raise Refused(f"an index of type {index_type}", statement.loc)
            # The index is checked before the value is evaluated, which is the
            # language's order: `a[1] = mark()` must not call `mark` when 1 is out
            # of range. The store checks again, which costs one compare and keeps
            # `index store` a checked instruction even when it stands alone.
            self.emit(block, ir.CheckIndex(base_type, base, index))
            value, value_type, block = self.expression(statement.value, block, env)
            if value_type != element_of(base_type):
                raise Refused(f"a {value_type} into an array of "
                              f"{element_of(base_type)}", statement.loc)
            self.emit(block, ir.IndexStore(base, index, value, base_type))
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
        if isinstance(statement, Match):
            return self.match_statement(statement, block, env)
        if isinstance(statement, Return):
            if statement.value is None:
                self.leave(block, 0, env)
                self.emit(block, ir.Ret(None))
                return block
            value, _typ, block = self.expression(statement.value, block, env)
            # The value is computed before the blocks are left, because a release
            # may free the array it was read from.
            self.leave(block, 0, env)
            self.emit(block, ir.Ret(value))
            return block
        if isinstance(statement, Break):
            loop = self.find_loop(statement.label, statement.loc)
            self.leave(block, loop.frame, env)
            self.emit(block,
                ir.Jump(loop.exit_label, [env[name][0] for name in loop.names]))
            return block
        if isinstance(statement, Continue):
            loop = self.find_loop(statement.label, statement.loc)
            if loop.back_edge is None:
                raise Refused("a continue in a loop without a next entry", statement.loc)
            self.leave(block, loop.frame, env)
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

    def propagate_let(self, statement: Let, block: ir.Block, env: dict) -> ir.Block:
        """`let x = f()?`: test the failure tag, leave the function, bind the payload.

        The checker has already proved that the callee answers an `Option`/`Result`
        whose *failure* type is the one this function returns, so the failure path is a
        `return` of the value itself. The success path is one payload read: the first
        variant's first field. Both are stated in the IR, so both backends answer the
        same question the same way — and the failure path leaves its scopes first, which
        is what runs the `defer` bodies and gives back the arrays.
        """
        node: Try = statement.value
        value, typ, block = self.expression(node.inner, block, env)
        variants = self.enum_layout(typ, statement.loc)
        if len(variants) < 2:
            raise Refused(f"a '?' on '{typ}', which has no failure variant", statement.loc)
        returns = self.function.returns
        if typ != returns:
            raise Refused(
                f"a '?' that forwards {typ} into a function returning {returns}: the "
                f"two instantiations are different records", statement.loc)
        success_name, success_types = variants[0]
        if not success_types:
            raise Refused(f"a '?' on '{typ}', whose success variant carries nothing",
                          statement.loc)
        ok_block = self.block("try_ok")
        fail_block = self.block("try_fail")
        tag = self.value()
        self.emit(block, ir.Tag(tag, value, typ))
        constant = self.value()
        self.emit(block, ir.Const(constant, "i64", len(variants) - 1))
        condition = self.value()
        self.emit(block, ir.Cmp(condition, "eq", tag, constant, "i64"))
        self.emit(block, ir.Branch(condition, fail_block.label, [], ok_block.label, []))
        # The failure is forwarded as it is: the checker proved the payload types match,
        # and an enum's record is its tag and its payload words whichever instance it is.
        self.leave(fail_block, 0, env)
        self.emit(fail_block, ir.Ret(value))
        payload_type = success_types[0]
        payload = self.value()
        self.emit(ok_block, ir.Payload(payload, value, typ, success_name, 0, payload_type))
        env[statement.name] = (payload, payload_type)
        return ok_block

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
        tail = self.body(statement.then_body, then_block, then_env)
        then_leaves = tail.terminator is not None
        if not then_leaves:
            self.emit(tail, ir.Jump(join.label,
                                             [then_env[name][0] for name in names]))
        else_leaves = False
        if else_block is not None:
            else_env = {**env, **{name: (else_block.params[index][0], env[name][1])
                                  for index, name in enumerate(names)}}
            tail = self.body(statement.else_body, else_block, else_env)
            else_leaves = tail.terminator is not None
            if not else_leaves:
                self.emit(tail, ir.Jump(join.label,
                                                 [else_env[name][0] for name in names]))
        for index, name in enumerate(names):
            env[name] = (join.params[index][0], env[name][1])
        if then_leaves and else_leaves:
            # Neither half falls through, so nothing arrives at the join: saying so
            # is what lets a function whose every path returns lower at all.
            self.emit(join, ir.Unreachable())
        return join

    def match_statement(self, statement: Match, block: ir.Block, env: dict) -> ir.Block:
        """`match` is a chain of tag comparisons, one arm per tag.

        The checker has already proved the arms cover every variant, so the last arm
        needs no test; a `_` arm becomes the default and tests nothing. Every arm jumps
        to one join, which carries the locals the arms assign — the same block-parameter
        join an `if` uses, because a `match` is a choice between paths and the values
        have to meet somewhere.
        """
        subject, subject_type, block = self.expression(statement.value, block, env)
        variants = self.enum_layout(subject_type, statement.loc)
        tags = {name: index for index, (name, _types) in enumerate(variants)}
        names = sorted({name for arm in statement.arms
                        for name in assigned_names(arm.body)} & set(env))
        tag_value = self.value()
        self.emit(block, ir.Tag(tag_value, subject, subject_type))
        join = self.block("match_join",
                          [(self.value(), env[name][1]) for name in names])
        wildcard = None
        tested: dict[str, ir.Block] = {}
        #: Whether each arm ended in a terminator, so the join can be told it is dead.
        leaves: list[bool] = []
        for arm in statement.arms:
            if arm.variant is None:
                wildcard = arm
                continue
            if arm.variant not in tags:
                raise Refused(f"the variant '{subject_type}::{arm.variant}'", arm.loc)
            arm_block = self.block("match_arm",
                                   [(self.value(), env[name][1]) for name in names])
            tested[arm.variant] = arm_block
            following = self.block("match_next")
            constant = self.value()
            self.emit(block, ir.Const(constant, "i64", tags[arm.variant]))
            condition = self.value()
            self.emit(block, ir.Cmp(condition, "eq", tag_value, constant, "i64"))
            self.emit(block, ir.Branch(condition, arm_block.label,
                                       [env[name][0] for name in names],
                                       following.label, []))
            leaves.append(self.match_arm(arm, subject, subject_type, arm_block, env,
                                         names, join))
            block = following
        if not any(arm_block for arm_block in tested.values()):
            raise Refused("a match with no arm", statement.loc)
        if wildcard is not None:
            wildcard_block = self.block("match_wild",
                                        [(self.value(), env[name][1]) for name in names])
            self.emit(block, ir.Jump(wildcard_block.label,
                                     [env[name][0] for name in names]))
            leaves.append(self.match_arm(wildcard, subject, subject_type,
                                         wildcard_block, env, names, join))
        else:
            # Every variant has an arm, so this block is unreachable: the checker
            # proved it. It still has to go somewhere, and the join is where a value
            # would belong — an arm that falls through arrives there too.
            self.emit(block, ir.Jump(join.label, [env[name][0] for name in names]))
        for index, name in enumerate(names):
            env[name] = (join.params[index][0], env[name][1])
        if all(leaves):
            self.emit(join, ir.Unreachable())
        return join

    def match_arm(self, arm, subject: str, subject_type: str, arm_block: ir.Block,
                  env: dict, names: list, join: ir.Block) -> None:
        """One arm: its bindings are payload reads, its body a block that joins."""
        arm_env = {**env, **{name: (arm_block.params[index][0], env[name][1])
                             for index, name in enumerate(names)}}
        choice = arm.choice
        if choice is not None:
            for index, name in enumerate(arm.bindings):
                payload_type = choice.types[index]
                value = self.value()
                self.emit(arm_block, ir.Payload(value, subject, subject_type,
                                                choice.name, index, payload_type))
                arm_env[name] = (value, payload_type)
        self.loc = arm.loc
        tail = self.body(arm.body, arm_block, arm_env)
        if tail.terminator is not None:
            return True
        self.emit(tail, ir.Jump(join.label, [arm_env[name][0] for name in names]))
        return False

    def while_statement(self, statement: While, block: ir.Block, env: dict) -> ir.Block:
        names = [name for name in sorted(assigned_names(statement.body)) if name in env]
        header = self.block("while_head", [(self.value(), env[name][1]) for name in names])
        exit_block = self.block("while_exit", [(self.value(), env[name][1]) for name in names])
        body_block = self.block("while_body")
        self.emit(block, ir.Jump(header.label, [env[name][0] for name in names]))
        header_env = {**env, **{name: (header.params[index][0], env[name][1])
                                for index, name in enumerate(names)}}
        condition, condition_type, test = self.expression(statement.condition, header,
                                                          header_env)
        if condition_type != "bool":
            raise Refused(f"a while on a {condition_type}", statement.loc)
        # The exit edge carries the values the header holds at the test: leaving a
        # loop normally is not a `break`, and the exit block is what the code after the
        # loop reads.
        #
        # The branch is emitted where the condition *ended*, which for `&&` and `||`
        # is a join block after the header — while the back edge still goes to the
        # header. Conflating the two made a `while a && b` loop forever on the native
        # backend as soon as `a` was false on the second turn: the back edge landed in
        # the join and handed a number to its `bool` parameter.
        self.emit(test, ir.Branch(
            condition, body_block.label, [], exit_block.label,
            [header_env[name][0] for name in names]))

        def back_edge(builder, current: ir.Block, current_env: dict) -> ir.Block:
            self.emit(current,
                ir.Jump(header.label, [current_env[name][0] for name in names]))
            return current

        loop = Loop(exit_block.label, names, statement.label, back_edge,
                    frame=len(self.frames))
        self.loops.append(loop)
        if statement.label:
            self.labels[statement.label] = loop
        try:
            body_env = dict(header_env)
            tail = self.body(statement.body, body_block, body_env)
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
        if not isinstance(statement.iterable, Range):
            return self.element_loop(statement, block, env)
        return self.range_loop(statement, block, env)

    def element_loop(self, statement: For, block: ir.Block, env: dict) -> ir.Block:
        """`for x in array`, `in vec`, `in map`, `in set`.

        The index is a block parameter of the header, exactly as in a range loop,
        and the element is one `index` instruction in the body — so a `continue`
        inside the loop is an early back edge like any other, and a `break` leaves
        through the same exit block.

        A table is walked over its *storage* instead: the bound is `slots` and a
        position is skipped unless `live` says it holds an entry, which is the same
        order the C backend's `for` over `slots[index].state` visits and therefore
        the same output.
        """
        base, base_type, block = self.expression(statement.iterable, block, env)
        if not (is_sequence(base_type) or is_table(base_type)):
            raise Refused("a 'for' over anything but a range, a sequence or a table",
                          statement.loc)
        self.check_element(base_type, statement.loc)
        if is_table(base_type):
            return self.table_loop(statement, block, env, base, base_type)
        element = container_element(base_type)
        length = self.value()
        self.emit(block, ir.Length(length, base))
        zero = self.value()
        self.emit(block, ir.Const(zero, "i64", 0))
        names = [name for name in sorted(assigned_names(statement.body)) if name in env]
        header = self.block("for_head", [(self.value(), "i64")]
                            + [(self.value(), env[name][1]) for name in names])
        exit_block = self.block("for_exit", [(self.value(), env[name][1]) for name in names])
        body_block = self.block("for_body")
        self.emit(block, ir.Jump(header.label, [zero, *[env[name][0] for name in names]]))
        header_env = dict(env)
        for index, name in enumerate(names):
            header_env[name] = (header.params[index + 1][0], env[name][1])
        condition = self.value()
        self.emit(header, ir.Cmp(condition, "lt", header.params[0][0], length))
        self.emit(header, ir.Branch(condition, body_block.label, [],
                                    exit_block.label,
                                    [header_env[name][0] for name in names]))
        element_value = self.value()
        self.emit(body_block, ir.Index(element_value, element, base, header.params[0][0]))
        body_env = {**header_env, statement.name: (element_value, element)}

        def back_edge(builder, current: ir.Block, current_env: dict) -> ir.Block:
            one = builder.value()
            self.emit(current, ir.Const(one, "i64", 1))
            next_index = builder.value()
            self.emit(current, ir.Bin(next_index, "add", header.params[0][0], one))
            self.emit(current, ir.Jump(
                header.label, [next_index, *[current_env[name][0] for name in names]]))
            return current

        loop = Loop(exit_block.label, names, statement.label, back_edge,
                    frame=len(self.frames))
        self.loops.append(loop)
        if statement.label:
            self.labels[statement.label] = loop
        try:
            tail = self.body(statement.body, body_block, body_env)
            if tail.terminator is None:
                back_edge(self, tail, body_env)
        finally:
            self.loops.pop()
            if statement.label:
                self.labels.pop(statement.label, None)
        for index, name in enumerate(names):
            env[name] = (exit_block.params[index][0], env[name][1])
        return exit_block

    def table_loop(self, statement: For, block: ir.Block, env: dict,
                   base: str, base_type: str) -> ir.Block:
        """`for key in map`, `for x in set`: every live position, in slot order.

        The header carries the position, and the body starts with a `live` test that
        jumps straight back to the header — which is what makes a dead slot cost one
        compare instead of a branch out of the loop, and what makes the order the
        table's own rather than a sorted one.
        """
        key_type = map_parts(base_type)[0] if is_map(base_type) else set_element(base_type)
        slots = self.value()
        self.emit(block, ir.Slots(slots, base))
        zero = self.value()
        self.emit(block, ir.Const(zero, "i64", 0))
        names = [name for name in sorted(assigned_names(statement.body)) if name in env]
        header = self.block("for_head", [(self.value(), "i64")]
                            + [(self.value(), env[name][1]) for name in names])
        advance = self.block("for_next")
        exit_block = self.block("for_exit", [(self.value(), env[name][1]) for name in names])
        body_block = self.block("for_body")
        self.emit(block, ir.Jump(header.label, [zero, *[env[name][0] for name in names]]))
        header_env = dict(env)
        for index, name in enumerate(names):
            header_env[name] = (header.params[index + 1][0], env[name][1])
        condition = self.value()
        self.emit(header, ir.Cmp(condition, "lt", header.params[0][0], slots))
        self.emit(header, ir.Branch(condition, advance.label, [],
                                    exit_block.label,
                                    [header_env[name][0] for name in names]))
        # A dead position is not an iteration: the loop moves to the next one, which
        # is the same step the end of the body takes.
        bump = self.block("for_bump")
        live = self.value()
        self.emit(advance, ir.Live(live, base, header.params[0][0]))
        self.emit(advance, ir.Branch(live, body_block.label, [], bump.label, []))
        one = self.value()
        self.emit(bump, ir.Const(one, "i64", 1))
        stepped = self.value()
        self.emit(bump, ir.Bin(stepped, "add", header.params[0][0], one))
        self.emit(bump, ir.Jump(header.label,
                                [stepped, *[header_env[name][0] for name in names]]))
        element_value = self.value()
        self.emit(body_block, ir.KeyAt(element_value, base, header.params[0][0], key_type))
        body_env = {**header_env, statement.name: (element_value, key_type)}

        def back_edge(builder, current: ir.Block, current_env: dict) -> ir.Block:
            one = builder.value()
            self.emit(current, ir.Const(one, "i64", 1))
            next_index = builder.value()
            self.emit(current, ir.Bin(next_index, "add", header.params[0][0], one))
            self.emit(current, ir.Jump(
                header.label, [next_index, *[current_env[name][0] for name in names]]))
            return current

        step = back_edge
        loop = Loop(exit_block.label, names, statement.label, step,
                    frame=len(self.frames))
        self.loops.append(loop)
        if statement.label:
            self.labels[statement.label] = loop
        try:
            tail = self.body(statement.body, body_block, body_env)
            if tail.terminator is None:
                back_edge(self, tail, body_env)
        finally:
            self.loops.pop()
            if statement.label:
                self.labels.pop(statement.label, None)
        for index, name in enumerate(names):
            env[name] = (exit_block.params[index][0], env[name][1])
        return exit_block

    def range_loop(self, statement: For, block: ir.Block, env: dict) -> ir.Block:
        """`for i in a..b` — the index is a block parameter of the header.

        The header receives the index from the entry block and from every back edge,
        so the loop variable is one SSA value and a `continue` is just an early back
        edge that computes the next index where it stands.
        """
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

        loop = Loop(exit_block.label, names, statement.label, back_edge,
                    frame=len(self.frames))
        self.loops.append(loop)
        if statement.label:
            self.labels[statement.label] = loop
        try:
            body_env = dict(header_env)
            tail = self.body(statement.body, body_block, body_env)
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
        if isinstance(node, Variable) and node.function is not None:
            # A function named as a value is reached too: the call that will run it
            # is a call through a register, which this walk cannot see into. A
            # function the program hands to `test::run` and never calls by name is
            # compiled for the same reason a called one is — it is what the program
            # asked for.
            found.append(node.function)
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
    reached = reachable(program)
    check_instantiations(reached)
    layouts = struct_layouts(program)
    enum_tables = enum_layouts(program)
    for function in reached:
        if function.type_params:
            raise make(
                "E0901",
                f"the native backend does not implement the generic function "
                f"'{function.qualified}'",
                function.loc,
                hint="build with the C backend (the default): `nava build` without "
                     "--backend native",
            )
        builder = Builder(function, layouts, enum_tables)
        entry = builder.block("entry")
        env: dict[str, tuple[str, str]] = {}
        for param in function.params:
            value = builder.value()
            builder.params.append((value, param.typ))
            if builder.is_struct(param.typ):
                # A struct parameter is a copy of the caller's value, and its fields
                # can be written, so it lives in a cell like a local does.
                slot = builder.declare_slot(param.name, param.typ)
                builder.emit(entry, ir.SlotStore(slot, value))
            else:
                env[param.name] = (value, param.typ)
        try:
            tail = builder.body(function.body, entry, env)
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
            module=function.module, slots=builder.slots, lines=builder.lines,
            decl_line=builder.decl_line))
    # Only the layouts a lowered instruction named: a struct the program declares and
    # never builds is a declaration the backend has no reason to lay out.
    used: dict[str, tuple] = {}
    for function in functions:
        for block in function.blocks:
            for instruction in block.instructions:
                for name in (getattr(instruction, "typ", None),
                             getattr(instruction, "base_type", None)):
                    if name in layouts:
                        used[name] = layouts[name]
    for name in list(used):
        for _field, typ in used[name]:
            if typ in layouts:
                used[typ] = layouts[typ]
    # The same rule for enums: a type the program never builds and never matches is
    # a declaration with no value behind it.
    used_enums: dict[str, tuple] = {}
    for function in functions:
        for block in function.blocks:
            for instruction in block.instructions:
                # A builtin that answers an enum names it as the type of the value it
                # defines (`map_get` answers `Option(V)`), so the type is an enum a
                # value has even when nothing matches on it here.
                for name in (getattr(instruction, "enum", None),
                             getattr(instruction, "typ", None)):
                    if name in enum_tables:
                        used_enums[name] = enum_tables[name]
    # A container is stated when an instruction names one: a `Vec(i64)` the program
    # only declared would be a family of helpers nobody calls, and the backend has no
    # reason to emit one.
    used_vectors: list[str] = []
    used_tables: list[str] = []
    for function in functions:
        for block in function.blocks:
            for instruction in block.instructions:
                for name in (getattr(instruction, "typ", None),
                             getattr(instruction, "base_type", None)):
                    if not isinstance(name, str):
                        continue
                    if is_vec(name) and name not in used_vectors:
                        used_vectors.append(name)
                    elif is_table(name) and name not in used_tables:
                        used_tables.append(name)
    return ir.Module(functions, structs=used, enums=used_enums,
                     vectors=used_vectors, tables=used_tables)
