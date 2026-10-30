"""The instruction encoder and a two-pass assembler (PLAN.md N14a).

The backend writes machine code, so it needs an encoder — and the encoder is
written here rather than borrowed, because the project's claim is that it needs no
C toolchain and no assembler to build a program. The set is small on purpose: the
instructions the IR needs, with `rel32` branches and `disp32` frame slots, nothing
else. Every method documents the encoding it emits in Intel's own notation, so a
reader can check a byte sequence against a manual without running anything.

Two decisions:

* **disp32 everywhere.** A frame slot is addressed as `[rbp-8*n]` with a 32-bit
  displacement even when 8 bits would do. It costs three bytes per access and
  removes a whole class of bug: a slot offset that grows past 127 as a program gets
  bigger would otherwise silently start meaning something else.
* **Labels are resolved in a second pass.** `branch` and `call` take a label name
  and emit a placeholder; `assemble` patches every reference once all labels are
  known, and raises `ValueError` for a label that was never defined — a dangling
  jump is a bug in the backend, not something to leave to the processor.
"""
from __future__ import annotations

#: Registers by their encoding numbers, with the byte registers named separately
#: because only the first four have a low-byte form without a REX prefix.
RAX, RCX, RDX, RBX, RSP, RBP, RSI, RDI = range(8)
R8, R9, R10, R11, R12, R13, R14, R15 = range(8, 16)
#: The two vector registers the backend uses, by encoding number.
XMM0, XMM1 = 0, 1
#: The registers the System V ABI passes integer arguments in.
ARGUMENT_REGISTERS = (RDI, RSI, RDX, RCX, R8, R9)
#: 64-bit registers a byte of which can be named: `al`, `cl`, `dl`, `bl`, and the
#: four that need a REX prefix (`spl`, `bpl`, `sil`, `dil`) — the last four are
#: encodable only because every byte store here already carries a REX prefix.
BYTE_REGISTERS = {RAX: 0, RCX: 1, RDX: 2, RBX: 3, RSP: 4, RBP: 5, RSI: 6, RDI: 7}


def _rex(w: bool = False, r: int = 0, x: int = 0, b: int = 0) -> bytes:
    """REX prefix: W for 64-bit operand size, R/X/B extend the three fields."""
    value = 0x40 | (0x8 if w else 0) | ((r >> 3) << 2) | ((x >> 3) << 1) | (b >> 3)
    return bytes([value]) if value != 0x40 or not (w or r or x or b) else bytes([value])


def _modrm(mod: int, reg: int, rm: int) -> bytes:
    return bytes([((mod & 3) << 6) | ((reg & 7) << 3) | (rm & 7)])


