"""N11, part four: text that a program builds — `str_*`, `String`, and the arena.

One claim, tested from both sides: **a program can build text, and the text it
builds is a `str` like any other.** `str_concat`, `str_slice`, `str_replace`,
`str_trim`, `str_from_i64` and `str_from_f64` return an immutable `str` that can
be printed, stored in a variable, passed on and read with `bytes_len`. `String`
is the owner of bytes assembled a piece at a time; `string_push` grows it by
doubling and `string_text` hands the finished text to the arena, so a `str` never
points into a buffer that may grow underneath it.

The second claim is what the type refuses, and each refusal is one code from one
place. A `String` cannot be copied, reassigned, returned, stored in a field or a
payload, or used as a type argument (`E0428`); an element of an array or a vector
may not be a `String` (`E0426` / `E0427`), which is also how `Vec(String)` fails;
a mutating call needs a writable local or a `mut String` parameter (`E0444`); and
a `mut String` argument must be created in the calling function (`E0333`).

The third claim is the accounting. Built text is allocated in an arena — chunks
that are bump-allocated and released together at exit — because a `str` may be
stored wherever a value may go, so a region cannot be reclaimed while something
that crossed a frame boundary still points into it. Under `-DNAVA_TRACK_ALLOCS`
the arena prints the bytes it handed out and the chunks it still holds, and the
release at exit is checked by AddressSanitizer: a chunk that is never freed is a
LeakSanitizer report, not a number nobody reads.

One further test pins a defect this increment found in N11c: the vector builtins
used to evaluate their arguments twice, so `vec_push(v, bump(1))` printed `1`
twice. Every builtin now materialises its arguments once, and the test asserts
the count.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nava_lang.cgen import CGenerator  # noqa: E402
from nava_lang.checker import check  # noqa: E402
from nava_lang.cli import compile_source  # noqa: E402
from nava_lang.model import NavaError  # noqa: E402
from nava_lang.parser import parse  # noqa: E402

CC = shutil.which("cc")
#: The instrument `tests/registry.py` insists on; without it this file fails.
SANITIZER_FLAGS = ("-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-g")


def frontend(text: str) -> str:
    """The generated C of a program that has already been checked."""
    return CGenerator(check(parse(text))).generate()


def compile_and_run(text: str, tracked: bool = True,
                    sanitize: bool = False) -> subprocess.CompletedProcess:
    """Build the generated C with the test instrumentation and run it."""
    with tempfile.TemporaryDirectory() as directory:
        c_file = Path(directory) / "program.c"
        binary = Path(directory) / "program"
        c_file.write_text(frontend(text), encoding="utf-8")
        extra = ["-DNAVA_TRACK_ALLOCS"] if tracked else []
        extra += list(SANITIZER_FLAGS) if sanitize else []
        build = subprocess.run([CC, "-std=c11", "-pedantic-errors",
                                "-O1" if sanitize else "-O2", *extra,
                                str(c_file), "-o", str(binary)],
                               capture_output=True, text=True, timeout=300)
        if build.returncode != 0:
            raise AssertionError(f"the generated C did not compile:\n{build.stderr}")
        return subprocess.run([str(binary)], capture_output=True, text=True, timeout=60)


def live_arrays(stderr: str) -> int:
    """The count printed by a `-DNAVA_TRACK_ALLOCS` build."""
    for line in stderr.splitlines():
        if line.startswith("nava: live arrays at exit:"):
            return int(line.rsplit(":", 1)[1])
    raise AssertionError(f"no live-array report in:\n{stderr}")


def arena_report(stderr: str) -> tuple[int, int]:
    """The arena's own accounting: bytes handed out, chunks still held."""
    for line in stderr.splitlines():
        if line.startswith("nava: arena bytes:"):
            body = line.split(":", 1)[1]
            bytes_out, chunks = body.split(",")
            return int(bytes_out.split(":")[1]), int(chunks.split(":")[1])
    raise AssertionError(f"no arena report in:\n{stderr}")


