"""An ELF64 executable, written by hand (PLAN.md N14b).

The output is a static Linux x86-64 executable: an ELF header, program headers, the
sections the linker laid out, a symbol table, a string table, and section headers. No
interpreter, no dynamic linking, no section the loader does not need — `file(1)` says
"ELF 64-bit LSB executable, x86-64, statically linked, not stripped", and the last two
words are both intentions.

Three rules make the file readable by the tools that already exist, and each of them
was a bug before it was a rule:

* **A symbol table starts with a null symbol.** Twenty-four zero bytes first, and the
  index of the first global symbol is the count of the local ones plus one. Without it
  `readelf -s` prints a table while `nm` reports "no symbols", because the tools skip
  entry zero and stop believing the rest.
* **A loadable section's file offset is its address minus `BASE`.** The mapping is the
  identity, so every address in the file is an address the loader will use, and a
  reader can compute one from the other without a segment table.
* **Section flags and segment flags are different bits.** `SHF_WRITE` is 1 and `PF_X`
  is 1: copying them across produces a writable text segment or an executable data
  segment, which is how the first version of this file managed to segfault on a
  two-instruction program.

The `layout` this writes is the one the linker decides: it passes sections with their
addresses already assigned, and this module answers the bytes and the offsets.
"""
from __future__ import annotations

import struct

from ..records import field, record

#: Where the first segment is loaded. A non-PIE executable is loaded where its
#: program headers say, and `0x400000` is the traditional place for one.
BASE = 0x00400000
#: The page size the segments are aligned to. Not read from the system: the kernel is
#: told this number in the program headers, and every Linux x86-64 kernel accepts it.
PAGE = 0x1000

ELF_HEADER_SIZE = 64
PROGRAM_HEADER_SIZE = 56
SECTION_HEADER_SIZE = 64
SYMBOL_SIZE = 24

ET_EXEC = 2
EM_X86_64 = 62
EV_CURRENT = 1
#: `e_ident`: magic, 64-bit, little endian, current version, System V ABI.
IDENT = b"\x7fELF" + bytes([2, 1, 1, 0]) + bytes(8)

PT_LOAD = 1
PF_X, PF_W, PF_R = 1, 2, 4

SHT_NULL = 0
SHT_PROGBITS = 1
SHT_SYMTAB = 2
SHT_STRTAB = 3
SHT_NOBITS = 8

SHF_WRITE = 0x1
SHF_ALLOC = 0x2
SHF_EXECINSTR = 0x4

STB_LOCAL = 0
STB_GLOBAL = 1
STT_NOTYPE = 0
STT_OBJECT = 1
STT_FUNC = 2
STT_SECTION = 3

#: The ELF symbol type of each kind of symbol a unit defines.
SYMBOL_TYPES = {"notype": STT_NOTYPE, "object": STT_OBJECT, "func": STT_FUNC,
                "section": STT_SECTION}
SYMBOL_BINDINGS = {"local": STB_LOCAL, "global": STB_GLOBAL}


def align(value: int, to: int) -> int:
    """Round `value` up to a multiple of `to`."""
    return (value + to - 1) // to * to


@record()
class Section:
    """One section: its name, its flags, where it lives, and its bytes."""

    name: str
    flags: int = SHF_ALLOC
    address: int = 0
    data: bytes = b""
    kind: str = "progbits"
    alignment: int = 16
    link: int = 0
    info: int = 0
    entsize: int = 0
    #: The file offset, filled in by `write`; `None` means it needs a segment.
    offset: int = -1

    @property
    def type(self) -> int:
        return SHT_NOBITS if self.kind == "nobits" else SHT_PROGBITS

    @property
    def executable(self) -> bool:
        return bool(self.flags & SHF_EXECINSTR)

    @property
    def writable(self) -> bool:
        return bool(self.flags & SHF_WRITE)


@record()
class Symbol:
    """One entry of `.symtab`, with its address already resolved."""

    name: str
    value: int
    size: int = 0
    kind: str = "func"
    binding: str = "global"
    section: str = ""

    @property
    def info(self) -> int:
        return (SYMBOL_BINDINGS[self.binding] << 4) | SYMBOL_TYPES[self.kind]


def segments(sections) -> list[tuple[int, int, int, int, int, int, int]]:
    """Group loadable sections into `PT_LOAD`s.

    Returns `(flags, offset, address, filesz, memsz, align)` tuples. Adjacent sections
    with the same permissions share a segment; the first segment starts at file offset
    zero so that it also covers the headers, and every later one starts at its own
    address minus `BASE`, which is the identity mapping rule stated above.
    """
    groups: list[list] = []
    for section in sections:
        if not section.flags & SHF_ALLOC:
            continue
        flags = PF_R | (PF_W if section.writable else 0) | (
            PF_X if section.executable else 0)
        if groups and groups[-1][0] == flags:
            groups[-1][1].append(section)
        else:
            groups.append([flags, [section]])
    answer = []
    for flags, members in groups:
        first, last = members[0], members[-1]
        address = first.address
        offset = 0 if address == BASE else address - BASE
        end = last.address + len(last.data)
        answer.append((flags, offset, address, end - address, end - address, PAGE))
    return answer


