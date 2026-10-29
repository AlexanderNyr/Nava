"""Units in, one executable out: layout, symbols, relocations, line table (N14b).

Three jobs, in this order, because each one needs the previous one's answer:

1. **Layout.** Every unit's text, then its constant data, then its writable data, each
   aligned; a section starts on a page where the permissions change. The text starts
   after the ELF and program headers, and the whole file mirrors addresses, so a
   loadable section's file offset is its address minus `BASE`.
2. **Symbols.** A unit's symbols become addresses once the layout is known, and the
   linker adds one of its own: `nv_trace_table`, the first byte of the line table,
   which the runtime reads when `NAVA_TRACE` is set.
3. **Relocations.** A `pc32` field becomes `S - (P + 4)` and an `abs64` field becomes
   `S`. A reference to a symbol nobody defines is a bug in the backend rather than
   something a program can cause, so it raises `LinkError` naming every missing symbol
   — never a silently zeroed field, which would be a jump into nothing.

The line table is where a trace and a reader meet, so its format is written down here
rather than inferred from the code that writes it:

```
u64 magic 0x4E415641 ("NAVA"), u64 count
count × 48 bytes: start, end, name pointer, file pointer, lines pointer, line count
payloads, in the order the records were written: each function's file and name as
          length-prefixed strings, then its line array as (address, line) 64-bit pairs
```

The pointers a record holds are **offsets from the first byte of this section**, which
is the address the symbol `nv_trace_table` names; every address in a line array is an
absolute text address, because that is what it is compared against. Offsets rather than
addresses in the record keep the payload independent of where the linker puts the
section, so the size pass and the filling pass cannot disagree about them.
"""
from __future__ import annotations

import struct

from ..records import record
from . import elf64
from .unit import DATA, RODATA, TEXT

#: "NAVA" little endian, at the first byte of the line table.
MAGIC = 0x4E415641
#: `_start`, the one symbol a program cannot do without.
ENTRY_SYMBOL = "_start"
#: The line table's first byte, which is also the symbol the runtime looks up.
TRACE_SYMBOL = "nv_trace_table"
#: Every output section, in layout order: unit section, name, permissions.
OUTPUT = ((TEXT, ".text", elf64.SHF_ALLOC | elf64.SHF_EXECINSTR),
          (RODATA, ".rodata", elf64.SHF_ALLOC),
          (DATA, ".data", elf64.SHF_ALLOC | elf64.SHF_WRITE))
LINES_SECTION = ".nava.lines"
TEXT_ALIGN = 16
DATA_ALIGN = 8


class LinkError(Exception):
    """A unit referenced a symbol nothing defines: a backend bug, not a program."""

    def __init__(self, missing: list[str]) -> None:
        self.missing = sorted(set(missing))
        super().__init__("undefined symbol(s): " + ", ".join(self.missing))


@record()
class Image:
    """A linked program: entry point, sections, symbols and the bytes."""

    entry: int
    sections: list
    symbols: list
    bytes: bytes

    def address(self, name: str) -> int:
        for symbol in self.symbols:
            if symbol.name == name:
                return symbol.value
        raise KeyError(name)

    def section(self, name: str):
        for section in self.sections:
            if section.name == name:
                return section
        raise KeyError(name)


def _align(value: int, to: int) -> int:
    return (value + to - 1) // to * to


def _length_prefixed(text: str) -> bytes:
    encoded = text.encode("utf-8")
    return struct.pack("<Q", len(encoded)) + encoded


def build_lines(units, locate=None) -> bytes:
    """The line table: the whole section, or only its size when `locate` is None.

    `locate(unit, offset)` answers the absolute address of an offset in that unit's
    text. Sizes do not depend on addresses, which is why the size pass and the fill
    pass cannot disagree — the linker calls this once for each.
    """
    functions = [(unit, function) for unit in units for function in unit.functions]
    layout = []
    cursor = 16 + 48 * len(functions)
    for unit, function in functions:
        file_bytes = _length_prefixed(function.file)
        name_bytes = _length_prefixed(function.symbol)
        layout.append((unit, function, file_bytes, name_bytes, cursor))
        cursor += len(file_bytes) + len(name_bytes) + 16 * len(function.lines)
    if locate is None:
        return bytearray(cursor)

    directory = bytearray()
    payload = bytearray()
    for unit, function, file_bytes, name_bytes, at in layout:
        lines_at = at + len(file_bytes) + len(name_bytes)
        directory += struct.pack(
            "<QQQQQQ",
            locate(unit, function.start),
            locate(unit, function.end),
            at + len(file_bytes),          # the name follows the file string
            at,                            # the file string comes first
            lines_at,
            len(function.lines),
        )
        payload += file_bytes + name_bytes
        for entry in function.lines:
            payload += struct.pack("<QQ", locate(unit, entry.offset), entry.line)
    return bytearray(struct.pack("<QQ", MAGIC, len(functions))) + directory + payload


