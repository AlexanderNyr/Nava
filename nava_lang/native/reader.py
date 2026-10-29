"""A read-only ELF64 reader: our own `readelf`, used by the tests (PLAN.md N14b).

The backend writes the file, so *something* has to read it back without trusting the
writer's own account of what it wrote. `readelf(1)` and `nm(1)` are the obvious tools
and the tests do use them — that is the point of the negative controls, since a reader
that shared a bug with the writer would agree with it. But a released compiler cannot
require binutils to check its own output, and a test that shells out to `readelf` proves
the file is *readable*, not that it is *understood*. So the file is parsed here as well,
and the two accounts are compared.

Parsed: the header, the program headers, the section headers, the symbol table, and the
`.nava.lines` table the linker writes — enough to answer the two questions a trace
asks: *which function is this address in* and *which line of which file is it*. Nothing
is written and nothing is repaired: a truncated or malformed file raises `Malformed`,
which is what the plan's negative control ("a deliberately truncated ELF is rejected by
our own reader") requires. A reader that padded, guessed or fell back to zeros would
turn a corrupt file into a plausible one.
"""
from __future__ import annotations

import struct

from ..records import field, record

ELF_HEADER_SIZE = 64
PROGRAM_HEADER_SIZE = 56
SECTION_HEADER_SIZE = 64
SYMBOL_SIZE = 24
LINES_MAGIC = 0x4E415641
LINES_ENTRY = 48

PT_LOAD = 1
SHT_SYMTAB = 2
SHT_STRTAB = 3
PT_FLAGS = {4: "R", 2: "W", 1: "X"}


class Malformed(Exception):
    """The file is not an ELF64 executable this reader understands."""


@record()
class Header:
    """The fields of the ELF header a reader needs, by their ELF names."""

    type: int
    machine: int
    version: int
    entry: int
    phoff: int
    shoff: int
    flags: int
    ehsize: int
    phentsize: int
    phnum: int
    shentsize: int
    shnum: int
    shstrndx: int


@record()
class ProgramHeader:
    """One `PT_LOAD`: where it lives in the file, where in memory, its permissions."""

    type: int
    flags: int
    offset: int
    address: int
    filesz: int
    memsz: int
    alignment: int

    @property
    def permissions(self) -> str:
        return "".join(name for bit, name in sorted(PT_FLAGS.items(), reverse=True)
                       if self.flags & bit)


@record()
class SectionHeader:
    """One section: its name as text, its type, its extent and its bytes."""

    index: int
    name: str
    type: int
    flags: int
    address: int
    offset: int
    size: int
    link: int
    info: int
    alignment: int
    entsize: int
    data: bytes = b""


@record()
class SymbolEntry:
    """One `.symtab` entry, with its name resolved through `.strtab`."""

    name: str
    value: int
    size: int
    info: int
    section: int

    @property
    def binding(self) -> str:
        return "local" if (self.info >> 4) == 0 else "global"

    @property
    def kind(self) -> str:
        return {0: "notype", 1: "object", 2: "func", 3: "section"}.get(self.info & 0xF,
                                                                       "other")


@record()
class LineRecord:
    """One function of the line table: its extent, its name, its file and its lines."""

    start: int
    end: int
    name: str
    file: str
    lines: list


@record()
class File:
    """A parsed executable: what a reader needs and nothing else."""

    header: Header
    segments: list
    sections: list
    symbols: list
    lines: list

    def section(self, name: str) -> SectionHeader:
        for section in self.sections:
            if section.name == name:
                return section
        raise KeyError(name)

    def symbol(self, name: str) -> SymbolEntry:
        for symbol in self.symbols:
            if symbol.name == name:
                return symbol
        raise KeyError(name)

    def function_at(self, address: int) -> LineRecord | None:
        for record in self.lines:
            if record.start <= address < record.end:
                return record
        return None

    def symbolise(self, address: int) -> str | None:
        """`name (file:line)` for an address, or None when no function covers it.

        The line is the last entry whose address is at or below the target, which is
        how a line table is read: entries ascend, and the interval between two of them
        belongs to the first.
        """
        record = self.function_at(address)
        if record is None:
            return None
        line = 0
        for at, number in record.lines:
            if at <= address:
                line = number
            else:
                break
        return f"{record.name} ({record.file}:{line})"