class Assembler:
    """A buffer of bytes with named labels and unresolved references.

    `bytes()` answers the buffer with every reference it can resolve itself, and
    `fixups` is what it could not: the unit turns those into relocations.
    """

    def __init__(self) -> None:
        self.buffer = bytearray()
        self.labels: dict[str, int] = {}
        self.fixups: list[tuple[str, int, int]] = []   # (label, at, width)
        self._defined: set[str] = set()

    # -- emission ------------------------------------------------------------
    def emit(self, data: bytes) -> None:
        self.buffer.extend(data)

    def label(self, name: str) -> None:
        if name in self._defined:
            raise ValueError(f"label {name!r} is defined twice")
        self._defined.add(name)
        self.labels[name] = len(self.buffer)

    def offset(self) -> int:
        return len(self.buffer)

    def _relative(self, label: str, size: int = 4) -> bytes:
        """A placeholder for a label-relative displacement."""
        self.fixups.append((label, len(self.buffer), size))
        return b"\x00" * size

    # -- moves ---------------------------------------------------------------
    def mov_imm(self, register: int, value: int) -> None:
        """`mov r64, imm64`: REX.W + B8+rd + imm64."""
        self.emit(_rex(w=True, b=register) + bytes([0xB8 + (register & 7)]))
        self.emit((value & 0xFFFFFFFFFFFFFFFF).to_bytes(8, "little"))

    def mov_reg(self, destination: int, source: int) -> None:
        """`mov r64, r64`: REX.W + 89 /r."""
        self.emit(_rex(w=True, r=source, b=destination) + b"\x89"
                  + _modrm(3, source, destination))

    def load(self, destination: int, base: int, displacement: int) -> None:
        """`mov r64, [base+disp32]`: REX.W + 8B /r."""
        self.emit(_rex(w=True, r=destination, b=base) + b"\x8B"
                  + _modrm(2, destination, base))
        self.emit(_int32(displacement))

    def store(self, base: int, displacement: int, source: int) -> None:
        """`mov [base+disp32], r64`: REX.W + 89 /r."""
        self.emit(_rex(w=True, r=source, b=base) + b"\x89" + _modrm(2, source, base))
        self.emit(_int32(displacement))

    def add_byte_imm(self, register: int, value: int) -> None:
        """`add r8, imm8`: 80 /0 ib, with a REX prefix so spl/bpl/sil/dil work."""
        low = BYTE_REGISTERS[register]
        self.emit(_rex(b=register) + b"\x80" + _modrm(3, 0, low) + bytes([value & 0xFF]))

    def store_byte_imm(self, base: int, displacement: int, value: int) -> None:
        """`mov byte [base+disp32], imm8`: C6 /0 ib."""
        self.emit(_rex(b=base) + b"\xC6" + _modrm(2, 0, base) + _int32(displacement)
                  + bytes([value & 0xFF]))

    def store_byte(self, base: int, displacement: int, source: int) -> None:
        """`mov [base+disp32], r8`: 88 /r with a REX prefix naming the byte register."""
        low = BYTE_REGISTERS[source]
        self.emit(_rex(r=0, b=base) + b"\x88" + _modrm(2, low, base))
        self.emit(_int32(displacement))

    def lea_rip(self, destination: int, label: str) -> None:
        """`lea r64, [rip+disp32]`: REX.W + 8D /r with mod=00, rm=101."""
        self.emit(_rex(w=True, r=destination) + b"\x8D" + _modrm(0, destination, 5))
        self.emit(self._relative(label))

    def lea(self, destination: int, base: int, displacement: int) -> None:
        """`lea r64, [base+disp32]`: REX.W + 8D /r."""
        self.emit(_rex(w=True, r=destination, b=base) + b"\x8D"
                  + _modrm(2, destination, base))
        self.emit(_int32(displacement))

    # -- arithmetic ----------------------------------------------------------
    def add_reg(self, destination: int, source: int) -> None:
        """`add r64, r64`: REX.W + 01 /r."""
        self.emit(_rex(w=True, r=source, b=destination) + b"\x01"
                  + _modrm(3, source, destination))

    def sub_reg(self, destination: int, source: int) -> None:
        """`sub r64, r64`: REX.W + 29 /r."""
        self.emit(_rex(w=True, r=source, b=destination) + b"\x29"
                  + _modrm(3, source, destination))

    def imul_reg(self, destination: int, source: int) -> None:
        """`imul r64, r64`: REX.W + 0F AF /r (two-operand form)."""
        self.emit(_rex(w=True, r=destination, b=source) + b"\x0F\xAF"
                  + _modrm(3, destination, source))

    def neg(self, register: int) -> None:
        """`neg r64`: REX.W + F7 /3."""
        self.emit(_rex(w=True, b=register) + b"\xF7" + _modrm(3, 3, register))

    def not_reg(self, register: int) -> None:
        """`not r64`: REX.W + F7 /2."""
        self.emit(_rex(w=True, b=register) + b"\xF7" + _modrm(3, 2, register))

    def div(self, register: int) -> None:
        """`div r64` — unsigned; the caller has cleared rdx: REX.W + F7 /6."""
        self.emit(_rex(w=True, b=register) + b"\xF7" + _modrm(3, 6, register))

    def idiv(self, register: int) -> None:
        """`idiv r64` — signed; the caller has run `cqo`: REX.W + F7 /7."""
        self.emit(_rex(w=True, b=register) + b"\xF7" + _modrm(3, 7, register))

    def cqo(self) -> None:
        """`cqo` — sign-extend rax into rdx: 48 99."""
        self.emit(b"\x48\x99")

    def inc(self, register: int) -> None:
        """`inc r64`: REX.W + FF /0."""
        self.emit(_rex(w=True, b=register) + b"\xFF" + _modrm(3, 0, register))

    def dec(self, register: int) -> None:
        """`dec r64`: REX.W + FF /1."""
        self.emit(_rex(w=True, b=register) + b"\xFF" + _modrm(3, 1, register))

    def add_imm8(self, register: int, value: int) -> None:
        """`add r64, imm8`: REX.W + 83 /0 ib."""
        self.emit(_rex(w=True, b=register) + b"\x83" + _modrm(3, 0, register)
                  + bytes([value & 0xFF]))

    def shl_imm(self, register: int, value: int) -> None:
        """`shl r64, imm8`: REX.W + C1 /4 ib — the bit fields of an IEEE-754 double."""
        self.emit(_rex(w=True, b=register) + b"\xC1" + _modrm(3, 4, register)
                  + bytes([value & 0xFF]))

    def shr_imm(self, register: int, value: int) -> None:
        """`shr r64, imm8`: REX.W + C1 /5 ib — logical, so the sign bit comes out as 1."""
        self.emit(_rex(w=True, b=register) + b"\xC1" + _modrm(3, 5, register)
                  + bytes([value & 0xFF]))

    def and_reg(self, destination: int, source: int) -> None:
        """`and r64, r64`: REX.W + 21 /r — a mask that is only known at run time.

        A table's probe wraps with `index & (capacity - 1)`, and the capacity is a
        field of the handle rather than a constant, so the mask cannot be an
        immediate the way an alignment mask can.
        """
        self.emit(_rex(w=True, r=source, b=destination) + b"\x21"
                  + _modrm(3, source, destination))

    def and_imm32(self, register: int, value: int) -> None:
        """`and r64, imm32`: REX.W + 81 /4 id — sign-extended, so -16 aligns."""
        self.emit(_rex(w=True, b=register) + b"\x81" + _modrm(3, 4, register))
        self.emit(_int32(value))

    def sub_rsp(self, value: int) -> None:
        """`sub rsp, imm32`: REX.W + 81 /5 id."""
        self.emit(_rex(w=True, b=RSP) + b"\x81" + _modrm(3, 5, RSP))
        self.emit(_int32(value))

    def add_rsp(self, value: int) -> None:
        """`add rsp, imm32`: REX.W + 81 /0 id."""
        self.emit(_rex(w=True, b=RSP) + b"\x81" + _modrm(3, 0, RSP))
        self.emit(_int32(value))

    # -- comparison ----------------------------------------------------------
    def cmp_reg(self, left: int, right: int) -> None:
        """`cmp r64, r64`: REX.W + 39 /r — flags for `left - right`."""
        self.emit(_rex(w=True, r=right, b=left) + b"\x39" + _modrm(3, right, left))

    def test_reg(self, register: int) -> None:
        """`test r64, r64`: REX.W + 85 /r."""
        self.emit(_rex(w=True, r=register, b=register) + b"\x85"
                  + _modrm(3, register, register))

    def setcc(self, condition: str, register: int) -> None:
        """`setcc r8`: 0F 90+cc /0 (no operand-size prefix; it is always a byte)."""
        codes = {"o": 0, "no": 1, "b": 2, "ae": 3, "e": 4, "ne": 5, "be": 6,
                 "a": 7, "s": 8, "ns": 9, "l": 0xC, "ge": 0xD, "le": 0xE, "g": 0xF}
        low = BYTE_REGISTERS[register]
        self.emit(_rex(b=register) + b"\x0F" + bytes([0x90 + codes[condition]])
                  + _modrm(3, 0, register))

    def load_byte(self, destination: int, base: int, displacement: int) -> None:
        """`movzx r64, byte [base+disp32]`: REX.W + 0F B6 /r."""
        self.emit(_rex(w=True, r=destination, b=base) + b"\x0F\xB6"
                  + _modrm(2, destination, base))
        self.emit(_int32(displacement))

    def movzx_byte(self, destination: int, source: int) -> None:
        """`movzx r64, r8`: REX.W + 0F B6 /r."""
        low = BYTE_REGISTERS[source]
        self.emit(_rex(w=True, r=destination, b=source) + b"\x0F\xB6"
                  + _modrm(3, destination, source))

    # -- control flow --------------------------------------------------------
    def jmp(self, label: str) -> None:
        """`jmp rel32`: E9 rel32."""
        self.emit(b"\xE9")
        self.emit(self._relative(label))

    def jcc(self, condition: str, label: str) -> None:
        """`jcc rel32`: 0F 80+cc rel32."""
        codes = {"o": 0, "no": 1, "b": 2, "ae": 3, "e": 4, "ne": 5, "be": 6, "a": 7,
                 "s": 8, "ns": 9, "p": 0xA, "np": 0xB, "l": 0xC, "ge": 0xD, "le": 0xE,
                 "g": 0xF}
        self.emit(b"\x0F" + bytes([0x80 + codes[condition]]))
        self.emit(self._relative(label))

    def call_indirect(self, register: int) -> None:
        """`call *%reg`: FF /2 — a call through a value the program is holding.

        The one call a backend cannot resolve at link time: the address is in a
        register, so the relocation table has nothing to say about it and the
        branch has no label.
        """
        self.emit(_rex(b=register) + b"\xFF" + _modrm(3, 2, register))

    def call(self, label: str) -> None:
        """`call rel32`: E8 rel32."""
        self.emit(b"\xE8")
        self.emit(self._relative(label))

    def ret(self) -> None:
        self.emit(b"\xC3")

    def syscall(self) -> None:
        self.emit(b"\x0F\x05")

    def push(self, register: int) -> None:
        self.emit(_rex(b=register) + bytes([0x50 + (register & 7)]))

    def pop(self, register: int) -> None:
        self.emit(_rex(b=register) + bytes([0x58 + (register & 7)]))

    def xor_reg(self, destination: int, source: int) -> None:
        """`xor r64, r64`: REX.W + 31 /r — the cheapest way to make a zero."""
        self.emit(_rex(w=True, r=source, b=destination) + b"\x31"
                  + _modrm(3, source, destination))

    # -- the vector registers ------------------------------------------------
    def movq_xmm(self, xmm: int, register: int) -> None:
        """`movq xmm, r64`: 66 REX.W 0F 6E /r — a bit pattern into a vector register.

        The backend keeps every `f64` in a frame slot like every other value, so the
        move between a general register and a vector one is where arithmetic gets its
        operands: the slot holds the bits, and the bits are what travels.
        """
        self.emit(b"\x66" + _rex(w=True, r=xmm, b=register) + b"\x0f\x6e"
                  + _modrm(3, xmm, register))

    def movq_xmm_to_reg(self, register: int, xmm: int) -> None:
        """`movq r64, xmm`: 66 REX.W 0F 7E /r — the bit pattern back out."""
        self.emit(b"\x66" + _rex(w=True, r=xmm, b=register) + b"\x0f\x7e"
                  + _modrm(3, xmm, register))

    def _sse(self, opcode: bytes, destination: int, source: int) -> None:
        """One `F2 0F <opcode> /r` arithmetic instruction: `destination op= source`."""
        self.emit(b"\xf2" + _rex(r=destination, b=source) + b"\x0f" + opcode
                  + _modrm(3, destination, source))

    def addsd(self, destination: int, source: int) -> None:
        """`addsd xmm1, xmm2`: F2 0F 58 /r — one `f64` added, IEEE, no flags checked."""
        self._sse(b"\x58", destination, source)

    def subsd(self, destination: int, source: int) -> None:
        """`subsd xmm1, xmm2`: F2 0F 5C /r."""
        self._sse(b"\x5c", destination, source)

    def mulsd(self, destination: int, source: int) -> None:
        """`mulsd xmm1, xmm2`: F2 0F 59 /r."""
        self._sse(b"\x59", destination, source)

    def divsd(self, destination: int, source: int) -> None:
        """`divsd xmm1, xmm2`: F2 0F 5E /r — `1.0 / 0.0` is `inf`, as in C."""
        self._sse(b"\x5e", destination, source)

    def cvtsi2sd(self, xmm: int, register: int) -> None:
        """`cvtsi2sd xmm, r64`: F2 REX.W 0F 2A /r — an `i64` to the nearest `f64`.

        Every `i64` that does not fit exactly is rounded to nearest-even, which is the
        language's rule and the C compiler's rule for the same conversion.
        """
        self.emit(b"\xf2" + _rex(w=True, r=xmm, b=register) + b"\x0f\x2a"
                  + _modrm(3, xmm, register))

    def cvttsd2si(self, register: int, xmm: int) -> None:
        """`cvttsd2si r64, xmm`: F2 REX.W 0F 2C /r — the `f64`, truncated toward zero.

        An inexact answer is not the only failure mode: a value outside the `i64` range
        and a `nan` both answer `int64_min`, which is why the caller compares the result
        back instead of trusting it.
        """
        self.emit(b"\xf2" + _rex(w=True, r=register, b=xmm) + b"\x0f\x2c"
                  + _modrm(3, register, xmm))

    def ucomisd(self, left: int, right: int) -> None:
        """`ucomisd xmm1, xmm2`: 66 0F 2E /r — flags for a comparison, `pf` if unordered.

        The unordered case is why every `f64` comparison here tests `pf` first: with a
        `nan` operand `zf`, `pf` and `cf` are all set, so the signed conditions the
        integer comparisons use would answer `true` for `nan < 1.0`.
        """
        self.emit(b"\x66" + _rex(r=left, b=right) + b"\x0f\x2e"
                  + _modrm(3, left, right))

    # -- resolution ----------------------------------------------------------
    def bytes(self) -> bytes:
        """The buffer with every reference the buffer itself can resolve filled in.

        A 4-byte reference to a label in this same buffer is a distance, so it is
        computable here and is written in immediately — that is every branch and
        every jump to a block of the same unit. Everything else stays zero and stays
        in `fixups`, because its value depends on where the section lands; a unit
        turns those into relocations and the linker decides.
        """
        resolved = bytearray(self.buffer)
        for label, at, width in self.fixups:
            if width == 4 and label in self.labels:
                resolved[at:at + width] = _int32(self.labels[label] - (at + width))
        return bytes(resolved)

    def absolute(self, label: str) -> None:
        """An 8-byte address of `label`: a pointer, resolved by the linker."""
        self.emit(b"\x00" * 8)
        self.fixups.append((label, len(self.buffer) - 8, 8))


def _int32(value: int) -> bytes:
    return (value & 0xFFFFFFFF).to_bytes(4, "little")
