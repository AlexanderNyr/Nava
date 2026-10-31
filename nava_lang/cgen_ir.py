"""The C backend as a lowering of the shared IR (PLAN.md N14d).

Until this increment `cgen.py` was the compiler's only backend, so it walked the
checker's AST and printed C. With two backends that shape is expensive: the
differential compares two *lowerings of an AST* rather than two *readers of one
program*, and a construct added to the language has to be invented twice — once in
the C generator and once in `native/lower.py`.

This module retires the AST walk for everything the IR can express. It prints C from
`nava_lang/native/ir.py`: a block is a label and a `goto`, a value is a local, and an
instruction becomes the runtime call the language says it is — `index` goes through
`nv_arr_i64_get`, which bounds-checks, and `release` through `nv_arr_i64_free`, so the
C backend's memory discipline does not depend on how the program got here.

Four decisions worth stating:

* **The subset is checked, not assumed.** `supports()` names what the printer can
  print; anything else — an instruction it does not implement, a value type it cannot
  spell, a comparison of two `str` values, which would compare pointers — sends the
  program down the old path. `tests/test_cgen_ir.py` asserts both directions of that
  boundary, so neither a silent fallback nor a silent use can pass unnoticed.
* **One instruction per line, one value per local.** Every value is a declared C local
  with a fixed name, and no expression is nested: the emitted C says what the IR says,
  in the order the IR says it. That is what makes the two backends comparable.
* **Function names are the ones the AST path used** — `nv_fn_<ordinal>` — so the C
  artefact does not change its vocabulary when it changes its producer, and a symbol in
  a crash report means the same thing either way.
* **The body says where it came from.** `nava emit-c` prints a comment naming the
  translation unit as a lowering of the IR, and one comment per function naming the
  link-time symbol, so a reader of the artefact can tell which path produced it without
  reading the compiler.
* **A function value is printed with the program's own typedefs.** C has no type
  expression for a pointer to a function, only a typedef, and the names of those
  typedefs are the checked program's (`cgen.function_types`) — the same table the AST
  path uses. The table travels with the module into `supports()` and into the printer,
  so a program that names a function as a value has one C type for it whichever path
  printed the file, and a program whose function types this printer was not given is
  declined rather than printed with a guess.
"""
from __future__ import annotations

from .cgen import c_string, function_types, map_tag, set_tag, vec_tag
from .checker import mangle
from .model import (is_function_type, is_map, is_set, is_string, is_vec, map_parts,
                    set_element, vec_element)
from .native import ir

#: The C spelling of a scalar IR type. `void` is here for a function that returns
#: nothing, which is a signature and never a value.
SCALAR_TYPES = {"i64": "int64_t", "bool": "bool", "f64": "double",
                "str": "const char *", "void": "void"}
#: The C name of a struct type. The declarations come from the program, and the AST
#: path names them the same way, so a struct is one type in the artefact whichever
#: path printed it. A type written with an argument — `Pair(i64)` — is a name the
#: printer does not spell: `spellable()` says so and the AST path prints the module.
STRUCT_PREFIX = "nv_struct_"
#: The C name of an enum type is the checker's: `Shape` -> `nv_enum_Shape`, and an
#: instantiation goes through the same mangle (`Result(i64, str)` ->
#: `nv_enum_Result_i64_str`). One rule, imported rather than copied, so the printer
#: and the checker cannot disagree about the name of a type.
ENUM_PREFIX = "nv_enum_"
#: The element names the C runtime knows: `nv_arr_<element>_*`, one set per type.
ELEMENT_NAMES = {"i64": "int64_t", "bool": "bool", "str": "const char *"}
#: Checked arithmetic is the runtime's, so overflow traps where the C backend has
#: always trapped it and with the message it has always printed.
BINARY_HELPERS = {"add": "nv_add", "sub": "nv_sub", "mul": "nv_mul",
                  "div": "nv_div", "mod": "nv_rem"}
#: Float arithmetic is the machine's too: `+` on two `double`s is IEEE and traps
#: nothing, which is the language's rule for `f64` and the reason it needs no helper.
FLOAT_OPERATORS = {"add": "+", "sub": "-", "mul": "*", "div": "/"}
#: Comparisons are the machine's: an `i64` or a `bool` compares by value.
COMPARE_OPERATORS = {"eq": "==", "ne": "!=", "lt": "<", "le": "<=", "gt": ">", "ge": ">="}
#: The builtins whose C name depends on the type of their first argument: the vector,
#: map and set families are generated per element type, so `vec_push` is
#: `nv_vec_i64_push` for a `Vec(i64)` and `nv_vec_str_push` for a `Vec(str)`. The
#: argument count is stated here and the helper name is derived from the value's type
#: in `container_builtin`.
CONTAINER_BUILTINS = {
    "vec_new": 0, "vec_len": 1, "vec_capacity": 1, "vec_push": 2, "vec_pop": 1,
    "vec_get": 2, "vec_set": 3,
    "map_new": 0, "map_len": 1, "map_contains": 2, "map_get": 2, "map_set": 3,
    "map_remove": 2, "map_clear": 1,
    "set_new": 0, "set_len": 1, "set_contains": 2, "set_add": 2, "set_remove": 2,
    "set_clear": 1,
}
#: The container builtins that answer nothing: they are statements, not values.
#: The builtins that answer nothing: they are statements, not values. `str_split`
#: is one of them — the pieces go into the vector it was handed, so what it answers
#: is nothing and what it changes is that vector.
CONTAINER_STATEMENTS = {"vec_push", "vec_set", "map_set", "map_clear", "set_add",
                        "set_clear", "str_split"}
