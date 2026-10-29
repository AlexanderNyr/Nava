"""IR → x86-64 machine code (PLAN.md N14a).

The whole of the backend is here: values to stack slots, instructions to encodings,
blocks to labels, and a runtime small enough to read. Three decisions carry most of
the weight.

* **Every value lives in a frame slot.** Registers are not allocated. A value is
  loaded into `rax` (and `r11` for the second operand) when it is needed and written
  back when it is defined, so no instruction ever has to care what is already in a
  register. That is slower than a register allocator and it is the right first
  version: the differential test compares two backends' *output*, and an allocator
  bug would look exactly like a semantic bug.
* **Overflow is checked where the CPU can see it.** `add`, `sub`, `imul` and `neg`
  set the overflow flag, so the check after each is `jo` and a jump to a `nv_fail`
  call, and a program that would wrap stops with the same message the C backend
  prints. Nothing here is a `-fwrapv` in disguise.
* **The runtime is syscalls.** `write` and `exit`, and nothing else: printing an
  integer is a decimal conversion loop in machine code, not a call into a C library,
  because there is no C library. The message texts are written out beside the code
  and are byte-for-byte the ones `cgen.py` prints — `%` says "remainder by zero",
  not "division by zero" — which is what lets the differential test treat a trap as
  ordinary output.

Calling convention, since these functions are called by our own code only: System V,
arguments in `rdi`, `rsi`, `rdx`, `rcx`, `r8`, `r9`, at most six of them — more is
refused with `E0901` rather than mis-passed. `rsp` is 16-byte aligned at every `call`
because every frame is a multiple of 16, and callee-saved registers are never used
except `rbp`, which is saved and restored by the prologue and the epilogue.
"""
from __future__ import annotations

from ..diagnostics import make
from . import elf64, ir
from .x86 import (
    ARGUMENT_REGISTERS, R8, R9, R10, R11, RAX, RBP, RCX, RDI, RDX, RSI, RSP, Assembler,
)

#: How many arguments a call may pass. Beyond this the System V ABI would need stack
#: arguments, which the backend does not implement; refusing is better than passing.
MAX_ARGUMENTS = len(ARGUMENT_REGISTERS)

#: What the C runtime prints before a message, kept identical on purpose.
FAIL_PREFIX = "nava runtime error: "
I64_MIN = -(2 ** 63)

#: The `cmp`/`setcc` conditions of the IR's comparison operations.
COMPARISONS = {"eq": "e", "ne": "ne", "lt": "l", "le": "le", "gt": "g", "ge": "ge"}
#: The message each checked operation prints when it fails.
OVERFLOW_MESSAGES = {"add": "i64 overflow in +", "sub": "i64 overflow in -",
                     "mul": "i64 overflow in *", "div": "i64 overflow in /",
                     "mod": "i64 overflow in %", "neg": "i64 overflow in unary -"}
#: `%` has its own zero message in the C runtime, and the two are compared.
ZERO_MESSAGES = {"div": "division by zero", "mod": "remainder by zero"}


class Data:
    """The writable-and-constant half of the image: strings and a print buffer.

    A string is stored as its length in eight bytes followed by its bytes, so `print`
    of a `str` is one load and one `write` with no terminator to scan for. The same
    shape serves the runtime's messages, which lets `nv_fail` take a pointer and no
    length.
    """

    def __init__(self) -> None:
        self.bytes = bytearray()
        self.labels: dict[str, int] = {}
        self._strings: dict[str, str] = {}

    def raw(self, label: str, payload: bytes) -> str:
        """Place raw bytes under a name; used for the pieces of the runtime's texts."""
        if label not in self.labels:
            self.labels[label] = len(self.bytes)
            self.bytes += payload
        return label

    def string(self, text: str) -> str:
        """Place a length-prefixed string, reusing an identical earlier one."""
        if text not in self._strings:
            label = f"str{len(self._strings)}"
            encoded = text.encode("utf-8")
            self.labels[label] = len(self.bytes)
            self.bytes += len(encoded).to_bytes(8, "little") + encoded
            self._strings[text] = label
        return self._strings[text]

    def address(self, label: str) -> int:
        return elf64.data_address(self.labels[label])


