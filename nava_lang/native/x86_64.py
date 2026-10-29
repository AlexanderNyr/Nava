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

from pathlib import Path

from ..diagnostics import make
from . import ir, linker
from .unit import DATA, RODATA, UnitBuilder
from .x86 import (
    ARGUMENT_REGISTERS, R8, R9, R10, R11, RAX, RBP, RCX, RDI, RDX, RSI, RSP, Assembler,
)

#: How many arguments a call may pass. Beyond this the System V ABI needs stack
#: arguments, which the backend does not implement; refusing is better than passing
#: them wrongly.
MAX_ARGUMENTS = len(ARGUMENT_REGISTERS)
#: What the C runtime prints before a message, kept identical on purpose.
FAIL_PREFIX = "nava runtime error: "
I64_MIN = -(2 ** 63)
#: The variable that turns the trace on. A *variable*, not a flag: the same binary
#: prints a trace when it is asked to and nothing when it is not.
TRACE_VARIABLE = "NAVA_TRACE="
#: How deep a trace walks before it stops. A broken frame chain must not hang a
#: program that is already failing.
TRACE_DEPTH = 64

COMPARISONS = {"eq": "e", "ne": "ne", "lt": "l", "le": "le", "gt": "g", "ge": "ge"}
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
        for block in function.blocks:
            for name, _typ in block.params:
                slots[name] = len(slots)
            for instruction in block.instructions:
                value = getattr(instruction, "value", "")
                if isinstance(instruction, (ir.Const, ir.Str, ir.Bin, ir.Neg, ir.Not,
                                            ir.Cmp, ir.Copy, ir.Call)):
                    if value and value not in slots:
                        slots[value] = len(slots)
        return slots

    # -- instructions --------------------------------------------------------
    def instruction(self, function: ir.Function, instruction) -> None:
        """Emit one instruction. One branch per IR instruction, no interpretation."""
        if isinstance(instruction, ir.Const):
            number = (int(instruction.payload) if instruction.typ == "i64"
                      else (1 if instruction.payload else 0))
            self.code.mov_imm(RAX, number)
            self.store(instruction.value, RAX)
            return
        if isinstance(instruction, ir.Str):
            self.code.lea_rip(RAX, self.string(instruction.text))
            self.store(instruction.value, RAX)
            return
        if isinstance(instruction, ir.Bin):
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
        raise AssertionError(f"unknown printed type {instruction.typ!r}")

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
                             ("nv_fail", self.fail_helper)):
            self.runtime_function(symbol, body)
        return self.builder.finalise()

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

        Line numbers are small and positive, so this is the unsigned loop without the
        sign and without the newline that `nv_print_i64` needs.
        """
        code = self.code
        buffer = self.raw("nv_number_buffer", bytes(32), DATA)
        code.push(RBP)
        code.mov_reg(RBP, RSP)
        code.sub_rsp(16)
        code.lea_rip(R8, buffer)
        code.add_imm8(R8, 32)
        code.mov_reg(R9, R8)
        code.mov_reg(RAX, RDI)
        code.mov_imm(R10, 10)
        code.label("nv_write_u64.digits")
        code.xor_reg(RDX, RDX)
        code.div(R10)
        code.add_byte_imm(RDX, ord("0"))
        code.dec(R9)
        code.store_byte(R9, 0, RDX)
        code.test_reg(RAX)
        code.jcc("ne", "nv_write_u64.digits")
        code.mov_reg(RDX, R8)
        code.sub_reg(RDX, R9)
        code.mov_reg(RSI, R9)
        code.mov_imm(RDI, 2)
        code.mov_imm(RAX, 1)
        code.syscall()
        code.mov_reg(RSP, RBP)
        code.pop(RBP)
        code.ret()

    def print_i64_helper(self) -> None:
        """`nv_print_i64(rdi)`: sign, decimal digits, newline, on stdout."""
        code = self.code
        buffer = self.raw("nv_number_buffer", bytes(32), DATA)
        code.push(RBP)
        code.mov_reg(RBP, RSP)
        code.sub_rsp(16)
        code.lea_rip(R8, buffer)
        code.add_imm8(R8, 32)
        code.mov_reg(R9, R8)
        code.mov_reg(RAX, RDI)
        code.xor_reg(RCX, RCX)
        code.test_reg(RAX)
        code.jcc("ns", "nv_print_i64.positive")
        code.neg(RAX)
        code.mov_imm(RCX, 1)
        code.label("nv_print_i64.positive")
        code.mov_imm(R10, 10)
        code.label("nv_print_i64.digits")
        code.xor_reg(RDX, RDX)
        code.div(R10)
        code.add_byte_imm(RDX, ord("0"))
        code.dec(R9)
        code.store_byte(R9, 0, RDX)
        code.test_reg(RAX)
        code.jcc("ne", "nv_print_i64.digits")
        code.test_reg(RCX)
        code.jcc("e", "nv_print_i64.write")
        code.dec(R9)
        code.store_byte_imm(R9, 0, ord("-"))
        code.label("nv_print_i64.write")
        code.mov_reg(RDX, R8)
        code.sub_reg(RDX, R9)
        code.mov_reg(RSI, R9)
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