class TextFrontendTests(unittest.TestCase):
    """What the text functions accept, and every way a `String` is refused."""

    def assert_code(self, text: str, code: str, message: str | None = None) -> NavaError:
        with self.assertRaises(NavaError) as caught:
            compile_source(text)
        self.assertEqual(caught.exception.code, code, caught.exception.message)
        if message is not None:
            self.assertIn(message, caught.exception.message)
        return caught.exception

    def test_every_text_function_is_usable_with_its_declared_type(self):
        source = ("edition 2;\n"
                  "fn main() {\n"
                  "    let s: String = string_new()\n"
                  "    string_push(s, \"a\")\n"
                  "    string_push_byte(s, 98)\n"
                  "    print(string_len(s))\n"
                  "    print(string_capacity(s))\n"
                  "    print(string_text(s))\n"
                  "    string_clear(s)\n"
                  "    print(string_len(s))\n"
                  "    print(str_concat(\"a\", \"b\"))\n"
                  "    print(str_from_i64(7))\n"
                  "    print(str_from_f64(1.5))\n"
                  "    print(str_slice(\"abcdef\", 1, 3))\n"
                  "    print(str_index_of(\"abc\", \"c\"))\n"
                  "    print(str_starts_with(\"abc\", \"ab\"))\n"
                  "    print(str_ends_with(\"abc\", \"bc\"))\n"
                  "    print(str_trim(\"  x  \"))\n"
                  "    print(str_replace(\"a-b\", \"-\", \"+\"))\n"
                  "    let parts: Vec(str) = vec_new()\n"
                  "    str_split(\"a,b\", \",\", parts)\n"
                  "    print(str_join(parts, \"|\"))\n"
                  "}\n")
        self.assertTrue(compile_source(source))

    def test_a_string_moves_and_a_moved_string_is_dead(self):
        """`let t = s` hands the bytes on; reading `s` afterwards is `E0445`."""
        error = self.assert_code("edition 2;\n"
                                 "fn main() {\n"
                                 "    let s: String = string_new()\n"
                                 "    let t: String = s\n"
                                 "    print(string_len(s))\n"
                                 "}\n", "E0445")
        self.assertIn("was moved", error.message)
        # A reinit after a move is refused too: the binding is dead for the
        # whole block, so there is exactly one way to get a buffer into it.
        self.assert_code("edition 2;\n"
                         "fn main() {\n"
                         "    let s: String = string_new()\n"
                         "    let t: String = s\n"
                         "    s = string_new()\n"
                         "    print(1)\n"
                         "}\n", "E0445")

    def test_a_string_is_returned_by_moving_it(self):
        compile_source("edition 2;\n"
                       "fn make() -> String {\n"
                       "    let s: String = string_new()\n"
                       "    string_push(s, \"out\")\n"
                       "    return s\n"
                       "}\n"
                       "fn main() {\n"
                       "    let s: String = make()\n"
                       "    print(string_text(s))\n"
                       "}\n")
        self.assert_code("edition 2;\n"
                         "fn make() -> String {\n"
                         "    let s: String = string_new()\n"
                         "    return s\n"
                         "}\n"
                         "fn main() { make() }\n", "E0428")

    def test_a_string_can_be_a_field_or_a_payload(self):
        """A field holds a String by owning its own bytes: the copy is deep."""
        source = ("edition 2;\n"
                  "struct Note {\n"
                  "    text: String\n"
                  "}\n"
                  "enum Slot {\n"
                  "    Empty,\n"
                  "    Full(String)\n"
                  "}\n"
                  "fn main() {\n"
                  "    let s: String = string_new()\n"
                  "    string_push(s, \"hi\")\n"
                  "    let note: Note = Note(text: s)\n"
                  "    print(string_text(note.text))\n"
                  "    let slot: Slot = Slot::Full(s)\n"
                  "    match slot {\n"
                  "        Slot::Full(inner) => { print(string_len(inner)) }\n"
                  "        Slot::Empty => { print(0) }\n"
                  "    }\n"
                  "}\n")
        self.assertIn("nv_string_copy", frontend(source))
        compile_source(source)

    def test_a_string_can_be_a_type_argument(self):
        compile_source("edition 2;\n"
                       "struct Pair<T> { first: T, second: T }\n"
                       "fn main() {\n"
                       "    let a: String = string_new()\n"
                       "    let b: String = string_new()\n"
                       "    let p = Pair(String)(first: a, second: b)\n"
                       "    print(string_len(p.first))\n"
                       "}\n")

    def test_a_string_cannot_be_an_element(self):
        """An element is read by value; an element that owns storage would have
        to be copied on every read and released on every write."""
        self.assert_code("edition 2;\n"
                         "fn main() {\n"
                         "    let words: [String] = [\"a\", \"b\"]\n"
                         "    print(len(words))\n"
                         "}\n", "E0426")
        self.assert_code("edition 2;\n"
                         "fn main() {\n"
                         "    let v: Vec(String) = vec_new()\n"
                         "    print(vec_len(v))\n"
                         "}\n", "E0427")

    def test_a_mutating_call_needs_a_writable_string(self):
        error = self.assert_code("edition 2;\n"
                                 "fn add(s: String) {\n"
                                 "    string_push(s, \"x\")\n"
                                 "}\n"
                                 "fn main() { print(1) }\n", "E0444")
        self.assertIn("mut String", error.message)
        self.assert_code("edition 2;\n"
                         "fn add(s: mut String) { string_push(s, \"x\") }\n"
                         "fn pass(s: String) { add(s) }\n"
                         "fn main() {\n"
                         "    let s: String = string_new()\n"
                         "    pass(s)\n"
                         "}\n", "E0333")

    def test_the_argument_types_and_arities_are_checked(self):
        self.assert_code("edition 2;\n"
                         "fn main() { print(str_concat(\"a\")) }\n", "E0326")
        self.assert_code("edition 2;\n"
                         "fn main() { print(str_from_i64(\"x\")) }\n", "E0327")
        self.assert_code("edition 2;\n"
                         "fn main() { print(str_from_f64(1)) }\n", "E0327")
        self.assert_code("edition 2;\n"
                         "fn main() {\n"
                         "    let s: String = string_new()\n"
                         "    string_push(s, 1)\n"
                         "}\n", "E0327")
        self.assert_code("edition 2;\n"
                         "fn main() {\n"
                         "    let a: [str] = [\"a\"]\n"
                         "    print(str_join(a, \",\"))\n"
                         "}\n", "E0327")
        self.assert_code("edition 2;\n"
                         "fn main() {\n"
                         "    let s: String = string_new()\n"
                         "    str_split(\"a,b\", \",\", 1)\n"
                         "}\n", "E0327")

    def test_a_string_is_not_printable_itself(self):
        """Printing the builder would print a pointer; print its text."""
        self.assert_code("edition 2;\n"
                         "fn main() {\n"
                         "    let s: String = string_new()\n"
                         "    print(s)\n"
                         "}\n", "E0323")

    def test_the_text_names_are_reserved(self):
        self.assert_code("edition 2;\n"
                         "fn str_concat(a: str, b: str) -> str { return a }\n"
                         "fn main() { print(1) }\n", "E0301")
        self.assert_code("edition 2;\n"
                         "fn main() {\n"
                         "    let string_new = 1\n"
                         "    print(string_new)\n"
                         "}\n", "E0301")

    def test_a_string_needs_edition_two(self):
        self.assert_code("fn main() {\n"
                         "    let s: String = string_new()\n"
                         "    print(1)\n"
                         "}\n", "E0372")

    def test_the_generated_c_owns_a_builder_and_uses_the_arena(self):
        code = frontend("edition 2;\n"
                        "fn main() {\n"
                        "    let s: String = string_new()\n"
                        "    string_push(s, \"hi\")\n"
                        "    print(string_text(s))\n"
                        "    print(str_concat(\"a\", \"b\"))\n"
                        "}\n")
        self.assertIn("nv_string * nv_v0", code)
        self.assertIn("typedef struct nv_string", code)
        self.assertIn("void nv_string_push(nv_string *text, const char *value)", code)
        self.assertIn("nv_string_free(&nv_v0);", code)
        self.assertIn("nv_str_concat(", code)
        self.assertIn("nv_arena_alloc", code)