def _string_table(strings: list[str]) -> tuple[bytes, dict[str, int]]:
    """A `\0`-separated table, and where each string starts in it."""
    data = bytearray(b"\0")
    offsets = {"": 0}
    for text in strings:
        if text in offsets:
            continue
        offsets[text] = len(data)
        data += text.encode("utf-8") + b"\0"
    return bytes(data), offsets


def write(entry: int, sections, symbols) -> bytes:
    """The bytes of an executable: headers, the given sections, and a symbol table."""
    loadable = [section for section in sections if section.flags & SHF_ALLOC]
    if not loadable:
        raise ValueError("an executable needs at least one loadable section")
    segment_list = segments(loadable)
    headers_size = ELF_HEADER_SIZE + PROGRAM_HEADER_SIZE * len(segment_list)
    if loadable[0].address < BASE + headers_size:
        raise ValueError("the first section would overlap the ELF headers")

    # Section headers: the null entry, the sections, then the tables.
    names = [section.name for section in sections] + [".symtab", ".strtab", ".shstrtab"]
    symtab_index = len(sections) + 1
    strtab_index = symtab_index + 1
    shstrtab_index = strtab_index + 1

    symbol_names = [symbol.name for symbol in symbols]
    strtab, strtab_offsets = _string_table(symbol_names)
    # A null symbol first: every tool skips index zero and stops believing a table
    # that does not have one.
    symtab = bytearray(SYMBOL_SIZE)
    section_index = {}
    for index, section in enumerate(sections, start=1):
        section_index[section.name] = index
    file_symbols = sorted(symbols, key=lambda symbol: symbol.binding != "local")
    first_global = 1 + sum(1 for symbol in file_symbols if symbol.binding == "local")
    for symbol in file_symbols:
        symtab += struct.pack(
            "<IBBHQQ",
            strtab_offsets[symbol.name],
            symbol.info,
            0,                                   # st_other
            section_index.get(symbol.section, 0),
            symbol.value,
            symbol.size,
        )
    shstrtab, shstrtab_offsets = _string_table(names)

    # Lay the file out: the sections where their addresses say, then the tables, then
    # the section headers. Padding is explicit so two builds are the same bytes.
    body = bytearray()
    offsets: list[int] = []
    for section in sections:
        offsets.append(section.address - BASE)
    table_start = max([section.address - BASE + len(section.data) for section in loadable])
    table_start = max(table_start, headers_size)
    symtab_offset = align(table_start, 8)
    strtab_offset = symtab_offset + len(symtab)
    shstrtab_offset = strtab_offset + len(strtab)
    shoff = align(shstrtab_offset + len(shstrtab), 8)
    total = shoff + SECTION_HEADER_SIZE * (len(sections) + 4)
    body = bytearray(total)
    body[0:headers_size] = bytes(headers_size)

    for section, offset in zip(sections, offsets):
        if section.kind == "nobits":
            continue
        body[offset:offset + len(section.data)] = section.data
    body[symtab_offset:symtab_offset + len(symtab)] = symtab
    body[strtab_offset:strtab_offset + len(strtab)] = strtab
    body[shstrtab_offset:shstrtab_offset + len(shstrtab)] = shstrtab

    # -- the ELF header -------------------------------------------------------
    struct.pack_into(
        "<16sHHIQQQIHHHHHH",
        body, 0,
        IDENT,
        ET_EXEC,
        EM_X86_64,
        EV_CURRENT,
        entry,
        ELF_HEADER_SIZE,
        shoff,
        0,                                       # e_flags
        ELF_HEADER_SIZE,
        PROGRAM_HEADER_SIZE,
        len(segment_list),
        SECTION_HEADER_SIZE,
        len(sections) + 4,
        shstrtab_index,
    )

    # -- program headers ------------------------------------------------------
    at = ELF_HEADER_SIZE
    for flags, offset, address, filesz, memsz, alignment in segment_list:
        struct.pack_into("<IIQQQQQQ", body, at, PT_LOAD, flags, offset, address,
                         address, filesz, memsz, alignment)
        at += PROGRAM_HEADER_SIZE

    # -- section headers ------------------------------------------------------
    headers = [struct.pack("<IIQQQQIIQQ", 0, SHT_NULL, 0, 0, 0, 0, 0, 0, 0, 0)]
    for section, offset in zip(sections, offsets):
        headers.append(struct.pack(
            "<IIQQQQIIQQ",
            shstrtab_offsets[section.name],
            section.type,
            section.flags,
            section.address,
            offset,
            len(section.data),
            section.link,
            section.info,
            section.alignment,
            section.entsize,
        ))
    headers.append(struct.pack(
        "<IIQQQQIIQQ",
        shstrtab_offsets[".symtab"], SHT_SYMTAB, 0, 0, symtab_offset, len(symtab),
        strtab_index, first_global, 8, SYMBOL_SIZE))
    headers.append(struct.pack(
        "<IIQQQQIIQQ",
        shstrtab_offsets[".strtab"], SHT_STRTAB, 0, 0, strtab_offset, len(strtab),
        0, 0, 1, 0))
    headers.append(struct.pack(
        "<IIQQQQIIQQ",
        shstrtab_offsets[".shstrtab"], SHT_STRTAB, 0, 0, shstrtab_offset,
        len(shstrtab), 0, 0, 1, 0))
    at = shoff
    for header in headers:
        body[at:at + SECTION_HEADER_SIZE] = header
        at += SECTION_HEADER_SIZE
    return bytes(body)