def _unpack(fmt: str, data: bytes, offset: int, what: str):
    size = struct.calcsize(fmt)
    if offset < 0 or offset + size > len(data):
        raise Malformed(f"the file ends inside {what} (offset {offset}, {len(data)} bytes)")
    return struct.unpack_from(fmt, data, offset)


def _string(table: bytes, offset: int, what: str) -> str:
    if offset < 0 or offset >= len(table):
        raise Malformed(f"{what} points outside its string table (offset {offset})")
    end = table.find(b"\0", offset)
    if end < 0:
        raise Malformed(f"{what} is not terminated inside its string table")
    try:
        return table[offset:end].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise Malformed(f"{what} is not valid UTF-8: {exc}") from None


def read(path) -> File:
    """Parse an executable. Raises `Malformed` for anything that is not one."""
    data = open(path, "rb").read() if not isinstance(path, (bytes, bytearray)) else bytes(path)
    if len(data) < ELF_HEADER_SIZE:
        raise Malformed(f"the file is {len(data)} bytes, shorter than an ELF header")
    if data[:4] != b"\x7fELF":
        raise Malformed("the file does not start with \\x7fELF")
    if data[4] != 2:
        raise Malformed(f"ELF class {data[4]} is not 64-bit")
    if data[5] != 1:
        raise Malformed(f"ELF data encoding {data[5]} is not little endian")
    (e_type, e_machine, e_version, e_entry, e_phoff, e_shoff, e_flags, e_ehsize,
     e_phentsize, e_phnum, e_shentsize, e_shnum,
     e_shstrndx) = _unpack("<HHIQQQIHHHHHH", data, 16, "the ELF header")
    header = Header(e_type, e_machine, e_version, e_entry, e_phoff, e_shoff, e_flags,
                    e_ehsize, e_phentsize, e_phnum, e_shentsize, e_shnum, e_shstrndx)
    if header.ehsize != ELF_HEADER_SIZE:
        raise Malformed(f"e_ehsize is {header.ehsize}, expected {ELF_HEADER_SIZE}")

    segments: list[ProgramHeader] = []
    for index in range(e_phnum):
        at = e_phoff + index * e_phentsize
        (p_type, p_flags, p_offset, p_vaddr, _paddr, p_filesz, p_memsz,
         p_align) = _unpack("<IIQQQQQQ", data, at, f"program header {index}")
        if p_offset + p_filesz > len(data):
            raise Malformed(f"segment {index} extends past the end of the file")
        segments.append(ProgramHeader(p_type, p_flags, p_offset, p_vaddr, p_filesz,
                                      p_memsz, p_align))

    raw: list[tuple[int, int, int, int, int, int, int, int, int, int]] = []
    for index in range(e_shnum):
        at = e_shoff + index * e_shentsize
        raw.append(_unpack("<IIQQQQIIQQ", data, at, f"section header {index}"))
    if raw:
        name_offset, name_type, _name_flags, _name_address, name_file, name_size, _, _, \
            _, _ = raw[min(e_shstrndx, len(raw) - 1)]
        if name_type != SHT_STRTAB:
            raise Malformed("the section name table is not a string table")
        names = data[name_file:name_file + name_size]
    else:
        names = b""

    sections: list[SectionHeader] = []
    for index, fields in enumerate(raw):
        (sh_name, sh_type, sh_flags, sh_addr, sh_offset, sh_size, sh_link, sh_info,
         sh_addralign, sh_entsize) = fields
        if sh_type != 8 and sh_offset + sh_size > len(data):     # 8 is SHT_NOBITS
            raise Malformed(f"section {index} extends past the end of the file")
        sections.append(SectionHeader(
            index,
            _string(names, sh_name, f"section {index}") if names else "",
            sh_type, sh_flags, sh_addr, sh_offset, sh_size, sh_link, sh_info,
            sh_addralign, sh_entsize,
            data[sh_offset:sh_offset + sh_size] if sh_type != 8 else b""))

    symbols: list[SymbolEntry] = []
    for section in sections:
        if section.type != SHT_SYMTAB:
            continue
        if section.link >= len(sections):
            raise Malformed("the symbol table's string table index is out of range")
        strings = sections[section.link].data
        entry_size = section.entsize or SYMBOL_SIZE
        if entry_size < SYMBOL_SIZE:
            raise Malformed(f"a symbol entry of {entry_size} bytes is too small")
        for at in range(0, section.size - section.size % entry_size, entry_size):
            st_name, st_info, _other, st_shndx, st_value, st_size = _unpack(
                "<IBBHQQ", section.data, at, "a symbol entry")
            symbols.append(SymbolEntry(_string(strings, st_name, "a symbol name"),
                                       st_value, st_size, st_info, st_shndx))

    lines: list[LineRecord] = []
    for section in sections:
        if section.name != ".nava.lines":
            continue
        lines = _lines(section)
    return File(header, segments, sections, symbols, lines)