class Generator:
    """One module's code generation."""

    def __init__(self, module: ir.Module) -> None:
        self.module = module
        self.code = Assembler()
        self.data = Data()
        self._next_label = 0
        #: The function whose blocks are being emitted; a terminator names blocks by
        #: label, and labels are only unique inside one function.
        self._current: ir.Function | None = None

    # -- small helpers -------------------------------------------------------
    def fresh(self, hint: str) -> str:
        name = f"{hint}_{self._next_label}"
        self._next_label += 1
        return name

    @staticmethod
    def block_label(function: ir.Function, block: ir.Block) -> str:
        return f"fn_{function.name}.{block.label}"

    def fail(self, message: str) -> None:
        """Emit `nv_fail(message)` — three registers and a call, then nothing runs."""
        self.code.lea_rip(RDI, self.data.string(message))
        self.code.call("nv_fail")

    # -- the image -----------------------------------------------------------
    def generate(self) -> bytes:
        """Generate the program and answer the bytes of a finished ELF file."""
        self.data.raw("nv_newline", b"\n")
        self.data.raw("nv_true", b"true\n")
        self.data.raw("nv_false", b"false\n")
        self.data.raw("nv_prefix", FAIL_PREFIX.encode())
        self.data.raw("nv_number_buffer", bytes(32))

        entry = self.module.function(self.module.entry)
        self.entry_point()
        for function in self.module.functions:
            self.function(function)
        self.print_i64_helper()
        self.print_bool_helper()
        self.fail_helper()

        entry_label = "nv_start"
        code = self.code.assemble(base=elf64.text_entry(0),
                                  resolver=self.data.address)
        data = bytes(self.data.bytes)
        return elf64.build(code, data, elf64.text_entry(self.code.labels[entry_label]))

    def entry_point(self) -> None:
        """`_start`: call `main`, then `exit` with its value as the status.

        The C backend returns `(int)(uint8_t)main()`, so the status is the value modulo
        256 — `movzx rdi, al` is that, and a `void main` leaves the status at zero.
        """
        main = self.module.function(self.module.entry)
        self.code.label("nv_start")
        self.code.call(f"fn_{main.name}")
        if main.returns == "void":
            self.code.xor_reg(RDI, RDI)
        else:
            self.code.movzx_byte(RDI, RAX)
        self.code.mov_imm(RAX, 60)
        self.code.syscall()

    # -- one function --------------------------------------------------------
    def function(self, function: ir.Function) -> None:
        if len(function.params) > MAX_ARGUMENTS:
            raise make(
                "E0901",
                f"the native backend does not implement a call with more than "
                f"{MAX_ARGUMENTS} arguments (in '{function.name}')",
                None,
                hint="build with the C backend (the default): `nava build` without "
                     "--backend native",
            )
        slots = self.assign_slots(function)
        frame = _frame_size(len(slots))
        self._current = function
        self.code.label(f"fn_{function.name}")
        self.code.push(RBP)
        self.code.mov_reg(RBP, RSP)
        self.code.sub_rsp(frame)
        for index, (name, _typ) in enumerate(function.params):
            self.code.store(RBP, -8 * (slots[name] + 1), ARGUMENT_REGISTERS[index])
        for block in function.blocks:
            self.code.label(self.block_label(function, block))
            for instruction in block.instructions:
                self.instruction(function, slots, instruction)
        # A function whose last block was left open ends in nothing; `lower` only
        # allows that for `void` functions, and it appends the `ret` itself, so
        # falling through here would be a lowering bug rather than a program.

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
    def instruction(self, function: ir.Function, slots: dict[str, int],
                    instruction) -> None:  # noqa: C901 - one branch per instruction
        if isinstance(instruction, ir.Const):
            payload = instruction.payload
            number = int(payload) if instruction.typ == "i64" else (1 if payload else 0)
            self.code.mov_imm(RAX, number)
            self.store(slots, instruction.value, RAX)
            return
        if isinstance(instruction, ir.Str):
            self.code.lea_rip(RAX, self.data.string(instruction.text))
            self.store(slots, instruction.value, RAX)
            return
        if isinstance(instruction, ir.Bin):
            self.load(slots, instruction.left, RAX)
            self.load(slots, instruction.right, R11)
            if instruction.op in ("add", "sub", "mul"):
                if instruction.op == "add":
                    self.code.add_reg(RAX, R11)
                elif instruction.op == "sub":
                    self.code.sub_reg(RAX, R11)
                else:
                    self.code.imul_reg(RAX, R11)
                self.code.jcc("o", self.overflow(instruction.op))
            elif instruction.op in ("div", "mod"):
                self.division(instruction.op)
            else:
                raise AssertionError(f"unknown IR binary op {instruction.op!r}")
            self.store(slots, instruction.value, RAX if instruction.op != "mod" else RDX)
            return
        if isinstance(instruction, ir.Neg):
            self.load(slots, instruction.operand, RAX)
            self.code.neg(RAX)
            self.code.jcc("o", self.overflow("neg"))
            self.store(slots, instruction.value, RAX)
            return
        if isinstance(instruction, ir.Not):
            self.load(slots, instruction.operand, RAX)
            self.code.mov_imm(R11, 1)
            self.code.xor_reg(RAX, R11)
            self.store(slots, instruction.value, RAX)
            return
        if isinstance(instruction, ir.Cmp):
            self.load(slots, instruction.left, RAX)
            self.load(slots, instruction.right, R11)
            self.code.cmp_reg(RAX, R11)
            self.code.setcc(COMPARISONS[instruction.op], RAX)
            self.code.movzx_byte(RAX, RAX)
            self.store(slots, instruction.value, RAX)
            return
        if isinstance(instruction, ir.Copy):
            self.load(slots, instruction.operand, RAX)
            self.store(slots, instruction.value, RAX)
            return
        if isinstance(instruction, ir.Call):
            if len(instruction.args) > MAX_ARGUMENTS:
                raise make(
                    "E0901",
                    f"the native backend does not implement a call with more than "
                    f"{MAX_ARGUMENTS} arguments (calling '{instruction.target}' in "
                    f"'{function.name}')",
                    None,
                    hint="build with the C backend (the default): `nava build` "
                         "without --backend native",
                )
            for index, argument in enumerate(instruction.args):
                self.load(slots, argument, ARGUMENT_REGISTERS[index])
            self.code.call(f"fn_{instruction.target}")
            if instruction.returns != "void":
                self.store(slots, instruction.value, RAX)
            return
        if isinstance(instruction, ir.Print):
            self.print_value(slots, instruction)
            return
        if isinstance(instruction, ir.Jump):
            self.jump(slots, instruction)
            return
        if isinstance(instruction, ir.Branch):
            self.branch(function, slots, instruction)
            return
        if isinstance(instruction, ir.Ret):
            if instruction.operand is not None:
                self.load(slots, instruction.operand, RAX)
            self.code.mov_reg(RSP, RBP)
            self.code.pop(RBP)
            self.code.ret()
            return
        raise AssertionError(f"unknown IR instruction {instruction!r}")

    def load(self, slots: dict[str, int], value: str, register: int) -> None:
        self.code.load(register, RBP, -8 * (slots[value] + 1))

    def store(self, slots: dict[str, int], value: str, register: int) -> None:
        self.code.store(RBP, -8 * (slots[value] + 1), register)

    def overflow(self, op: str) -> str:
        """Emit the trap for a checked operation and answer the label after it.

        The flag was set by the instruction just emitted, so `jo` has to come first:
        a trap is a `call` into a function that exits, and control reaches the label
        that follows only when the flag was clear.
        """
        skipped = self.fresh("checked")
        self.code.jcc("no", skipped)
        self.fail(OVERFLOW_MESSAGES[op])
        self.code.label(skipped)
        return skipped

    def division(self, op: str) -> None:
        """`div`/`mod` on `rax` by `r11`: zero, then the one overflow, then `idiv`."""
        zero = self.fresh("div_zero")
        room = self.fresh("div_ok")
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
        skip = self.fresh("div_done")
        self.code.jmp(skip)
        self.code.label(zero)
        self.fail(ZERO_MESSAGES[op])
        self.code.label(skip)

    def jump(self, slots: dict[str, int], instruction: ir.Jump) -> None:
        for value, target in zip(instruction.args, self._parameters(instruction.target)):
            self.load(slots, value, RAX)
            self.store(slots, target, RAX)
        self.code.jmp(self._block_of(instruction.target))

    def branch(self, function: ir.Function, slots: dict[str, int],
               instruction: ir.Branch) -> None:
        self.load(slots, instruction.condition, RAX)
        self.code.test_reg(RAX)
        then_edge = self.fresh("then_edge")
        self.code.jcc("ne", then_edge)
        for value, target in zip(instruction.else_args,
                                 self._parameters(instruction.else_target)):
            self.load(slots, value, RAX)
            self.store(slots, target, RAX)
        self.code.jmp(self._block_of(instruction.else_target))
        self.code.label(then_edge)
        for value, target in zip(instruction.then_args,
                                 self._parameters(instruction.then_target)):
            self.load(slots, value, RAX)
            self.store(slots, target, RAX)
        self.code.jmp(self._block_of(instruction.then_target))

    # The block labels a terminator refers to are per-function, and `_current` is the
    # function being emitted.

    def _block_of(self, label: str) -> str:
        assert self._current is not None
        return f"fn_{self._current.name}.{label}"

    def _parameters(self, label: str) -> list[str]:
        assert self._current is not None
        for block in self._current.blocks:
            if block.label == label:
                return [name for name, _typ in block.params]
        raise AssertionError(f"no block {label!r} in '{self._current.name}'")

    # -- printing ------------------------------------------------------------
    def print_value(self, slots: dict[str, int], instruction: ir.Print) -> None:
        if instruction.typ == "i64":
            self.load(slots, instruction.operand, RDI)
            self.code.call("nv_print_i64")
            return
        if instruction.typ == "bool":
            self.load(slots, instruction.operand, RDI)
            self.code.call("nv_print_bool")
            return
        if instruction.typ == "str":
            # The value is the address of a length-prefixed string: write its bytes,
            # then a newline, in two `write` calls because there is no `writev` here.
            self.load(slots, instruction.operand, RAX)
            self.code.load(RDX, RAX, 0)
            self.code.lea(RSI, RAX, 8)
            self.code.mov_imm(RDI, 1)
            self.code.mov_imm(RAX, 1)
            self.code.syscall()
            self.newline_to(1)
            return
        raise AssertionError(f"unknown printed type {instruction.typ!r}")

    def newline_to(self, descriptor: int) -> None:
        self.code.lea_rip(RSI, "nv_newline")
        self.code.mov_imm(RDX, 1)
        self.code.mov_imm(RDI, descriptor)
        self.code.mov_imm(RAX, 1)
        self.code.syscall()

    # -- the runtime ---------------------------------------------------------
    def print_i64_helper(self) -> None:
        """`nv_print_i64(rdi)`: decimal, sign first, then a newline.

        The digits are produced into the end of a buffer and written from the first
        digit, which is why the buffer's address is taken as an end pointer and the
        length is computed as `end - start`. `neg` on `INT64_MIN` is the one value
        that does not change, and dividing it as an unsigned number is why the
        division here is `div` and not `idiv`.
        """
        code = self.code
        code.label("nv_print_i64")
        code.push(RBP)
        code.mov_reg(RBP, RSP)
        code.sub_rsp(16)
        code.lea_rip(R8, "nv_number_buffer")
        code.add_imm8(R8, 32)
        code.mov_reg(R9, R8)
        code.mov_reg(RAX, RDI)
        code.xor_reg(RCX, RCX)                  # negative?
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
        code.sub_reg(RDX, R9)                   # length
        code.mov_reg(RSI, R9)
        code.mov_imm(RDI, 1)
        code.mov_imm(RAX, 1)
        code.syscall()
        self.newline_to(1)
        code.mov_reg(RSP, RBP)
        code.pop(RBP)
        code.ret()

    def print_bool_helper(self) -> None:
        """`nv_print_bool(rdi)`: `true` or `false`, then a newline."""
        code = self.code
        code.label("nv_print_bool")
        code.test_reg(RDI)
        code.jcc("e", "nv_print_bool.false")
        code.lea_rip(RSI, "nv_true")
        code.mov_imm(RDX, 5)
        code.jmp("nv_print_bool.write")
        code.label("nv_print_bool.false")
        code.lea_rip(RSI, "nv_false")
        code.mov_imm(RDX, 6)
        code.label("nv_print_bool.write")
        code.mov_imm(RDI, 1)
        code.mov_imm(RAX, 1)
        code.syscall()
        code.ret()

    def fail_helper(self) -> None:
        """`nv_fail(rdi)`: the C runtime's message on stderr, then `exit(1)`."""
        code = self.code
        code.label("nv_fail")
        code.push(RBP)
        code.mov_reg(RBP, RSP)
        code.sub_rsp(16)
        code.store(RBP, -8, RDI)                # keep the message across the syscall
        code.lea_rip(RSI, "nv_prefix")
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
        code.mov_imm(RDI, 1)
        code.mov_imm(RAX, 60)
        code.syscall()
        code.ret()                              # unreachable; keeps the assembler happy


def _frame_size(slots: int) -> int:
    """Frame bytes: every slot is eight bytes, rounded up to keep `rsp` aligned."""
    return max(16, (8 * slots + 15) // 16 * 16)


def generate(module: ir.Module) -> bytes:
    """Lower-level entry point: an IR module to the bytes of an ELF executable."""
    return Generator(module).generate()