#: The builtins the C printer can print: an IR `builtin` name -> the runtime function
#: that implements it, with how many arguments it takes. The native backend has its own
#: list, and a builtin that is on only one list is a gap in one backend — which is a
#: refusal with the builtin's name, not a silent difference.
BUILTIN_HELPERS = {
    "bytes_len": ("nv_bytes_len", 1),
    "byte_at": ("nv_byte_at", 2),
    "str_concat": ("nv_str_concat", 2),
    "str_slice": ("nv_str_slice", 3),
    "str_from_i64": ("nv_str_from_i64", 1),
    "to_i64": ("nv_to_i64", 1),
    "to_f64": ("nv_to_f64", 1),
    "read_i64": ("nv_read_i64", 0),
    "clock_seconds": ("nv_clock_seconds", 0),
    "clock_millis": ("nv_clock_millis", 0),
    "clock_monotonic_millis": ("nv_clock_monotonic_millis", 0),
    "arg_count": ("nv_arg_count", 0),
    "arg_at": ("nv_arg_at", 1),
    "str_split": ("nv_str_split", 3),
}
#: `print` of a value, per type. The three helpers print exactly what the native
#: backend's three writers print, which is what the differential compares.
#: A printed value, by type. An `f64` prints through the same `nv_format_f64` the
#: AST walk calls: the shortest decimal that reads back as the same double.
PRINT_HELPERS = {"i64": "nv_print_i64", "bool": "nv_print_bool", "str": "nv_print_str",
                 "f64": "nv_print_f64"}
#: What this printer implements. A program that lowers to anything else is printed by
#: the AST walk instead, and `supports()` is where that decision is made.
SUPPORTED = (
    ir.Const, ir.Str, ir.Bin, ir.Neg, ir.Not, ir.Cmp, ir.TextCompare, ir.Copy, ir.Call,
    ir.Print, ir.ArrayLiteral, ir.ArrayZeros, ir.Index, ir.CheckIndex, ir.IndexStore,
    ir.Length, ir.Release, ir.SlotLoad, ir.SlotStore, ir.StructNew, ir.Field,
    ir.FieldStore, ir.Builtin, ir.Variant, ir.Tag, ir.Payload, ir.Slice, ir.Jump,
    ir.Branch, ir.Ret, ir.Unreachable, ir.Slots, ir.Live, ir.KeyAt, ir.FuncRef,
)
#: The `String` builtins. A builder is not a container, but it grows like one and
#: the two questions about its size are the same shape of question.
STRING_BUILTINS = {"string_new": 0, "string_len": 1, "string_capacity": 1,
                   "string_push": 2, "string_push_byte": 2, "string_clear": 1,
                   "string_text": 1}
#: The compound-literal element types a strided initialiser can take.
INITIALISER_TYPES = {"i64": "const int64_t[]", "bool": "const bool[]",
                     "str": "const char *const[]"}
I64_MIN = -(2 ** 63)


def element_of(typ: str) -> str | None:
    """`[i64]` → `i64`; anything that is not an array of one word → None."""
    if typ.startswith("[") and typ.endswith("]"):
        element = typ[1:-1]
        return element if element in ELEMENT_NAMES else None
    return None


def array_element(typ: str, structs: dict | None = None) -> str | None:
    """`[Point]` -> the C tag of its elements (`Point`), or None if unspellable.

    An array's C type is `nv_arr_<tag>`, and the tag is the element's own name — a
    scalar's (`i64`, `bool`, `str`) or a struct's (`Point`). The C backend builds the
    same name from the same rule (`cgen.array_tag`), so a helper name in a crash report
    means the same thing whichever path printed the file.
    """
    if not (typ.startswith("[") and typ.endswith("]")):
        return None
    element = typ[1:-1].strip()
    if element in ELEMENT_NAMES:
        return element
    if is_struct(element, structs):
        return element
    return None


def c_type(typ: str, structs: dict | None = None,
           enums: dict | None = None, fnptr: dict | None = None) -> str:
    """The C type of an IR value, or a KeyError naming the type nobody can spell."""
    if typ in SCALAR_TYPES:
        return SCALAR_TYPES[typ]
    element = array_element(typ, structs)
    if element is not None:
        return f"nv_arr_{element}"
    if is_struct(typ, structs):
        return STRUCT_PREFIX + typ
    if enums is not None and typ in enums:
        return ENUM_PREFIX + mangle(typ)
    # A container is a pointer to its own handle type, which the generated family
    # declares; the printer only has to name it.
    if is_vec(typ):
        return f"nv_vec_{vec_tag(typ)} *"
    if is_map(typ):
        return f"nv_map_{map_tag(typ)} *"
    if is_set(typ):
        return f"nv_set_{set_tag(typ)} *"
    if is_string(typ):
        # The builder is a pointer: `string_push` may replace its bytes, and every
        # holder has to see the same builder rather than a copy of it.
        return "nv_string *"
    pointer = fnptr.get(typ) if fnptr else None
    if pointer is not None:
        return pointer
    raise KeyError(typ)


