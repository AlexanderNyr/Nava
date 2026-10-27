"""Helpers for tests that exercise the file capabilities (PLAN.md phase N6).

A file test needs three things at once: a program with `read_file`/`write_file`,
directories the program may or may not use, and a working directory that is the
same one the grants are resolved against.  `run` builds all of that in one
temporary tree, compiles the program with the requested grants and executes it
with that directory as its working directory.
"""
from __future__ import annotations

import contextlib
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nava_lang.cli import compile_source  # noqa: E402

CC = shutil.which("cc")


def emit(source: str, capabilities=None, path=None) -> str:
    """The generated C for `source` with the given grants baked in.

    `path` is where the source would live, so `import` statements resolve
    against its directory; it is optional for a self-contained program.
    """
    return compile_source(source, path=path, capabilities=capabilities).code


def build(code: str, directory: pathlib.Path, *, sanitizers: bool = False) -> pathlib.Path:
    binary = directory / "program"
    c_file = directory / "program.c"
    c_file.write_text(code, encoding="utf-8")
    flags = ["-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-g"] if sanitizers else []
    result = subprocess.run([CC, "-std=c11", "-pedantic-errors", "-O2", *flags,
                             str(c_file), "-o", str(binary)],
                            capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise AssertionError(f"the generated C did not compile:\n{result.stderr}")
    return binary


def run(source: str, *, files=None, stdin: str = "", capabilities=None,
        timeout: int = 60, sanitizers: bool = False):
    """Compile and run `source` in a directory that holds `files`."""
    with tempfile.TemporaryDirectory() as temp:
        root = pathlib.Path(temp)
        work = root / "work"
        work.mkdir()
        for name, data in (files or {}).items():
            path = work / name
            path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(data, tuple) and data[0] == "link":
                path.symlink_to(data[1])
            else:
                path.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)
        entry = root / "main.nv"
        entry.write_text(source, encoding="utf-8")
        binary = build(emit(source, capabilities=capabilities, path=entry), root,
                       sanitizers=sanitizers)
        return subprocess.run([str(binary)], input=stdin, capture_output=True, text=True,
                              timeout=timeout, cwd=work)


@contextlib.contextmanager
def workspace(files=None):
    """A temporary directory populated with `files`, for tests that need paths."""
    with tempfile.TemporaryDirectory() as temp:
        root = pathlib.Path(temp)
        for name, data in (files or {}).items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)
        yield root
