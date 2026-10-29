"""The native backend: Nava to x86-64 ELF without a C compiler (PLAN.md N14a).

`image` is the whole public surface: a checked program in, the bytes of an
executable out. The pieces underneath are `lower` (AST → IR), `x86_64` (IR →
machine code) and `elf64` (code and data → a file the kernel will load); `ir` is the
thin waist that makes a second backend a backend rather than a second compiler.
"""
from __future__ import annotations

from . import elf64, ir, lower, x86, x86_64


def image(program) -> bytes:
    """The bytes of a static x86-64 Linux executable for `program`.

    Refusals are diagnostics (`E0901`), never a partially built program:
    what this backend cannot express is named, and the C backend compiles it.
    """
    return x86_64.generate(lower.lower(program))


__all__ = ["elf64", "image", "ir", "lower", "x86", "x86_64"]