def is_struct(typ: str, structs: dict | None = None) -> bool:
    """Whether `typ` is a struct type this printer can name.

    An identifier is spellable; `Pair(i64)` is not, because the C name of an
    instantiation is mangled by the checker and the IR does not carry the mangle.
    """
    return structs is not None and typ in structs and typ.isidentifier()


def spellable(typ: str, structs: dict | None = None,
              enums: dict | None = None, fnptr: dict | None = None) -> bool:
    """Whether `c_type` can name this type."""
    if typ in SCALAR_TYPES or array_element(typ, structs) is not None:
        return True
    if is_struct(typ, structs) or (enums is not None and typ in enums):
        return True
    if is_vec(typ):
        return spellable(vec_element(typ), structs, enums)
    if is_map(typ):
        key, value = map_parts(typ)
        return spellable(key, structs, enums) and spellable(value, structs, enums)
    if is_set(typ):
        return spellable(set_element(typ), structs, enums, fnptr)
    if is_string(typ):
        # `nv_string` is declared by the runtime block a `String` builtin pulls in:
        # the body names `nv_string_new` and the closure keeps the whole unit.
        return True
    return bool(fnptr and is_function_type(typ) and typ in fnptr)


def variable(value: str) -> str:
    """The C local of an IR value: `%3` → `v3`."""
    return f"v{value}"


def cell(slot: str) -> str:
    """The C local of a slot: `%c0` → `c0`. A cell and a value never share a name."""
    return slot


def constant(payload, typ: str) -> str:
    """A constant the C compiler will not read as a different number."""
    if typ == "bool":
        return "true" if payload else "false"
    if typ == "f64":
        # A hexadecimal float is exact: no decimal rounding happens in the artefact.
        return f"({float.hex(float(payload))})"
    number = int(payload)
    if number == I64_MIN:
        # `INT64_C(-9223372036854775808)` is a negated constant that does not fit.
        return "INT64_MIN"
    return f"INT64_C({number})"


def _is_container(typ: str | None) -> bool:
    """Is this a handle — a vector, a map or a set — rather than a value?"""
    return bool(typ) and (is_vec(typ) or is_map(typ) or is_set(typ))


def container_prefix(name: str, typ: str | None) -> str | None:
    """`vec_push` on a `Vec(i64)` -> `nv_vec_i64`: the family the call belongs to.

    A vector, a map and a set are three families of generated helpers, one set per
    element type, so the name of the function a builtin calls is a fact about the
    *type of the value* — which the IR states, because the value came from somewhere
    that had to name it.
    """
    if typ is None:
        return None
    if name.startswith("vec_") and is_vec(typ):
        return f"nv_vec_{vec_tag(typ)}"
    if name.startswith("map_") and is_map(typ):
        return f"nv_map_{map_tag(typ)}"
    if name.startswith("set_") and is_set(typ):
        return f"nv_set_{set_tag(typ)}"
    return None


def container_element_type(typ: str) -> str | None:
    """The element type of a `Vec`, or the key type of a map or a set."""
    if is_vec(typ):
        return vec_element(typ)
    if is_map(typ):
        return map_parts(typ)[0]
    if is_set(typ):
        return set_element(typ)
    return None


def value_types(function: ir.Function) -> dict[str, str]:
    """The type of every value a function defines: a fact about the IR, kept there."""
    return ir.value_types(function)


def supports(module: ir.Module, fnptr: dict | None = None) -> bool:
    """Whether this printer can print the whole module, and print it correctly.

    `fnptr` is the program's function-type table (`cgen.function_types`): the
    typedef names a function value is printed with. Without it a function value
    has no C type this printer can spell, and a program that has one is declined
    rather than printed with a guess at its type.
    """
    return unprintable(module, fnptr) is None


def unprintable(module: ir.Module, fnptr: dict | None = None) -> str | None:
    """What this printer cannot print about `module`, or None if it prints it all.

    `supports` answers yes or no; this answers *which thing*, so that a program the
    lowering accepts but this printer declines has a named reason instead of a
    silence. The reason is what the next piece of work is.
    """
    structs = module.structs
    enums = module.enums
    for function in module.functions:
        if not spellable(function.returns, structs, enums, fnptr):
            return f"the return type of '{function.name}' ({function.returns})"
        for name, typ in function.params:
            if not spellable(typ, structs, enums, fnptr):
                return f"the type of parameter '{name}' of '{function.name}' ({typ})"
        for cell in function.slots:
            if not spellable(cell.typ, structs, enums, fnptr):
                return f"the type of a cell of '{function.name}' ({cell.typ})"
        types = value_types(function)
        for value, typ in types.items():
            if not spellable(typ, structs, enums, fnptr):
                return f"the type of {value} in '{function.name}' ({typ})"
        for block in function.blocks:
            for instruction in block.instructions:
                if not isinstance(instruction, SUPPORTED):
                    return (f"the instruction '{type(instruction).__name__}' in "
                            f"'{function.name}'")
                if not instruction_supported(instruction, types, structs, enums, fnptr):
                    return (f"the instruction '{type(instruction).__name__}' in "
                            f"'{function.name}' ({instruction})")
    return None