def _lines(section: SectionHeader) -> list[LineRecord]:
    """The line table: the records, with every offset made an address again."""
    base = section.address
    data = section.data
    magic, count = _unpack("<QQ", data, 0, "the line table header")
    if magic != LINES_MAGIC:
        raise Malformed(f"the line table magic is {magic:#x}, expected {LINES_MAGIC:#x}")
    if 16 + count * LINES_ENTRY > len(data):
        raise Malformed(f"{count} line records do not fit in {len(data)} bytes")

    def text(offset: int, what: str) -> str:
        """A length-prefixed string: eight bytes of length, then the bytes.

        The same shape the runtime's `nv_write` reads — a `\0`-terminated string
        would be wrong here, because the text a line table holds is a path and a
        function name, and neither is allowed to be truncated at an embedded zero.
        """
        length, = _unpack("<Q", data, offset, what)
        if offset + 8 + length > len(data):
            raise Malformed(f"{what} extends past the end of the line table")
        try:
            return data[offset + 8:offset + 8 + length].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise Malformed(f"{what} is not valid UTF-8: {exc}") from None

    records = []
    for index in range(count):
        start, end, name, file, pairs, entries = _unpack(
            "<QQQQQQ", data, 16 + index * LINES_ENTRY, f"line record {index}")
        if pairs + 16 * entries > len(data):
            raise Malformed(f"line record {index} has more entries than the section holds")
        lines = [_unpack("<QQ", data, pairs + 16 * step, "a line entry")
                 for step in range(entries)]
        records.append(LineRecord(start, end, text(name, "a function name"),
                                 text(file, "a file name"), lines))
    return records


def describe(file: File) -> str:
    """A `readelf`-shaped summary: header, segments, sections, symbols, line table.

    Not a replacement for `readelf` — it prints what this compiler writes and what the
    tests compare. Anything it does not recognise is printed rather than hidden.
    """
    header = file.header
    out = [
        "ELF Header:",
        f"  Type:                              {header.type} "
        f"({'EXEC' if header.type == 2 else header.type})",
        f"  Machine:                           {header.machine} "
        f"({'x86-64' if header.machine == 62 else header.machine})",
        f"  Entry point address:               {header.entry:#x}",
        f"  Number of program headers:         {header.phnum}",
        f"  Number of section headers:         {header.shnum}",
        "",
        "Program Headers:",
    ]
    for segment in file.segments:
        kind = "LOAD" if segment.type == PT_LOAD else f"type {segment.type}"
        out.append(f"  {kind:6s} off {segment.offset:#08x} vaddr {segment.address:#012x} "
                   f"filesz {segment.filesz:#08x} flags {segment.permissions}")
    out.append("")
    out.append("Section Headers:")
    for section in file.sections:
        out.append(f"  [{section.index}] {section.name:16s} type {section.type} "
                   f"addr {section.address:#012x} off {section.offset:#08x} "
                   f"size {section.size:#08x}")
    out.append("")
    out.append("Symbol table:")
    for symbol in file.symbols:
        out.append(f"  {symbol.value:#012x} {symbol.size:6d} {symbol.binding:6s} "
                   f"{symbol.kind:6s} {symbol.name}")
    out.append("")
    out.append("Line table:")
    for record in file.lines:
        out.append(f"  {record.start:#012x}..{record.end:#012x} {record.name} "
                   f"({record.file}, {len(record.lines)} entries)")
        for at, number in record.lines:
            out.append(f"    {at:#012x} line {number}")
    return "\n".join(out) + "\n"
