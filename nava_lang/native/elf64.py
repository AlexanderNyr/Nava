"""Writing a static x86-64 ELF executable (PLAN.md N14a).

There is no linker in the picture: the code the encoder produced is already
positioned, so the file is a header, two program headers, the text and the data.
Two `PT_LOAD` segments, not one, because they have different permissions — text is
readable and executable, data is readable and writable, and merging them would make
the whole image writable and executable for no reason. The kernel maps the file
directly: `p_offset ≡ p_vaddr (mod align)` holds for both segments by construction,
so no relocation happens at load time and the program is position-dependent, which
is what `ET_EXEC` says.

The layout, which the code generator has to agree with:

```
0x0000   ELF header (64) + two program headers (2 × 56) = 176 bytes
0x00B0   .text
0x1000 * n  .data   (page aligned, mapped at DATA_VADDR)
```

`TEXT_OFFSET` is therefore the number the code generator adds to a label's offset in
the text buffer to get an absolute address, and that is why it is exported rather
than hidden here.
"""
from __future__ import annotations

import struct

#: Where the text segment is mapped. Non-zero and page aligned, so a load address of
#: 0 — which would make a null pointer indistinguishable from the entry point — cannot
#: happen.
TEXT_VADDR = 0x00400000
#: Where the writable segment is mapped, far enough that the text segment cannot grow
#: into it.
DATA_VADDR = 0x00500000
PAGE = 0x1000

ELF_HEADER_SIZE = 64
PROGRAM_HEADER_SIZE = 56
PROGRAM_HEADERS = 2
#: The first byte of code in the text segment.
TEXT_OFFSET = ELF_HEADER_SIZE + PROGRAM_HEADERS * PROGRAM_HEADER_SIZE

EM_X86_64 = 62
ET_EXEC = 2
PT_LOAD = 1
PF_X, PF_W, PF_R = 1, 2, 4


def text_entry(offset: int = 0) -> int:
    """The absolute address of a byte at `offset` in the text buffer."""
    return TEXT_VADDR + TEXT_OFFSET + offset


def data_address(offset: int) -> int:
    """The absolute address of a byte at `offset` in the data buffer."""
    return DATA_VADDR + offset


def _align(value: int, to: int = PAGE) -> int:
    return (value + to - 1) // to * to


def build(code: bytes, data: bytes, entry: int) -> bytes:
    """Assemble the file: headers, text, padding to a page, data."""
    data_offset = _align(TEXT_OFFSET + len(code))
    text_filesz = TEXT_OFFSET + len(code)

    header = bytearray()
    header += b"\x7fELF"
    header += bytes([2, 1, 1, 0])          # 64-bit, little endian, version 1, System V
    header += bytes(8)                     # padding
    header += struct.pack(
        "<HHIQQQIHHHHHH",
        ET_EXEC,                           # e_type
        EM_X86_64,                         # e_machine
        1,                                 # e_version
        entry,                             # e_entry
        ELF_HEADER_SIZE,                   # e_phoff
        0,                                 # e_shoff — no sections; the kernel reads phdrs
        0,                                 # e_flags
        ELF_HEADER_SIZE,                   # e_ehsize
        PROGRAM_HEADER_SIZE,               # e_phentsize
        PROGRAM_HEADERS,                   # e_phnum
        64,                                # e_shentsize
        0,                                 # e_shnum
        0,                                 # e_shstrndx
    )
    assert len(header) == ELF_HEADER_SIZE

    headers = bytearray()
    # Segment 1: headers and code, readable and executable.
    headers += struct.pack(
        "<IIQQQQQQ",
        PT_LOAD, PF_R | PF_X,              # p_type, p_flags
        0, TEXT_VADDR, TEXT_VADDR,         # p_offset, p_vaddr, p_paddr
        text_filesz, text_filesz,          # p_filesz, p_memsz
        PAGE,                              # p_align
    )
    # Segment 2: data, readable and writable, mapped at a page boundary. A program
    # with no data still gets the header; its size is zero.
    headers += struct.pack(
        "<IIQQQQQQ",
        PT_LOAD, PF_R | PF_W,
        data_offset, DATA_VADDR, DATA_VADDR,
        len(data), len(data),
        PAGE,
    )
    assert len(headers) == PROGRAM_HEADERS * PROGRAM_HEADER_SIZE

    image = bytearray()
    image += header
    image += headers
    image += code
    image += bytes(data_offset - text_filesz)
    image += data
    return bytes(image)