def instruction_supported(instruction, types: dict[str, str],  # noqa: C901
                          structs: dict | None = None,
                          enums: dict | None = None,
                          fnptr: dict | None = None) -> bool:
    """The per-instruction half of `supports()`: types this printer must not guess."""
    if isinstance(instruction, ir.Cmp):
        # Two `str` values compare by pointer in C, so a comparison of strings is never
        # a `Cmp`: the lowering states it as `TextCompare`, which prints as `strcmp`.
        return types.get(instruction.left) != ir.STRING_TYPE
    if isinstance(instruction, ir.Builtin):
        arity = CONTAINER_BUILTINS.get(instruction.name)
        if arity is not None:
            if arity != len(instruction.args):
                return False
            if arity == 0:
                # A constructor states the container in its own type.
                return spellable(instruction.typ, structs, enums)
            return container_prefix(instruction.name, types.get(instruction.args[0])) \
                is not None
        arity = STRING_BUILTINS.get(instruction.name)
        if arity is not None:
            if arity != len(instruction.args):
                return False
            if arity == 0:
                # A constructor states the type it builds in its own result.
                return spellable(instruction.typ, structs, enums, fnptr)
            return is_string(types.get(instruction.args[0]))
        routine = BUILTIN_HELPERS.get(instruction.name)
        return routine is not None and routine[1] == len(instruction.args)
    if isinstance(instruction, ir.Bin):
        # `nv_add` and its siblings are the checked `i64` ones, so an `i64` sum is a
        # helper call. A float sum is `+` on two `double`s: nothing is checked, an
        # overflow is `inf` and the language says so, and that is what C does with `+`.
        return types.get(instruction.left) in ("i64", "f64")
    if isinstance(instruction, ir.Neg):
        return types.get(instruction.operand) in ("i64", "f64")
    if isinstance(instruction, ir.Print):
        return instruction.typ in PRINT_HELPERS
    if isinstance(instruction, (ir.ArrayLiteral, ir.ArrayZeros, ir.CheckIndex)):
        return array_element(instruction.typ, structs) is not None
    if isinstance(instruction, ir.Slice):
        return array_element(instruction.typ, structs) is not None
    if isinstance(instruction, ir.Index):
        # An `Index` states the *element* type: the array is what it reads from, and an
        # element may be a struct, whose C type is a struct value the array copies.
        return (instruction.typ in ELEMENT_NAMES or is_struct(instruction.typ, structs)
                or (enums is not None and instruction.typ in enums))
    if isinstance(instruction, (ir.Slots, ir.Live, ir.KeyAt)):
        # A table's storage is walked by position; the value has to be a table whose
        # family of helpers exists, which `spellable` already says for every type this
        # printer can name.
        return types.get(instruction.base) is not None
    if isinstance(instruction, ir.Release):
        return array_element(instruction.typ, structs) is not None
    if isinstance(instruction, (ir.Variant, ir.Tag, ir.Payload)):
        # The enum has to be in the module's table: a tag compared against a number
        # nobody declared is how a `match` silently picks the wrong arm.
        return enums is not None and instruction.enum in enums
    if isinstance(instruction, ir.Call):
        # A call answers whatever its callee returns, including a struct: the C type of
        # the value is the callee's return type, and both are spelled the same way.
        return spellable(instruction.returns, structs, enums, fnptr)
    if isinstance(instruction, ir.FuncRef):
        # A function value is a C function pointer, which C can only spell through a
        # typedef: no typedef for this exact signature, no C type for the variable.
        return spellable(instruction.typ, structs, enums, fnptr)
    return True