def link(units, entry_symbol: str = ENTRY_SYMBOL, sources: dict | None = None) -> Image:
    """Lay the units out, resolve every reference and write the file.

    `sources` is accepted for symmetry with the facade and is not used here: the file
    names are already in each unit's line table, decided by the generator that knows
    which module each function came from.
    """
    names = [unit.name for unit in units]
    if len(set(names)) != len(names):
        raise LinkError([f"two units are named {name!r}" for name in names])

    header_bytes = elf64.ELF_HEADER_SIZE + 3 * elf64.PROGRAM_HEADER_SIZE
    text_address = elf64.BASE + header_bytes

    offsets = {TEXT: {}, RODATA: {}, DATA: {}}
    sizes = {TEXT: {}, RODATA: {}, DATA: {}}
    for key in (TEXT, RODATA, DATA):
        cursor = 0
        for unit in units:
            offsets[key][unit.name] = cursor
            body = getattr(unit, key)
            sizes[key][unit.name] = len(body)
            cursor = _align(cursor + len(body), TEXT_ALIGN if key == TEXT else DATA_ALIGN)
        sizes[key]["total"] = cursor

    rodata_address = _align(text_address + sizes[TEXT]["total"], elf64.PAGE)
    lines_address = rodata_address + sizes[RODATA]["total"]
    lines = build_lines(units)
    data_address = _align(lines_address + len(lines), elf64.PAGE)
    base = {TEXT: text_address, RODATA: rodata_address, DATA: data_address}

    def locate(unit, offset: int) -> int:
        return base[TEXT] + offsets[TEXT][unit.name] + offset

    lines = build_lines(units, locate)
    if len(lines) != _align(lines_address + len(lines), 1) - lines_address:
        raise AssertionError("the line table changed size between the two passes")

    addresses: dict[str, int] = {}
    for unit in units:
        for symbol in unit.symbols:
            addresses[symbol.name] = (base[symbol.section]
                                      + offsets[symbol.section][unit.name]
                                      + symbol.offset)
    addresses[TRACE_SYMBOL] = lines_address

    entry = addresses.get(entry_symbol)
    if entry is None:
        raise LinkError([entry_symbol])

    bodies = {key: bytearray(sizes[key]["total"]) for key in (TEXT, RODATA, DATA)}
    for key in (TEXT, RODATA, DATA):
        for unit in units:
            at = offsets[key][unit.name]
            bodies[key][at:at + sizes[key][unit.name]] = getattr(unit, key)

    missing: list[str] = []
    for unit in units:
        for relocation in unit.relocations:
            target = addresses.get(relocation.symbol)
            if target is None:
                missing.append(relocation.symbol)
                continue
            body = bodies[relocation.section]
            at = offsets[relocation.section][unit.name] + relocation.offset
            where = base[relocation.section] + at
            if relocation.kind == "pc32":
                value, width = target + relocation.addend - (where + 4), 4
            elif relocation.kind == "abs64":
                value, width = target + relocation.addend, 8
            else:  # pragma: no cover - a unit emits only the two kinds
                raise LinkError([f"relocation kind {relocation.kind!r}"])
            body[at:at + width] = (value & ((1 << (8 * width)) - 1)).to_bytes(
                width, "little")
    if missing:
        raise LinkError(missing)

    sections = [elf64.Section(name, flags=flags, address=base[key], data=bytes(bodies[key]))
                for key, name, flags in OUTPUT]
    sections.insert(2, elf64.Section(LINES_SECTION, flags=elf64.SHF_ALLOC,
                                     address=lines_address, data=bytes(lines)))

    symbols = []
    for unit in units:
        for symbol in unit.symbols:
            symbols.append(elf64.Symbol(
                symbol.name, addresses[symbol.name], symbol.size, symbol.kind,
                "local" if symbol.local else "global",
                {"text": ".text", "rodata": ".rodata", "data": ".data"}[symbol.section]))
    symbols.append(elf64.Symbol(TRACE_SYMBOL, lines_address, len(lines), "object",
                                "global", LINES_SECTION))
    return Image(entry, sections, symbols, elf64.write(entry, sections, symbols))
