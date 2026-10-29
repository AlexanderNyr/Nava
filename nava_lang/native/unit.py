"""A translation unit: code, data, symbols, relocations and line entries (N14b).

Until N14b the backend produced one blob and resolved every reference while it was
being emitted. That works for a program that is one file and stops working the moment
two files can call each other, so the unit is the smallest thing the backend now
produces and the linker is what turns units into a file.

A unit has three sections — `text`, `rodata`, `data` — one table of **symbols** it
defines, one list of **relocations** it cannot resolve alone, and the line entries of
its functions. The three Assemblers inside `UnitBuilder` still resolve a branch to a
block of the same function eagerly, because that distance is known; everything else
becomes a relocation, which is the split a real assembler makes.

Two naming rules, both stated because they are the whole of link-time hygiene:

* **Symbols are global and qualified.** A Nava function is `nava.<module>.<name>`
  (`nava.entry.main` for the entry file), and the runtime's helpers are named
  explicitly, so two modules may each define `abs` without colliding and `nm` on the
  output names the function its author wrote.
* **A unit-local label never leaves its unit.** String literals in `.rodata` and the
  number buffer in `.data` are named after the unit that emitted them, so a reference
  from another unit can only reach something its owner exported on purpose.
"""
from __future__ import annotations

from .x86 import Assembler
from ..records import field, record

TEXT, RODATA, DATA = "text", "rodata", "data"
#: The sections a unit has, in the order the linker lays them out.
SECTIONS = (TEXT, RODATA, DATA)


@record()
class Symbol:
    """A name the unit defines, at an offset in one of its sections."""

    name: str
    section: str
    offset: int
    #: `func` for a function, `object` for data; the ELF symbol type follows from it.
    kind: str = "object"
    size: int = 0
    local: bool = False


@record()
class Relocation:
    """A reference the unit could not fill in: only the linker knows the address."""

    section: str
    offset: int
    symbol: str
    #: `pc32` is a 4-byte displacement from the end of the field (`call`, `jmp`,
    #: `lea r64,[rip+…]`); `abs64` is an 8-byte address.
    kind: str = "pc32"
    addend: int = 0


@record()
class Line:
    """One `code offset → source line` fact, at an offset in the unit's text."""

    offset: int
    line: int


@record()
class FunctionRecord:
    """What a trace and a reader need in order to name an address."""

    symbol: str
    start: int
    end: int
    file: str
    lines: list = field(default_factory=list)


@record()
class Unit:
    """One compiled module: its sections, its symbols, and what depends on others."""

    name: str
    text: bytes = b""
    rodata: bytes = b""
    data: bytes = b""
    symbols: list = field(default_factory=list)
    relocations: list = field(default_factory=list)
    functions: list = field(default_factory=list)


class UnitBuilder:
    """Emit into one unit, then `finalise` it into a `Unit`."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.buffers = {section: Assembler() for section in SECTIONS}
        #: Label -> (symbol name, kind, size). Every other label stays unit-local.
        self.exports: dict[str, tuple[str, str, int]] = {}
        self.functions: list[FunctionRecord] = []
        #: The source file of this module, as the line table and a trace name it.
        self.file = ""

    # -- buffers -------------------------------------------------------------
    @property
    def text(self) -> Assembler:
        return self.buffers[TEXT]

    @property
    def rodata(self) -> Assembler:
        return self.buffers[RODATA]

    @property
    def data(self) -> Assembler:
        return self.buffers[DATA]

    def export(self, label: str, name: str, kind: str = "func", size: int = 0) -> None:
        """Give a label a global name; every other label stays local to this unit."""
        self.exports[label] = (name, kind, size)

    def local_name(self, section: str, label: str) -> str:
        return f"{self.name}.{section}.{label}"

    def locate(self, label: str) -> str | None:
        """The section a label is defined in, or None when another unit defines it."""
        for section in SECTIONS:
            if label in self.buffers[section].labels:
                return section
        return None

    def symbol_for(self, label: str) -> str:
        """The name `label` has at link time.

        Three cases, and the difference between them is the whole of link-time
        hygiene: an exported label keeps its global name; a label this unit defines
        privately is qualified with the unit and the section it lives in, so no other
        unit can reach it by accident; and a label this unit does not define at all is
        already the global name of something another unit exports.
        """
        exported = self.exports.get(label)
        if exported is not None:
            return exported[0]
        section = self.locate(label)
        if section is None:
            return label
        return self.local_name(section, label)

    def function(self, symbol: str, start: int) -> None:
        """Open a function record; `close_function` fills in where it ends."""
        self.functions.append(FunctionRecord(symbol, start, start, self.file, []))

    def close_function(self, end: int) -> None:
        self.functions[-1].end = end

    def line(self, offset: int, line: int) -> None:
        """Record that the code at `offset` came from `line`, once per line."""
        entries = self.functions[-1].lines if self.functions else []
        if not entries or entries[-1].line != line:
            entries.append(Line(offset, line))

    # -- finalisation --------------------------------------------------------
    def finalise(self) -> Unit:
        symbols: list[Symbol] = []
        for section in SECTIONS:
            buffer = self.buffers[section]
            for label, offset in buffer.labels.items():
                exported = self.exports.get(label)
                if exported is not None:
                    name, kind, size = exported
                    symbols.append(Symbol(name, section, offset, kind, size, local=False))
                else:
                    # A private label is a name for an address: a block label is not a
                    # function and not a variable, and calling it an object would make
                    # a debugger look for storage that does not exist.
                    symbols.append(Symbol(
                        self.local_name(section, label), section, offset,
                        kind="notype" if section == TEXT else "object", local=True))
        relocations: list[Relocation] = []
        for section in SECTIONS:
            buffer = self.buffers[section]
            for label, at, width in buffer.fixups:
                if width == 4 and label in buffer.labels:
                    continue  # resolved inside the buffer; never a relocation
                relocations.append(Relocation(
                    section, at, self.symbol_for(label),
                    "pc32" if width == 4 else "abs64"))
        return Unit(
            self.name,
            self.buffers[TEXT].bytes(),
            self.buffers[RODATA].bytes(),
            self.buffers[DATA].bytes(),
            symbols,
            relocations,
            list(self.functions),
        )