class Printer:
    """One program, printed: the names it uses and the functions it emits."""

    def __init__(self, module: ir.Module, names: dict[str, str] | None = None,
                 helpers: str = "", fnptr: dict[str, str] | None = None) -> None:
        self.module = module
        #: A function type -> the typedef it is printed as. The names are the
        #: checked program's (`cgen.function_types`), which is the same table the
        #: AST path uses, so a function value has one C type either way it is
        #: printed.
        self.fnptr = dict(fnptr or {})
        #: The per-array-type helpers, already printed: the IR calls them by name and
        #: the generated family is the same one the AST path uses.
        self.helpers = helpers
        #: A link-time name → the C function that implements it. `names` is the C
        #: backend's own table (`nv_fn_<ordinal>`, the numbering the checker gave each
        #: function), passed in so that a symbol in a crash report means the same thing
        #: whichever path printed the file. The fallback derives the name from the
        #: symbol, which is stable and says which module and function it belongs to.
        self.names = dict(names or {})
        for index, function in enumerate(module.functions):
            self.names.setdefault(ir.symbol(function), f"nv_fn_{index}")

    # -- the unit ------------------------------------------------------------
    def body(self) -> str:
        blocks = sum(len(function.blocks) for function in self.module.functions)
        lines = [f"/* A lowering of the shared IR: {len(self.module.functions)} "
                 f"function(s), {blocks} block(s). */", ""]
        lines.extend(self.enum_declarations())
        lines.extend(self.struct_declarations())
        if self.helpers:
            lines.extend(self.helpers.rstrip("\n").split("\n"))
            lines.append("")
        for function in self.module.functions:
            lines.append(self.signature(function) + ";")
        lines.append("")
        for function in self.module.functions:
            lines.extend(self.function_body(function))
            lines.append("")
        lines.extend(self.entry_wrapper())
        return "\n".join(lines) + "\n"

    def struct_declarations(self) -> list[str]:
        """The `typedef` of every struct the module lays out, dependencies first.

        The AST path emits these from the checker's declarations; a body printed from
        the IR carries the layouts it was given, so it declares the types it uses. A C
        struct is complete before it is a field type, which is what the order is for.
        """
        order: list[str] = []

        def visit(typ: str) -> None:
            if typ in order or typ not in self.module.structs:
                return
            for _name, field_type in self.module.structs[typ]:
                visit(field_type)
            order.append(typ)

        for typ in self.module.structs:
            visit(typ)
        lines: list[str] = []
        for typ in order:
            fields = self.module.structs[typ]
            name = STRUCT_PREFIX + typ
            if not fields:
                # C has no empty struct; one padding byte keeps the type usable.
                lines.append(f"typedef struct {name} {{ unsigned char _unused; }} {name};")
                continue
            lines.append(f"typedef struct {name} {{")
            for field, field_type in fields:
                lines.append(f"    {self.c_type(field_type)} {field};")
            lines.append(f"}} {name};")
        if lines:
            lines.append("")
        return lines

    def enum_declarations(self) -> list[str]:
        """The `typedef` of every enum the module lays out, in declaration order.

        A tag and a union of payload structs, which is the shape the AST path emits and
        the shape a `switch` on the tag reads. The tag is the variant's position, so
        the printer never has to name a variant to compare it. A unit-only enum still
        needs one byte: C has no empty struct, and a union needs a member.
        """
        lines: list[str] = []
        for typ, variants in self.module.enums.items():
            name = ENUM_PREFIX + mangle(typ)
            payloads = [(variant, types) for variant, types in variants if types]
            if not payloads:
                lines.append(f"typedef struct {name} {{ int32_t _tag; }} {name};")
                continue
            lines.append(f"typedef struct {name} {{")
            lines.append("    int32_t _tag;")
            lines.append("    union {")
            for variant, types in payloads:
                fields = " ".join(f"{self.c_type(payload_type)} _f{index};"
                                  for index, payload_type in enumerate(types))
                lines.append(f"        struct {{ {fields} }} _v_{variant};")
            lines.append("    } _payload;")
            lines.append(f"}} {name};")
        if lines:
            lines.append("")
        return lines

    def c_type(self, typ: str) -> str:
        return c_type(typ, self.module.structs, self.module.enums, self.fnptr)

    def signature(self, function: ir.Function) -> str:
        params = ", ".join(f"{self.c_type(typ)} {variable(name)}"
                           for name, typ in function.params)
        return f"static {self.c_type(function.returns)} {self.names[ir.symbol(function)]}" \
               f"({params or 'void'})"

    def entry_wrapper(self) -> list[str]:
        """`int main` calls the program's own `main` and returns it modulo 256.

        `argc`/`argv` are recorded exactly as the AST path records them: `argv[0]` is
        the program, not an argument, and the two globals are what `arg_count` and
        `arg_at` read.
        """
        entry = self.module.function(self.module.entry)
        lines = ["int main(int argc, char **argv) {",
                 "    nv_argc = argc > 0 ? (int64_t)argc - 1 : 0;",
                 "    nv_argv = argv + (argc > 0 ? 1 : 0);"]
        if entry is None or entry.returns == "void":
            if entry is not None:
                lines.append(f"    {self.names[ir.symbol(entry)]}();")
            lines.append("    return 0;")
        else:
            # Exit codes are defined modulo 256, including for negative values.
            lines.append(f"    return (int)(uint8_t){self.names[ir.symbol(entry)]}();")
        lines.append("}")
        return lines

    # -- one function --------------------------------------------------------
    def function_body(self, function: ir.Function) -> list[str]:
        types = value_types(function)
        parameters = {name for name, _typ in function.params}
        lines = [f"/* {ir.symbol(function)} */", self.signature(function) + " {"]
        for name in sorted(types, key=int):
            if name in parameters:
                continue
            lines.append(f"    {self.c_type(types[name])} {variable(name)};")
        for slot in function.slots:
            # A cell is storage with an identity: an ordinary C local, declared once
            # and assigned by name, which is what a struct field write needs.
            lines.append(f"    {self.c_type(slot.typ)} {cell(slot.name)};")
        lines.append(f"    goto {self.block_label(function, function.blocks[0].label)};")
        for block in function.blocks:
            lines.append(f"{self.block_label(function, block.label)}:")
            for instruction in block.instructions:
                lines.extend(self.instruction_lines(instruction, function, types))
        lines.append("}")
        return lines

    def variant_literal(self, instruction: ir.Variant) -> str:
        """`Shape::Circle(2)` as a C compound literal with the tag and the payload set.

        One literal for the whole value, so a variant cannot exist half-built: the tag
        and the fields it describes are written together, which is what makes a `match`
        on a value that came through a parameter safe.
        """
        name = ENUM_PREFIX + mangle(instruction.enum)
        arguments = ", ".join(variable(argument) for argument in instruction.args)
        payload = (f" ._payload = {{ ._v_{instruction.variant} = "
                   f"{{ {', '.join(f'._f{index} = {variable(argument)}' for index, argument in enumerate(instruction.args))} }} }},"
                   if instruction.args else "")
        return f"({name}){{ ._tag = {instruction.tag},{payload} }}"

    def table_line(self, instruction) -> str:
        """One line of a walk over a table's storage: its capacity, a slot's state, a key.

        The three are what a `for` over a map or a set is made of on this backend — the
        same three questions the C runtime's own loop asks, in the same order, which is
        why the two backends visit the entries in the same order.
        """
        base = variable(instruction.base)
        if isinstance(instruction, ir.Slots):
            return f"{variable(instruction.value)} = {base}->cap;"
        index = variable(instruction.index)
        if isinstance(instruction, ir.Live):
            return f"{variable(instruction.value)} = {base}->slots[{index}].state == 1;"
        return f"{variable(instruction.value)} = {base}->slots[{index}].key;"

    def container_call(self, instruction: ir.Builtin, types: dict[str, str]) -> str:
        """The C line of a vector, map or set builtin.

        The family comes from the type of the value, and the shape from the builtin: a
        constructor answers a new handle, a query answers a value, and a mutation is a
        statement. `len` and `capacity` read the handle's own fields — one load, no
        call — which is what the AST path prints too.
        """
        name = instruction.name
        arity = CONTAINER_BUILTINS[name]
        if arity == 0:
            prefix = (f"nv_vec_{vec_tag(instruction.typ)}" if name.startswith("vec_")
                      else f"nv_map_{map_tag(instruction.typ)}" if name.startswith("map_")
                      else f"nv_set_{set_tag(instruction.typ)}")
            return f"{variable(instruction.value)} = {prefix}_new();"
        container = variable(instruction.args[0])
        prefix = container_prefix(name, types.get(instruction.args[0]))
        assert prefix is not None, f"{name} on a value this printer cannot name"
        arguments = ", ".join(variable(argument) for argument in instruction.args[1:])
        if name in ("vec_len", "map_len", "set_len"):
            return f"{variable(instruction.value)} = {container}->len;"
        if name == "vec_capacity":
            return f"{variable(instruction.value)} = {container}->cap;"
        call = f"{prefix}_{name.split('_', 1)[1]}({container}"
        call += f", {arguments});" if arguments else ");"
        if name in CONTAINER_STATEMENTS:
            return call
        return f"{variable(instruction.value)} = {call};"

    def string_call(self, instruction: ir.Builtin) -> str:
        """The C line of a `String` builtin.

        The shape is the AST path's: a constructor answers a builder, a query
        answers a value, and a mutation is a statement. The names are the runtime's,
        so the identifier that pulls the `nv_string` block into the translation
        unit is the same one whichever path printed the call.
        """
        name = instruction.name
        if name == "string_new":
            return f"{variable(instruction.value)} = nv_string_new();"
        arguments = ", ".join(variable(argument)
                              for argument in instruction.args[1:])
        call = f"nv_string_{name[len('string_'):]}({variable(instruction.args[0])}"
        call += f", {arguments});" if arguments else ");"
        if instruction.typ == "void":
            return call
        return f"{variable(instruction.value)} = {call};"

    def block_label(self, function: ir.Function, label: str) -> str:
        """A block label qualified by its function: two functions may both say `entry`."""
        return f"{self.names[ir.symbol(function)]}_{label}"

    def edge_lines(self, function: ir.Function, target: str, args: list[str]) -> list[str]:
        """A jump's arguments become the target block's parameters, one each."""
        block = next(item for item in function.blocks if item.label == target)
        return [f"{variable(name)} = {variable(arg)};"
                for (name, _typ), arg in zip(block.params, args)]

    def call_name(self, target: str) -> str:
        """The C name of a called symbol; a symbol nobody lowered keeps its own name."""
        return self.names.get(target, target.replace(".", "__"))

    def instruction_lines(self, instruction, function: ir.Function,  # noqa: C901
                          types: dict[str, str]) -> list[str]:
        if isinstance(instruction, ir.Const):
            return [f"    {variable(instruction.value)} = "
                    f"{constant(instruction.payload, instruction.typ)};"]
        if isinstance(instruction, ir.Str):
            return [f"    {variable(instruction.value)} = {c_string(instruction.text)};"]
        if isinstance(instruction, ir.Bin):
            if types.get(instruction.left) == "f64":
                return [f"    {variable(instruction.value)} = "
                        f"{variable(instruction.left)} "
                        f"{FLOAT_OPERATORS[instruction.op]} "
                        f"{variable(instruction.right)};"]
            return [f"    {variable(instruction.value)} = "
                    f"{BINARY_HELPERS[instruction.op]}({variable(instruction.left)}, "
                    f"{variable(instruction.right)});"]
        if isinstance(instruction, ir.Neg):
            if types.get(instruction.operand) == "f64":
                return [f"    {variable(instruction.value)} = "
                        f"-{variable(instruction.operand)};"]
            return [f"    {variable(instruction.value)} = nv_neg("
                    f"{variable(instruction.operand)});"]
        if isinstance(instruction, ir.Not):
            return [f"    {variable(instruction.value)} = !{variable(instruction.operand)};"]
        if isinstance(instruction, ir.Cmp):
            operator = COMPARE_OPERATORS[instruction.op]
            return [f"    {variable(instruction.value)} = ({variable(instruction.left)} "
                    f"{operator} {variable(instruction.right)});"]
        if isinstance(instruction, ir.TextCompare):
            return [f"    {variable(instruction.value)} = "
                    f"(strcmp({variable(instruction.left)}, "
                    f"{variable(instruction.right)}) "
                    f"{COMPARE_OPERATORS[instruction.op]} 0);"]
        if isinstance(instruction, ir.Copy):
            return [f"    {variable(instruction.value)} = {variable(instruction.operand)};"]
        if isinstance(instruction, ir.Builtin):
            if instruction.name in CONTAINER_BUILTINS:
                return [f"    {self.container_call(instruction, types)}"]
            if instruction.name in STRING_BUILTINS:
                return [f"    {self.string_call(instruction)}"]
            helper, _arity = BUILTIN_HELPERS[instruction.name]
            arguments = ", ".join(variable(argument) for argument in instruction.args)
            call = f"{helper}({arguments})"
            if instruction.typ == "void":
                return [f"    {call};"]
            return [f"    {variable(instruction.value)} = {call};"]
        if isinstance(instruction, ir.FuncRef):
            # A function named as a value is the address of its code, and the C name
            # of that code is the checker's -- the same name a direct call prints, so
            # the two paths cannot disagree about which function a value names.
            return [f"    {variable(instruction.value)} = "
                    f"{self.call_name(instruction.target)};"]
        if isinstance(instruction, ir.Call):
            arguments = ", ".join(variable(argument) for argument in instruction.args)
            if instruction.callee is not None:
                # A call through a value: the callee is not named here, it is whatever
                # function the pointer holds at this point.
                call = f"(*{variable(instruction.callee)})({arguments})"
            else:
                call = f"{self.call_name(instruction.target)}({arguments})"
            if instruction.returns == "void":
                return [f"    {call};"]
            return [f"    {variable(instruction.value)} = {call};"]
        if isinstance(instruction, ir.Print):
            return [f"    {PRINT_HELPERS[instruction.typ]}("
                    f"{variable(instruction.operand)});"]
        if isinstance(instruction, ir.ArrayLiteral):
            return [f"    {variable(instruction.value)} = "
                    f"{array_literal(instruction, self.module.structs)};"]
        if isinstance(instruction, ir.ArrayZeros):
            element = array_element(instruction.typ, self.module.structs)
            return [f"    {variable(instruction.value)} = nv_arr_{element}_new("
                    f"{variable(instruction.count)});"]
        if isinstance(instruction, ir.Index):
            base_type = types.get(instruction.base)
            if is_vec(base_type or ""):
                family = f"nv_vec_{vec_tag(base_type)}"
            elif is_map(base_type or "") or is_set(base_type or ""):
                family = (f"nv_map_{map_tag(base_type)}" if is_map(base_type)
                          else f"nv_set_{set_tag(base_type)}")
            else:
                family = f"nv_arr_{instruction.typ}"
            return [f"    {variable(instruction.value)} = {family}_get("
                    f"{variable(instruction.base)}, {variable(instruction.index)});"]
        if isinstance(instruction, ir.CheckIndex):
            # The check on its own, before the value being stored is evaluated. The
            # store below checks again: one compare, and `index store` stays a checked
            # instruction whether or not a check came first.
            return [f"    (void)nv_check_index({variable(instruction.index)}, "
                    f"{variable(instruction.base)}.len);"]
        if isinstance(instruction, ir.IndexStore):
            base_type = types[instruction.base]
            if is_vec(base_type):
                return [f"    nv_vec_{vec_tag(base_type)}_set({variable(instruction.base)}, "
                        f"{variable(instruction.index)}, "
                        f"{variable(instruction.value)});"]
            element = array_element(base_type, self.module.structs)
            return [f"    nv_arr_{element}_set(&{variable(instruction.base)}, "
                    f"{variable(instruction.index)}, {variable(instruction.value)});"]
        if isinstance(instruction, ir.Slice):
            tag = array_element(instruction.typ, self.module.structs)
            return [f"    {variable(instruction.value)} = "
                    f"nv_arr_{tag}_slice({variable(instruction.base)}, "
                    f"{variable(instruction.start)}, {variable(instruction.end)});"]
        if isinstance(instruction, ir.Length):
            # An array is a value, so its length is read through `.`; a vector, a map
            # and a set are handles, so theirs is a field of the pointed-to record.
            base_type = types.get(instruction.base)
            arrow = "->" if _is_container(base_type) else "."
            return [f"    {variable(instruction.value)} = "
                    f"{variable(instruction.base)}{arrow}len;"]
        if isinstance(instruction, (ir.Slots, ir.Live, ir.KeyAt)):
            return [f"    {self.table_line(instruction)}"]
        if isinstance(instruction, ir.Release):
            element = array_element(instruction.typ, self.module.structs)
            return [f"    nv_arr_{element}_free(&{variable(instruction.operand)});"]
        if isinstance(instruction, ir.SlotLoad):
            return [f"    {variable(instruction.value)} = {cell(instruction.slot)};"]
        if isinstance(instruction, ir.SlotStore):
            return [f"    {cell(instruction.slot)} = {variable(instruction.operand)};"]
        if isinstance(instruction, ir.StructNew):
            fields = ", ".join(f".{name} = {variable(value)}"
                               for name, value in instruction.fields)
            return [f"    {variable(instruction.value)} = "
                    f"({self.c_type(instruction.typ)}){{ {fields} }};"]
        if isinstance(instruction, ir.Field):
            return [f"    {variable(instruction.value)} = "
                    f"{variable(instruction.base)}.{instruction.name};"]
        if isinstance(instruction, ir.FieldStore):
            path = ".".join(instruction.path)
            return [f"    {cell(instruction.slot)}.{path} = "
                    f"{variable(instruction.operand)};"]
        if isinstance(instruction, ir.Jump):
            lines = [f"    {line}" for line in
                     self.edge_lines(function, instruction.target, instruction.args)]
            lines.append(f"    goto {self.block_label(function, instruction.target)};")
            return lines
        if isinstance(instruction, ir.Branch):
            lines = [f"    if ({variable(instruction.condition)}) {{"]
            lines += [f"        {line}" for line in self.edge_lines(
                function, instruction.then_target, instruction.then_args)]
            lines.append(f"        goto {self.block_label(function, instruction.then_target)};")
            lines.append("    } else {")
            lines += [f"        {line}" for line in self.edge_lines(
                function, instruction.else_target, instruction.else_args)]
            lines.append(f"        goto {self.block_label(function, instruction.else_target)};")
            lines.append("    }")
            return lines
        if isinstance(instruction, ir.Variant):
            return [f"    {variable(instruction.value)} = "
                    f"{self.variant_literal(instruction)};"]
        if isinstance(instruction, ir.Tag):
            return [f"    {variable(instruction.value)} = "
                    f"{variable(instruction.operand)}._tag;"]
        if isinstance(instruction, ir.Payload):
            return [f"    {variable(instruction.value)} = "
                    f"{variable(instruction.operand)}._payload._v_"
                    f"{instruction.variant}._f{instruction.index};"]
        if isinstance(instruction, ir.Unreachable):
            lines = ['    nv_unreachable();']
            if function.returns != "void":
                # C wants a path out of a non-void function even when the code is
                # dead; a zero compound literal is the value nobody will read.
                lines.append(f"    return ({self.c_type(function.returns)}){{0}};")
            return lines
        if isinstance(instruction, ir.Ret):
            if instruction.operand is None:
                return ["    return;"]
            return [f"    return {variable(instruction.operand)};"]
        raise AssertionError(f"unsupported IR instruction {instruction!r}")


def array_literal(instruction: ir.ArrayLiteral, structs: dict | None = None) -> str:
    """`[a, b, c]` → `nv_arr_i64_init(3, (const int64_t[]){a, b, c})`."""
    element = array_element(instruction.typ, structs)
    if not instruction.items:
        # A compound literal with no elements is not something C will type.
        return f"nv_arr_{element}_new(INT64_C(0))"
    items = ", ".join(variable(item) for item in instruction.items)
    holder = INITIALISER_TYPES.get(element, f"const {STRUCT_PREFIX}{element}[]")
    return (f"nv_arr_{element}_init(INT64_C({len(instruction.items)}), "
            f"({holder}){{{items}}})")


def body(module: ir.Module, names: dict[str, str] | None = None,
         helpers: str = "", fnptr: dict[str, str] | None = None) -> str:
    """The translated program, without the runtime prelude.

    `names` is the C backend's function table, so that the names in the artefact do not
    depend on which path printed it. `helpers` is the family of per-array-type routines
    the IR calls by name (`nv_arr_Point_slice` and its siblings): it is printed after the
    struct declarations, because a helper that copies an element needs the element's
    `sizeof`, and before the functions, because they are what calls it.
    """
    return Printer(module, names, helpers, fnptr=fnptr).body()
