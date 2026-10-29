"""The native backend: Nava to x86-64 ELF without a C compiler (PLAN.md N14a).

`image` is the whole public surface: a checked program in, the bytes of an
executable out. The pieces underneath are `lower` (AST → IR), `x86_64` (IR →
machine code) and `elf64` (code and data → a file the kernel will load); `ir` is the
thin waist that makes a second backend a backend rather than a second compiler.
"""
from __future__ import annotations

from . import elf64, ir, linker, lower, reader, x86, x86_64


def image(program, sources: dict | None = None) -> bytes:
    """The bytes of a static x86-64 Linux executable for `program`.

    Refusals are diagnostics (`E0901`), never a partially built program: what this
    backend cannot express is named, and the C backend compiles it.

    `sources` is the module → path map the loader already keeps (`Program.sources`);
    only the file's *name* reaches the binary, never the path, because a path would
    make two builds of one tree two different files.
    """
    return link(program, sources).bytes


def link(program, sources: dict | None = None) -> linker.Image:
    """The linked image: units in, one executable out, with its sections and symbols."""
    if sources is None:
        sources = dict(getattr(program, "sources", {}) or {})
    return linker.link(x86_64.units(lower.lower(program), sources), sources=sources)


__all__ = ["elf64", "image", "ir", "link", "linker", "lower", "reader", "x86", "x86_64"]
