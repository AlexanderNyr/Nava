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
from ..model import (is_map, is_set, is_vec, map_parts, set_element, vec_element)
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
#: A `Vec(T)` is a handle to three words: how many elements the buffer holds, how
#: many of them the program has, and where they are. `capacity` is at 0 so that a
#: table and a vector read their count from the same place.
VEC_CAPACITY, VEC_LENGTH, VEC_DATA, VEC_SIZE = 0, 8, 16, 24
#: A `HashMap(K, V)` and a `HashSet(T)` are the same handle: a power-of-two capacity,
#: how many entries are live, how many of the rest are tombstones, and the slots.
TABLE_CAPACITY, TABLE_LENGTH, TABLE_TOMBS, TABLE_SLOTS, TABLE_SIZE = 0, 8, 16, 24, 32
#: One position of a map holds a key, the value it maps to and its state; a set
#: holds a key and the state. The state is one byte: 0 empty, 1 live, 2 removed.
MAP_KEY, MAP_VALUE, MAP_STATE, MAP_SLOT = 0, 8, 16, 24
SET_KEY, SET_STATE, SET_SLOT = 0, 8, 16
EMPTY, LIVE, TOMBSTONE = 0, 1, 2

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
        self.types: dict[str, str] = {}
        self.current: ir.Function | None = None
        #: Which builtins the program actually calls, cached: a routine that nothing
        #: calls is not emitted, which is why `vec_pop` on a vector nobody pops from
        #: does not drag the `Option` it answers into the module.
        self._builtins: set[str] | None = None
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
        #: The type of every value this function defines, so an instruction can ask
        #: what it is holding: an array and a vector read their length from different
        #: words of their handle, and a container builtin's name is a fact about the
        #: type of the value it is called on.
        self.types = ir.value_types(function)
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
                                            ir.Builtin, ir.TextCompare, ir.Variant,
                                            ir.Tag, ir.Payload, ir.Slice, ir.Slots,
                                            ir.Live, ir.KeyAt)):
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
        if isinstance(instruction, ir.Slice):
            self.slice(instruction)
            return
        if isinstance(instruction, ir.Length):
            self.length(instruction)
            return
        if isinstance(instruction, ir.Slots):
            # `slots`, not `self.slots`: the frame's slots are the other `slots`.
            self.table_slots(instruction)
            return
        if isinstance(instruction, ir.Live):
            self.live(instruction)
            return
        if isinstance(instruction, ir.KeyAt):
            self.key_at(instruction)
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
        if isinstance(instruction, ir.Variant):
            self.variant(instruction)
            return
        if isinstance(instruction, ir.Tag):
            self.tag(instruction)
            return
        if isinstance(instruction, ir.Payload):
            self.payload(instruction)
            return
        if isinstance(instruction, ir.Unreachable):
            # The program proved nothing arrives here. If a lowering bug arrives
            # anyway, a message is better than executing whatever follows.
            self.fail("reached a block the program proved unreachable")
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

    def variant(self, instruction: ir.Variant) -> None:
        """`Shape::Circle(2)`: one record whose first word is the tag.

        The tag is in the record rather than beside it, so a value travelling through a
        parameter or an array carries which variant it is — an enum is one word in the
        IR and one allocation here, whatever it holds.
        """
        code = self.code
        code.mov_imm(RDI, FIELD_SIZE * (1 + len(instruction.args)))
        code.call("nv_alloc")
        self.store(instruction.value, RAX)
        code.mov_imm(R11, instruction.tag)
        code.store(RAX, 0, R11)
        for index, argument in enumerate(instruction.args):
            self.load(argument, R11)
            self.load(instruction.value, RAX)   # the record, again: `load` uses rax
            code.store(RAX, FIELD_SIZE * (index + 1), R11)

    def tag(self, instruction: ir.Tag) -> None:
        """Which variant a value is: the first word of its record."""
        self.load(instruction.operand, RAX)
        self.code.load(RAX, RAX, 0)
        self.store(instruction.value, RAX)

    def payload(self, instruction: ir.Payload) -> None:
        """One field a variant carries: the word after the tag, by position."""
        self.load(instruction.operand, RAX)
        self.code.load(RAX, RAX, FIELD_SIZE * (instruction.index + 1))
        self.store(instruction.value, RAX)

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

    #: The container builtins, with how many arguments each takes and what it
    #: answers. A name on this list and not on the native runtime's own list is a
    #: gap in one backend, which is a refusal with the builtin's name.
    CONTAINER_BUILTINS = {
        "vec_new": 0, "vec_len": 1, "vec_capacity": 1, "vec_push": 2, "vec_pop": 1,
        "vec_get": 2, "vec_set": 3,
        "map_new": 0, "map_len": 1, "map_contains": 2, "map_get": 2, "map_set": 3,
        "map_remove": 2, "map_clear": 1,
        "set_new": 0, "set_len": 1, "set_contains": 2, "set_add": 2, "set_remove": 2,
        "set_clear": 1,
    }

    def builtin(self, instruction: ir.Builtin) -> None:
        """One call, with the arguments in the ABI's registers, exactly like a function.

        A container builtin's name is a fact about the type of the value it is
        called on: `vec_push` on a `Vec(i64)` and on a `Vec(str)` are two different
        functions, because the element and the hash are known here rather than at
        run time. Two of them — `len` and `capacity` — read a word of the handle and
        cost no call at all, which is what the C backend prints for them too.
        """
        name = instruction.name
        arity = self.CONTAINER_BUILTINS.get(name)
        if arity is not None:
            self.container_builtin(instruction, arity)
            return
        routine = BUILTIN_ROUTINES.get(name)
        if routine is None:
            raise make(
                "E0901",
                f"the native backend does not implement the builtin "
                f"'{name}'",
                None,
                hint="build with the C backend (the default): `nava build` without "
                     "--backend native",
            )
        symbol, arity = routine
        if len(instruction.args) != arity:
            raise AssertionError(
                f"the IR states {name} with {len(instruction.args)} "
                f"argument(s); the runtime implements {arity}")
        for index, argument in enumerate(instruction.args):
            self.load(argument, ARGUMENT_REGISTERS[index])
        self.code.call(symbol)
        if instruction.typ != "void":
            self.store(instruction.value, RAX)

    def container_builtin(self, instruction: ir.Builtin, arity: int) -> None:
        """A `Vec`, `HashMap` or `HashSet` builtin: the family is the value's type."""
        code = self.code
        name = instruction.name
        if len(instruction.args) != arity:
            raise AssertionError(
                f"the IR states {name} with {len(instruction.args)} argument(s); "
                f"the runtime implements {arity}")
        typ = (self.types.get(instruction.args[0]) if arity else instruction.typ)
        if not (is_vec(typ) or is_map(typ) or is_set(typ)):
            raise make(
                "E0901",
                f"the native backend does not implement '{name}' on a {typ}",
                None,
                hint="build with the C backend (the default): `nava build` without "
                     "--backend native",
            )
        if arity == 0:
            symbol = (f"nv_vec_{self.vector_tag(typ)}_new" if is_vec(typ)
                      else self.table_symbol(typ, "new"))
            code.call(symbol)
            self.store(instruction.value, RAX)
            return
        if name in ("vec_len", "map_len", "set_len"):
            self.load(instruction.args[0], RAX)
            code.load(RAX, RAX, VEC_LENGTH)
            self.store(instruction.value, RAX)
            return
        if name == "vec_capacity":
            self.load(instruction.args[0], RAX)
            code.load(RAX, RAX, VEC_CAPACITY)
            self.store(instruction.value, RAX)
            return
        for index, argument in enumerate(instruction.args):
            self.load(argument, ARGUMENT_REGISTERS[index])
        code.call(self.container_routine(name, typ))
        if instruction.typ != "void":
            self.store(instruction.value, RAX)

    def container_routine(self, name: str, typ: str) -> str:
        """The symbol of one container builtin on one container type."""
        kind = "vec" if is_vec(typ) else ("map" if is_map(typ) else "set")
        routine = name[len(kind) + 1:]
        if kind == "vec":
            return f"nv_vec_{self.vector_tag(typ)}_{routine}"
        return self.table_symbol(typ, "set" if routine == "set" else routine)

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
        struct = self.struct_type_of(self.element_type(instruction.typ))
        for offset, item in enumerate(instruction.items):
            self.load(item, R11)
            if struct is not None:
                # A struct element is copied as it enters the array, exactly as a field
                # of a construction is: two elements naming one record must stay two.
                code.mov_reg(RDI, R11)
                code.call(self.copy_helper(struct))
                code.mov_reg(R11, RAX)
            self.load(instruction.value, RAX)
            code.store(RAX, 16 + ELEMENT_SIZE * offset, R11)

    def array_zeros(self, instruction: ir.ArrayZeros) -> None:
        code = self.code
        self.load(instruction.count, RDI)
        code.call("nv_array_new")
        self.store(instruction.value, RAX)
        element = self.element_type(instruction.typ)
        if self.struct_type_of(element) is not None:
            # A struct element is an address, so a zeroed array of them needs one zeroed
            # record each: a null pointer would read its fields out of nothing.
            self.load(instruction.value, RDI)
            self.load(instruction.count, RSI)
            code.mov_imm(RDX, FIELD_SIZE * max(1, len(self.layout(element))))
            code.call("nv_fill_structs")

    @staticmethod
    def element_type(typ: str) -> str:
        """`[T]` -> `T`. An array type is always bracketed; nothing else is."""
        return typ[1:-1].strip() if typ.startswith("[") and typ.endswith("]") else typ

    def struct_type_of(self, typ: str) -> str | None:
        """`typ` if it is a struct this module laid out, else None."""
        return typ if typ in self.layouts() else None

    def slice(self, instruction: ir.Slice) -> None:
        """`a[i..j]`: a header whose element pointer points into the view's elements.

        A view, not a copy: the parent owns the storage and the binding that holds the
        slice is borrowed, so nothing releases it. Two words and one check, which is what
        the C backend does with `nv_arr_T_slice`.
        """
        self.load(instruction.base, RDI)
        self.load(instruction.start, RSI)
        self.load(instruction.end, RDX)
        self.code.call("nv_array_view")
        self.store(instruction.value, RAX)

    def length(self, instruction: ir.Length) -> None:
        """How many elements: the first word of an array's handle, or a vector's second.

        An array is `[length][elements]` and a vector, a map and a set are
        `[capacity][length]…`, so where the count lives is a fact about the type and
        this is the one place that knows it.
        """
        self.load(instruction.base, RAX)
        self.code.load(RAX, RAX, self.count_offset(self.types.get(instruction.base)))
        self.store(instruction.value, RAX)

    @staticmethod
    def count_offset(typ: str | None) -> int:
        """Where a handle keeps the number of elements it holds."""
        return 0 if typ is not None and typ.startswith("[") else VEC_LENGTH

    def index(self, instruction: ir.Index) -> None:
        code = self.code
        base_type = self.types.get(instruction.base)
        length_at = self.count_offset(base_type)
        data_at = 8 if base_type is not None and base_type.startswith("[") else VEC_DATA
        self.load(instruction.base, RAX)
        code.load(R11, RAX, length_at)          # the length
        self.load(instruction.index, R10)       # the index
        self.array_bounds()
        self.load(instruction.base, RAX)
        code.load(R11, RAX, data_at)            # the elements
        code.mov_imm(R9, ELEMENT_SIZE)
        code.imul_reg(R10, R9)
        code.add_reg(R11, R10)
        code.load(RAX, R11, 0)
        self.store(instruction.value, RAX)

    def index_store(self, instruction: ir.IndexStore) -> None:
        code = self.code
        base_type = self.types.get(instruction.base)
        length_at = self.count_offset(base_type)
        data_at = 8 if base_type is not None and base_type.startswith("[") else VEC_DATA
        self.load(instruction.base, RAX)
        code.load(R11, RAX, length_at)
        self.load(instruction.index, R10)
        self.array_bounds()
        self.load(instruction.value, R9)
        struct = self.struct_type_of(self.element_type(instruction.typ))
        if struct is not None:
            code.mov_reg(RDI, R9)
            code.call(self.copy_helper(struct))
            code.mov_reg(R9, RAX)
        # The copy is a call, and a call keeps nothing caller-saved: the base, the index
        # and the pointer into the elements are all read again afterwards.
        self.load(instruction.base, RAX)
        code.load(R11, RAX, data_at)
        self.load(instruction.index, R10)
        code.mov_imm(R8, ELEMENT_SIZE)
        code.imul_reg(R10, R8)
        code.add_reg(R11, R10)
        code.store(R11, 0, R9)

    def check_index(self, instruction: ir.CheckIndex) -> None:
        """`check a[i]`: the bounds check on its own, so an order can be stated."""
        code = self.code
        self.load(instruction.base, RAX)
        code.load(R11, RAX, self.count_offset(self.types.get(instruction.base)))
        self.load(instruction.index, R10)
        self.array_bounds()

    def table_slots(self, instruction: ir.Slots) -> None:
        """How many positions a table has: the first word of its handle."""
        self.load(instruction.base, RAX)
        self.code.load(RAX, RAX, TABLE_CAPACITY)
        self.store(instruction.value, RAX)

    def live(self, instruction: ir.Live) -> None:
        """Is there an entry in that position? The state byte says which."""
        code = self.code
        _key, _size, state = self.table_parts(self.types[instruction.base])
        self.slot_address(instruction.base, instruction.index)
        code.load_byte(RAX, R11, state)
        code.mov_imm(R10, LIVE)
        code.cmp_reg(RAX, R10)
        code.setcc("e", RAX)
        code.movzx_byte(RAX, RAX)
        self.store(instruction.value, RAX)

    def key_at(self, instruction: ir.KeyAt) -> None:
        """The key stored in that position — the word a `for` over a table binds."""
        self.slot_address(instruction.base, instruction.index)
        self.code.load(RAX, R11, 0)
        self.store(instruction.value, RAX)

    def slot_address(self, base: str, index: str) -> None:
        """R11 = the address of one slot: `slots + index * slot_size`."""
        code = self.code
        _key, size, _state = self.table_parts(self.types[base])
        self.load(base, RAX)
        code.load(R11, RAX, TABLE_SLOTS)
        self.load(index, RCX)
        code.mov_imm(R10, size)
        code.imul_reg(RCX, R10)
        code.add_reg(R11, RCX)

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
        if instruction.typ == "f64":
            # The text is the shortest decimal that reads back as this value, which is
            # what the C runtime computes: see `nv_format_f64`.
            self.load(instruction.operand, RDI)
            code = self.code
            code.lea_rip(RSI, self.f64_buffer())
            code.call("nv_format_f64")
            code.mov_reg(RSI, RAX)
            code.mov_reg(RDX, RDX)
            code.mov_imm(RDI, 1)
            code.mov_imm(RAX, 1)
            code.syscall()
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
    # -- containers ---------------------------------------------------------
    #
    # A `Vec(T)` and a `HashMap`/`HashSet` are the two owning containers the
    # language has, and they are the same shape here as in the C runtime: a
    # handle to a buffer, and a table of slots probed by a hash of the key.
    # What is generated per type is the family of routines, because the slot
    # layout, the hash and the equality are facts about the key, not about the
    # machine — and the alternative, a runtime that switches on a type tag at
    # run time, would be a third implementation of the language's types.

    def _mangled(self, text: str) -> str:
        """`Vec(i64)` -> `Vec_i64`: the identifier-safe form of a type."""
        return ("".join(character if character.isalnum() else "_" for character in text)
                .replace("__", "_").strip("_"))

    def vector_tag(self, typ: str) -> str:
        return self._mangled(vec_element(typ))

    def map_tag(self, typ: str) -> str:
        key, value = map_parts(typ)
        return f"{self._mangled(key)}_{self._mangled(value)}"

    def set_tag(self, typ: str) -> str:
        return self._mangled(set_element(typ))

    def table_parts(self, typ: str) -> tuple[str, int, int]:
        """A table's key type, its slot's size and where the state byte sits."""
        if is_map(typ):
            return map_parts(typ)[0], MAP_SLOT, MAP_STATE
        return set_element(typ), SET_SLOT, SET_STATE

    def enum_tag(self, enum: str, variant: str) -> int:
        """The number the checker gave a variant — the module states the layout."""
        variants = self.module.enums.get(enum)
        if variants is None:
            raise AssertionError(f"the module does not lay out the enum '{enum}'")
        for index, (name, _types) in enumerate(variants):
            if name == variant:
                return index
        raise AssertionError(f"the enum '{enum}' has no variant '{variant}'")

    def option_record(self, enum: str, some: bool, value_slot: int = 0) -> None:
        """An `Option(T)` as a record: the tag first, then the payload word.

        The value is read from a slot of the caller's frame, because `nv_alloc`
        keeps nothing caller-saved and the payload has to survive the call.
        """
        code = self.code
        code.push(RDI)
        code.mov_imm(RDI, FIELD_SIZE * 2)
        code.call("nv_alloc")
        code.pop(RDI)
        code.mov_imm(R10, self.enum_tag(enum, "Some" if some else "None"))
        code.store(RAX, 0, R10)
        if some:
            code.load(R11, RBP, value_slot)
            code.store(RAX, FIELD_SIZE, R11)

    def hash_key(self, key: str, register: int) -> None:
        """`nv_hash_i64` or `nv_hash_str`: the C runtime's two functions, in asm."""
        code = self.code
        code.mov_reg(RDI, register)
        code.call("nv_hash_str" if key == "str" else "nv_hash_i64")

    def keys_equal(self, key: str, left: int, right: int) -> None:
        """Set ZF when two keys are the same key, by value, however they are stored."""
        code = self.code
        if key == "str":
            code.push(RDI)
            code.push(RSI)
            code.mov_reg(RDI, left)
            code.mov_reg(RSI, right)
            code.call("nv_str_cmp")
            code.mov_reg(R11, RAX)
            code.pop(RSI)
            code.pop(RDI)
            code.mov_imm(R10, 0)
            code.cmp_reg(R11, R10)
            return
        code.cmp_reg(left, right)

    # -- the vector family ---------------------------------------------------
    def vec_new(self, typ: str) -> None:
        """`nv_vec_T_new(void)`: an empty handle, zeroed by the arena."""
        code = self.code
        code.push(RBP)
        code.mov_reg(RBP, RSP)
        code.sub_rsp(16)
        code.mov_imm(RDI, VEC_SIZE)
        code.call("nv_alloc")
        code.mov_reg(RSP, RBP)
        code.pop(RBP)
        code.ret()

    def vec_reserve(self, typ: str) -> None:
        """`nv_vec_T_reserve(rdi = vector, rsi = needed)`: grow by doubling.

        The capacity doubles until it holds what is needed, which is what makes a
        run of pushes cost amortised constant time per element. The new buffer is
        zeroed by the arena, and the elements are copied across by hand — a
        `memcpy` is a library call and this runtime makes none.
        """
        code = self.code
        sized, grow, sized_ok, move, copied, done = (
            self.local("vec_sized"), self.local("vec_grow"), self.local("vec_alloc"),
            self.local("vec_move"), self.local("vec_copied"), self.local("vec_done"))
        code.push(RBP)
        code.mov_reg(RBP, RSP)
        code.sub_rsp(48)
        code.store(RBP, -8, RDI)                # the vector
        code.store(RBP, -16, RSI)               # how many elements are needed
        code.load(R11, RDI, VEC_CAPACITY)
        code.cmp_reg(RSI, R11)
        code.jcc("le", done)
        code.load(R10, RBP, -8)
        code.load(R10, R10, VEC_CAPACITY)
        code.mov_imm(R11, 0)
        code.cmp_reg(R10, R11)
        code.jcc("g", sized)
        code.mov_imm(R10, 4)                    # the first capacity
        code.label(sized)
        code.label(grow)
        code.load(R11, RBP, -16)
        code.cmp_reg(R10, R11)
        code.jcc("ge", sized_ok)
        code.mov_reg(RAX, R10)
        code.mov_imm(R11, 2)
        code.imul_reg(RAX, R11)
        self.overflow("mul")
        code.mov_reg(R10, RAX)
        code.jmp(grow)
        code.label(sized_ok)
        # The limit is the array's: a buffer is an array of elements, and the
        # message the C runtime prints for one that is too long is the language's.
        within = self.local("grow_within")
        code.mov_imm(R11, MAX_ELEMENTS)
        code.cmp_reg(R10, R11)
        code.jcc("le", within)
        code.mov_reg(RDI, R10)
        code.mov_imm(RSI, MAX_ELEMENTS)
        code.mov_imm(RDX, 1)
        code.call("nv_fail_pair")
        code.label(within)
        code.store(RBP, -24, R10)               # the capacity
        code.mov_reg(RAX, R10)
        code.mov_imm(R11, ELEMENT_SIZE)
        code.imul_reg(RAX, R11)
        self.overflow("mul")
        code.mov_reg(RDI, RAX)
        code.call("nv_alloc")
        code.store(RBP, -32, RAX)               # the new buffer
        code.load(R8, RBP, -32)                 # where the elements go
        code.load(R9, RBP, -8)
        code.load(RCX, R9, VEC_DATA)            # the old buffer
        code.load(R11, R9, VEC_LENGTH)          # how many to move
        code.label(move)
        code.test_reg(R11)
        code.jcc("e", copied)
        code.load(RAX, RCX, 0)
        code.store(R8, 0, RAX)
        code.add_imm8(R8, ELEMENT_SIZE)
        code.add_imm8(RCX, ELEMENT_SIZE)
        code.dec(R11)
        code.jmp(move)
        code.label(copied)
        code.load(R9, RBP, -8)
        code.load(R10, RBP, -32)
        code.store(R9, VEC_DATA, R10)
        code.load(R10, RBP, -24)
        code.store(R9, VEC_CAPACITY, R10)
        code.label(done)
        code.mov_reg(RSP, RBP)
        code.pop(RBP)
        code.ret()

    def vec_push(self, typ: str) -> None:
        """`nv_vec_T_push(rdi = vector, rsi = value)`: room for one more, then it."""
        code = self.code
        code.push(RBP)
        code.mov_reg(RBP, RSP)
        code.sub_rsp(32)
        code.store(RBP, -8, RDI)
        code.store(RBP, -16, RSI)
        code.load(RAX, RDI, VEC_LENGTH)
        code.mov_imm(R11, 1)
        code.add_reg(RAX, R11)
        self.overflow("add")
        code.mov_reg(RSI, RAX)
        code.call("nv_vec_" + self.vector_tag(typ) + "_reserve")
        code.load(R9, RBP, -8)
        code.load(R10, R9, VEC_DATA)
        code.load(R11, R9, VEC_LENGTH)
        code.load(RCX, RBP, -16)
        code.mov_reg(RAX, R11)
        code.mov_imm(R8, ELEMENT_SIZE)
        code.imul_reg(RAX, R8)
        code.add_reg(R10, RAX)
        code.store(R10, 0, RCX)
        code.load(R11, R9, VEC_LENGTH)
        code.add_imm8(R11, 1)
        code.store(R9, VEC_LENGTH, R11)
        code.mov_reg(RSP, RBP)
        code.pop(RBP)
        code.ret()

    def vec_pop(self, typ: str) -> None:
        """`nv_vec_T_pop(rdi = vector)`: `Some(last)` or `None` when there is none."""
        code = self.code
        empty = self.local("vec_empty")
        element = vec_element(typ)
        code.push(RBP)
        code.mov_reg(RBP, RSP)
        code.sub_rsp(32)
        code.store(RBP, -8, RDI)
        code.load(R11, RDI, VEC_LENGTH)
        code.mov_imm(R10, 0)
        code.cmp_reg(R11, R10)
        code.jcc("e", empty)
        code.load(R9, RBP, -8)
        code.load(R11, R9, VEC_LENGTH)
        code.add_imm8(R11, -1)
        code.store(R9, VEC_LENGTH, R11)
        code.load(R10, R9, VEC_DATA)
        code.mov_reg(RAX, R11)
        code.mov_imm(R8, ELEMENT_SIZE)
        code.imul_reg(RAX, R8)
        code.add_reg(R10, RAX)
        code.load(RAX, R10, 0)
        code.store(RBP, -16, RAX)
        self.option_record(f"Option({element})", True, -16)
        code.mov_reg(RSP, RBP)
        code.pop(RBP)
        code.ret()
        code.label(empty)
        self.option_record(f"Option({element})", False)
        code.mov_reg(RSP, RBP)
        code.pop(RBP)
        code.ret()

    def vec_get(self, typ: str) -> None:
        """`nv_vec_T_get(rdi = vector, rsi = index)`: a checked read of one element."""
        code = self.code
        code.push(RBP)
        code.mov_reg(RBP, RSP)
        code.sub_rsp(32)
        code.store(RBP, -8, RDI)
        code.store(RBP, -16, RSI)
        self.bounded(RSI, RDI, VEC_LENGTH)
        code.load(R9, RBP, -8)
        code.load(R10, R9, VEC_DATA)
        code.load(RAX, RBP, -16)
        code.mov_imm(R8, ELEMENT_SIZE)
        code.imul_reg(RAX, R8)
        code.add_reg(R10, RAX)
        code.load(RAX, R10, 0)
        code.mov_reg(RSP, RBP)
        code.pop(RBP)
        code.ret()

    def vec_set(self, typ: str) -> None:
        """`nv_vec_T_set(rdi = vector, rsi = index, rdx = value)`: a checked write.

        The index is checked before the value is used, which is the language's
        order: a write that cannot happen must not be observed doing anything.
        """
        code = self.code
        code.push(RBP)
        code.mov_reg(RBP, RSP)
        code.sub_rsp(48)
        code.store(RBP, -8, RDI)
        code.store(RBP, -16, RSI)
        code.store(RBP, -24, RDX)
        self.bounded(RSI, RDI, VEC_LENGTH)
        code.load(R9, RBP, -8)
        code.load(R10, R9, VEC_DATA)
        code.load(RAX, RBP, -16)
        code.mov_imm(R8, ELEMENT_SIZE)
        code.imul_reg(RAX, R8)
        code.add_reg(R10, RAX)
        code.load(RCX, RBP, -24)
        code.store(R10, 0, RCX)
        code.mov_reg(RSP, RBP)
        code.pop(RBP)
        code.ret()

    def bounded(self, index: int, handle: int, length_offset: int) -> None:
        """`0 <= index < length`, or the C runtime's message for the index."""
        code = self.code
        trap = self.local("bounded")
        inside = self.local("inside")
        code.load(R11, handle, length_offset)
        code.mov_imm(R10, 0)
        code.cmp_reg(index, R10)
        code.jcc("l", trap)
        code.cmp_reg(index, R11)
        code.jcc("ge", trap)
        code.jmp(inside)
        code.label(trap)
        code.mov_reg(RDI, index)
        code.mov_reg(RSI, R11)
        code.xor_reg(RDX, RDX)
        code.call("nv_fail_pair")
        code.label(inside)

    # -- the table family ----------------------------------------------------
    def table_new(self, typ: str) -> None:
        """`nv_map_T_new` / `nv_set_T_new`: an empty table, zeroed by the arena."""
        code = self.code
        code.push(RBP)
        code.mov_reg(RBP, RSP)
        code.sub_rsp(16)
        code.mov_imm(RDI, TABLE_SIZE)
        code.call("nv_alloc")
        code.mov_reg(RSP, RBP)
        code.pop(RBP)
        code.ret()

    def table_find(self, typ: str) -> None:
        """`nv_map_T_find(rdi = table, rsi = key)`: rax = the slot, rdx = whether it is there.

        Linear probing from the hash, with a tombstone remembered on the way: a
        removal may not break the probe chain of a key that collided with it, so
        the first tombstone passed is where a new key goes and the search keeps
        going until an empty slot says the key is not in the table.
        """
        code = self.code
        key, size, state = self.table_parts(typ)
        probe, tomb, check, hit, gone = (self.local("find_probe"),
                                         self.local("find_tomb"), self.local("find_check"),
                                         self.local("find_hit"), self.local("find_gone"))
        found, onward, missed = (self.local("find_found"), self.local("find_on"),
                                 self.local("find_missed"))
        code.push(RBP)
        code.mov_reg(RBP, RSP)
        code.sub_rsp(64)
        code.store(RBP, -8, RDI)                # the table
        code.store(RBP, -16, RSI)               # the key
        code.load(R11, RDI, TABLE_CAPACITY)
        code.store(RBP, -24, R11)               # the capacity
        code.add_imm8(R11, -1)
        code.store(RBP, -32, R11)               # the mask
        code.mov_imm(R11, -1)
        code.store(RBP, -40, R11)               # the tombstone, or -1
        code.mov_imm(R11, 0)
        code.store(RBP, -48, R11)               # the step
        self.hash_key(key, RSI)
        code.mov_reg(R10, RAX)
        code.load(R11, RBP, -32)
        code.and_reg(R10, R11)
        code.store(RBP, -56, R10)               # where the probe starts
        code.label(probe)
        code.load(R10, RBP, -48)
        code.load(R11, RBP, -24)
        code.cmp_reg(R10, R11)
        code.jcc("ge", missed)
        code.load(R9, RBP, -8)
        code.load(R9, R9, TABLE_SLOTS)
        code.load(R10, RBP, -56)
        code.mov_reg(RAX, R10)
        code.mov_imm(R11, size)
        code.imul_reg(RAX, R11)
        code.add_reg(R9, RAX)
        code.store(RBP, -64, R9)                # the slot
        code.load_byte(RAX, R9, state)
        code.mov_imm(R11, EMPTY)
        code.cmp_reg(RAX, R11)
        code.jcc("e", gone)
        code.mov_imm(R11, TOMBSTONE)
        code.cmp_reg(RAX, R11)
        code.jcc("e", tomb)
        code.load(RDI, R9, 0)                   # the stored key
        code.load(RSI, RBP, -16)
        self.keys_equal(key, RDI, RSI)
        code.jcc("e", found)
        code.label(onward)
        code.load(R10, RBP, -56)
        code.add_imm8(R10, 1)
        code.load(R11, RBP, -32)
        code.and_reg(R10, R11)
        code.store(RBP, -56, R10)
        code.load(R10, RBP, -48)
        code.add_imm8(R10, 1)
        code.store(RBP, -48, R10)
        code.jmp(probe)
        code.label(tomb)
        code.load(R11, RBP, -40)
        code.mov_imm(R10, 0)
        code.cmp_reg(R11, R10)
        code.jcc("ge", onward)
        code.load(R10, RBP, -56)
        code.store(RBP, -40, R10)
        code.jmp(onward)
        code.label(gone)
        # An empty slot ends the search: the key is not here, and it belongs where
        # the first tombstone on the way was — not here, because a tombstone is
        # where a key that collided with it would have probed first.
        code.load(RAX, RBP, -40)                # the first tombstone, or -1
        code.mov_imm(R11, 0)
        code.cmp_reg(RAX, R11)
        code.jcc("ge", check)
        code.load(RAX, RBP, -56)                # no tombstone: the empty slot
        code.label(check)
        code.mov_imm(RDX, 0)
        code.jmp(hit)
        code.label(found)
        code.load(RAX, RBP, -56)
        code.mov_imm(RDX, 1)
        code.jmp(hit)
        code.label(missed)
        code.load(RAX, RBP, -40)
        code.mov_imm(RDX, 0)
        code.label(hit)
        code.mov_reg(RSP, RBP)
        code.pop(RBP)
        code.ret()

    def table_reserve(self, typ: str) -> None:
        """`nv_map_T_reserve(rdi = table, rsi = wanted)`: grow and rehash.

        Three quarters full, counting tombstones, is when the table doubles. The
        live entries are moved by probing again into the new slots, so a key
        whose chain was broken by a removal finds its place; nothing is copied
        twice and the old slots are simply left to the arena.
        """
        code = self.code
        key, size, state = self.table_parts(typ)
        sized, grow, move, skip, scan, moved, done = (
            self.local("res_sized"), self.local("res_grow"), self.local("res_move"),
            self.local("res_skip"), self.local("res_scan"), self.local("res_moved"),
            self.local("res_done"))
        have, within = self.local("res_have"), self.local("res_within")
        code.push(RBP)
        code.mov_reg(RBP, RSP)
        code.sub_rsp(80)
        code.store(RBP, -8, RDI)                # the table
        code.store(RBP, -16, RSI)               # how many entries are wanted
        code.load(R11, RDI, TABLE_CAPACITY)
        code.mov_imm(R10, 0)
        code.cmp_reg(R11, R10)
        code.jcc("e", sized)
        code.load(R9, RBP, -8)
        code.load(RAX, R9, TABLE_LENGTH)
        code.load(R11, R9, TABLE_TOMBS)
        code.add_reg(RAX, R11)
        code.add_imm8(RAX, 1)
        code.mov_imm(R11, 4)
        code.imul_reg(RAX, R11)
        self.overflow("mul")
        code.mov_reg(R10, RAX)
        code.load(RAX, R9, TABLE_CAPACITY)
        code.mov_imm(R11, 3)
        code.imul_reg(RAX, R11)
        self.overflow("mul")
        code.cmp_reg(R10, RAX)
        code.jcc("le", done)
        code.label(sized)
        code.load(R10, RBP, -8)
        code.load(R10, R10, TABLE_CAPACITY)
        code.mov_imm(R11, 0)
        code.cmp_reg(R10, R11)
        code.jcc("g", have)
        code.mov_imm(R10, 8)                    # the first capacity
        code.label(have)
        code.label(grow)
        code.load(R9, RBP, -8)
        code.load(RAX, RBP, -16)
        code.load(R11, R9, TABLE_TOMBS)
        code.add_reg(RAX, R11)
        code.add_imm8(RAX, 1)
        code.mov_imm(R11, 4)
        code.imul_reg(RAX, R11)
        self.overflow("mul")
        code.mov_reg(RCX, R10)
        code.mov_imm(R11, 3)
        code.imul_reg(RCX, R11)
        self.overflow("mul")
        code.cmp_reg(RAX, RCX)
        code.jcc("le", move)
        code.mov_reg(RAX, R10)
        code.mov_imm(R11, 2)
        code.imul_reg(RAX, R11)
        self.overflow("mul")
        code.mov_reg(R10, RAX)
        code.jmp(grow)
        code.label(move)
        code.mov_imm(R11, MAX_ELEMENTS)
        code.cmp_reg(R10, R11)
        code.jcc("le", within)
        code.mov_reg(RDI, R10)
        code.mov_imm(RSI, MAX_ELEMENTS)
        code.mov_imm(RDX, 1)
        code.call("nv_fail_pair")
        code.label(within)
        code.store(RBP, -24, R10)               # the capacity
        code.mov_reg(RAX, R10)
        code.mov_imm(R11, size)
        code.imul_reg(RAX, R11)
        self.overflow("mul")
        code.mov_reg(RDI, RAX)
        code.call("nv_alloc")
        code.store(RBP, -32, RAX)               # the new slots
        code.load(R9, RBP, -8)
        code.load(R11, R9, TABLE_SLOTS)
        code.store(RBP, -40, R11)               # the old slots
        code.load(R11, R9, TABLE_CAPACITY)
        code.store(RBP, -48, R11)               # the old capacity
        code.load(R10, RBP, -32)
        code.store(R9, TABLE_SLOTS, R10)
        code.load(R10, RBP, -24)
        code.store(R9, TABLE_CAPACITY, R10)
        code.mov_imm(R10, 0)
        code.store(R9, TABLE_TOMBS, R10)
        code.store(RBP, -56, R10)               # the index
        code.label(scan)
        code.load(R10, RBP, -56)
        code.load(R11, RBP, -48)
        code.cmp_reg(R10, R11)
        code.jcc("ge", moved)
        code.load(R9, RBP, -40)
        code.load(R10, RBP, -56)
        code.mov_reg(RAX, R10)
        code.mov_imm(R11, size)
        code.imul_reg(RAX, R11)
        code.add_reg(R9, RAX)
        code.store(RBP, -64, R9)                # the old slot
        code.load_byte(RAX, R9, state)
        code.mov_imm(R11, LIVE)
        code.cmp_reg(RAX, R11)
        code.jcc("ne", skip)
        code.load(RDI, RBP, -8)
        code.load(RSI, R9, 0)
        code.call(self.table_symbol(typ, "find"))
        code.load(R9, RBP, -64)
        code.load(R10, RBP, -32)
        code.mov_reg(R11, RAX)
        code.mov_imm(R8, size)
        code.imul_reg(R11, R8)
        code.add_reg(R10, R11)
        code.load(RCX, R9, 0)
        code.store(R10, MAP_KEY, RCX)
        if is_map(typ):
            code.load(RCX, R9, MAP_VALUE)
            code.store(R10, MAP_VALUE, RCX)
        code.load_byte(RCX, R9, state)
        code.store_byte(R10, state, RCX)
        code.label(skip)
        code.load(R10, RBP, -56)
        code.add_imm8(R10, 1)
        code.store(RBP, -56, R10)
        code.jmp(scan)
        code.label(moved)
        code.jmp(done)
        code.label(done)
        code.mov_reg(RSP, RBP)
        code.pop(RBP)
        code.ret()

    def table_set(self, typ: str) -> None:
        """`nv_map_T_set(rdi = table, rsi = key, rdx = value)`: an insert or an overwrite."""
        code = self.code
        key, size, state = self.table_parts(typ)
        known, place = self.local("set_known"), self.local("set_place")
        code.push(RBP)
        code.mov_reg(RBP, RSP)
        code.sub_rsp(64)
        code.store(RBP, -8, RDI)
        code.store(RBP, -16, RSI)
        code.store(RBP, -24, RDX)
        code.load(RAX, RDI, TABLE_LENGTH)
        code.add_imm8(RAX, 1)
        self.overflow("add")
        code.mov_reg(RSI, RAX)
        code.call(self.table_symbol(typ, "reserve"))
        code.load(RDI, RBP, -8)
        code.load(RSI, RBP, -16)
        self.call_find(typ)
        code.store(RBP, -32, RAX)               # the slot index
        code.store(RBP, -40, RDX)               # whether the key is there
        code.load(R11, RBP, -40)
        code.mov_imm(R10, 0)
        code.cmp_reg(R11, R10)
        code.jcc("ne", known)
        code.load(R9, RBP, -8)
        code.load(R9, R9, TABLE_SLOTS)
        code.load(R10, RBP, -32)
        code.mov_reg(RAX, R10)
        code.mov_imm(R11, size)
        code.imul_reg(RAX, R11)
        code.add_reg(R9, RAX)
        code.store(RBP, -48, R9)
        code.load_byte(RAX, R9, state)
        code.mov_imm(R11, TOMBSTONE)
        code.cmp_reg(RAX, R11)
        code.jcc("ne", place)
        code.load(R10, RBP, -8)
        code.load(R11, R10, TABLE_TOMBS)
        code.add_imm8(R11, -1)
        code.store(R10, TABLE_TOMBS, R11)
        code.load(R9, RBP, -48)
        code.label(place)
        code.load(RCX, RBP, -16)
        code.store(R9, MAP_KEY, RCX)
        if is_map(typ):
            code.load(RCX, RBP, -24)
            code.store(R9, MAP_VALUE, RCX)
        code.mov_imm(RCX, LIVE)
        code.store_byte(R9, state, RCX)
        code.load(R10, RBP, -8)
        code.load(R11, R10, TABLE_LENGTH)
        code.add_imm8(R11, 1)
        self.overflow("add")
        code.store(R10, TABLE_LENGTH, R11)
        code.mov_reg(RSP, RBP)
        code.pop(RBP)
        code.ret()
        code.label(known)
        code.load(R9, RBP, -8)
        code.load(R9, R9, TABLE_SLOTS)
        code.load(R10, RBP, -32)
        code.mov_reg(RAX, R10)
        code.mov_imm(R11, size)
        code.imul_reg(RAX, R11)
        code.add_reg(R9, RAX)
        if is_map(typ):
            code.load(RCX, RBP, -24)
            code.store(R9, MAP_VALUE, RCX)
        code.mov_reg(RSP, RBP)
        code.pop(RBP)
        code.ret()

    def table_get(self, typ: str) -> None:
        """`nv_map_T_get(rdi = table, rsi = key)`: `Some(value)` or `None`."""
        code = self.code
        key, size, state = self.table_parts(typ)
        missing = self.local("get_missing")
        code.push(RBP)
        code.mov_reg(RBP, RSP)
        code.sub_rsp(64)
        code.store(RBP, -8, RDI)
        code.store(RBP, -16, RSI)
        code.load(R11, RDI, TABLE_CAPACITY)
        code.mov_imm(R10, 0)
        code.cmp_reg(R11, R10)
        code.jcc("e", missing)
        code.load(RDI, RBP, -8)
        code.load(RSI, RBP, -16)
        self.call_find(typ)
        code.mov_imm(R11, 0)
        code.cmp_reg(RDX, R11)
        code.jcc("e", missing)
        code.store(RBP, -24, RAX)
        code.load(R9, RBP, -8)
        code.load(R9, R9, TABLE_SLOTS)
        code.mov_imm(R11, size)
        code.imul_reg(RAX, R11)
        code.add_reg(R9, RAX)
        code.load(RSI, R9, MAP_VALUE)
        code.store(RBP, -32, RSI)
        self.option_record(f"Option({map_parts(typ)[1]})", True, -32)
        code.mov_reg(RSP, RBP)
        code.pop(RBP)
        code.ret()
        code.label(missing)
        self.option_record(f"Option({map_parts(typ)[1]})", False)
        code.mov_reg(RSP, RBP)
        code.pop(RBP)
        code.ret()

    def table_contains(self, typ: str) -> None:
        """`nv_map_T_contains(rdi = table, rsi = key)`: a bool, and nothing else."""
        code = self.code
        missing = self.local("has_missing")
        code.push(RBP)
        code.mov_reg(RBP, RSP)
        code.sub_rsp(32)
        code.store(RBP, -8, RDI)
        code.store(RBP, -16, RSI)
        code.load(R11, RDI, TABLE_CAPACITY)
        code.mov_imm(R10, 0)
        code.cmp_reg(R11, R10)
        code.jcc("e", missing)
        code.load(RDI, RBP, -8)
        code.load(RSI, RBP, -16)
        self.call_find(typ)
        code.mov_imm(R11, 0)
        code.cmp_reg(RDX, R11)
        code.setcc("ne", RAX)
        code.movzx_byte(RAX, RAX)
        code.mov_reg(RSP, RBP)
        code.pop(RBP)
        code.ret()
        code.label(missing)
        code.mov_imm(RAX, 0)
        code.mov_reg(RSP, RBP)
        code.pop(RBP)
        code.ret()

    def table_remove(self, typ: str) -> None:
        """`nv_map_T_remove(rdi = table, rsi = key)`: a bool — whether it was there.

        The entry becomes a tombstone rather than an empty slot, because an empty
        slot would end the probe chain of a key that had collided with this one.
        """
        code = self.code
        key, size, state = self.table_parts(typ)
        missing = self.local("del_missing")
        code.push(RBP)
        code.mov_reg(RBP, RSP)
        code.sub_rsp(64)
        code.store(RBP, -8, RDI)
        code.store(RBP, -16, RSI)
        code.load(R11, RDI, TABLE_CAPACITY)
        code.mov_imm(R10, 0)
        code.cmp_reg(R11, R10)
        code.jcc("e", missing)
        code.load(RDI, RBP, -8)
        code.load(RSI, RBP, -16)
        self.call_find(typ)
        code.store(RBP, -24, RAX)
        code.mov_imm(R11, 0)
        code.cmp_reg(RDX, R11)
        code.jcc("e", missing)
        code.load(R9, RBP, -8)
        code.load(R9, R9, TABLE_SLOTS)
        code.load(R10, RBP, -24)
        code.mov_reg(RAX, R10)
        code.mov_imm(R11, size)
        code.imul_reg(RAX, R11)
        code.add_reg(R9, RAX)
        code.mov_imm(RCX, TOMBSTONE)
        code.store_byte(R9, state, RCX)
        code.load(R10, RBP, -8)
        code.load(R11, R10, TABLE_LENGTH)
        code.add_imm8(R11, -1)
        code.store(R10, TABLE_LENGTH, R11)
        code.load(R11, R10, TABLE_TOMBS)
        code.add_imm8(R11, 1)
        self.overflow("add")
        code.store(R10, TABLE_TOMBS, R11)
        code.mov_imm(RAX, 1)
        code.mov_reg(RSP, RBP)
        code.pop(RBP)
        code.ret()
        code.label(missing)
        code.mov_imm(RAX, 0)
        code.mov_reg(RSP, RBP)
        code.pop(RBP)
        code.ret()

    def table_clear(self, typ: str) -> None:
        """`nv_map_T_clear(rdi = table)`: every slot empty again, the capacity kept."""
        code = self.code
        key, size, state = self.table_parts(typ)
        wipe = self.local("clear_wipe")
        wiped = self.local("clear_wiped")
        code.push(RBP)
        code.mov_reg(RBP, RSP)
        code.sub_rsp(48)
        code.store(RBP, -8, RDI)
        code.load(R11, RDI, TABLE_CAPACITY)
        code.store(RBP, -16, R11)
        code.mov_imm(R11, 0)
        code.store(RBP, -24, R11)               # the index
        code.label(wipe)
        code.load(R10, RBP, -24)
        code.load(R11, RBP, -16)
        code.cmp_reg(R10, R11)
        code.jcc("ge", wiped)
        code.load(R9, RBP, -8)
        code.load(R9, R9, TABLE_SLOTS)
        code.load(R10, RBP, -24)
        code.mov_reg(RAX, R10)
        code.mov_imm(R11, size)
        code.imul_reg(RAX, R11)
        code.add_reg(R9, RAX)
        code.mov_imm(RCX, EMPTY)
        code.store_byte(R9, state, RCX)
        code.load(R10, RBP, -24)
        code.add_imm8(R10, 1)
        code.store(RBP, -24, R10)
        code.jmp(wipe)
        code.label(wiped)
        code.load(R9, RBP, -8)
        code.mov_imm(R10, 0)
        code.store(R9, TABLE_LENGTH, R10)
        code.store(R9, TABLE_TOMBS, R10)
        code.mov_reg(RSP, RBP)
        code.pop(RBP)
        code.ret()

    def uses_builtin(self, name: str) -> bool:
        """Does the program call this builtin? Decided by the IR, not by the type."""
        if self._builtins is None:
            names: set[str] = set()
            for function in self.module.functions:
                for block in function.blocks:
                    for instruction in block.instructions:
                        if isinstance(instruction, ir.Builtin):
                            names.add(instruction.name)
            self._builtins = names
        return name in self._builtins

    def table_symbol(self, typ: str, name: str) -> str:
        """`nv_map_str_i64_find`: one table type's one routine."""
        kind = "map" if is_map(typ) else "set"
        tag = self.map_tag(typ) if is_map(typ) else self.set_tag(typ)
        return f"nv_{kind}_{tag}_{name}"

    def call_find(self, typ: str) -> None:
        """Call this table's `find`: rax = the slot, rdx = whether the key is there."""
        self.code.call(self.table_symbol(typ, "find"))

    def table_routines(self, typ: str) -> list[tuple[str, object]]:
        """The family of routines one table type needs, in the order they are emitted."""
        if is_map(typ):
            return [("new", self.table_new), ("find", self.table_find),
                    ("reserve", self.table_reserve), ("get", self.table_get),
                    ("set", self.table_set), ("contains", self.table_contains),
                    ("remove", self.table_remove), ("clear", self.table_clear)]
        return [("new", self.table_new), ("find", self.table_find),
                ("reserve", self.table_reserve), ("add", self.table_set),
                ("contains", self.table_contains), ("remove", self.table_remove),
                ("clear", self.table_clear)]

    def hash_i64(self) -> None:
        """`nv_hash_i64(rdi = value)`: the C runtime's mixer, in asm.

        The two hashes are the language's own: a 64-bit mixer for an integer and
        FNV-1a over the bytes for a string. Which one a key uses is decided by the
        key's type where the table is emitted, so a table of `str` and a table of
        `i64` never disagree about where a key went.
        """
        code = self.code
        code.push(RBP)
        code.mov_reg(RBP, RSP)
        code.sub_rsp(32)
        code.store(RBP, -8, RDI)
        code.mov_imm(R11, 33)
        code.load(RAX, RBP, -8)
        code.shr_imm(RAX, 33)
        code.load(R11, RBP, -8)
        code.xor_reg(RAX, R11)
        code.store(RBP, -8, RAX)
        code.mov_imm(R11, 0xff51afd7ed558ccd)
        code.load(RAX, RBP, -8)
        code.mov_reg(R10, R11)
        code.imul_reg(RAX, R10)
        code.store(RBP, -8, RAX)
        code.load(RAX, RBP, -8)
        code.shr_imm(RAX, 33)
        code.load(R11, RBP, -8)
        code.xor_reg(RAX, R11)
        code.store(RBP, -8, RAX)
        code.mov_imm(R11, 0xc4ceb9fe1a85ec53)
        code.load(RAX, RBP, -8)
        code.mov_reg(R10, R11)
        code.imul_reg(RAX, R10)
        code.store(RBP, -8, RAX)
        code.load(RAX, RBP, -8)
        code.shr_imm(RAX, 33)
        code.load(R11, RBP, -8)
        code.xor_reg(RAX, R11)
        code.mov_reg(RSP, RBP)
        code.pop(RBP)
        code.ret()

    def hash_str(self) -> None:
        """`nv_hash_str(rdi = string)`: FNV-1a over the bytes, as the C runtime does."""
        code = self.code
        loop, done = self.local("hash_loop"), self.local("hash_done")
        code.push(RBP)
        code.mov_reg(RBP, RSP)
        code.sub_rsp(48)
        code.store(RBP, -8, RDI)                # the string
        code.mov_imm(R11, 0xcbf29ce484222325)
        code.store(RBP, -16, R11)               # the hash
        code.mov_imm(R11, 0)
        code.store(RBP, -24, R11)               # the index
        code.load(R11, RBP, -8)
        code.mov_imm(R10, 0)
        code.cmp_reg(R11, R10)
        code.jcc("e", done)
        code.load(R11, R11, 0)
        code.store(RBP, -32, R11)               # the length
        code.load(R11, RBP, -8)
        code.lea(R11, R11, 8)
        code.store(RBP, -40, R11)               # the bytes
        code.label(loop)
        code.load(R10, RBP, -24)
        code.load(R11, RBP, -32)
        code.cmp_reg(R10, R11)
        code.jcc("ge", done)
        code.load(RCX, RBP, -40)
        code.load(R10, RBP, -24)
        code.mov_reg(RAX, R10)
        code.add_reg(RCX, RAX)
        code.load_byte(RAX, RCX, 0)
        code.load(R11, RBP, -16)
        code.xor_reg(RAX, R11)
        code.store(RBP, -16, RAX)
        code.mov_imm(R11, 0x100000001b3)
        code.load(RAX, RBP, -16)
        code.mov_reg(R10, R11)
        code.imul_reg(RAX, R10)
        code.store(RBP, -16, RAX)
        code.load(R10, RBP, -24)
        code.add_imm8(R10, 1)
        code.store(RBP, -24, R10)
        code.jmp(loop)
        code.label(done)
        code.load(RAX, RBP, -16)
        code.mov_reg(RSP, RBP)
        code.pop(RBP)
        code.ret()

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
                             ("nv_array_view", self.array_view_helper),
                             ("nv_fill_structs", self.fill_structs_helper),
                             ("nv_str_len", self.str_len_helper),
                             ("nv_str_cmp", self.str_cmp_helper),
                             ("nv_byte_at", self.byte_at_helper),
                             ("nv_str_concat", self.str_concat_helper),
                             ("nv_str_slice", self.str_slice_helper),
                             ("nv_str_from_i64", self.str_from_i64_helper),
                             ("nv_to_i64", self.to_i64_helper),
                             ("nv_to_f64", self.to_f64_helper),
                             ("nv_format_f64", self.format_f64_helper),
                             ("nv_getc", self.getc_helper),
                             ("nv_read_i64", self.read_i64_helper)):
            self.runtime_function(symbol, body)
        self.bn_runtime()
        # The two hashes are shared: which one a key uses is decided by the key's
        # type, so a `HashMap(str, i64)` and a `HashSet(i64)` in one program need one
        # copy of each and no more.
        keys = {self.table_parts(typ)[0] for typ in self.module.tables}
        if "i64" in keys:
            self.runtime_function("nv_hash_i64", self.hash_i64)
        if "str" in keys:
            self.runtime_function("nv_hash_str", self.hash_str)
        for typ in self.module.vectors:
            # `reserve` is not a builtin the program calls — it is what `push`
            # needs — so the family is emitted from what the program uses rather
            # than from the shape of the type.
            growing = self.uses_builtin("vec_push") or self.uses_builtin("vec_set")
            for name, body in (("new", self.vec_new), ("reserve", self.vec_reserve),
                               ("push", self.vec_push), ("pop", self.vec_pop),
                               ("get", self.vec_get), ("set", self.vec_set)):
                if name == "reserve" and not growing:
                    continue
                if name != "reserve" and not self.uses_builtin(f"vec_{name}"):
                    continue
                self.runtime_function(f"nv_vec_{self.vector_tag(typ)}_{name}",
                                      lambda body=body, typ=typ: body(typ))
        for typ in self.module.tables:
            kind = "map" if is_map(typ) else "set"
            grows = self.uses_builtin(f"{kind}_set" if kind == "map" else "set_add")
            probes = grows or any(
                self.uses_builtin(f"{kind}_{name}")
                for name in ("get", "contains", "remove"))
            for name, body in self.table_routines(typ):
                if name == "new":
                    pass
                elif name == "reserve" and not grows:
                    continue
                elif name == "find" and not probes:
                    continue
                elif name not in ("new", "reserve", "find") and \
                        not self.uses_builtin(f"{kind}_{name}"):
                    continue
                self.runtime_function(self.table_symbol(typ, name),
                                      lambda body=body, typ=typ: body(typ))
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

    def f64_buffer(self) -> str:
        """A 512-byte scratch buffer in the writable segment, answered by its end.

        A number's decimal text is at most 327 characters — sign, `0.`, 324 zeros and a
        digit — and the conversion writes it backwards from the end, as `nv_format`
        writes the digits of an `i64`.
        """
        self.raw("nv_f64_buffer", bytes(512), DATA)
        return self.raw("nv_f64_buffer_end", b"", DATA)

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
    def array_view_helper(self) -> None:
        """`nv_array_view(rdi = array, rsi = start, rdx = end)` -> a view of the range.

        The checks are the C runtime's two, in its order and with its words: a start that
        is not inside `[0, length]` first, then an end that is not inside
        `[start, length]`. The second message names three numbers, because that is what it
        takes to see which of the two bounds was wrong. Like the C runtime's
        `nv_check_slice`, a failure exits without a trace.
        """
        code = self.code
        start_ok, end_ok = self.local("view_start"), self.local("view_end")
        start_bad, end_bad = self.local("view_start_bad"), self.local("view_end_bad")
        code.push(RBP)
        code.mov_reg(RBP, RSP)
        code.sub_rsp(48)
        code.store(RBP, -8, RDI)                # the array
        code.store(RBP, -16, RSI)               # start
        code.store(RBP, -24, RDX)               # end
        code.load(R11, RDI, 0)                  # the length
        code.store(RBP, -32, R11)
        code.xor_reg(R10, R10)
        code.cmp_reg(RSI, R10)
        code.jcc("l", start_bad)
        code.cmp_reg(RSI, R11)
        code.jcc("le", start_ok)
        code.label(start_bad)
        self.write_bytes("nava runtime error: slice start ")
        self.write_formatted(-16, 1)
        self.write_bytes(" is out of range for length ")
        self.write_formatted(-32, 2)
        self.newline_to(2)
        self.exit_now()
        code.label(start_ok)
        code.load(R11, RBP, -32)
        code.load(R10, RBP, -16)
        code.load(RAX, RBP, -24)
        code.cmp_reg(RAX, R10)
        code.jcc("l", end_bad)
        code.cmp_reg(RAX, R11)
        code.jcc("le", end_ok)
        code.label(end_bad)
        self.write_bytes("nava runtime error: slice end ")
        self.write_formatted(-24, 1)
        self.write_bytes(" is out of range for start ")
        self.write_formatted(-16, 2)
        self.write_bytes(" and length ")
        self.write_formatted(-32, 3)
        self.newline_to(2)
        self.exit_now()
        code.label(end_ok)
        # Two words: how many elements, and where the first of them is.
        code.mov_imm(RDI, FIELD_SIZE * 2)
        code.call("nv_alloc")
        code.store(RBP, -40, RAX)
        code.load(R11, RBP, -24)
        code.load(R10, RBP, -16)
        code.sub_reg(R11, R10)                  # end - start
        code.store(RAX, 0, R11)                 # the length of the view
        code.load(R11, RBP, -8)
        code.load(R11, R11, 8)                  # the parent's elements
        code.load(R10, RBP, -16)
        code.mov_imm(R9, ELEMENT_SIZE)
        code.imul_reg(R10, R9)
        code.add_reg(R11, R10)                  # the first element of the view
        code.store(RAX, 8, R11)
        code.mov_reg(RSP, RBP)
        code.pop(RBP)
        code.ret()

    def write_formatted(self, slot: int, buffer: int) -> None:
        """Write the number in frame slot `slot` to standard error.

        `nv_format` answers a pointer and a length, so the pointer has to move into `rsi`
        before the syscall's own number goes into `rax` — the first version of this wrote
        the buffer's address as the text and the digits came out as whatever the buffer
        held.
        """
        code = self.code
        code.load(RDI, RBP, slot)
        code.lea_rip(RSI, self.number_buffer(buffer))
        code.call("nv_format")                  # rax = the digits, rdx = how many
        code.mov_reg(RSI, RAX)
        code.mov_imm(RDI, 2)
        code.mov_imm(RAX, 1)
        code.syscall()

    def exit_now(self) -> None:
        """`exit(1)` by syscall: what the C runtime's numbered stops do."""
        self.code.mov_imm(RDI, 1)
        self.code.mov_imm(RAX, 60)
        self.code.syscall()

    def fill_structs_helper(self) -> None:
        """`nv_fill_structs(rdi = array, rsi = count, rdx = bytes)`: a record per element.

        An array of structs holds record addresses, so a zeroed array of them must hold a
        zeroed record each. The arena hands out zeroed bytes, which is what makes this a
        fill rather than a loop of field stores.
        """
        code = self.code
        step, done = self.local("fill_step"), self.local("fill_done")
        code.push(RBP)
        code.mov_reg(RBP, RSP)
        code.sub_rsp(48)
        code.store(RBP, -16, RSI)               # how many
        code.store(RBP, -24, RDX)               # the size of one record
        code.load(R11, RDI, 8)                  # where the elements start
        code.store(RBP, -32, R11)
        code.xor_reg(R10, R10)
        code.store(RBP, -40, R10)               # the index
        code.label(step)
        code.load(R10, RBP, -40)
        code.load(R11, RBP, -16)
        code.cmp_reg(R10, R11)
        code.jcc("ge", done)
        code.load(RDI, RBP, -24)
        code.call("nv_alloc")
        code.load(R11, RBP, -32)
        code.load(R10, RBP, -40)
        code.mov_imm(R9, ELEMENT_SIZE)
        code.imul_reg(R10, R9)
        code.add_reg(R11, R10)
        code.store(R11, 0, RAX)
        code.load(R10, RBP, -40)
        code.inc(R10)
        code.store(RBP, -40, R10)
        code.jmp(step)
        code.label(done)
        code.mov_reg(RSP, RBP)
        code.pop(RBP)
        code.ret()

    # -- the decimal text of an `f64` ----------------------------------------
    #: A decimal bignum in this runtime: a length word and forty-eight base-10^9 limbs,
    #: little-endian. The widest intermediate of the conversion below is ten to the
    #: 325th — the whole range of the type — which is thirty-seven limbs, so the
    #: capacity is that range with room to spare. Base 10^9 keeps a limb product under
    #: 10^18, inside an `i64`, so the divider does the arithmetic exactly.
    BN_CAP = 48
    BN_BASE = 1_000_000_000
    BN_STRIDE = 8 + 8 * BN_CAP              # a length word, then the limbs
    #: The frame of the conversion: five bignums, then the text, then the digits.
    # A bignum's limbs grow upward from its base, so each one owns
    # [base, base + BN_STRIDE); the offsets leave the slots above -144 alone.
    BN_R, BN_S, BN_MP, BN_MM, BN_T = -536, -928, -1320, -1712, -2104
    F64_TEXT, F64_DIGITS = -2664, -3264
    F64_FRAME = 3264
    S_END, S_SIGN, S_MANT, S_MEXP, S_SCALE = -8, -16, -24, -32, -40
    S_LEN, S_FIRST, S_E10, S_DIGIT, S_LOW, S_HIGH = -48, -56, -64, -72, -80, -88
    S_MAG, S_BITLEN, S_TEMP, S_TEMP2, S_CURSOR = -96, -104, -112, -120, -128
    S_EVEN = -136

    def bn_runtime(self) -> None:
        """The bignum routines the decimal conversion needs, and nothing else.

        A `double` is up to 1074 binary exponents away from a decimal digit, so the exact
        value of the smallest subnormal has 751 significant decimal digits: this
        conversion cannot be done in machine words, and it does not guess. These are the
        operations Burger and Dybvig's algorithm asks for — compare, subtract, multiply
        by a small number, scale by a power of two or ten — with base 10^9 limbs so that
        a decimal digit costs one division by a constant.
        """
        for symbol, body in (("nv_bn_set_u64", self.bn_set_u64_helper),
                             ("nv_bn_copy", self.bn_copy_helper),
                             ("nv_bn_cmp", self.bn_cmp_helper),
                             ("nv_bn_sub", self.bn_sub_helper),
                             ("nv_bn_mul_small", self.bn_mul_small_helper),
                             ("nv_bn_mul_pow2", self.bn_mul_pow2_helper),
                             ("nv_bn_mul_pow10", self.bn_mul_pow10_helper)):
            self.runtime_function(symbol, body)

    def bn_set_u64_helper(self) -> None:
        """`nv_bn_set_u64(rdi = bn, rsi = value)`: the bignum holding one machine word."""
        code = self.code
        clear, cleared = self.local("bn0_clear"), self.local("bn0_cleared")
        split, splitted = self.local("bn0_split"), self.local("bn0_splitted")
        code.push(RBP)
        code.mov_reg(RBP, RSP)
        code.sub_rsp(32)
        code.store(RBP, -8, RDI)
        code.store(RBP, -16, RSI)
        code.xor_reg(RAX, RAX)
        code.xor_reg(R10, R10)
        code.label(clear)
        code.mov_imm(R11, self.BN_CAP + 1)
        code.cmp_reg(R10, R11)
        code.jcc("ae", cleared)
        code.mov_reg(R11, R10)
        code.mov_imm(R9, 8)
        code.imul_reg(R11, R9)
        code.add_reg(R11, RDI)
        code.store(R11, 0, RAX)                 # what the value does not reach stays 0
        code.inc(R10)
        code.jmp(clear)
        code.label(cleared)
        code.load(RAX, RBP, -16)
        code.mov_imm(R8, self.BN_BASE)
        code.xor_reg(R10, R10)
        code.label(split)
        code.test_reg(RAX)
        code.jcc("e", splitted)
        code.xor_reg(RDX, RDX)
        code.div(R8)                            # rax = the quotient, rdx = the limb
        code.load(RDI, RBP, -8)
        code.mov_reg(R11, R10)
        code.mov_imm(R9, 8)
        code.imul_reg(R11, R9)
        code.add_reg(R11, RDI)
        code.store(R11, 8, RDX)                 # the limbs follow the length word
        code.inc(R10)
        code.jmp(split)
        code.label(splitted)
        code.load(RDI, RBP, -8)
        code.store(RDI, 0, R10)
        code.mov_reg(RSP, RBP)
        code.pop(RBP)
        code.ret()

    def bn_copy_helper(self) -> None:
        """`nv_bn_copy(rdi = destination, rsi = source)`: every word, length included."""
        code = self.code
        loop, done = self.local("bnc_loop"), self.local("bnc_done")
        code.mov_reg(R8, RDI)
        code.mov_reg(R9, RSI)
        code.xor_reg(R10, R10)
        code.mov_imm(R11, self.BN_CAP + 1)
        code.label(loop)
        code.cmp_reg(R10, R11)
        code.jcc("ae", done)
        code.load(RDX, R9, 0)
        code.store(R8, 0, RDX)
        code.mov_imm(RCX, 8)
        code.add_reg(R8, RCX)
        code.add_reg(R9, RCX)
        code.inc(R10)
        code.jmp(loop)
        code.label(done)
        code.ret()

    def bn_cmp_helper(self) -> None:
        """`nv_bn_cmp(rdi = a, rsi = b)` -> `-1`, `0` or `1`.

        The length is part of the value: every routine here keeps the limbs above it
        zero, so the length decides first and the limbs decide after it.
        """
        code = self.code
        loop, equal = self.local("bncmp_loop"), self.local("bncmp_equal")
        less, greater = self.local("bncmp_less"), self.local("bncmp_greater")
        code.load(R8, RDI, 0)
        code.load(R9, RSI, 0)
        code.cmp_reg(R8, R9)
        code.jcc("b", less)
        code.jcc("a", greater)
        code.mov_reg(R10, R8)
        code.label(loop)
        code.test_reg(R10)
        code.jcc("e", equal)
        code.dec(R10)
        code.mov_reg(R11, R10)
        code.mov_imm(RCX, 8)
        code.imul_reg(R11, RCX)
        code.mov_reg(RDX, RDI)
        code.add_reg(RDX, R11)
        code.load(RDX, RDX, 8)
        code.mov_reg(RAX, RSI)
        code.add_reg(RAX, R11)
        code.load(RAX, RAX, 8)
        code.cmp_reg(RDX, RAX)
        code.jcc("b", less)
        code.jcc("a", greater)
        code.jmp(loop)
        code.label(equal)
        code.xor_reg(RAX, RAX)
        code.ret()
        code.label(less)
        code.mov_imm(RAX, -1)
        code.ret()
        code.label(greater)
        code.mov_imm(RAX, 1)
        code.ret()

    def bn_sub_helper(self) -> None:
        """`nv_bn_sub(rdi = a, rsi = b)`: `a -= b`, for `a >= b`, its length trimmed."""
        code = self.code
        loop, absent, borrow = (self.local("bns_loop"), self.local("bns_absent"),
                                self.local("bns_borrow"))
        borrowed, strip, strip_loop, stripped = (
            self.local("bns_borrowed"), self.local("bns_strip"),
            self.local("bns_strip_loop"), self.local("bns_stripped"))
        code.push(RBP)
        code.mov_reg(RBP, RSP)
        code.sub_rsp(48)
        code.store(RBP, -8, RDI)                # a
        code.store(RBP, -16, RSI)               # b
        code.load(R8, RDI, 0)
        code.store(RBP, -24, R8)                # the length of a
        code.load(R9, RSI, 0)
        code.store(RBP, -32, R9)                # the length of b
        code.xor_reg(R10, R10)                  # i
        code.xor_reg(R11, R11)                  # the borrow
        code.label(loop)
        code.load(R8, RBP, -24)
        code.cmp_reg(R10, R8)
        code.jcc("ae", strip)
        code.load(RDI, RBP, -8)
        code.mov_reg(RCX, R10)
        code.mov_imm(RDX, 8)
        code.imul_reg(RCX, RDX)
        code.add_reg(RDI, RCX)
        code.load(RAX, RDI, 8)                  # a[i]
        code.load(RSI, RBP, -32)
        code.cmp_reg(R10, RSI)
        code.jcc("ae", absent)
        code.load(RSI, RBP, -16)
        code.mov_reg(RCX, R10)
        code.mov_imm(RDX, 8)
        code.imul_reg(RCX, RDX)
        code.add_reg(RSI, RCX)
        code.load(RCX, RSI, 8)                  # b[i]
        code.jmp(borrow)
        code.label(absent)
        code.xor_reg(RCX, RCX)                  # a limb b does not have is zero
        code.label(borrow)
        code.add_reg(RCX, R11)
        code.xor_reg(R11, R11)
        code.cmp_reg(RAX, RCX)
        code.jcc("ae", borrowed)
        code.mov_imm(R8, self.BN_BASE)
        code.add_reg(RAX, R8)                   # borrow one limb
        code.mov_imm(R11, 1)
        code.label(borrowed)
        code.sub_reg(RAX, RCX)
        code.load(RDI, RBP, -8)
        code.mov_reg(RCX, R10)
        code.mov_imm(RDX, 8)
        code.imul_reg(RCX, RDX)
        code.add_reg(RDI, RCX)
        code.store(RDI, 8, RAX)
        code.inc(R10)
        code.jmp(loop)
        code.label(strip)
        code.load(R8, RBP, -24)
        code.load(RDI, RBP, -8)
        code.label(strip_loop)
        code.test_reg(R8)
        code.jcc("e", stripped)
        code.mov_reg(RCX, R8)
        code.dec(RCX)
        code.mov_imm(RDX, 8)
        code.imul_reg(RCX, RDX)
        code.mov_reg(R9, RDI)
        code.add_reg(R9, RCX)
        code.load(R9, R9, 8)
        code.test_reg(R9)
        code.jcc("ne", stripped)
        code.dec(R8)
        code.jmp(strip_loop)
        code.label(stripped)
        code.store(RDI, 0, R8)
        code.mov_reg(RSP, RBP)
        code.pop(RBP)
        code.ret()

    def bn_mul_small_helper(self) -> None:
        """`nv_bn_mul_small(rdi = a, rsi = k)`: `a *= k`, for `k` below the base."""
        code = self.code
        loop, carry, done = (self.local("bnm_loop"), self.local("bnm_carry"),
                             self.local("bnm_done"))
        code.push(RBP)
        code.mov_reg(RBP, RSP)
        code.sub_rsp(32)
        code.store(RBP, -8, RDI)
        code.store(RBP, -16, RSI)
        code.load(R8, RDI, 0)
        code.store(RBP, -24, R8)                # the length
        code.xor_reg(R10, R10)                  # i
        code.xor_reg(R11, R11)                  # the carry
        code.label(loop)
        code.load(R8, RBP, -24)
        code.cmp_reg(R10, R8)
        code.jcc("ae", carry)
        code.load(RDI, RBP, -8)
        code.mov_reg(RCX, R10)
        code.mov_imm(RDX, 8)
        code.imul_reg(RCX, RDX)
        code.add_reg(RDI, RCX)
        code.load(RAX, RDI, 8)
        code.imul_reg(RAX, RSI)                 # below 10^18, so inside an i64
        code.add_reg(RAX, R11)
        code.mov_imm(R8, self.BN_BASE)
        code.xor_reg(RDX, RDX)
        code.div(R8)
        code.mov_reg(R11, RAX)                  # the carry
        code.store(RDI, 8, RDX)                 # the limb
        code.inc(R10)
        code.jmp(loop)
        code.label(carry)
        code.test_reg(R11)
        code.jcc("e", done)
        code.load(RDI, RBP, -8)
        code.mov_reg(RCX, R10)
        code.mov_imm(RDX, 8)
        code.imul_reg(RCX, RDX)
        code.add_reg(RDI, RCX)
        code.mov_reg(RAX, R11)
        code.mov_imm(R8, self.BN_BASE)
        code.xor_reg(RDX, RDX)
        code.div(R8)
        code.mov_reg(R11, RAX)
        code.store(RDI, 8, RDX)
        code.inc(R10)
        code.jmp(carry)
        code.label(done)
        code.load(RDI, RBP, -8)
        code.store(RDI, 0, R10)                 # the length grew with the carries
        code.mov_reg(RSP, RBP)
        code.pop(RBP)
        code.ret()

    def bn_mul_power_helper(self, hint: str, factor: int) -> None:
        """`a *= factor**n`, one step at a time: the scaling the conversion asks for."""
        code = self.code
        loop, done = self.local(f"{hint}_loop"), self.local(f"{hint}_done")
        code.push(RBP)
        code.mov_reg(RBP, RSP)
        code.sub_rsp(32)
        code.store(RBP, -8, RDI)
        code.store(RBP, -16, RSI)
        code.label(loop)
        code.load(RAX, RBP, -16)
        code.test_reg(RAX)
        code.jcc("e", done)
        code.load(RDI, RBP, -8)
        code.mov_imm(RSI, factor)
        code.call("nv_bn_mul_small")
        code.load(RAX, RBP, -16)
        code.dec(RAX)
        code.store(RBP, -16, RAX)
        code.jmp(loop)
        code.label(done)
        code.mov_reg(RSP, RBP)
        code.pop(RBP)
        code.ret()

    def bn_mul_pow2_helper(self) -> None:
        """`nv_bn_mul_pow2(rdi = a, rsi = n)`: `a *= 2**n`."""
        self.bn_mul_power_helper("bnp2", 2)

    def bn_mul_pow10_helper(self) -> None:
        """`nv_bn_mul_pow10(rdi = a, rsi = n)`: `a *= 10**n`."""
        self.bn_mul_power_helper("bnp10", 10)


    def format_f64_helper(self) -> None:
        """`nv_format_f64(rdi = bits, rsi = end of a buffer)` -> `(rax = start, rdx = len)`.

        The shortest decimal that reads back as this `f64`, which is what the C runtime's
        `nv_format_f64` produces: fixed notation when the decimal exponent is in
        `[-4, 15]`, exponent notation outside it, the mantissa's trailing zeros dropped,
        and `.0` added so a float never looks like an integer. `nan`, `inf`, `-inf`,
        `0.0` and `-0.0` are the five names the language gives them.

        The digits come from Burger and Dybvig's algorithm: the value, its upper gap and
        its lower gap are exact integers once scaled by a power of two, a digit is one
        subtraction loop, and the loop stops at the first count whose nearest candidate
        lies inside the gap. The gap ends halfway to each neighbour, and a value that
        sits exactly on such a midpoint reads back only when it is the even of the two —
        one bit of the significand decides that, so this conversion and `strtod` agree
        digit for digit rather than merely closely.

        "Nearest candidate at the first count that reads back" is the rule the C runtime's
        `%.*e`/`%.*f` loop follows, and it is *not* the same question as "the shortest
        decimal that reads back": `2**-1017` has no fifteen-digit text that reads back and
        its nearest sixteen-digit text is an exact midpoint that rounds to the neighbour
        (an odd significand), so both backends print seventeen digits there. A loop that
        stops at the first count *containing* a round-tripping text answers `7.12023e-307`
        where this one answers `7.120236347223044e-307`, which is why the stop condition
        is stated in terms of the nearest candidate. Seventeen digits are always enough,
        so the loop has that floor as well as the gap as its end.
        """
        code = self.code
        (not_finite, infinite_negative, not_number, zero_text, zero_negative,
         special_done, finish) = (
            self.local("f64_not_finite"), self.local("f64_inf_neg"), self.local("f64_nan"),
            self.local("f64_zero"), self.local("f64_zero_neg"),
            self.local("f64_special"), self.local("f64_finish"))
        (subnormal, sub_shift, sub_done, ready) = (
            self.local("f64_subnormal"), self.local("f64_sub_shift"),
            self.local("f64_sub_done"), self.local("f64_ready"))
        (negative_bitlen, scaled_bitlen) = (self.local("f64_bitlen_neg"),
                                            self.local("f64_bitlen_done"))
        (pad_ready, pad_done, mm_shift) = (
            self.local("f64_pad_ready"), self.local("f64_pad_done"),
            self.local("f64_mm_shift"))
        (fix_high, fix_low, fix_done) = (
            self.local("f64_fix_high"), self.local("f64_fix_low"),
            self.local("f64_fix_done"))
        (digit_loop, subtract, sub_end) = (
            self.local("f64_digit"), self.local("f64_subtract"),
            self.local("f64_sub_end"))
        (round_up_flag, round_down_flag) = (self.local("f64_round_flag_up"),
                                          self.local("f64_round_flag_down"))
        (nearest_up, nearest_down, gap_test, inside, outside, force_nearest) = (
            self.local("f64_nearest_up"), self.local("f64_nearest_down"),
            self.local("f64_gap_test"), self.local("f64_inside"),
            self.local("f64_outside"), self.local("f64_force"))
        (round_up, round_plain, round_carry, round_nine, round_first,
         shift_loop, shift_done, round_done) = (
            self.local("f64_round"), self.local("f64_round_plain"),
            self.local("f64_round_carry"), self.local("f64_round_nine"),
            self.local("f64_round_first"), self.local("f64_shift"),
            self.local("f64_shift_done"), self.local("f64_round_done"))
        (first_loop, first_done, choose, fixed_positive, fixed_short, fixed_negative,
         exponential, mantissa_done, exponent_negative) = (
            self.local("f64_first_loop"), self.local("f64_first_done"),
            self.local("f64_choose"), self.local("f64_fixed_pos"),
            self.local("f64_fixed_short"), self.local("f64_fixed_neg"),
            self.local("f64_exp"), self.local("f64_mantissa_done"),
            self.local("f64_exp_neg"))
        (copy_loop, copy_done) = (self.local("f64_copy"), self.local("f64_copy_done"))
        (scale_negative, scale_done) = (self.local("f64_scale_neg"),
                                        self.local("f64_scale_done"))
        exponent_positive = self.local("f64_exp_pos")
        exponent_digits = self.local("f64_exp_digits")
        exponent_last_two = self.local("f64_exp_last")
        gaps_done = self.local("f64_gaps_done")
        def at(register: int, offset: int) -> None:
            """`register` = the address of the frame slot or buffer at `offset`."""
            code.lea(register, RBP, offset)

        def slot(name: int, register: int) -> None:
            code.load(register, RBP, name)

        def put(name: int, register: int) -> None:
            code.store(RBP, name, register)

        def put_char(char: str) -> None:
            """Append one constant character to the text the cursor points into."""
            slot(self.S_CURSOR, R9)
            code.mov_imm(RAX, ord(char))
            code.store_byte(R9, 0, RAX)
            code.inc(R9)
            put(self.S_CURSOR, R9)

        def put_byte(register: int) -> None:
            """Append the character in `register` to the text."""
            slot(self.S_CURSOR, R9)
            code.store_byte(R9, 0, register)
            code.inc(R9)
            put(self.S_CURSOR, R9)

        def put_zeros() -> None:
            """Append `self.S_TEMP2` zeroes; the count is spent."""
            loop, done = self.local("f64_zeros"), self.local("f64_zeros_done")
            zeros_loop, zeros_done = loop, done
            code.label(zeros_loop)
            slot(self.S_TEMP2, R11)
            code.test_reg(R11)
            code.jcc("e", zeros_done)
            put_char("0")
            slot(self.S_TEMP2, R11)
            code.dec(R11)
            put(self.S_TEMP2, R11)
            code.jmp(zeros_loop)
            code.label(zeros_done)

        def put_digits() -> None:
            """Append `self.S_TEMP2` digits from the index `self.S_TEMP`; both are spent."""
            loop, done = self.local("f64_run"), self.local("f64_run_done")
            run_loop, run_done = loop, done
            code.label(run_loop)
            slot(self.S_TEMP2, R11)
            code.test_reg(R11)
            code.jcc("e", run_done)
            slot(self.S_TEMP, R10)
            at(R11, self.F64_DIGITS)
            code.add_reg(R11, R10)
            code.load_byte(RAX, R11, 0)
            put_byte(RAX)
            slot(self.S_TEMP, R10)
            code.inc(R10)
            put(self.S_TEMP, R10)
            slot(self.S_TEMP2, R11)
            code.dec(R11)
            put(self.S_TEMP2, R11)
            code.jmp(run_loop)
            code.label(run_done)

        # -- decode the bits ---------------------------------------------------
        code.push(RBP)
        code.mov_reg(RBP, RSP)
        code.sub_rsp(self.F64_FRAME)
        put(self.S_END, RSI)
        code.mov_reg(RAX, RDI)
        code.mov_reg(R10, RAX)
        code.shr_imm(R10, 63)
        put(self.S_SIGN, R10)
        code.shl_imm(RAX, 1)
        code.shr_imm(RAX, 1)                        # the magnitude
        put(self.S_MAG, RAX)
        code.mov_imm(R11, 0x7FF0000000000000)
        code.cmp_reg(RAX, R11)
        code.jcc("ae", not_finite)
        code.test_reg(RAX)
        code.jcc("e", zero_text)
        code.mov_reg(R11, RAX)
        code.shr_imm(R11, 52)                       # the biased exponent
        put(self.S_TEMP, R11)
        code.mov_reg(R10, RAX)
        code.shl_imm(R10, 12)
        code.shr_imm(R10, 12)                       # the fifty-two fraction bits
        put(self.S_MANT, R10)
        # The rounding interval ends exactly halfway to each neighbour, and a value
        # that sits on such a midpoint reads back only if it is the even of the two:
        # one bit of the significand decides that, and it has to be read before the
        # hidden bit is added or a subnormal is shifted.
        code.mov_reg(RAX, R10)
        code.inc(RAX)
        code.and_imm32(RAX, 1)                      # one when the significand is even
        put(self.S_EVEN, RAX)
        code.test_reg(R11)
        code.jcc("e", subnormal)
        code.mov_imm(R9, 1)
        code.shl_imm(R9, 52)
        code.add_reg(R10, R9)                       # the hidden bit, added in
        put(self.S_MANT, R10)
        slot(self.S_TEMP, R11)
        code.mov_imm(R9, 1075)
        code.sub_reg(R11, R9)
        put(self.S_MEXP, R11)                       # value = mantissa * 2**this
        code.mov_imm(R9, 53)
        code.add_reg(R11, R9)
        put(self.S_BITLEN, R11)                     # one more than floor(log2(value))
        code.jmp(ready)
        # A subnormal keeps its fraction as it stands: the spacing beside it is
        # uniform, so shifting it up would give it the gaps of a normal number that
        # it is not — and the gaps are what the whole conversion rests on.
        code.label(subnormal)
        code.mov_imm(R11, -1074)
        put(self.S_MEXP, R11)
        code.mov_reg(R9, R10)
        code.xor_reg(R8, R8)
        code.label(sub_shift)
        code.mov_imm(RAX, 1)
        code.shl_imm(RAX, 52)
        code.cmp_reg(R9, RAX)
        code.jcc("ae", sub_done)
        code.add_reg(R9, R9)
        code.inc(R8)
        code.jmp(sub_shift)
        code.label(sub_done)
        code.mov_imm(R11, 53 - 1074)                # its bits, counted down to the low end
        code.sub_reg(R11, R8)
        put(self.S_BITLEN, R11)
        code.label(ready)
        # -- the decimal scale -------------------------------------------------
        # s = floor(bitlen * log10(2)) + 1, so that the scaled value is below one and the
        # digit loop never has to answer a leading digit above nine.
        slot(self.S_BITLEN, R11)
        code.test_reg(R11)
        code.jcc("s", negative_bitlen)
        code.mov_imm(R9, 1233)
        code.imul_reg(R11, R9)
        code.shr_imm(R11, 12)                       # 1233 / 4096 is log10(2)
        code.jmp(scaled_bitlen)
        code.label(negative_bitlen)
        code.neg(R11)
        code.mov_imm(R9, 1233)
        code.imul_reg(R11, R9)
        code.mov_imm(R9, 4095)
        code.add_reg(R11, R9)
        code.shr_imm(R11, 12)                       # rounding up, so the floor holds
        code.neg(R11)
        code.label(scaled_bitlen)
        code.inc(R11)
        put(self.S_SCALE, R11)
        # -- R, S, and the two gaps --------------------------------------------
        # In the frame of 2**(m-2) every one of them is an integer: pad clears the
        # fraction, and the gaps are one or two of those units, halved below a power
        # of two because its lower neighbour is one binade closer.
        slot(self.S_MEXP, R11)
        code.mov_imm(R9, 2)
        code.cmp_reg(R11, R9)
        code.jcc("ge", pad_ready)
        code.mov_imm(R9, 2)
        code.sub_reg(R9, R11)                       # pad = 2 - m
        code.jmp(pad_done)
        code.label(pad_ready)
        code.xor_reg(R9, R9)
        code.label(pad_done)
        put(self.S_TEMP, R9)
        slot(self.S_MEXP, R10)
        code.add_reg(R10, R9)
        put(self.S_TEMP2, R10)                      # shift = m + pad
        at(RDI, self.BN_S)
        code.mov_imm(RSI, 1)
        code.call("nv_bn_set_u64")
        at(RDI, self.BN_S)
        slot(self.S_TEMP, RSI)
        code.call("nv_bn_mul_pow2")
        at(RDI, self.BN_R)
        slot(self.S_MANT, RSI)
        code.call("nv_bn_set_u64")
        at(RDI, self.BN_R)
        slot(self.S_TEMP2, RSI)
        code.call("nv_bn_mul_pow2")
        at(RDI, self.BN_MP)
        code.mov_imm(RSI, 1)
        code.call("nv_bn_set_u64")
        at(RDI, self.BN_MP)
        slot(self.S_TEMP2, RSI)
        code.dec(RSI)                               # half of the upper gap
        code.call("nv_bn_mul_pow2")
        at(RDI, self.BN_MM)
        code.mov_imm(RSI, 1)
        code.call("nv_bn_set_u64")
        at(RDI, self.BN_MM)
        slot(self.S_TEMP2, RSI)
        code.dec(RSI)
        slot(self.S_MANT, R10)
        code.mov_imm(R9, 1)
        code.shl_imm(R9, 52)
        code.cmp_reg(R10, R9)
        code.jcc("ne", mm_shift)
        code.dec(RSI)                               # a power of two: half of the lower gap
        code.label(mm_shift)
        code.call("nv_bn_mul_pow2")

        # -- the decimal scaling -----------------------------------------------
        code.label(gaps_done)
        slot(self.S_SCALE, R11)
        code.test_reg(R11)
        code.jcc("s", scale_negative)
        at(RDI, self.BN_S)
        code.mov_reg(RSI, R11)
        code.call("nv_bn_mul_pow10")
        code.jmp(scale_done)
        code.label(scale_negative)
        code.neg(R11)
        put(self.S_TEMP, R11)
        for offset in (self.BN_R, self.BN_MP, self.BN_MM):
            at(RDI, offset)
            slot(self.S_TEMP, RSI)
            code.call("nv_bn_mul_pow10")
        code.label(scale_done)
        # -- the scale lands between a tenth and one ---------------------------
        # The estimate above only has to be close: these two loops make the first
        # digit a significant one, which is what lets seventeen digits be enough.
        code.label(fix_high)
        at(RDI, self.BN_R)
        at(RSI, self.BN_S)
        code.call("nv_bn_cmp")
        code.xor_reg(R10, R10)
        code.cmp_reg(RAX, R10)
        code.jcc("l", fix_low)
        at(RDI, self.BN_S)
        code.mov_imm(RSI, 10)
        code.call("nv_bn_mul_small")
        slot(self.S_SCALE, R11)
        code.inc(R11)
        put(self.S_SCALE, R11)
        code.jmp(fix_high)
        code.label(fix_low)
        at(RDI, self.BN_T)
        at(RSI, self.BN_R)
        code.call("nv_bn_copy")
        at(RDI, self.BN_T)
        code.mov_imm(RSI, 10)
        code.call("nv_bn_mul_small")
        at(RDI, self.BN_T)
        at(RSI, self.BN_S)
        code.call("nv_bn_cmp")
        code.xor_reg(R10, R10)
        code.cmp_reg(RAX, R10)
        code.jcc("ge", fix_done)
        for offset in (self.BN_R, self.BN_MP, self.BN_MM):
            at(RDI, offset)
            code.mov_imm(RSI, 10)
            code.call("nv_bn_mul_small")
        slot(self.S_SCALE, R11)
        code.dec(R11)
        put(self.S_SCALE, R11)
        code.jmp(fix_low)
        code.label(fix_done)
        # -- the digits ---------------------------------------------------------
        code.xor_reg(RAX, RAX)
        put(self.S_LEN, RAX)
        code.label(digit_loop)
        # One place finer every turn: the remainder is measured in the units of the
        # digit being written, so the two gaps are multiplied by ten beside it.
        for offset in (self.BN_R, self.BN_MP, self.BN_MM):
            at(RDI, offset)
            code.mov_imm(RSI, 10)
            code.call("nv_bn_mul_small")
        code.xor_reg(RAX, RAX)
        put(self.S_DIGIT, RAX)
        code.label(subtract)
        at(RDI, self.BN_R)
        at(RSI, self.BN_S)
        code.call("nv_bn_cmp")
        code.xor_reg(R10, R10)
        code.cmp_reg(RAX, R10)
        code.jcc("l", sub_end)
        at(RDI, self.BN_R)
        at(RSI, self.BN_S)
        code.call("nv_bn_sub")
        slot(self.S_DIGIT, RAX)
        code.inc(RAX)
        put(self.S_DIGIT, RAX)
        code.jmp(subtract)
        code.label(sub_end)
        slot(self.S_DIGIT, RAX)
        code.add_byte_imm(RAX, ord("0"))
        slot(self.S_LEN, R11)
        at(R10, self.F64_DIGITS)
        code.add_reg(R10, R11)
        code.store_byte(R10, 0, RAX)
        slot(self.S_LEN, RAX)
        code.inc(RAX)
        put(self.S_LEN, RAX)
        # -- which way the digit reads ------------------------------------------
        # Twice what is left against the scale picks the nearer candidate, and an
        # exact halfway point goes to the even digit — the way a correct reader
        # rounds it back.
        at(RDI, self.BN_T)
        at(RSI, self.BN_R)
        code.call("nv_bn_copy")
        at(RDI, self.BN_T)
        code.mov_imm(RSI, 2)
        code.call("nv_bn_mul_small")
        at(RDI, self.BN_T)
        at(RSI, self.BN_S)
        code.call("nv_bn_cmp")
        code.xor_reg(R10, R10)
        code.cmp_reg(RAX, R10)
        code.jcc("g", round_up_flag)
        code.jcc("l", round_down_flag)
        slot(self.S_DIGIT, RAX)
        code.and_imm32(RAX, 1)                      # exactly halfway: the even digit wins
        code.test_reg(RAX)
        code.jcc("e", round_down_flag)
        code.label(round_up_flag)
        code.mov_imm(RAX, 1)
        put(self.S_TEMP, RAX)                       # one: the candidate above is the nearer
        code.jmp(nearest_up)
        code.label(round_down_flag)
        code.xor_reg(RAX, RAX)
        put(self.S_TEMP, RAX)                       # zero: the digits as they stand are nearer
        code.jmp(nearest_down)
        # Up: what is left to the scale is the distance, and the upper gap bounds it.
        code.label(nearest_up)
        at(RDI, self.BN_T)
        at(RSI, self.BN_S)
        code.call("nv_bn_copy")
        at(RDI, self.BN_T)
        at(RSI, self.BN_R)
        code.call("nv_bn_sub")
        at(RDI, self.BN_T)
        at(RSI, self.BN_MP)
        code.call("nv_bn_cmp")
        code.jmp(gap_test)
        # Down: what is left is the distance already, and the lower gap bounds it.
        code.label(nearest_down)
        at(RDI, self.BN_R)
        at(RSI, self.BN_MM)
        code.call("nv_bn_cmp")
        code.label(gap_test)                        # -1 inside, 0 on the edge, 1 outside
        code.xor_reg(R10, R10)
        code.cmp_reg(RAX, R10)
        code.jcc("l", inside)
        code.jcc("g", outside)
        slot(self.S_EVEN, RAX)
        code.test_reg(RAX)
        code.jcc("ne", inside)                      # a midpoint belongs to the even value
        code.jmp(outside)
        code.label(inside)
        slot(self.S_TEMP, RAX)
        code.test_reg(RAX)
        code.jcc("ne", round_up)
        code.jmp(round_done)
        code.label(outside)
        slot(self.S_LEN, RAX)
        code.mov_imm(R9, 17)
        code.cmp_reg(RAX, R9)
        code.jcc("ae", force_nearest)
        code.jmp(digit_loop)
        code.label(force_nearest)                   # seventeen digits always read back
        slot(self.S_TEMP, RAX)
        code.test_reg(RAX)
        code.jcc("ne", round_up)
        code.jmp(round_done)

        # -- the carry a round-up may need --------------------------------------
        code.label(round_up)
        slot(self.S_DIGIT, RAX)
        code.mov_imm(R11, 9)
        code.cmp_reg(RAX, R11)
        code.jcc("b", round_plain)
        slot(self.S_LEN, R11)
        code.dec(R11)                           # the digit just written becomes a zero
        at(R10, self.F64_DIGITS)
        code.add_reg(R10, R11)
        code.mov_imm(RAX, ord("0"))
        code.store_byte(R10, 0, RAX)
        code.label(round_carry)
        code.test_reg(R11)
        code.jcc("e", round_first)
        code.dec(R11)
        at(R10, self.F64_DIGITS)
        code.add_reg(R10, R11)
        code.load_byte(RAX, R10, 0)
        code.mov_imm(RCX, ord("9"))
        code.cmp_reg(RAX, RCX)
        code.jcc("e", round_nine)
        code.add_byte_imm(RAX, 1)
        code.store_byte(R10, 0, RAX)
        code.jmp(round_done)
        code.label(round_nine)
        code.mov_imm(RAX, ord("0"))
        code.store_byte(R10, 0, RAX)
        code.jmp(round_carry)
        code.label(round_first)
        # Every digit carried: the answer is one decade up, and one digit longer.
        slot(self.S_LEN, R11)
        code.label(shift_loop)
        code.test_reg(R11)
        code.jcc("e", shift_done)
        code.dec(R11)
        at(R10, self.F64_DIGITS)
        code.add_reg(R10, R11)
        code.load_byte(RAX, R10, 0)
        code.inc(R10)
        code.store_byte(R10, 0, RAX)
        code.jmp(shift_loop)
        code.label(shift_done)
        at(R10, self.F64_DIGITS)
        code.mov_imm(RAX, ord("1"))
        code.store_byte(R10, 0, RAX)
        slot(self.S_LEN, RAX)
        code.inc(RAX)
        put(self.S_LEN, RAX)
        slot(self.S_SCALE, RAX)
        code.inc(RAX)
        put(self.S_SCALE, RAX)
        code.jmp(round_done)
        code.label(round_plain)
        code.inc(RAX)
        code.add_byte_imm(RAX, ord("0"))
        slot(self.S_LEN, R11)
        code.dec(R11)
        at(R10, self.F64_DIGITS)
        code.add_reg(R10, R11)
        code.store_byte(R10, 0, RAX)
        code.label(round_done)
        # -- where the digits start, and the decimal exponent --------------------
        code.xor_reg(R11, R11)                  # j
        slot(self.S_LEN, R9)
        at(R10, self.F64_DIGITS)
        code.label(first_loop)
        code.cmp_reg(R11, R9)
        code.jcc("ae", first_done)
        code.mov_reg(RAX, R10)
        code.add_reg(RAX, R11)
        code.load_byte(RAX, RAX, 0)
        code.mov_imm(RCX, ord("0"))
        code.cmp_reg(RAX, RCX)
        code.jcc("ne", first_done)
        code.inc(R11)
        code.jmp(first_loop)
        code.label(first_done)
        put(self.S_FIRST, R11)
        slot(self.S_SCALE, RAX)                      # e10 = s - 1 - j
        code.dec(RAX)
        code.sub_reg(RAX, R11)
        put(self.S_E10, RAX)

        # -- the notation -------------------------------------------------------
        at(R10, self.F64_TEXT)
        put(self.S_CURSOR, R10)
        slot(self.S_SIGN, RAX)
        code.test_reg(RAX)
        code.jcc("e", choose)
        put_char("-")
        code.label(choose)
        slot(self.S_E10, RAX)
        code.mov_imm(R11, -4)
        code.cmp_reg(RAX, R11)
        code.jcc("l", exponential)
        code.mov_imm(R11, 16)
        code.cmp_reg(RAX, R11)
        code.jcc("ge", exponential)
        # The text is built forward in the frame and copied into the caller's buffer at
        # the end, so that every branch above writes its characters in its own order.
        def start_first() -> None:
            """The digit run starts at the first significant digit."""
            slot(self.S_FIRST, RAX)
            put(self.S_TEMP, RAX)

        def start_after_places() -> None:
            """The digit run starts at the first digit right of the decimal point."""
            slot(self.S_FIRST, RAX)
            slot(self.S_E10, R11)
            code.inc(R11)
            code.add_reg(RAX, R11)
            put(self.S_TEMP, RAX)

        def count_digits() -> None:
            slot(self.S_LEN, RAX)
            slot(self.S_FIRST, R11)
            code.sub_reg(RAX, R11)
            put(self.S_TEMP2, RAX)

        def count_places() -> None:
            slot(self.S_E10, RAX)
            code.inc(RAX)
            put(self.S_TEMP2, RAX)

        def count_rest() -> None:
            slot(self.S_LEN, RAX)
            slot(self.S_FIRST, R11)
            code.sub_reg(RAX, R11)
            slot(self.S_E10, R11)
            code.inc(R11)
            code.sub_reg(RAX, R11)
            put(self.S_TEMP2, RAX)

        def count_leading_zeros() -> None:
            """The zeros between `0.` and the first significant digit: one less than -e10."""
            slot(self.S_E10, RAX)
            code.neg(RAX)
            code.dec(RAX)
            put(self.S_TEMP2, RAX)

        def count_fraction() -> None:
            """Exponent notation's fraction: every digit but the first, trailing zeros dropped.

            A decimal needs neither the zeros the value does not reach inside its own gap,
            so `1e+300` where a fixed notation would have written `1.000...`.
            """
            loop, done = self.local("f64_fraction"), self.local("f64_fraction_done")
            slot(self.S_LEN, RAX)
            slot(self.S_FIRST, R11)
            code.sub_reg(RAX, R11)
            code.dec(RAX)                       # the digits after the one before the point
            code.label(loop)
            code.test_reg(RAX)
            code.jcc("e", done)
            slot(self.S_FIRST, R10)
            code.add_reg(R10, RAX)
            at(R11, self.F64_DIGITS)
            code.add_reg(R11, R10)
            code.load_byte(R11, R11, 0)
            code.mov_imm(R10, ord("0"))
            code.cmp_reg(R11, R10)
            code.jcc("ne", done)
            code.dec(RAX)
            code.jmp(loop)
            code.label(done)
            put(self.S_TEMP2, RAX)

        def count_trailing_zeros() -> None:
            slot(self.S_E10, RAX)
            code.inc(RAX)
            slot(self.S_LEN, R11)
            slot(self.S_FIRST, R10)
            code.sub_reg(R11, R10)
            code.sub_reg(RAX, R11)
            put(self.S_TEMP2, RAX)

        slot(self.S_E10, RAX)
        code.test_reg(RAX)
        code.jcc("ns", fixed_positive)
        # Below one: `0.`, the zeros the exponent names, then every digit.
        put_char("0")
        put_char(".")
        count_leading_zeros()
        put_zeros()
        start_first()
        count_digits()
        put_digits()
        code.jmp(mantissa_done)
        code.label(fixed_positive)
        slot(self.S_LEN, RAX)
        slot(self.S_FIRST, R11)
        code.sub_reg(RAX, R11)
        put(self.S_HIGH, RAX)                        # how many digits there are
        slot(self.S_E10, R11)
        code.inc(R11)
        put(self.S_LOW, R11)                         # how many places stand left of the point
        code.cmp_reg(RAX, R11)
        code.jcc("g", fixed_short)
        # Fewer digits than places: the zeros the value does not reach are written out,
        # and `.0` keeps a whole number looking like the float it is.
        start_first()
        count_digits()
        put_digits()
        count_trailing_zeros()
        put_zeros()
        put_char(".")
        put_char("0")
        code.jmp(mantissa_done)
        code.label(fixed_short)
        start_first()
        count_places()
        put_digits()
        put_char(".")
        start_after_places()
        count_rest()
        put_digits()
        code.jmp(mantissa_done)
        # -- exponent notation --------------------------------------------------
        code.label(exponential)
        start_first()
        code.mov_imm(RAX, 1)
        put(self.S_TEMP2, RAX)
        put_digits()                            # the digit left of the point
        count_fraction()
        slot(self.S_TEMP2, RAX)
        code.test_reg(RAX)
        code.jcc("e", exponent_negative)        # no mantissa fraction
        put_char(".")
        slot(self.S_FIRST, RAX)
        code.inc(RAX)
        put(self.S_TEMP, RAX)
        put_digits()
        code.label(exponent_negative)
        put_char("e")
        slot(self.S_E10, RAX)
        code.test_reg(RAX)
        code.jcc("ns", exponent_positive)
        put_char("-")
        slot(self.S_E10, RAX)
        code.neg(RAX)
        code.jmp(exponent_digits)
        code.label(exponent_positive)
        put_char("+")
        slot(self.S_E10, RAX)
        code.label(exponent_digits)
        put(self.S_TEMP, RAX)
        code.mov_imm(R11, 100)
        code.cmp_reg(RAX, R11)
        code.jcc("b", exponent_last_two)        # two digits, as `%e` writes them
        code.xor_reg(RDX, RDX)
        code.mov_imm(R8, 100)
        code.div(R8)                            # rax = hundreds, rdx = the last two
        code.add_byte_imm(RAX, ord("0"))
        put_byte(RAX)
        code.mov_reg(RAX, RDX)
        code.label(exponent_last_two)
        code.xor_reg(RDX, RDX)
        code.mov_imm(R8, 10)
        code.div(R8)                            # rax = tens, rdx = units
        code.add_byte_imm(RAX, ord("0"))
        code.add_byte_imm(RDX, ord("0"))
        put_byte(RAX)
        put_byte(RDX)
        code.label(mantissa_done)

        # -- the text leaves the frame ------------------------------------------
        slot(self.S_CURSOR, R11)
        at(R10, self.F64_TEXT)
        code.sub_reg(R11, R10)                  # how many characters were written
        put(self.S_TEMP2, R11)
        slot(self.S_END, RDI)
        code.sub_reg(RDI, R11)                  # where they belong in the caller's buffer
        code.mov_reg(R8, R10)
        put(self.S_TEMP, R11)
        code.label(copy_loop)
        slot(self.S_TEMP, R11)
        code.test_reg(R11)
        code.jcc("e", copy_done)
        code.load_byte(RAX, R8, 0)
        code.store_byte(RDI, 0, RAX)
        code.inc(R8)
        code.inc(RDI)
        slot(self.S_TEMP, R11)
        code.dec(R11)
        put(self.S_TEMP, R11)
        code.jmp(copy_loop)
        code.label(copy_done)
        slot(self.S_END, RAX)
        slot(self.S_TEMP2, RDX)
        code.sub_reg(RAX, RDX)                  # the start, and the length beside it
        code.jmp(finish)
        # -- the five names that are not a conversion ---------------------------
        code.label(not_finite)
        slot(self.S_MAG, R10)
        code.shl_imm(R10, 12)
        code.shr_imm(R10, 12)                   # any fraction bit means NaN
        code.test_reg(R10)
        code.jcc("ne", not_number)
        slot(self.S_SIGN, R10)
        code.test_reg(R10)
        code.jcc("ne", infinite_negative)
        code.lea_rip(R10, self.string("inf"))
        code.jmp(special_done)
        code.label(infinite_negative)
        code.lea_rip(R10, self.string("-inf"))
        code.jmp(special_done)
        code.label(not_number)
        code.lea_rip(R10, self.string("nan"))
        code.jmp(special_done)
        code.label(zero_text)
        slot(self.S_SIGN, R10)
        code.test_reg(R10)
        code.jcc("ne", zero_negative)
        code.lea_rip(R10, self.string("0.0"))
        code.jmp(special_done)
        code.label(zero_negative)
        code.lea_rip(R10, self.string("-0.0"))
        code.label(special_done)
        code.load(RDX, R10, 0)                  # a string here is length-prefixed
        code.lea(RAX, R10, 8)
        code.label(finish)
        code.mov_reg(RSP, RBP)
        code.pop(RBP)
        code.ret()

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
