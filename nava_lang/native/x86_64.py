"""IR → x86-64 machine code, one translation unit per module (PLAN.md N14a, N14b).

The whole of the backend's code generation is here: values to stack slots,
instructions to encodings, blocks to labels, and a runtime small enough to read. Four
decisions carry most of the weight.

* **Every value lives in a frame slot.** Registers are not allocated. A value is loaded
  into `rax` (and `r11` for a second operand) when it is needed and written back when
  it is defined, so no instruction has to care what is already in a register. That is
  slower than a register allocator and it is the right first version: the differential
  compares two backends' *output*, and an allocator bug would look exactly like a
  semantic bug.
* **Overflow is checked where the CPU can see it.** `add`, `sub`, `imul` and `neg` set
  the overflow flag, so the check after each is `jo` and a jump to a `nv_fail` call, and
  a program that would wrap stops with the same message the C backend prints.
* **The runtime is syscalls.** `write` and `exit`, and nothing else: printing an
  integer is a decimal conversion loop in machine code, because there is no C library.
  The messages are byte-for-byte the ones `cgen.py` prints, which is what lets the
  differential treat a trap as ordinary output.
* **A unit is one module.** Since N14b each module gets its own unit (`unit.py`) and
  refers to other modules through **symbols**, so a call from one file to a function in
  another is a relocation the linker resolves rather than a name the generator hopes
  exists.

The runtime is one extra unit, emitted once per program: `_start`, the two printers,
`nv_fail`, and the trace. It is the unit every other unit refers to by name, which is
also a demonstration that the linker works — a program's own modules call into it
exactly the way they call into each other.

**Traces.** Every function carries a line table (`linker.py` writes the section). When
the environment variable `NAVA_TRACE` is set, `_start` records that fact and `nv_fail`
walks the frame chain and prints

```
  at nava.entry.main (main.nv:5)
```

after the message. Unset — the default — nothing is printed, so a trap's output stays
byte-for-byte what the C backend produces and the differential keeps its meaning.
"""
from __future__ import annotations

import struct as _struct
import zlib
from pathlib import Path

from .. import limits
from ..diagnostics import make
from . import ir, linker
from .unit import DATA, RODATA, UnitBuilder
from .x86 import (
    ARGUMENT_REGISTERS, R8, R9, R10, R11, RAX, RBP, RCX, RDI, RDX, RSI, RSP, XMM0, XMM1,
    Assembler,
)

#: How many arguments a call may pass. Beyond this the System V ABI needs stack
#: arguments, which the backend does not implement; refusing is better than passing
#: them wrongly.
MAX_ARGUMENTS = len(ARGUMENT_REGISTERS)
#: What the C runtime prints before a message, kept identical on purpose.
FAIL_PREFIX = "nava runtime error: "
#: How many bytes one array element takes in the native backend. All three element
#: types are one machine word — an `i64`, a `bool` and a pointer to a
#: length-prefixed string — so there is one stride and no per-type array helper.
ELEMENT_SIZE = 8
#: The length limit the C runtime enforces, so that the same program stops at the
#: same `array_zeros` in either backend rather than running out of arena first.
MAX_ELEMENTS = limits.MAX_ARRAY_ELEMENTS
#: The two system calls the arena needs, with the flags a private anonymous
#: mapping takes on x86-64 Linux.
SYSCALL_MMAP = 9
PROT_READ, PROT_WRITE = 1, 2
MAP_PRIVATE, MAP_ANONYMOUS = 2, 0x20
I64_MIN = -(2 ** 63)
I64_MAX = 2 ** 63 - 1
#: The variable that turns the trace on. A *variable*, not a flag: the same binary
#: prints a trace when it is asked to and nothing when it is not.
TRACE_VARIABLE = "NAVA_TRACE="
#: How deep a trace walks before it stops. A broken frame chain must not hang a
#: program that is already failing.
TRACE_DEPTH = 64

#: The builtins the native runtime implements: an IR `builtin` name -> the routine
#: that does the work and how many arguments it takes. A builtin that is not here is
#: refused by name at code generation, because the IR states the program and the
#: backend is the half that knows which routines exist.
BUILTIN_ROUTINES = {
    "bytes_len": ("nv_str_len", 1),
    "byte_at": ("nv_byte_at", 2),
    "str_concat": ("nv_str_concat", 2),
    "str_slice": ("nv_str_slice", 3),
    "str_from_i64": ("nv_str_from_i64", 1),
    "to_i64": ("nv_to_i64", 1),
    "to_f64": ("nv_to_f64", 1),
    "read_i64": ("nv_read_i64", 0),
}
#: How many bytes one word of a struct takes. A struct is a record of words — an
#: `i64`, a `bool`, an `f64` or a string pointer — so a field is a position in it.
FIELD_SIZE = 8

COMPARISONS = {"eq": "e", "ne": "ne", "lt": "l", "le": "le", "gt": "g", "ge": "ge"}
#: `ucomisd` sets `cf`, `zf` and `pf`, and says nothing about `sf` or `of`, so the
#: signed conditions `l`/`le`/`g`/`ge` an integer comparison uses are wrong here: the
#: unsigned pair `b`/`be`/`a`/`ae` is the one that reads those flags.
FLOAT_COMPARISONS = {"eq": "e", "ne": "ne", "lt": "b", "le": "be", "gt": "a", "ge": "ae"}
OVERFLOW_MESSAGES = {"add": "i64 overflow in +", "sub": "i64 overflow in -",
                     "mul": "i64 overflow in *", "div": "i64 overflow in /",
                     "mod": "i64 overflow in %", "neg": "i64 overflow in unary -"}
#: `%` has its own zero message in the C runtime, and the two are compared.
ZERO_MESSAGES = {"div": "division by zero", "mod": "remainder by zero"}