class TextRuntimeTests(unittest.TestCase):
    """The text itself: values, growth, splitting, and the accounting."""

    def run_program(self, text: str) -> subprocess.CompletedProcess:
        result = compile_and_run("edition 2;\n" + text)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def test_the_constructors_produce_the_text_that_print_writes(self):
        result = self.run_program("fn main() {\n"
                                  "    print(str_from_i64(0))\n"
                                  "    print(str_from_i64(-9223372036854775807 - 1))\n"
                                  "    print(str_from_f64(1.0))\n"
                                  "    print(str_from_f64(0.5))\n"
                                  "    print(str_concat(\"\", \"\"))\n"
                                  "    print(bytes_len(str_from_i64(12345)))\n"
                                  "}\n")
        self.assertEqual(result.stdout.splitlines()[:6],
                         ["0", "-9223372036854775808", "1.0", "0.5", "", "5"])
        self.assertEqual(live_arrays(result.stderr), 0)

    def test_slices_and_searches_are_checked_and_exact(self):
        result = self.run_program("fn main() {\n"
                                  "    print(str_slice(\"abcdef\", 0, 0))\n"
                                  "    print(str_slice(\"abcdef\", 2, 6))\n"
                                  "    print(str_slice(\"abcdef\", 6, 6))\n"
                                  "    print(str_index_of(\"banana\", \"na\"))\n"
                                  "    print(str_index_of(\"banana\", \"zz\"))\n"
                                  "    print(str_starts_with(\"banana\", \"ban\"))\n"
                                  "    print(str_ends_with(\"banana\", \"na\"))\n"
                                  "    print(str_starts_with(\"banana\", \"\"))\n"
                                  "    print(bytes_len(str_slice(\"\\u00e9\", 0, 2)))\n"
                                  "}\n")
        self.assertEqual(result.stdout.splitlines(), ["", "cdef", "", "2", "-1", "true",
                                                      "true", "true", "2"])
        self.assertEqual(live_arrays(result.stderr), 0)

    def test_a_bad_slice_stops_the_program(self):
        result = compile_and_run("edition 2;\n"
                                 "fn main() {\n"
                                 "    print(str_slice(\"abc\", 1, 9))\n"
                                 "}\n", tracked=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("string slice 1..9 is out of bounds for a string of 3 bytes",
                      result.stderr)
        self.assertEqual(result.stdout, "")

    def test_trim_and_replace_build_the_expected_text(self):
        result = self.run_program("fn main() {\n"
                                  "    print(str_trim(\"  ab  \"))\n"
                                  "    print(str_trim(\"\"))\n"
                                  "    print(str_trim(\"   \"))\n"
                                  "    print(str_replace(\"a-b-c\", \"-\", \"--\"))\n"
                                  "    print(str_replace(\"aaa\", \"a\", \"\"))\n"
                                  "    print(str_replace(\"abc\", \"\", \"x\"))\n"
                                  "    print(bytes_len(str_replace(\"\", \"a\", \"bb\")))\n"
                                  "}\n")
        self.assertEqual(result.stdout.splitlines()[:7],
                         ["ab", "", "", "a--b--c", "", "abc", "0"])
        self.assertEqual(live_arrays(result.stderr), 0)

    def test_the_builder_grows_by_doubling_and_keeps_its_text(self):
        result = self.run_program("fn append(s: mut String, n: i64) {\n"
                                  "    let i = 0\n"
                                  "    while i < n {\n"
                                  "        string_push(s, \"ab\")\n"
                                  "        i = i + 1\n"
                                  "    }\n"
                                  "}\n"
                                  "fn main() {\n"
                                  "    let s: String = string_new()\n"
                                  "    print(string_len(s))\n"
                                  "    string_push(s, \"0123456789\")\n"
                                  "    print(string_len(s))\n"
                                  "    print(string_capacity(s))\n"
                                  "    append(s, 40)\n"
                                  "    print(string_len(s))\n"
                                  "    print(string_capacity(s))\n"
                                  "    print(bytes_len(string_text(s)))\n"
                                  "    string_clear(s)\n"
                                  "    print(string_len(s))\n"
                                  "}\n")
        self.assertEqual(result.stdout.split(), ["0", "10", "32", "90", "128", "90", "0"])
        self.assertEqual(live_arrays(result.stderr), 0)

    def test_a_byte_outside_the_range_and_a_nul_are_refused_at_run_time(self):
        for byte, message in ((256, "a string byte must be in 0..255"),
                              (-1, "a string byte must be in 0..255"),
                              (0, "a String cannot contain a NUL byte")):
            result = compile_and_run("edition 2;\n"
                                     "fn main() {\n"
                                     "    let s: String = string_new()\n"
                                     f"    string_push_byte(s, {byte})\n"
                                     "}\n", tracked=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(message, result.stderr)

    def test_a_returned_string_is_owned_by_the_caller_and_released(self):
        """The callee stops owning what it returns; the caller frees it once."""
        result = self.run_program("fn make(n: i64) -> String {\n"
                                  "    let s: String = string_new()\n"
                                  "    string_push(s, \"out-\")\n"
                                  "    string_push(s, str_from_i64(n))\n"
                                  "    return s\n"
                                  "}\n"
                                  "fn main() {\n"
                                  "    let a: String = make(1)\n"
                                  "    let b: String = make(2)\n"
                                  "    print(string_text(a))\n"
                                  "    print(string_text(b))\n"
                                  "}\n")
        self.assertEqual(result.stdout.splitlines(), ["out-1", "out-2"])
        self.assertEqual(result.stderr.count("nava: live arrays at exit: 0"), 1,
                         result.stderr)

    def test_split_fills_a_vector_and_join_reads_one(self):
        result = self.run_program("fn main() {\n"
                                  "    let parts: Vec(str) = vec_new()\n"
                                  "    str_split(\"a,bb,,ccc\", \",\", parts)\n"
                                  "    print(vec_len(parts))\n"
                                  "    for part in parts { print(part) }\n"
                                  "    print(str_join(parts, \"-\"))\n"
                                  "    let single: Vec(str) = vec_new()\n"
                                  "    str_split(\"\", \",\", single)\n"
                                  "    print(vec_len(single))\n"
                                  "    print(str_join(single, \"+\"))\n"
                                  "}\n")
        self.assertEqual(result.stdout.splitlines()[:8],
                         ["4", "a", "bb", "", "ccc", "a-bb--ccc", "1", ""])
        self.assertEqual(live_arrays(result.stderr), 0)

    def test_an_empty_separator_is_refused_at_run_time(self):
        result = compile_and_run("edition 2;\n"
                                 "fn main() {\n"
                                 "    let parts: Vec(str) = vec_new()\n"
                                 "    str_split(\"abc\", \"\", parts)\n"
                                 "}\n", tracked=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("str_split needs a non-empty separator", result.stderr)

    def test_text_built_in_a_loop_and_from_a_parameter_is_stable(self):
        """Built text is immutable: later builder writes never change it."""
        result = self.run_program("fn label(prefix: str, n: i64) -> str {\n"
                                  "    let out: String = string_new()\n"
                                  "    string_push(out, prefix)\n"
                                  "    string_push(out, str_from_i64(n))\n"
                                  "    let text = string_text(out)\n"
                                  "    string_push(out, \"!\")\n"
                                  "    return text\n"
                                  "}\n"
                                  "fn main() {\n"
                                  "    let i = 0\n"
                                  "    while i < 3 {\n"
                                  "        print(label(\"item-\", i))\n"
                                  "        i = i + 1\n"
                                  "    }\n"
                                  "}\n")
        self.assertEqual(result.stdout.split(), ["item-0", "item-1", "item-2"])
        self.assertEqual(live_arrays(result.stderr), 0)

    def test_the_arena_is_one_chunk_for_small_text_and_freed_at_exit(self):
        result = self.run_program("fn main() {\n"
                                  "    print(str_concat(\"a\", \"b\"))\n"
                                  "}\n")
        self.assertEqual(result.stdout.split(), ["ab"])
        bytes_out, chunks = arena_report(result.stderr)
        self.assertGreater(bytes_out, 0)
        self.assertEqual(chunks, 1)
        self.assertEqual(live_arrays(result.stderr), 0)

    def test_the_reports_are_printed_once_each(self):
        """One report per instrument: the arena and the array counter share none."""
        result = self.run_program("fn main() {\n"
                                  "    let s: String = string_new()\n"
                                  "    string_push(s, \"x\")\n"
                                  "    print(string_text(s))\n"
                                  "}\n")
        self.assertEqual(result.stderr.count("nava: arena bytes:"), 1, result.stderr)
        self.assertEqual(result.stderr.count("nava: live arrays at exit:"), 1,
                         result.stderr)

    def test_a_large_amount_of_text_uses_several_chunks(self):
        """A chunk is 64 KiB: text that outgrows one is served by the next."""
        result = self.run_program("fn main() {\n"
                                  "    let piece = str_replace(\"x\", \"x\", "
                                  "\"0123456789abcdef\")\n"
                                  "    let total = 0\n"
                                  "    let i = 0\n"
                                  "    while i < 20000 {\n"
                                  "        total = total + bytes_len(str_concat(piece, piece))\n"
                                  "        i = i + 1\n"
                                  "    }\n"
                                  "    print(total)\n"
                                  "}\n")
        self.assertEqual(result.stdout.split(), [str(20000 * 32)])
        bytes_out, chunks = arena_report(result.stderr)
        self.assertGreaterEqual(chunks, 2)
        self.assertGreaterEqual(bytes_out, 20000 * 33)
        self.assertEqual(live_arrays(result.stderr), 0)

    def test_text_and_vectors_under_the_sanitizers(self):
        """Growth, arena reuse and splitting, with nothing left unfreed."""
        result = compile_and_run("edition 2;\n"
                                 "fn build(parts: Vec(str), sep: str) -> str {\n"
                                 "    let out: String = string_new()\n"
                                 "    let first = true\n"
                                 "    for part in parts {\n"
                                 "        if !first { string_push(out, sep) }\n"
                                 "        string_push(out, part)\n"
                                 "        first = false\n"
                                 "    }\n"
                                 "    return string_text(out)\n"
                                 "}\n"
                                 "fn main() {\n"
                                 "    let parts: Vec(str) = vec_new()\n"
                                 "    str_split(\"alpha,beta,gamma,delta\", \",\", parts)\n"
                                 "    print(build(parts, \"+\"))\n"
                                 "    let i = 0\n"
                                 "    while i < 200 {\n"
                                 "        print(bytes_len(str_replace(build(parts, \"/\"), \"a\", \"AA\")))\n"
                                 "        i = i + 1\n"
                                 "    }\n"
                                 "    print(str_trim(str_slice(str_concat(\"  xy  \", \"\"), 2, 4)))\n"
                                 "}\n", sanitize=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.split()
        self.assertEqual(lines[0], "alpha+beta+gamma+delta")
        joined = "alpha/beta/gamma/delta".replace("a", "AA")
        self.assertEqual(lines[1], str(len(joined)))
        self.assertEqual(lines[-1], "xy")
        self.assertEqual(live_arrays(result.stderr), 0)


class ArgumentOnceTests(unittest.TestCase):
    """A builtin evaluates each argument exactly once.

    N11c emitted the vector builtins *after* the generic argument
    materialisation, so `vec_push(v, bump(1))` evaluated `bump(1)` twice — a
    visible side effect repeated. The dispatch now happens before the arguments
    are built, and these two tests pin the fix for the vector and text families.
    """

    def assert_once(self, body: str, expected: list[str]) -> None:
        result = compile_and_run("edition 2;\n" + body, tracked=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.split(), expected)

    def test_a_vector_argument_with_a_side_effect_runs_once(self):
        self.assert_once("fn bump(n: i64) -> i64 {\n"
                         "    print(n)\n"
                         "    return n * 10\n"
                         "}\n"
                         "fn main() {\n"
                         "    let xs: Vec(i64) = vec_new()\n"
                         "    vec_push(xs, bump(1))\n"
                         "    vec_set(xs, 0, bump(2))\n"
                         "    print(vec_get(xs, 0))\n"
                         "}\n", ["1", "2", "20"])

    def test_a_text_argument_with_a_side_effect_runs_once(self):
        self.assert_once("fn speak(text: str) -> str {\n"
                         "    print(text)\n"
                         "    return text\n"
                         "}\n"
                         "fn main() {\n"
                         "    let s: String = string_new()\n"
                         "    string_push(s, speak(\"a\"))\n"
                         "    print(string_text(s))\n"
                         "    print(str_concat(speak(\"b\"), speak(\"c\")))\n"
                         "}\n", ["a", "a", "b", "c", "bc"])


if __name__ == "__main__":
    unittest.main()