class Generator:
    """One program's code generation: a unit per module, plus the runtime."""

    def __init__(self, module: ir.Module, sources: dict[str, str] | None = None) -> None:
        self.module = module
        self.sources = sources or {}
        self.builder: UnitBuilder | None = None
        self.strings: dict[str, str] = {}
        self.rawns: dict[str, str] = {}
        self.slots: dict[str, int] = {}
        self.current: ir.Function | None = None
        self._next_label = 0

    # -- units ---------------------------------------------------------------
    def units(self):
        """Every unit of the program, the entry module first and the runtime last."""
        groups: dict[str, list] = {}
        for function in self.module.functions:
            groups.setdefault(function.module, []).append(function)
        order = [""] + sorted(name for name in groups if name)
        units = [self.module_unit(name, groups[name])
                 for name in order if name in groups]
        units.append(self.runtime_unit())
        return units

    def module_unit(self, module: str, functions) -> object:
        self.builder = UnitBuilder(f"module.{module or 'entry'}")
        self.builder.file = self.file_name(module)
        self.begin_unit()
        for function in functions:
            self.function(function)
        return self.builder.finalise()

    def begin_unit(self) -> None:
        self.strings = {}
        self.rawns = {}
        self._next_label = 0

    def file_name(self, module: str) -> str:
        """The name a line table and a trace show: the file's name, never its path.

        A path would put the checkout's directory into the binary and make two builds
        of one tree two different files.
        """
        path = self.sources.get(module)
        if path:
            return Path(path).name
        return f"{module}.nv" if module else "main.nv"

    # -- small helpers -------------------------------------------------------
    @property
    def code(self) -> Assembler:
        assert self.builder is not None
        return self.builder.text

    def local(self, hint: str) -> str:
        name = f"{hint}_{self._next_label}"
        self._next_label += 1
        return name

    def string(self, text: str) -> str:
        """A length-prefixed string in this unit's constant data."""
        assert self.builder is not None
        if text not in self.strings:
            label = f"s{len(self.strings)}"
            encoded = text.encode("utf-8")
            self.strings[text] = label
            self.builder.rodata.label(label)
            self.builder.rodata.emit(len(encoded).to_bytes(8, "little") + encoded)
        return self.strings[text]

    def raw(self, label: str, payload: bytes, section: str = RODATA) -> str:
        """Bytes that are not a string: the failure prefix, a buffer, a flag."""
        assert self.builder is not None
        if label not in self.rawns:
            buffer = self.builder.buffers[section]
            buffer.label(label)
            buffer.emit(payload)
            self.rawns[label] = label
        return label

    def fail(self, message: str) -> None:
        """Emit `nv_fail(message)` — one register and a call, then nothing runs."""
        self.code.lea_rip(RDI, self.string(message))
        self.code.call("nv_fail")

    def note_line(self, line: int) -> None:
        assert self.builder is not None
        self.builder.line(self.code.offset(), line)

    # -- one function --------------------------------------------------------
    def function(self, function: ir.Function) -> None:
        assert self.builder is not None
        if len(function.params) > MAX_ARGUMENTS:
            raise make(
                "E0901",
                f"the native backend does not implement a call with more than "
                f"{MAX_ARGUMENTS} arguments (in '{function.name}')",
                None,
                hint="build with the C backend (the default): `nava build` without "
                     "--backend native",
            )
        self.current = function
        self.slots = self.assign_slots(function)
        symbol = ir.symbol(function)
        label = self.local("fn")
        self.code.label(label)
        start = self.code.offset()
        self.builder.function(symbol, start)
        self.note_line(function.decl_line)
        self.code.push(RBP)
        self.code.mov_reg(RBP, RSP)
        self.code.sub_rsp(frame_size(len(self.slots)))
        for index, (name, _typ) in enumerate(function.params):
            self.code.store(RBP, -8 * (self.slots[name] + 1), ARGUMENT_REGISTERS[index])
        emit_index = 0
        for block in function.blocks:
            self.code.label(self.block_label(function, block))
            for instruction in block.instructions:
                if emit_index < len(function.lines):
                    self.note_line(function.lines[emit_index])
                emit_index += 1
                self.instruction(function, instruction)
        end = self.code.offset()
        self.builder.close_function(end)
        self.builder.export(label, symbol, "func", size=end - start)

    @staticmethod
    def block_label(function: ir.Function, block: ir.Block) -> str:
        return f"{ir.symbol(function)}.{block.label}"

    @staticmethod
    def assign_slots(function: ir.Function) -> dict[str, int]:
        """One frame slot per value, in the order the values are defined."""
        slots: dict[str, int] = {}
        for name, _typ in function.params:
            slots[name] = len(slots)
        for cell in function.slots:
            slots[cell.name] = len(slots)
        for block in function.blocks:
            for name, _typ in block.params:
                slots[name] = len(slots)
            for instruction in block.instructions:
                value = getattr(instruction, "value", "")
                if isinstance(instruction, (ir.Const, ir.Str, ir.Bin, ir.Neg, ir.Not,
                                            ir.Cmp, ir.Copy, ir.Call, ir.ArrayLiteral,
                                            ir.ArrayZeros, ir.Index, ir.Length,
                                            ir.SlotLoad, ir.StructNew, ir.Field,
                                            ir.Builtin, ir.TextCompare)):
                    if value and value not in slots:
                        slots[value] = len(slots)
        return slots

    # -- instructions --------------------------------------------------------
    def instruction(self, function: ir.Function, instruction) -> None:
        """Emit one instruction. One branch per IR instruction, no interpretation."""
        if isinstance(instruction, ir.Const):
            self.code.mov_imm(RAX, self.constant(instruction))
            self.store(instruction.value, RAX)
            return
        if isinstance(instruction, ir.Str):
            self.code.lea_rip(RAX, self.string(instruction.text))
            self.store(instruction.value, RAX)
            return
        if isinstance(instruction, ir.Bin):
            if instruction.typ == "f64":
                self.f64_binary(instruction)
                return
            self.load(instruction.left, RAX)
            self.load(instruction.right, R11)
            if instruction.op in ("add", "sub", "mul"):
                if instruction.op == "add":
                    self.code.add_reg(RAX, R11)
                elif instruction.op == "sub":
                    self.code.sub_reg(RAX, R11)
                else:
                    self.code.imul_reg(RAX, R11)
                self.overflow(instruction.op)
            elif instruction.op in ("div", "mod"):
                self.division(instruction.op)
            else:  # pragma: no cover - the IR has no other binary operations
                raise AssertionError(f"unknown IR binary op {instruction.op!r}")
            self.store(instruction.value, RDX if instruction.op == "mod" else RAX)
            return
        if isinstance(instruction, ir.Neg):
            self.load(instruction.operand, RAX)
            if instruction.typ == "f64":
                # Negating a float is flipping the sign bit: no rounding, and `-0.0`
                # stays a zero with the other sign, which is what IEEE says.
                self.code.mov_imm(R11, I64_MIN)
                self.code.xor_reg(RAX, R11)
            else:
                self.code.neg(RAX)
                self.overflow("neg")
            self.store(instruction.value, RAX)
            return
        if isinstance(instruction, ir.Not):
            self.load(instruction.operand, RAX)
            self.code.mov_imm(R11, 1)
            self.code.xor_reg(RAX, R11)
            self.store(instruction.value, RAX)
            return
        if isinstance(instruction, ir.Cmp):
            if instruction.typ == "f64":
                self.f64_compare(instruction)
                return
            self.load(instruction.left, RAX)
            self.load(instruction.right, R11)
            self.code.cmp_reg(RAX, R11)
            self.code.setcc(COMPARISONS[instruction.op], RAX)
            self.code.movzx_byte(RAX, RAX)
            self.store(instruction.value, RAX)
            return
        if isinstance(instruction, ir.Copy):
            self.load(instruction.operand, RAX)
            self.store(instruction.value, RAX)
            return
        if isinstance(instruction, ir.Call):
            self.call(function, instruction)
            return
        if isinstance(instruction, ir.Print):
            self.print_value(instruction)
            return
        if isinstance(instruction, ir.ArrayLiteral):
            self.array_literal(instruction)
            return
        if isinstance(instruction, ir.ArrayZeros):
            self.array_zeros(instruction)
            return
        if isinstance(instruction, ir.Index):
            self.index(instruction)
            return
        if isinstance(instruction, ir.IndexStore):
            self.index_store(instruction)
            return
        if isinstance(instruction, ir.CheckIndex):
            return self.check_index(instruction)
        if isinstance(instruction, ir.Length):
            self.length(instruction)
            return
        if isinstance(instruction, ir.Release):
            # The arena is never given memory back, so releasing is not an
            # instruction the machine needs. It is not dead code either: it is where
            # the IR says who owns what, and a backend that frees implements it.
            return
        if isinstance(instruction, ir.SlotLoad):
            self.code.load(RAX, RBP, self.frame(instruction.slot))
            self.store(instruction.value, RAX)
            return
        if isinstance(instruction, ir.SlotStore):
            self.load(instruction.operand, RAX)
            if self.struct_type(instruction.slot):
                # A struct is a value: storing one copies the record, so that the two
                # names cannot see each other's writes. The C backend's struct
                # assignment copies too, which is what makes the two comparable.
                self.code.mov_reg(RDI, RAX)
                self.code.call(self.copy_helper(self.slot_type(instruction.slot)))
            self.code.store(RBP, self.frame(instruction.slot), RAX)
            return
        if isinstance(instruction, ir.StructNew):
            self.struct_new(instruction)
            return
        if isinstance(instruction, ir.Field):
            self.field(instruction)
            return
        if isinstance(instruction, ir.FieldStore):
            self.field_store(instruction)
            return
        if isinstance(instruction, ir.TextCompare):
            self.text_compare(instruction)
            return
        if isinstance(instruction, ir.Builtin):
            self.builtin(instruction)
            return
        if isinstance(instruction, ir.Jump):
            self.jump(instruction)
            return
        if isinstance(instruction, ir.Branch):
            self.branch(function, instruction)
            return
        if isinstance(instruction, ir.Ret):
            if instruction.operand is not None:
                self.load(instruction.operand, RAX)
            self.code.mov_reg(RSP, RBP)
            self.code.pop(RBP)
            self.code.ret()
            return
        raise AssertionError(f"unknown IR instruction {instruction!r}")

    # -- constants, cells and the vector unit --------------------------------
    @staticmethod
    def constant(instruction) -> int:
        """The 64-bit word an IR constant names.

        An `f64` constant is its IEEE bit pattern: the value lives in a frame slot like
        every other, and the only place that knows it is a float is the instruction that
        operates on it.
        """
        if instruction.typ == "f64":
            return _struct.unpack("<q", _struct.pack("<d", float(instruction.payload)))[0]
        if instruction.typ == "i64":
            return int(instruction.payload)
        return 1 if instruction.payload else 0

    def frame(self, name: str) -> int:
        """The frame displacement of a cell: `[rbp-8*(slot+1)]`, like a value."""
        return -8 * (self.slots[name] + 1)

    def slots_of(self) -> dict[str, str]:
        assert self.current is not None
        return {cell.name: cell.typ for cell in self.current.slots}

    def slot_type(self, slot: str) -> str:
        """What a cell holds. A cell's type is the type of the variable it is."""
        return self.slots_of().get(slot, "")

    def struct_type(self, slot: str) -> bool:
        """Whether a cell holds a struct, which is the one type that is copied by value."""
        return self.slot_type(slot) in self.layouts()

    def layouts(self) -> dict:
        return self.module.structs

    def layout(self, typ: str) -> tuple:
        """The fields of a struct type in declaration order, or nothing if there is none."""
        return self.layouts().get(typ, ())

    def field_offset(self, typ: str, name: str) -> int:
        for index, (field, _field_type) in enumerate(self.layout(typ)):
            if field == name:
                return FIELD_SIZE * index
        raise AssertionError(f"no field {name!r} in {typ!r}")

    def field_type(self, typ: str, name: str) -> str:
        for field, field_type in self.layout(typ):
            if field == name:
                return field_type
        raise AssertionError(f"no field {name!r} in {typ!r}")

    @staticmethod
    def copy_helper(typ: str) -> str:
        """The runtime function that copies a struct value: `nv_copy_<name>`.

        A type name may contain `(` and `)` — an instantiation is written `Pair(i64)` —
        and a symbol may not, so everything that is not a letter, a digit or an
        underscore becomes an underscore. Two types could then collide, so the mangled
        name carries a checksum of the type it came from — a checksum rather than
        `hash()`, because a symbol has to be the same symbol on the next build.
        """
        safe = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in typ)
        return f"nv_copy_{safe}_{zlib.crc32(typ.encode('utf-8')) % 0x10000:04x}"

    def f64_binary(self, instruction: ir.Bin) -> None:
        """`add`, `sub`, `mul`, `div` on two `f64` values, in SSE.

        No overflow check: an `f64` that leaves the range becomes `inf` and the language
        says so, which is exactly what the C backend's `+` on two `double`s does.
        """
        code = self.code
        self.load(instruction.left, RAX)
        code.movq_xmm(XMM0, RAX)
        self.load(instruction.right, RAX)
        code.movq_xmm(XMM1, RAX)
        {"add": code.addsd, "sub": code.subsd, "mul": code.mulsd,
         "div": code.divsd}[instruction.op](XMM0, XMM1)
        code.movq_xmm_to_reg(RAX, XMM0)
        self.store(instruction.value, RAX)

    def f64_compare(self, instruction: ir.Cmp) -> None:
        """A comparison of two floats, with `nan` answering the way the language says.

        `ucomisd` sets `pf` when an operand is a `nan`, and in that case every ordered
        comparison is false while `!=` is true — so the unordered case is answered
        separately instead of being left to the condition codes, which would call
        `nan < 1.0` true.
        """
        code = self.code
        self.load(instruction.left, RAX)
        code.movq_xmm(XMM0, RAX)
        self.load(instruction.right, RAX)
        code.movq_xmm(XMM1, RAX)
        code.xor_reg(RAX, RAX)                  # the answer, before the flags
        code.ucomisd(XMM0, XMM1)
        done = self.local("cmp_done")
        if instruction.op == "ne":
            unordered = self.local("cmp_unordered")
            code.jcc("p", unordered)
            code.setcc("ne", RAX)
            code.jmp(done)
            code.label(unordered)
            code.mov_imm(RAX, 1)                # `nan != x` is true
        else:
            code.jcc("p", done)                 # `nan` is not less, equal or greater
            code.setcc(FLOAT_COMPARISONS[instruction.op], RAX)
        code.label(done)
        self.store(instruction.value, RAX)

    # -- structs -------------------------------------------------------------
    def struct_new(self, instruction: ir.StructNew) -> None:
        """`Point(x: 1, y: 2)`: one record from the arena, one store per field.

        A field that is itself a struct takes a copy of the value it is given. Without
        it `Segment(from: origin, ...)` would hold the record `origin` names, and a
        write through the segment would be visible in `origin` — which is not what the
        language says a construction does, and not what the C backend does.
        """
        code = self.code
        code.mov_imm(RDI, FIELD_SIZE * max(1, len(self.layout(instruction.typ))))
        code.call("nv_alloc")
        self.store(instruction.value, RAX)
        for name, argument in instruction.fields:
            self.load(argument, RAX)
            field_type = self.field_type(instruction.typ, name)
            if field_type in self.layouts():
                code.mov_reg(RDI, RAX)
                code.call(self.copy_helper(field_type))
            code.mov_reg(R11, RAX)
            self.load(instruction.value, RAX)
            code.store(RAX, self.field_offset(instruction.typ, name), R11)

    def field(self, instruction: ir.Field) -> None:
        """A field read is a load from a fixed offset of the record."""
        self.load(instruction.base, RAX)
        self.code.load(RAX, RAX, self.field_offset(instruction.base_type, instruction.name))
        self.store(instruction.value, RAX)

    def field_store(self, instruction: ir.FieldStore) -> None:
        """A field write is a store into the record the cell points at.

        Every step of the path but the last is a load of the nested record; the last is
        the store. The nested records are this value's own — a construction copied them
        and a cell copies what it is given — so a write here is a write to one place.
        """
        code = self.code
        typ = self.slot_type(instruction.slot)
        self.load(instruction.operand, R11)
        code.load(RAX, RBP, self.frame(instruction.slot))
        for step in instruction.path[:-1]:
            code.load(RAX, RAX, self.field_offset(typ, step))
            typ = self.field_type(typ, step)
        code.store(RAX, self.field_offset(typ, instruction.path[-1]), R11)

    # -- text and the builtins -----------------------------------------------
    def text_compare(self, instruction: ir.TextCompare) -> None:
        """`nv_str_cmp` answers -1, 0 or 1; the operator picks one of the six flags."""
        code = self.code
        self.load(instruction.left, RDI)
        self.load(instruction.right, RSI)
        code.call("nv_str_cmp")
        code.mov_imm(R11, 0)
        code.cmp_reg(RAX, R11)
        code.setcc(COMPARISONS[instruction.op], RAX)
        code.movzx_byte(RAX, RAX)
        self.store(instruction.value, RAX)

    def builtin(self, instruction: ir.Builtin) -> None:
        """One call, with the arguments in the ABI's registers, exactly like a function."""
        routine = BUILTIN_ROUTINES.get(instruction.name)
        if routine is None:
            raise make(
                "E0901",
                f"the native backend does not implement the builtin "
                f"'{instruction.name}'",
                None,
                hint="build with the C backend (the default): `nava build` without "
                     "--backend native",
            )
        symbol, arity = routine
        if len(instruction.args) != arity:
            raise AssertionError(
                f"the IR states {instruction.name} with {len(instruction.args)} "
                f"argument(s); the runtime implements {arity}")
        for index, argument in enumerate(instruction.args):
            self.load(argument, ARGUMENT_REGISTERS[index])
        self.code.call(symbol)
        if instruction.typ != "void":
            self.store(instruction.value, RAX)

    def copy_helper_body(self, typ: str) -> None:
        """`nv_copy_<T>(rdi = record)` -> a copy of it, fields and nested structs.

        A struct field that is itself a struct is copied by calling its own helper, so a
        copy is as deep as the type is nested and two names never share a record a write
        could reach. Strings are shared, and that is sound because a string is never
        written through.
        """
        code = self.code
        code.push(RBP)
        code.mov_reg(RBP, RSP)
        code.sub_rsp(32)
        code.store(RBP, -8, RDI)                # the source
        code.mov_imm(RDI, FIELD_SIZE * max(1, len(self.layout(typ))))
        code.call("nv_alloc")
        code.store(RBP, -16, RAX)               # the copy
        for name, field_type in self.layout(typ):
            offset = self.field_offset(typ, name)
            code.load(R11, RBP, -8)
            code.load(RAX, R11, offset)
            if field_type in self.layouts():
                code.mov_reg(RDI, RAX)
                code.call(self.copy_helper(field_type))
            code.load(R11, RBP, -16)
            code.store(R11, offset, RAX)
        code.load(RAX, RBP, -16)
        code.mov_reg(RSP, RBP)
        code.pop(RBP)
        code.ret()

    def load(self, value: str, register: int) -> None:
        self.code.load(register, RBP, -8 * (self.slots[value] + 1))

    def store(self, value: str, register: int) -> None:
        self.code.store(RBP, -8 * (self.slots[value] + 1), register)

    def overflow(self, op: str) -> None:
        """Trap if the flag is set. The flag was set by the instruction just emitted."""
        skipped = self.local("checked")
        self.code.jcc("no", skipped)
        self.fail(OVERFLOW_MESSAGES[op])
        self.code.label(skipped)

    def division(self, op: str) -> None:
        """`div`/`mod` on `rax` by `r11`: zero, then the one overflow, then `idiv`."""
        zero = self.local("div_zero")
        room = self.local("div_ok")
        done = self.local("div_done")
        self.code.test_reg(R11)
        self.code.jcc("e", zero)
        self.code.mov_imm(R10, I64_MIN)
        self.code.cmp_reg(RAX, R10)
        self.code.jcc("ne", room)
        self.code.mov_imm(R10, -1)
        self.code.cmp_reg(R11, R10)
        self.code.jcc("ne", room)
        self.fail(OVERFLOW_MESSAGES[op])
        self.code.label(room)
        self.code.cqo()
        self.code.idiv(R11)
        self.code.jmp(done)
        self.code.label(zero)
        self.fail(ZERO_MESSAGES[op])
        self.code.label(done)

    def call(self, function: ir.Function, instruction: ir.Call) -> None:
        if len(instruction.args) > MAX_ARGUMENTS:
            raise make(
                "E0901",
                f"the native backend does not implement a call with more than "
                f"{MAX_ARGUMENTS} arguments (calling '{instruction.target}' in "
                f"'{function.name}')",
                None,
                hint="build with the C backend (the default): `nava build` without "
                     "--backend native",
            )
        for index, argument in enumerate(instruction.args):
            self.load(argument, ARGUMENT_REGISTERS[index])
        self.code.call(instruction.target)
        if instruction.returns != "void":
            self.store(instruction.value, RAX)

    # -- arrays --------------------------------------------------------------
    def array_literal(self, instruction: ir.ArrayLiteral) -> None:
        """A new array: one header from the arena, then one store per element."""
        code = self.code
        code.mov_imm(RDI, len(instruction.items))
        code.call("nv_array_new")
        self.store(instruction.value, RAX)
        for offset, item in enumerate(instruction.items):
            self.load(item, R11)
            self.load(instruction.value, RAX)
            code.store(RAX, 16 + ELEMENT_SIZE * offset, R11)

    def array_zeros(self, instruction: ir.ArrayZeros) -> None:
        self.load(instruction.count, RDI)
        self.code.call("nv_array_new")
        self.store(instruction.value, RAX)

    def length(self, instruction: ir.Length) -> None:
        self.load(instruction.base, RAX)
        self.code.load(RAX, RAX, 0)
        self.store(instruction.value, RAX)

    def index(self, instruction: ir.Index) -> None:
        code = self.code
        self.load(instruction.base, RAX)
        code.load(R11, RAX, 0)                  # the length
        self.load(instruction.index, R10)       # the index
        self.array_bounds()
        self.load(instruction.base, RAX)
        code.load(R11, RAX, 8)                  # the elements
        code.mov_imm(R9, ELEMENT_SIZE)
        code.imul_reg(R10, R9)
        code.add_reg(R11, R10)
        code.load(RAX, R11, 0)
        self.store(instruction.value, RAX)

    def index_store(self, instruction: ir.IndexStore) -> None:
        code = self.code
        self.load(instruction.base, RAX)
        code.load(R11, RAX, 0)
        self.load(instruction.index, R10)
        self.array_bounds()
        self.load(instruction.value, R9)
        self.load(instruction.base, RAX)
        code.load(R11, RAX, 8)
        code.mov_imm(R8, ELEMENT_SIZE)
        code.imul_reg(R10, R8)
        code.add_reg(R11, R10)
        code.store(R11, 0, R9)

    def check_index(self, instruction: ir.CheckIndex) -> None:
        """`check a[i]`: the bounds check on its own, so an order can be stated."""
        code = self.code
        self.load(instruction.base, RAX)
        code.load(R11, RAX, 0)
        self.load(instruction.index, R10)
        self.array_bounds()

    def array_bounds(self) -> None:
        """The language's `Index` is checked: `0 <= index < length` or the program stops.

        The trap is the C runtime's, word for word — `array index 5 is out of bounds
        for length 3` — because the differential compares standard error too.
        """
        code = self.code
        trap, inside = self.local("bounds"), self.local("in_range")
        code.mov_imm(R9, 0)
        code.cmp_reg(R10, R9)
        code.jcc("l", trap)
        code.cmp_reg(R10, R11)
        code.jcc("ge", trap)
        code.jmp(inside)
        code.label(trap)
        code.mov_reg(RDI, R10)
        code.mov_reg(RSI, R11)
        code.xor_reg(RDX, RDX)
        code.call("nv_fail_pair")
        code.label(inside)

    def jump(self, instruction: ir.Jump) -> None:
        for value, target in zip(instruction.args, self._parameters(instruction.target)):
            self.load(value, RAX)
            self.store(target, RAX)
        self.code.jmp(self._block_of(instruction.target))

    def branch(self, function: ir.Function, instruction: ir.Branch) -> None:
        self.load(instruction.condition, RAX)
        self.code.test_reg(RAX)
        then_edge = self.local("then_edge")
        self.code.jcc("ne", then_edge)
        for value, target in zip(instruction.else_args,
                                 self._parameters(instruction.else_target)):
            self.load(value, RAX)
            self.store(target, RAX)
        self.code.jmp(self._block_of(instruction.else_target))
        self.code.label(then_edge)
        for value, target in zip(instruction.then_args,
                                 self._parameters(instruction.then_target)):
            self.load(value, RAX)
            self.store(target, RAX)
        self.code.jmp(self._block_of(instruction.then_target))

    def _block_of(self, label: str) -> str:
        assert self.current is not None
        return self.block_label(self.current, self._block(label))

    def _block(self, label: str) -> ir.Block:
        assert self.current is not None
        for block in self.current.blocks:
            if block.label == label:
                return block
        raise AssertionError(f"no block {label!r} in '{self.current.name}'")

    def _parameters(self, label: str) -> list[str]:
        return [name for name, _typ in self._block(label).params]

    # -- printing ------------------------------------------------------------
    def print_value(self, instruction: ir.Print) -> None:
        if instruction.typ == "i64":
            self.load(instruction.operand, RDI)
            self.code.call("nv_print_i64")
            return
        if instruction.typ == "bool":
            self.load(instruction.operand, RDI)
            self.code.call("nv_print_bool")
            return
        if instruction.typ == "str":
            # The value is the address of a length-prefixed string: write its bytes,
            # then a newline, in two `write` calls because there is no `writev` here.
            self.load(instruction.operand, RAX)
            self.code.load(RDX, RAX, 0)
            self.code.lea(RSI, RAX, 8)
            self.code.mov_imm(RDI, 1)
            self.code.mov_imm(RAX, 1)
            self.code.syscall()
            self.newline_to(1)
            return
        raise make(
            "E0901",
            f"the native backend does not implement printing an {instruction.typ}",
            None,
            hint="build with the C backend (the default): `nava build` without "
                 "--backend native",
        )

    def newline_to(self, descriptor: int) -> None:
        self.code.lea_rip(RSI, self.raw("nv_newline", b"\n"))
        self.code.mov_imm(RDX, 1)
        self.code.mov_imm(RDI, descriptor)
        self.code.mov_imm(RAX, 1)
        self.code.syscall()

    # -- the runtime ---------------------------------------------------------
    def runtime_unit(self):
        """`_start`, the printers, `nv_fail` and the trace, in their own unit."""
        self.builder = UnitBuilder("runtime")
        self.builder.file = ""
        self.begin_unit()
        for symbol, body in (("_start", self.start),
                             ("nv_write", self.write_helper),
                             ("nv_write_u64", self.write_u64_helper),
                             ("nv_print_i64", self.print_i64_helper),
                             ("nv_print_bool", self.print_bool_helper),
                             ("nv_trace_stack", self.trace_helper),
                             ("nv_fail", self.fail_helper),
                             ("nv_format", self.format_helper),
                             ("nv_alloc", self.alloc_helper),
                             ("nv_array_new", self.array_new_helper),
                             ("nv_fail_pair", self.fail_pair_helper),
                             ("nv_str_len", self.str_len_helper),
                             ("nv_str_cmp", self.str_cmp_helper),
                             ("nv_byte_at", self.byte_at_helper),
                             ("nv_str_concat", self.str_concat_helper),
                             ("nv_str_slice", self.str_slice_helper),
                             ("nv_str_from_i64", self.str_from_i64_helper),
                             ("nv_to_i64", self.to_i64_helper),
                             ("nv_to_f64", self.to_f64_helper),
                             ("nv_getc", self.getc_helper),
                             ("nv_read_i64", self.read_i64_helper)):
            self.runtime_function(symbol, body)
        for typ in self.copy_types():
            # One copier per struct type, because a copy is as deep as the type is
            # nested and the depth is known here rather than at run time.
            self.runtime_function(self.copy_helper(typ),
                                  lambda typ=typ: self.copy_helper_body(typ))
        return self.builder.finalise()

    def copy_types(self) -> list[str]:
        """Every struct type a copier is emitted for, innermost first."""
        ordered: list[str] = []
        def visit(typ: str) -> None:
            if typ in ordered:
                return
            for _name, field_type in self.layout(typ):
                if field_type in self.layouts():
                    visit(field_type)
            ordered.append(typ)
        for typ in sorted(self.layouts()):
            visit(typ)
        return ordered

    def runtime_function(self, symbol: str, body) -> None:
        """Emit one runtime function: a label, its body, and a symbol with its size.

        A symbol without a size is a symbol a debugger and a trace cannot bound, so
        the size is measured here rather than left at zero.
        """
        assert self.builder is not None
        label = symbol
        self.code.label(label)
        start = self.code.offset()
        body()
        self.builder.export(label, symbol, "func", size=self.code.offset() - start)

    def entry_function(self) -> ir.Function:
        for function in self.module.functions:
            if function.name == self.module.entry and not function.module:
                return function
        raise AssertionError("the IR has no entry function")

    def start(self) -> None:
        """`_start`: the trace variable, then `main`, then `exit` with its value.

        The kernel hands `_start` the environment on the stack, so no syscall is needed
        to read `NAVA_TRACE`. One frame is pushed before the scan because the trace
        walks frame chains, and the alignment of `rsp` at a `call` is the same rule
        everywhere in this file.
        """
        code = self.code
        main = self.entry_function()
        flag = self.raw("nv_trace_flag", bytes(8), DATA)
        code.push(RBP)
        code.mov_reg(RBP, RSP)
        code.sub_rsp(24)
        self.trace_variable(flag)
        code.mov_reg(RSP, RBP)
        code.pop(RBP)
        code.call(ir.symbol(main))
        if main.returns == "void":
            code.xor_reg(RDI, RDI)
        else:
            code.movzx_byte(RDI, RAX)
        code.mov_imm(RAX, 60)
        code.syscall()

    def trace_variable(self, flag: str) -> None:
        """Set `flag` when `NAVA_TRACE` is in the environment.

        `envp` follows `argv` on the stack. For each variable the eleven bytes of
        `NAVA_TRACE=` are compared one at a time, advancing both pointers, because the
        encoder has no indexed addressing and does not need it for anything else.
        """
        code = self.code
        prefix = self.raw("nv_trace_prefix", TRACE_VARIABLE.encode())
        loop, compare = self.local("env_loop"), self.local("env_compare")
        matched, next_env, done = (self.local("env_matched"), self.local("env_next"),
                                   self.local("env_done"))
        code.load(RAX, RBP, 8)                 # argc
        code.add_imm8(RAX, 1)                  # and the NULL that ends argv
        code.mov_imm(R11, 8)
        code.imul_reg(RAX, R11)
        code.lea(RSI, RBP, 16)                 # argv[0]
        code.add_reg(RSI, RAX)                 # envp[0]
        code.label(loop)
        code.load(RAX, RSI, 0)
        code.test_reg(RAX)
        code.jcc("e", done)
        code.lea_rip(R10, prefix)
        code.xor_reg(R9, R9)
        code.label(compare)
        code.mov_imm(R11, len(TRACE_VARIABLE))
        code.cmp_reg(R9, R11)
        code.jcc("ae", matched)
        code.load_byte(R8, RAX, 0)
        code.load_byte(R11, R10, 0)
        code.cmp_reg(R8, R11)
        code.jcc("ne", next_env)
        code.inc(RAX)
        code.inc(R10)
        code.inc(R9)
        code.jmp(compare)
        code.label(matched)
        code.mov_imm(RAX, 1)
        code.lea_rip(R11, flag)
        code.store(R11, 0, RAX)
        code.jmp(done)
        code.label(next_env)
        code.lea(RSI, RSI, 8)
        code.jmp(loop)
        code.label(done)

    def write_helper(self) -> None:
        """`nv_write(rdi)`: a length-prefixed string to stderr."""
        code = self.code
        code.push(RBP)
        code.mov_reg(RBP, RSP)
        code.sub_rsp(16)
        code.load(RDX, RDI, 0)
        code.lea(RSI, RDI, 8)
        code.mov_imm(RDI, 2)
        code.mov_imm(RAX, 1)
        code.syscall()
        code.mov_reg(RSP, RBP)
        code.pop(RBP)
        code.ret()

    def write_u64_helper(self) -> None:
        """`nv_write_u64(rdi)`: the decimal digits of an unsigned value, to stderr.

        Line numbers are small and positive, so `nv_format` is used for the digits
        and the newline `nv_print_i64` adds is left off.
        """
        code = self.code
        code.push(RBP)
        code.mov_reg(RBP, RSP)
        code.sub_rsp(16)
        code.lea_rip(RSI, self.number_buffer(1))
        code.call("nv_format")
        code.mov_reg(RSI, RAX)
        code.mov_imm(RDI, 2)
        code.mov_imm(RAX, 1)
        code.syscall()
        code.mov_reg(RSP, RBP)
        code.pop(RBP)
        code.ret()

    def print_i64_helper(self) -> None:
        """`nv_print_i64(rdi)`: sign, decimal digits, newline, on stdout."""
        code = self.code
        code.push(RBP)
        code.mov_reg(RBP, RSP)
        code.sub_rsp(16)
        code.lea_rip(RSI, self.number_buffer(2))
        code.call("nv_format")
        code.mov_reg(RSI, RAX)
        code.mov_imm(RDI, 1)
        code.mov_imm(RAX, 1)
        code.syscall()
        self.newline_to(1)
        code.mov_reg(RSP, RBP)
        code.pop(RBP)
        code.ret()

    def print_bool_helper(self) -> None:
        """`nv_print_bool(rdi)`: `true` or `false`, then a newline, on stdout."""
        code = self.code
        true_text = self.raw("nv_true", b"true\n")
        false_text = self.raw("nv_false", b"false\n")
        code.test_reg(RDI)
        code.jcc("e", "nv_print_bool.false")
        code.lea_rip(RSI, true_text)
        code.mov_imm(RDX, 5)
        code.jmp("nv_print_bool.write")
        code.label("nv_print_bool.false")
        code.lea_rip(RSI, false_text)
        code.mov_imm(RDX, 6)
        code.label("nv_print_bool.write")
        code.mov_imm(RDI, 1)
        code.mov_imm(RAX, 1)
        code.syscall()
        code.ret()

    def number_buffer(self, index: int) -> str:
        """A 32-byte scratch buffer in the writable segment; answers the end label.

        Writing digits backwards from the end is what makes the length known only
        when the number is finished, so the buffer is asked for by its end.
        """
        self.raw(f"nv_number_buffer{index}", bytes(32), DATA)
        return self.raw(f"nv_number_buffer{index}_end", b"", DATA)

    def format_helper(self) -> None:
        """`nv_format(rdi = value, rsi = end of a buffer)` -> `(rax = start, rdx = len)`.

        `i64_min` is printed by dividing its **unsigned** magnitude: the negation
        does not fit in an `i64` but fits in a `u64`, and that is the value the
        digits come from. The C runtime prints the same digits, which is what makes
        the two backends comparable on `print(-9223372036854775807 - 1)`.
        """
        code = self.code
        digits, positive, write = (self.local("format_digits"),
                                   self.local("format_positive"),
                                   self.local("format_write"))
        code.push(RBP)
        code.mov_reg(RBP, RSP)
        code.mov_reg(R8, RSI)                   # the end of the buffer
        code.mov_reg(R9, R8)
        code.mov_reg(RAX, RDI)
        code.xor_reg(R10, R10)                  # the sign
        code.test_reg(RAX)
        code.jcc("ns", positive)
        code.neg(RAX)
        code.mov_imm(R10, 1)
        code.label(positive)
        code.mov_imm(R11, 10)
        code.label(digits)
        code.xor_reg(RDX, RDX)
        code.div(R11)
        code.add_byte_imm(RDX, ord("0"))
        code.dec(R9)
        code.store_byte(R9, 0, RDX)
        code.test_reg(RAX)
        code.jcc("ne", digits)
        code.test_reg(R10)
        code.jcc("e", write)
        code.dec(R9)
        code.store_byte_imm(R9, 0, ord("-"))
        code.label(write)
        code.mov_reg(RAX, R9)
        code.mov_reg(RDX, R8)
        code.sub_reg(RDX, R9)
        code.mov_reg(RSP, RBP)
        code.pop(RBP)
        code.ret()

    def alloc_helper(self) -> None:
        """`nv_alloc(rdi = bytes)`: a zeroed, 16-byte-aligned block from the arena.

        The arena is a chain of `mmap`ed regions. The first allocation maps one; a
        request that does not fit maps another at least as large as the request, so
        running out means the kernel refused. Nothing is ever given back: `release`
        is a no-op here, and a program that allocates arrays in a loop grows the
        arena until it exits. That is the documented limit of this backend, and it is
        the honest one — an arena with no collector is a backend that runs the
        programs it was asked to run, and saying so is better than a `free` that does
        nothing quietly.

        Zeroing comes from the kernel: a fresh `mmap` is zero-filled and the bump
        pointer never revisits a byte, so a block is zeroed without a loop.
        """
        code = self.code
        base = self.raw("nv_arena_base", bytes(8), DATA)
        left = self.raw("nv_arena_left", bytes(8), DATA)
        room, large, sized, mapped = (self.local("arena_room"), self.local("arena_large"),
                                      self.local("arena_sized"), self.local("arena_mapped"))
        code.push(RBP)
        code.mov_reg(RBP, RSP)
        code.sub_rsp(32)
        code.mov_imm(R11, ELEMENT_SIZE * 2 - 1)
        code.add_reg(RDI, R11)                  # round up to
        code.and_imm32(RDI, -16)                # a multiple of sixteen
        code.store(RBP, -8, RDI)                # the size that was asked for
        code.lea_rip(R10, left)
        code.load(R11, R10, 0)
        code.cmp_reg(RDI, R11)
        code.jcc("be", room)
        # Not enough left: a new region, at least as large as the request.
        code.mov_imm(R11, 1 << 20)
        code.cmp_reg(RDI, R11)
        code.jcc("b", sized)
        code.mov_reg(R11, RDI)
        code.jmp(large)
        code.label(sized)
        code.mov_imm(R11, 1 << 20)
        code.label(large)
        code.store(RBP, -16, R11)               # the size of the region
        code.mov_reg(RSI, R11)
        code.xor_reg(RDI, RDI)                  # nowhere in particular
        code.mov_imm(RDX, PROT_READ | PROT_WRITE)
        code.mov_imm(R10, MAP_PRIVATE | MAP_ANONYMOUS)
        code.mov_imm(R8, -1)                    # no file
        code.xor_reg(R9, R9)                    # no offset
        code.mov_imm(RAX, SYSCALL_MMAP)
        code.syscall()
        code.mov_imm(R11, -4096)
        code.cmp_reg(RAX, R11)
        code.jcc("b", mapped)                   # an address below -errno: it worked
        self.fail("out of memory allocating an array")
        code.label(mapped)
        code.lea_rip(R10, base)
        code.store(R10, 0, RAX)
        code.load(R11, RBP, -16)
        code.lea_rip(R10, left)
        code.store(R10, 0, R11)
        # Take the block: `base` moves on, `left` shrinks.
        code.label(room)
        code.lea_rip(R10, base)
        code.load(R11, R10, 0)                  # the next free byte
        code.mov_reg(RAX, R11)                  # the block
        code.load(RDX, RBP, -8)
        code.add_reg(R11, RDX)
        code.store(R10, 0, R11)
        code.lea_rip(R10, left)
        code.load(R11, R10, 0)
        code.sub_reg(R11, RDX)
        code.store(R10, 0, R11)
        code.mov_reg(RSP, RBP)
        code.pop(RBP)
        code.ret()

    def array_new_helper(self) -> None:
        """`nv_array_new(rdi = count)`: `[length][elements]`, zeroed, from the arena.

        The header is what makes an array one value: a handle is a pointer to it, so
        passing an array passes one register and the length travels with the data.
        The checks are the C runtime's, in its order — a negative length, then the
        limit — because a program that trips one has to stop in both backends with
        the same line on standard error.
        """
        code = self.code
        positive = self.local("length_positive")
        within = self.local("length_within")
        code.push(RBP)
        code.mov_reg(RBP, RSP)
        code.sub_rsp(32)
        code.store(RBP, -8, RDI)                # the count
        code.mov_imm(R11, 0)
        code.cmp_reg(RDI, R11)
        code.jcc("ge", positive)
        code.lea_rip(RDI, self.string("array length must not be negative"))
        code.call("nv_fail")
        code.label(positive)
        code.load(RDI, RBP, -8)
        code.mov_imm(R11, MAX_ELEMENTS)
        code.cmp_reg(RDI, R11)
        code.jcc("le", within)
        code.mov_imm(RSI, MAX_ELEMENTS)
        code.mov_imm(RDX, 1)                    # the length message
        code.call("nv_fail_pair")
        code.label(within)
        code.load(RDI, RBP, -8)
        code.mov_imm(R11, ELEMENT_SIZE)
        code.imul_reg(R11, RDI)                 # the elements
        code.mov_imm(RDX, 16)
        code.add_reg(R11, RDX)                  # and the header
        code.mov_reg(RDI, R11)
        code.call("nv_alloc")
        code.store(RBP, -16, RAX)
        code.load(R11, RBP, -8)
        code.store(RAX, 0, R11)                 # the length
        code.lea(R11, RAX, 16)
        code.store(RAX, 8, R11)                 # the elements
        code.load(RAX, RBP, -16)
        code.mov_reg(RSP, RBP)
        code.pop(RBP)
        code.ret()

    def fail_pair_helper(self) -> None:
        """`nv_fail_pair(rdi = a, rsi = b, rdx = kind, rcx = c)`: a numbered trap.

        Four kinds, because four runtime errors carry numbers: an index out of bounds for
        a length, a length above the limit, a byte index out of bounds for a string, and a
        slice whose two indices do not fit one. The wording is the C runtime's, character
        for character, because the differential compares standard error and a message that
        differs by a word is a difference.
        """
        code = self.code
        open_slice, open_byte, open_length = (self.local("pair_slice"),
                                              self.local("pair_byte"),
                                              self.local("pair_length"))
        first, slice_mid, byte_mid = (self.local("pair_first"), self.local("pair_slice_mid"),
                                      self.local("pair_byte_mid"))
        length_mid, bytes_tail = (self.local("pair_length_mid"),
                                  self.local("pair_bytes"))
        newline = self.local("pair_newline")
        code.push(RBP)
        code.mov_reg(RBP, RSP)
        code.sub_rsp(112)
        code.store(RBP, -8, RDI)                # a
        code.store(RBP, -16, RSI)               # b
        code.store(RBP, -24, RDX)               # kind
        code.store(RBP, -32, RCX)               # c
        for value, start_slot, length_slot, buffer in (
                (-8, -40, -48, 1), (-16, -56, -64, 2), (-32, -72, -80, 3)):
            code.load(RDI, RBP, value)
            code.lea_rip(RSI, self.number_buffer(buffer))
            code.call("nv_format")
            code.store(RBP, start_slot, RAX)
            code.store(RBP, length_slot, RDX)
        code.lea_rip(RSI, self.raw("nv_prefix", FAIL_PREFIX.encode()))
        code.mov_imm(RDX, len(FAIL_PREFIX))
        code.mov_imm(RDI, 2)
        code.mov_imm(RAX, 1)
        code.syscall()
        code.load(RAX, RBP, -24)
        code.mov_imm(R11, 3)
        code.cmp_reg(RAX, R11)
        code.jcc("e", open_slice)
        code.mov_imm(R11, 2)
        code.cmp_reg(RAX, R11)
        code.jcc("e", open_byte)
        code.mov_imm(R11, 1)
        code.cmp_reg(RAX, R11)
        code.jcc("e", open_length)
        self.write_bytes("array index ")
        code.jmp(first)
        code.label(open_length)
        self.write_bytes("array length ")
        code.jmp(first)
        code.label(open_byte)
        self.write_bytes("byte index ")
        code.jmp(first)
        code.label(open_slice)
        self.write_bytes("string slice ")
        code.label(first)
        self.write_number(-40, -48)             # a
        code.load(RAX, RBP, -24)
        code.mov_imm(R11, 3)
        code.cmp_reg(RAX, R11)
        code.jcc("ne", slice_mid)
        self.write_bytes("..")
        self.write_number(-56, -64)             # the second index of a slice
        code.label(slice_mid)
        code.load(RAX, RBP, -24)
        code.mov_imm(R11, 3)
        code.cmp_reg(RAX, R11)
        code.jcc("ne", byte_mid)
        self.write_bytes(" is out of bounds for a string of ")
        self.write_number(-72, -80)             # the length a slice did not fit
        code.jmp(bytes_tail)
        code.label(byte_mid)
        code.mov_imm(R11, 2)
        code.cmp_reg(RAX, R11)
        code.jcc("ne", length_mid)
        self.write_bytes(" is out of bounds for a string of ")
        self.write_number(-56, -64)
        code.jmp(bytes_tail)
        code.label(length_mid)
        code.mov_imm(R11, 1)
        code.cmp_reg(RAX, R11)
        code.jcc("ne", "nv_fail_pair.index_tail")
        self.write_bytes(" exceeds the limit of ")
        self.write_number(-56, -64)
        code.jmp(newline)
        code.label("nv_fail_pair.index_tail")
        self.write_bytes(" is out of bounds for length ")
        self.write_number(-56, -64)
        code.jmp(newline)
        code.label(bytes_tail)
        self.write_bytes(" bytes")
        code.label(newline)
        self.newline_to(2)
        flag = self.raw("nv_trace_flag", bytes(8), DATA)
        no_trace = self.local("no_trace")
        code.lea_rip(RAX, flag)
        code.load(RAX, RAX, 0)
        code.test_reg(RAX)
        code.jcc("e", no_trace)
        code.mov_reg(RDI, RBP)
        code.call("nv_trace_stack")
        code.label(no_trace)
        code.mov_imm(RDI, 1)
        code.mov_imm(RAX, 60)
        code.syscall()
        code.mov_reg(RSP, RBP)
        code.pop(RBP)
        code.ret()

    def write_number(self, start_slot: int, length_slot: int) -> None:
        """Write one already-formatted number to standard error."""
        code = self.code
        code.load(RSI, RBP, start_slot)
        code.load(RDX, RBP, length_slot)
        code.mov_imm(RDI, 2)
        code.mov_imm(RAX, 1)
        code.syscall()

    def write_bytes(self, text: str) -> None:
        """`write(2, text)`, for a literal that is part of a message.

        `self.string` stores a length prefix in front of the bytes, so the pointer is
        advanced past it: what goes on standard error is the text alone.
        """
        self.code.lea_rip(RSI, self.string(text))
        self.code.lea(RSI, RSI, 8)
        self.code.mov_imm(RDX, len(text.encode("utf-8")))
        self.code.mov_imm(RDI, 2)
        self.code.mov_imm(RAX, 1)
        self.code.syscall()

    def trace_helper(self) -> None:
        """`nv_trace_stack(rdi)`: walk the frame chain and name every caller.

        The record of a frame is the return address minus one — the byte *inside* the
        `call`, which is the address a symbol table and a line table both agree
        about. Frames that belong to the runtime have no record and are skipped:
        printing `nv_fail` would tell a reader nothing.

        The line table's pointers are offsets from its first byte, so the table's own
        address — `nv_trace_table` — is kept in a frame slot and added to each of them.
        """
        code = self.code
        at = self.string("  at ")
        open_paren = self.string(" (")
        colon = self.string(":")
        close = self.string(")\n")
        loop, scan, scan_next, found = (self.local("trace_frame"), self.local("trace_scan"),
                                       self.local("trace_next"), self.local("trace_found"))
        line_scan, line_done, advance, done = (
            self.local("trace_line"), self.local("trace_line_done"),
            self.local("trace_advance"), self.local("trace_done"))
        # The frame: -8 the frame we are walking, -16 its record, -24 the line,
        # -32 how deep we are, -40 the call site, -48 the table's own address.
        code.push(RBP)
        code.mov_reg(RBP, RSP)
        code.sub_rsp(80)
        code.store(RBP, -8, RDI)                # the frame to start from
        code.xor_reg(RAX, RAX)
        code.store(RBP, -32, RAX)               # depth
        code.store(RBP, -24, RAX)               # the line, chosen or zero
        code.lea_rip(R8, linker.TRACE_SYMBOL)
        code.store(RBP, -48, R8)                # the table, whose offsets we add to
        code.label(loop)
        code.load(RAX, RBP, -8)
        code.test_reg(RAX)
        code.jcc("e", done)
        code.load(RDX, RAX, 8)                  # the return address
        code.test_reg(RDX)
        code.jcc("e", done)
        code.dec(RDX)                           # the instruction after the call
        code.store(RBP, -40, RDX)
        code.load(R8, RBP, -48)
        code.load(R9, R8, 8)                    # how many functions there are
        code.lea(R10, R8, 16)
        code.xor_reg(RCX, RCX)
        code.label(scan)
        code.cmp_reg(RCX, R9)
        code.jcc("ae", advance)
        code.load(R11, R10, 0)                  # start
        code.cmp_reg(RDX, R11)
        code.jcc("b", scan_next)
        code.load(R11, R10, 8)                  # end
        code.cmp_reg(RDX, R11)
        code.jcc("b", found)
        code.label(scan_next)
        code.lea(R10, R10, 48)
        code.inc(RCX)
        code.jmp(scan)
        code.label(found)
        code.store(RBP, -16, R10)               # the record
        code.load(R10, R10, 32)                 # line entries: an offset into the table
        code.load(R11, RBP, -48)
        code.add_reg(R10, R11)
        code.load(R11, RBP, -16)
        code.load(R9, R11, 40)                  # how many line entries
        code.xor_reg(RCX, RCX)
        code.load(RDX, RBP, -40)
        code.label(line_scan)
        code.cmp_reg(RCX, R9)
        code.jcc("ae", line_done)
        code.load(R8, R10, 0)                   # the address of this entry
        code.cmp_reg(RDX, R8)
        code.jcc("b", line_done)                # entries ascend: the last one wins
        code.load(RAX, R10, 8)
        code.store(RBP, -24, RAX)
        code.lea(R10, R10, 16)
        code.inc(RCX)
        code.jmp(line_scan)
        code.label(line_done)
        code.lea_rip(RDI, at)
        code.call("nv_write")
        self.string_pointer(16)
        code.call("nv_write")
        code.lea_rip(RDI, open_paren)
        code.call("nv_write")
        self.string_pointer(24)
        code.call("nv_write")
        code.lea_rip(RDI, colon)
        code.call("nv_write")
        code.load(RDI, RBP, -24)
        code.call("nv_write_u64")
        code.lea_rip(RDI, close)
        code.call("nv_write")
        code.label(advance)
        code.load(RAX, RBP, -8)
        code.load(RAX, RAX, 0)                  # the caller's frame
        code.store(RBP, -8, RAX)
        code.load(RAX, RBP, -32)
        code.inc(RAX)
        code.store(RBP, -32, RAX)
        code.mov_imm(R11, TRACE_DEPTH)
        code.cmp_reg(RAX, R11)
        code.jcc("ae", done)
        code.jmp(loop)
        code.label(done)
        code.mov_reg(RSP, RBP)
        code.pop(RBP)
        code.ret()

    def string_pointer(self, field_offset: int) -> None:
        """Leave in `rdi` the address of a string the record at `-16` points at.

        The table's pointers are offsets from the table, so each one is added to the
        table's address, which the walk keeps at `-48`.
        """
        code = self.code
        code.load(R11, RBP, -16)
        code.load(RDI, R11, field_offset)
        code.load(RAX, RBP, -48)
        code.add_reg(RDI, RAX)

    # -- text, numbers and input ---------------------------------------------
    def str_len_helper(self) -> None:
        """`nv_str_len(rdi)` -> the byte length a `str` carries in its header.

        A native `str` is a pointer to `[length][bytes]`, so the length is a load rather
        than a scan: that is the whole difference between this and the C runtime's
        `strlen`, and it is why the two representations never have to agree.
        """
        code = self.code
        code.load(RAX, RDI, 0)
        code.ret()

    def str_cmp_helper(self) -> None:
        """`nv_str_cmp(rdi = a, rsi = b)` -> -1, 0 or 1 by the text, byte by byte.

        `strcmp`'s rule, which is the language's: bytes compared as unsigned, and a word
        that is a prefix of another sorts first.
        """
        code = self.code
        loop, prefix = self.local("cmp_loop"), self.local("cmp_prefix")
        less, greater = self.local("cmp_less"), self.local("cmp_greater")
        done, keep = self.local("cmp_done"), self.local("cmp_short")
        code.push(RBP)
        code.mov_reg(RBP, RSP)
        code.sub_rsp(32)
        code.store(RBP, -8, RDI)
        code.store(RBP, -16, RSI)
        code.load(R10, RDI, 0)                  # the length of a
        code.load(R11, RSI, 0)                  # the length of b
        code.lea(R8, RDI, 8)                    # a's bytes
        code.lea(R9, RSI, 8)                    # b's bytes
        code.cmp_reg(R10, R11)
        code.jcc("be", keep)
        code.mov_reg(R10, R11)                  # the shorter of the two
        code.label(keep)
        code.label(loop)
        code.test_reg(R10)
        code.jcc("e", prefix)
        code.load_byte(RDX, R8, 0)
        code.load_byte(RCX, R9, 0)
        code.cmp_reg(RDX, RCX)
        code.jcc("b", less)
        code.jcc("a", greater)
        code.inc(R8)
        code.inc(R9)
        code.dec(R10)
        code.jmp(loop)
        code.label(prefix)
        code.load(R8, RBP, -8)
        code.load(R8, R8, 0)                    # the length of a, again
        code.load(R9, RBP, -16)
        code.load(R9, R9, 0)                    # the length of b
        code.cmp_reg(R8, R9)
        code.jcc("b", less)
        code.jcc("a", greater)
        code.xor_reg(RAX, RAX)
        code.jmp(done)
        code.label(less)
        code.mov_imm(RAX, -1)
        code.jmp(done)
        code.label(greater)
        code.mov_imm(RAX, 1)
        code.label(done)
        code.mov_reg(RSP, RBP)
        code.pop(RBP)
        code.ret()

    def byte_at_helper(self) -> None:
        """`nv_byte_at(rdi = str, rsi = index)` -> one unsigned byte, or a trap."""
        code = self.code
        trap, inside = self.local("byte_trap"), self.local("byte_inside")
        code.push(RBP)
        code.mov_reg(RBP, RSP)
        code.sub_rsp(32)
        code.store(RBP, -8, RDI)
        code.store(RBP, -16, RSI)
        code.load(R11, RDI, 0)                  # the length
        code.xor_reg(R10, R10)
        code.cmp_reg(RSI, R10)
        code.jcc("l", trap)
        code.cmp_reg(RSI, R11)
        code.jcc("ge", trap)
        code.jmp(inside)
        code.label(trap)
        code.mov_reg(RDI, RSI)                  # the index
        code.mov_reg(RSI, R11)                  # the length
        code.mov_imm(RDX, 2)                    # kind 2: a byte of a string
        code.call("nv_fail_pair")
        code.label(inside)
        code.load(R11, RBP, -8)
        code.lea(R11, R11, 8)
        code.load(RDX, RBP, -16)
        code.add_reg(R11, RDX)
        code.load_byte(RAX, R11, 0)
        code.mov_reg(RSP, RBP)
        code.pop(RBP)
        code.ret()

    def str_concat_helper(self) -> None:
        """`nv_str_concat(rdi = a, rsi = b)` -> a new string holding both, in order."""
        code = self.code
        loop, second = self.local("cat_loop"), self.local("cat_second")
        finished = self.local("cat_done")
        code.push(RBP)
        code.mov_reg(RBP, RSP)
        code.sub_rsp(64)
        code.store(RBP, -8, RDI)                # a
        code.store(RBP, -16, RSI)               # b
        code.load(R8, RDI, 0)
        code.load(R9, RSI, 0)
        code.store(RBP, -24, R8)                # the length of a
        code.store(RBP, -32, R9)                # the length of b
        code.add_reg(R8, R9)
        code.store(RBP, -40, R8)                # the total
        code.mov_reg(RDI, R8)
        code.add_imm8(RDI, FIELD_SIZE)          # the header word, and the bytes
        code.call("nv_alloc")
        code.store(RBP, -48, RAX)               # the new string
        code.load(R11, RBP, -40)
        code.store(RAX, 0, R11)                 # its length
        code.lea(R8, RAX, 8)                    # where the bytes go
        code.load(R9, RBP, -8)
        code.lea(R9, R9, 8)                     # a's bytes
        code.load(R10, RBP, -24)
        code.label(loop)
        code.test_reg(R10)
        code.jcc("e", second)
        code.load_byte(RDX, R9, 0)
        code.store_byte(R8, 0, RDX)
        code.inc(R8)
        code.inc(R9)
        code.dec(R10)
        code.jmp(loop)
        code.label(second)
        code.load(R9, RBP, -16)
        code.lea(R9, R9, 8)                     # b's bytes
        code.load(R10, RBP, -32)
        code.label("nv_str_concat.third")
        code.test_reg(R10)
        code.jcc("e", finished)
        code.load_byte(RDX, R9, 0)
        code.store_byte(R8, 0, RDX)
        code.inc(R8)
        code.inc(R9)
        code.dec(R10)
        code.jmp("nv_str_concat.third")
        code.label(finished)
        code.load(RAX, RBP, -48)
        code.mov_reg(RSP, RBP)
        code.pop(RBP)
        code.ret()

    def str_slice_helper(self) -> None:
        """`nv_str_slice(rdi = str, rsi = start, rdx = end)` -> the bytes between.

        The check is `0 <= start <= end <= length`, and its message states all three
        numbers because that is what the C runtime states: a slice of the wrong shape is
        a mistake about two indices and one length, and naming two of the three would
        send the reader back to the source.
        """
        code = self.code
        trap, inside, loop = (self.local("slice_trap"), self.local("slice_inside"),
                              self.local("slice_loop"))
        finished = self.local("slice_done")
        code.push(RBP)
        code.mov_reg(RBP, RSP)
        code.sub_rsp(64)
        code.store(RBP, -8, RDI)                # the string
        code.store(RBP, -16, RSI)               # start
        code.store(RBP, -24, RDX)               # end
        code.load(R11, RDI, 0)                  # the length
        code.store(RBP, -32, R11)
        code.xor_reg(R10, R10)
        code.cmp_reg(RSI, R10)
        code.jcc("l", trap)
        code.cmp_reg(RSI, R11)
        code.jcc("g", trap)
        code.cmp_reg(RDX, RSI)
        code.jcc("l", trap)
        code.cmp_reg(RDX, R11)
        code.jcc("g", trap)
        code.jmp(inside)
        code.label(trap)
        code.mov_reg(RDI, RSI)                  # the start
        code.mov_reg(RSI, RDX)                  # the end
        code.load(RCX, RBP, -32)                # the length, as the third number
        code.mov_imm(RDX, 3)                    # kind 3: a slice of a string
        code.call("nv_fail_pair")
        code.label(inside)
        code.load(R10, RBP, -24)
        code.sub_reg(R10, RSI)                  # how many bytes
        code.store(RBP, -40, R10)
        code.mov_reg(RDI, R10)
        code.add_imm8(RDI, FIELD_SIZE)
        code.call("nv_alloc")
        code.store(RBP, -48, RAX)
        code.load(R11, RBP, -40)
        code.store(RAX, 0, R11)                 # the length
        code.lea(R8, RAX, 8)                    # where the bytes go
        code.load(R9, RBP, -8)
        code.lea(R9, R9, 8)
        code.load(R10, RBP, -16)
        code.add_reg(R9, R10)                   # the first byte of the slice
        code.load(R10, RBP, -40)
        code.label(loop)
        code.test_reg(R10)
        code.jcc("e", finished)
        code.load_byte(RDX, R9, 0)
        code.store_byte(R8, 0, RDX)
        code.inc(R8)
        code.inc(R9)
        code.dec(R10)
        code.jmp(loop)
        code.label(finished)
        code.load(RAX, RBP, -48)
        code.mov_reg(RSP, RBP)
        code.pop(RBP)
        code.ret()

    def str_from_i64_helper(self) -> None:
        """`nv_str_from_i64(rdi)` -> the decimal digits as a string.

        The digits are `nv_format`'s, the same routine that prints an integer, so the
        text of `str_from_i64(-42)` and the text `print(-42)` writes cannot disagree.
        """
        code = self.code
        loop, finished = self.local("from_loop"), self.local("from_done")
        code.push(RBP)
        code.mov_reg(RBP, RSP)
        code.sub_rsp(64)
        code.lea_rip(RSI, self.number_buffer(3))
        code.call("nv_format")                  # rax = the first digit, rdx = how many
        code.store(RBP, -8, RAX)
        code.store(RBP, -16, RDX)
        code.mov_reg(RDI, RDX)
        code.add_imm8(RDI, FIELD_SIZE)
        code.call("nv_alloc")
        code.store(RBP, -24, RAX)
        code.load(R11, RBP, -16)
        code.store(RAX, 0, R11)                 # the length
        code.lea(R8, RAX, 8)
        code.load(R9, RBP, -8)
        code.load(R10, RBP, -16)
        code.label(loop)
        code.test_reg(R10)
        code.jcc("e", finished)
        code.load_byte(RDX, R9, 0)
        code.store_byte(R8, 0, RDX)
        code.inc(R8)
        code.inc(R9)
        code.dec(R10)
        code.jmp(loop)
        code.label(finished)
        code.load(RAX, RBP, -24)
        code.mov_reg(RSP, RBP)
        code.pop(RBP)
        code.ret()

    def to_f64_helper(self) -> None:
        """`nv_to_f64(rdi = i64)` -> the nearest `f64`, as its bits in `rax`."""
        code = self.code
        code.cvtsi2sd(XMM0, RDI)
        code.movq_xmm_to_reg(RAX, XMM0)
        code.ret()

    def to_i64_helper(self) -> None:
        """`nv_to_i64(rdi = f64 bits)` -> the value truncated toward zero, or a trap.

        `cvttsd2si` answers `int64_min` for a `nan` and for anything outside the range,
        so the answer is converted back and compared: a value that converts to itself is
        one the machine could hold, and `-2^63` converting to itself is why the check is
        a round trip rather than a range test.
        """
        code = self.code
        done, nan, outside = (self.local("to_i64_done"), self.local("to_i64_nan"),
                              self.local("to_i64_range"))
        code.push(RBP)
        code.mov_reg(RBP, RSP)
        code.sub_rsp(16)
        code.movq_xmm(XMM0, RDI)
        code.ucomisd(XMM0, XMM0)
        code.jcc("p", nan)                      # unordered: a nan
        code.cvttsd2si(RAX, XMM0)
        code.mov_imm(R11, I64_MIN)
        code.cmp_reg(RAX, R11)
        code.jcc("ne", done)
        code.cvtsi2sd(XMM1, RAX)
        code.ucomisd(XMM0, XMM1)
        code.jcc("ne", outside)
        code.jmp(done)
        code.label(nan)
        self.fail("to_i64 of NaN")
        code.jmp(done)
        code.label(outside)
        self.fail("to_i64 of a value outside the i64 range")
        code.label(done)
        code.mov_reg(RSP, RBP)
        code.pop(RBP)
        code.ret()

    def getc_helper(self) -> None:
        """`nv_getc()` -> the next byte of standard input, or -1 at the end.

        One `read` of one byte: no buffer, so nothing is ever read that the program did
        not ask for. That is a cost (a syscall per byte) taken deliberately, because a
        buffer would have to hold back the byte that ended a token, and holding a byte
        back is the part of `getchar` that is easy to get wrong.
        """
        code = self.code
        eof, done = self.local("getc_eof"), self.local("getc_done")
        buffer = self.raw("nv_stdin_byte", bytes(1), DATA)
        code.push(RBP)
        code.mov_reg(RBP, RSP)
        code.sub_rsp(16)
        code.xor_reg(RDI, RDI)                  # stdin
        code.lea_rip(RSI, buffer)
        code.mov_imm(RDX, 1)
        code.xor_reg(RAX, RAX)                  # read
        code.syscall()
        code.mov_imm(R11, 1)
        code.cmp_reg(RAX, R11)
        code.jcc("b", eof)                      # nothing read, or the call failed
        code.lea_rip(R11, buffer)
        code.load_byte(RAX, R11, 0)
        code.jmp(done)
        code.label(eof)
        code.mov_imm(RAX, -1)
        code.label(done)
        code.mov_reg(RSP, RBP)
        code.pop(RBP)
        code.ret()

    def read_space(self, register: int, yes: str) -> None:
        """`jmp yes` when `register` holds an ASCII space character.

        The set is C's `isspace` for the default locale — space, tab, newline, vertical
        tab, form feed and carriage return — because the C runtime's reader uses it and
        the two backends have to agree about where a token ends.
        """
        code = self.code
        not_space = self.local("read_word")
        code.mov_imm(R11, 32)
        code.cmp_reg(register, R11)
        code.jcc("e", yes)
        code.mov_imm(R11, 13)
        code.cmp_reg(register, R11)
        code.jcc("a", not_space)                # above 13 is either a space or nothing
        code.mov_imm(R11, 9)
        code.cmp_reg(register, R11)
        code.jcc("ae", yes)                     # 9..13 are all whitespace
        code.label(not_space)

    def read_i64_helper(self) -> None:
        """`nv_read_i64()` -> one whitespace-delimited decimal, or the C runtime's trap.

        The four refusals are the C runtime's, in its order and with its words: no token
        at all, a token that is not decimal, one that does not fit, and one with a
        character where its end should be. Overflow is decided before it happens — the
        value is compared against `(limit - digit) / 10` — so a number one past the limit
        is a trap rather than a wrapped value.
        """
        code = self.code
        skip, no_sign, after_sign = (self.local("read_skip"), self.local("read_plain"),
                                     self.local("read_sign"))
        step, separator = self.local("read_step"), self.local("read_tail")
        finish, done = self.local("read_finish"), self.local("read_done")
        no_token = self.local("read_none")
        not_decimal = self.local("read_undecimal")
        out_of_range = self.local("read_range")
        bad_tail = self.local("read_bad")
        code.push(RBP)
        code.mov_reg(RBP, RSP)
        code.sub_rsp(64)
        code.label(skip)
        code.call("nv_getc")
        code.mov_imm(R11, -1)
        code.cmp_reg(RAX, R11)
        code.jcc("e", no_token)
        self.read_space(RAX, skip)
        # `rax` is the first character of the token.
        code.xor_reg(R10, R10)
        code.store(RBP, -16, R10)               # not negative
        code.mov_imm(R11, 45)                   # '-'
        code.cmp_reg(RAX, R11)
        code.jcc("ne", no_sign)
        code.mov_imm(R10, 1)
        code.store(RBP, -16, R10)               # negative
        code.call("nv_getc")
        code.jmp(after_sign)
        code.label(no_sign)
        code.mov_imm(R11, 43)                   # '+'
        code.cmp_reg(RAX, R11)
        code.jcc("ne", after_sign)
        code.call("nv_getc")
        code.label(after_sign)
        code.mov_imm(R11, 48)                   # '0'
        code.cmp_reg(RAX, R11)
        code.jcc("b", not_decimal)
        code.mov_imm(R11, 57)                   # '9'
        code.cmp_reg(RAX, R11)
        code.jcc("a", not_decimal)
        code.mov_imm(R10, I64_MAX)
        code.load(R11, RBP, -16)
        code.test_reg(R11)
        code.jcc("e", step)
        code.add_imm8(R10, 1)                   # a negative may reach one further
        code.label(step)
        code.store(RBP, -24, R10)               # the limit
        code.xor_reg(R11, R11)
        code.store(RBP, -8, R11)                # the value
        code.label("nv_read_i64.digit")
        code.mov_reg(R10, RAX)
        code.mov_imm(R11, 48)
        code.sub_reg(R10, R11)                  # the digit
        code.load(R11, RBP, -24)
        code.sub_reg(R11, R10)                  # the limit, less this digit
        code.mov_reg(RAX, R11)
        code.xor_reg(RDX, RDX)
        code.mov_imm(R9, 10)
        code.div(R9)                            # (limit - digit) / 10
        code.load(R11, RBP, -8)
        code.cmp_reg(R11, RAX)
        code.jcc("a", out_of_range)
        code.mov_imm(R9, 10)
        code.imul_reg(R11, R9)
        code.add_reg(R11, R10)
        code.store(RBP, -8, R11)
        code.call("nv_getc")
        code.mov_imm(R11, -1)
        code.cmp_reg(RAX, R11)
        code.jcc("e", finish)
        code.mov_imm(R11, 48)
        code.cmp_reg(RAX, R11)
        code.jcc("b", separator)
        code.mov_imm(R11, 57)
        code.cmp_reg(RAX, R11)
        code.jcc("be", "nv_read_i64.digit")
        code.label(separator)
        self.read_space(RAX, finish)            # a space ends the token
        code.jmp(bad_tail)                      # anything else is refused
        code.label(finish)
        code.load(RAX, RBP, -8)
        code.load(R11, RBP, -16)
        code.test_reg(R11)
        code.jcc("e", done)
        code.mov_imm(R10, I64_MIN)
        code.cmp_reg(RAX, R10)
        code.jcc("e", done)                     # exactly 2^63: the answer is int64_min
        code.neg(RAX)
        code.jmp(done)
        code.label(no_token)
        self.fail("expected an i64, reached end of input")
        code.jmp(finish)
        code.label(not_decimal)
        self.fail("expected a decimal i64")
        code.jmp(finish)
        code.label(out_of_range)
        self.fail("input outside i64 range")
        code.jmp(finish)
        code.label(bad_tail)
        self.fail("invalid character in i64 input")
        code.label(done)
        code.mov_reg(RSP, RBP)
        code.pop(RBP)
        code.ret()

    def fail_helper(self) -> None:
        """`nv_fail(rdi)`: the C runtime's message on stderr, then the trace, then exit."""
        code = self.code
        prefix = self.raw("nv_prefix", FAIL_PREFIX.encode())
        flag = self.raw("nv_trace_flag", bytes(8), DATA)
        no_trace = self.local("no_trace")
        code.push(RBP)
        code.mov_reg(RBP, RSP)
        code.sub_rsp(16)
        code.store(RBP, -8, RDI)                # keep the message across the syscall
        code.lea_rip(RSI, prefix)
        code.mov_imm(RDX, len(FAIL_PREFIX))
        code.mov_imm(RDI, 2)
        code.mov_imm(RAX, 1)
        code.syscall()
        code.load(RAX, RBP, -8)
        code.load(RDX, RAX, 0)                  # the length-prefixed message
        code.lea(RSI, RAX, 8)
        code.mov_imm(RDI, 2)
        code.mov_imm(RAX, 1)
        code.syscall()
        self.newline_to(2)
        code.lea_rip(RAX, flag)
        code.load(RAX, RAX, 0)
        code.test_reg(RAX)
        code.jcc("e", no_trace)
        code.mov_reg(RDI, RBP)                  # our own frame: the walk starts above it
        code.call("nv_trace_stack")
        code.label(no_trace)
        code.mov_imm(RDI, 1)
        code.mov_imm(RAX, 60)
        code.syscall()
        code.ret()                              # unreachable; keeps the assembler happy


def frame_size(slots: int) -> int:
    """Frame bytes: every slot is eight bytes, rounded up to keep `rsp` aligned."""
    return max(16, (8 * slots + 15) // 16 * 16)


def units(module: ir.Module, sources: dict[str, str] | None = None):
    """The units of one program: one per module, then the runtime."""
    return Generator(module, sources).units()


def generate(module: ir.Module, sources: dict[str, str] | None = None) -> bytes:
    """IR to the bytes of a static x86-64 Linux executable."""
    return linker.link(units(module, sources), sources=sources).bytes
