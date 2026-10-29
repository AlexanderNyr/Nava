"""N11, part five: a value that owns storage crosses a frame boundary by moving.

Three claims, each with a control that fails when the claim is untrue.

**One — a move is a transfer, not a copy.** `let b: Vec(i64) = a`, `a = b` and
`return a` hand the buffer on. Nothing is copied, the source is emptied, and the
binding that receives it releases it exactly once. The control is the array
counter under `-DNAVA_TRACK_ALLOCS` (`nava: live arrays at exit: 0`) plus
AddressSanitizer, which turns a double release into a report instead of a
silent free of somebody else's memory.

**Two — a moved-from binding is dead.** Reading it, passing it, returning it,
moving it again or assigning to it is `E0445`; moving out of a borrow — returning
a parameter, or assigning over one — is `E0446`. Both refusals come from one
place, so a parameter cannot be laundered through a call and a `mut` parameter
cannot be emptied behind the caller's back.

**Three — the arena has regions, and the rule that gives a frame one is visible.**
A call is given a region when no allocation of it can be reached after it
returns: the return type holds no text and no `mut` parameter holds text. A loop
of 2 000 calls that each build text internally therefore reports the same arena
figure as a loop of one — before this increment the figure grew with the loop.
A function that returns `str`, or that writes through `mut Vec(str)`, keeps its
chunks for the whole run, which is the price of soundness and is asserted too.
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


def compile_and_run(text: str, sanitize: bool = False) -> subprocess.CompletedProcess:
    """Build with the allocation instrument and run, or with the sanitizers."""
    with tempfile.TemporaryDirectory() as directory:
        c_file = Path(directory) / "program.c"
        binary = Path(directory) / "program"
        c_file.write_text(frontend(text), encoding="utf-8")
        extra = ["-DNAVA_TRACK_ALLOCS"] + (list(SANITIZER_FLAGS) if sanitize else [])
        build = subprocess.run([CC, "-std=c11", "-pedantic-errors",
                                "-O1" if sanitize else "-O2", *extra,
                                str(c_file), "-o", str(binary)],
                               capture_output=True, text=True, timeout=300)
        if build.returncode != 0:
            raise AssertionError(f"the generated C did not compile:\n{build.stderr}")
        return subprocess.run([str(binary)], capture_output=True, text=True, timeout=60)


def live_arrays(stderr: str) -> int:
    for line in stderr.splitlines():
        if line.startswith("nava: live arrays at exit:"):
            return int(line.rsplit(":", 1)[1])
    raise AssertionError(f"no live-array report in:\n{stderr}")


def arena_bytes(stderr: str) -> tuple[int, int]:
    for line in stderr.splitlines():
        if line.startswith("nava: arena bytes:"):
            body = line.split(":", 1)[1]
            bytes_out, chunks = body.split(",")
            return int(bytes_out.split(":")[1]), int(chunks.split(":")[1])
    raise AssertionError(f"no arena report in:\n{stderr}")


RETURNING_VECTOR = ("fn make(n: i64) -> Vec(i64) {\n"
                    "    let xs: Vec(i64) = vec_new()\n"
                    "    let i = 0\n"
                    "    while i < n {\n"
                    "        vec_push(xs, i)\n"
                    "        i = i + 1\n"
                    "    }\n"
                    "    return xs\n"
                    "}\n")


class MoveFrontendTests(unittest.TestCase):
    """What a move may leave behind, and every way a move is refused."""

    def assert_code(self, text: str, code: str, message: str | None = None) -> NavaError:
        with self.assertRaises(NavaError) as caught:
            compile_source(text)
        self.assertEqual(caught.exception.code, code, caught.exception.message)
        if message is not None:
            self.assertIn(message, caught.exception.message)
        return caught.exception

    def test_a_vector_and_a_string_can_be_moved_out_of_a_function(self):
        compile_source("edition 2;\n" + RETURNING_VECTOR +
                       "fn text() -> String {\n"
                       "    let s: String = string_new()\n"
                       "    string_push(s, \"x\")\n"
                       "    return s\n"
                       "}\n"
                       "fn main() {\n"
                       "    let xs: Vec(i64) = make(3)\n"
                       "    let s: String = text()\n"
                       "    print(vec_len(xs))\n"
                       "    print(string_text(s))\n"
                       "}\n")

    def test_a_local_moves_into_another_binding_and_into_an_assignment(self):
        compile_source("edition 2;\n"
                       "fn main() {\n"
                       "    let xs: Vec(i64) = vec_new()\n"
                       "    vec_push(xs, 1)\n"
                       "    let ys: Vec(i64) = xs\n"
                       "    let zs: Vec(i64) = vec_new()\n"
                       "    zs = ys\n"
                       "    print(vec_len(zs))\n"
                       "}\n")

    def test_a_moved_binding_cannot_be_read_passed_or_moved_again(self):
        move = ("edition 2;\n"
                "fn take(xs: Vec(i64)) { print(vec_len(xs)) }\n"
                "fn main() {\n"
                "    let xs: Vec(i64) = vec_new()\n"
                "    let ys: Vec(i64) = xs\n")
        for use in ("    print(vec_len(xs))\n",
                    "    take(xs)\n",
                    "    let zs: Vec(i64) = xs\n",
                    "    xs = vec_new()\n"):
            with self.subTest(use=use.strip()):
                error = self.assert_code(move + use + "    print(1)\n}\n", "E0445")
                self.assertIn("was moved", error.message)
                self.assertIn("'xs'", error.message)

    def test_a_moved_string_cannot_be_read_or_returned(self):
        self.assert_code("edition 2;\n"
                         "fn main() {\n"
                         "    let s: String = string_new()\n"
                         "    let t: String = s\n"
                         "    print(string_len(s))\n"
                         "}\n", "E0445")
        self.assert_code("edition 2;\n"
                         "fn give() -> String {\n"
                         "    let s: String = string_new()\n"
                         "    let t: String = s\n"
                         "    return s\n"
                         "}\n"
                         "fn main() { print(1) }\n", "E0445")

    def test_a_borrow_cannot_be_moved_out_of_the_frame(self):
        error = self.assert_code("edition 2;\n"
                                 "fn hand_on(xs: Vec(i64)) -> Vec(i64) {\n"
                                 "    return xs\n"
                                 "}\n"
                                 "fn main() { print(1) }\n", "E0446")
        self.assertIn("borrow", error.message)
        error = self.assert_code("edition 2;\n"
                                 "fn hand_on(s: String) -> String {\n"
                                 "    return s\n"
                                 "}\n"
                                 "fn main() { print(1) }\n", "E0446")
        self.assertIn("String", error.message)

    def test_a_mut_parameter_cannot_be_replaced(self):
        """A `mut Vec(T)` parameter is the caller's buffer: writing a new handle
        into the local copy would free the caller's and never reach it."""
        self.assert_code("edition 2;\n"
                         "fn refill(xs: mut Vec(i64)) {\n"
                         "    xs = vec_new()\n"
                         "}\n"
                         "fn main() {\n"
                         "    let xs: Vec(i64) = vec_new()\n"
                         "    refill(xs)\n"
                         "}\n", "E0446")
        self.assert_code("edition 2;\n"
                         "fn refill(s: mut String) {\n"
                         "    s = string_new()\n"
                         "}\n"
                         "fn main() {\n"
                         "    let s: String = string_new()\n"
                         "    refill(s)\n"
                         "}\n", "E0446")

    def test_an_owning_result_must_be_bound(self):
        """A call that returns a buffer and is thrown away leaks it."""
        error = self.assert_code("edition 2;\n" + RETURNING_VECTOR +
                                 "fn main() { make(3) }\n", "E0428")
        self.assertIn("no owner", error.message)
        self.assert_code("edition 2;\n"
                         "fn text() -> String {\n"
                         "    let s: String = string_new()\n"
                         "    return s\n"
                         "}\n"
                         "fn main() { text() }\n", "E0428")

    def test_an_owning_value_is_stored_but_not_as_an_element(self):
        """A field and a payload hold a copy; an element cannot, because an
        element is read by value on every access."""
        compile_source("edition 2;\n"
                       "struct Bag {\n"
                       "    items: Vec(i64)\n"
                       "}\n"
                       "enum Slot {\n"
                       "    Empty,\n"
                       "    Full(Vec(i64))\n"
                       "}\n"
                       "fn main() {\n"
                       "    let xs: Vec(i64) = vec_new()\n"
                       "    let bag: Bag = Bag(items: xs)\n"
                       "    print(vec_len(bag.items))\n"
                       "}\n")
        self.assert_code("edition 2;\n"
                         "fn main() {\n"
                         "    let v: Vec(Vec(i64)) = vec_new()\n"
                         "}\n", "E0427")

    def test_moving_is_a_value_transfer_and_not_a_general_expression(self):
        """Only a local, a constructor or a call can hand a buffer on."""
        self.assert_code("edition 2;\n"
                         "fn main() {\n"
                         "    let parts: Vec(str) = vec_new()\n"
                         "    let s: String = str_concat(\"a\", \"b\")\n"
                         "    print(string_len(s))\n"
                         "}\n", "E0311")

    def test_a_region_is_emitted_only_where_it_is_sound(self):
        """The rule, read off the generated C: a frame gets a mark when nothing
        it builds can be reached after it returns."""
        resettable = frontend("edition 2;\n"
                              "fn width(n: i64) -> i64 {\n"
                              "    let s: String = string_new()\n"
                              "    string_push(s, str_from_i64(n))\n"
                              "    return string_len(s)\n"
                              "}\n"
                              "fn main() { print(width(3)) }\n")
        self.assertIn("nv_arena_mark nv_region = nv_arena_mark_now();", resettable)
        self.assertIn("nv_arena_release_to(nv_region);", resettable)

        returns_text = frontend("edition 2;\n"
                                "fn label(n: i64) -> str {\n"
                                "    return str_from_i64(n)\n"
                                "}\n"
                                "fn main() { print(label(3)) }\n")
        self.assertNotIn("nv_arena_mark", returns_text)

        writes_text = frontend("edition 2;\n"
                               "fn add(parts: mut Vec(str)) {\n"
                               "    vec_push(parts, str_concat(\"a\", \"b\"))\n"
                               "}\n"
                               "fn main() {\n"
                               "    let parts: Vec(str) = vec_new()\n"
                               "    add(parts)\n"
                               "    print(vec_len(parts))\n"
                               "}\n")
        self.assertNotIn("nv_arena_mark", writes_text)

        main_only = frontend("edition 2;\n"
                             "fn main() { print(str_concat(\"a\", \"b\")) }\n")
        self.assertNotIn("nv_arena_mark", main_only)

        # A payload that holds text is a way out too: the caller reads it after
        # the call, so the frame keeps its chunks.
        returns_payload = frontend("edition 2;\n"
                                   "fn maybe(n: i64) -> Option(str) {\n"
                                   "    if n > 0 {\n"
                                   "        return Option(str)::Some(str_from_i64(n))\n"
                                   "    }\n"
                                   "    return Option(str)::None\n"
                                   "}\n"
                                   "fn main() {\n"
                                   "    match maybe(1) {\n"
                                   "        Option(str)::Some(value) => { print(value) }\n"
                                   "        Option(str)::None => { print(\"none\") }\n"
                                   "    }\n"
                                   "}\n")
        self.assertNotIn("nv_arena_mark", returns_payload)


class MoveRuntimeTests(unittest.TestCase):
    """The transfer at run time: one release, one owner, no bytes copied."""

    def run_program(self, text: str, sanitize: bool = False) -> subprocess.CompletedProcess:
        result = compile_and_run("edition 2;\n" + text, sanitize=sanitize)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def test_a_returned_vector_is_owned_by_the_caller(self):
        result = self.run_program(RETURNING_VECTOR +
                                  "fn main() {\n"
                                  "    let a: Vec(i64) = make(3)\n"
                                  "    let b: Vec(i64) = make(5)\n"
                                  "    print(vec_len(a))\n"
                                  "    print(vec_len(b))\n"
                                  "    print(vec_capacity(a))\n"
                                  "}\n")
        self.assertEqual(result.stdout.splitlines(), ["3", "5", "4"])
        self.assertEqual(live_arrays(result.stderr), 0, result.stderr)

    def test_a_move_copies_no_elements_and_releases_once(self):
        """The buffer address survives the move: a copy would have moved it."""
        result = self.run_program("fn main() {\n"
                                  "    let xs: Vec(i64) = vec_new()\n"
                                  "    let i = 0\n"
                                  "    while i < 100 {\n"
                                  "        vec_push(xs, i)\n"
                                  "        i = i + 1\n"
                                  "    }\n"
                                  "    let ys: Vec(i64) = xs\n"
                                  "    print(vec_len(ys))\n"
                                  "    print(vec_capacity(ys))\n"
                                  "    print(vec_get(ys, 99))\n"
                                  "}\n")
        self.assertEqual(result.stdout.splitlines(), ["100", "128", "99"])
        self.assertEqual(live_arrays(result.stderr), 0, result.stderr)

    def test_an_early_return_moves_the_value_out_of_a_loop(self):
        result = self.run_program("fn first_big(limit: i64) -> String {\n"
                                  "    let s: String = string_new()\n"
                                  "    let i = 0\n"
                                  "    while i < 10 {\n"
                                  "        if i == limit {\n"
                                  "            string_push(s, \"hit\")\n"
                                  "            return s\n"
                                  "        }\n"
                                  "        i = i + 1\n"
                                  "    }\n"
                                  "    return s\n"
                                  "}\n"
                                  "fn main() {\n"
                                  "    let inside: String = first_big(3)\n"
                                  "    let after: String = first_big(99)\n"
                                  "    print(string_text(inside))\n"
                                  "    print(string_len(after))\n"
                                  "}\n")
        self.assertEqual(result.stdout.splitlines(), ["hit", "0"])
        self.assertEqual(live_arrays(result.stderr), 0, result.stderr)

    def test_an_assignment_releases_the_buffer_it_replaces(self):
        result = self.run_program("fn fill(n: i64) -> Vec(i64) {\n"
                                  "    let xs: Vec(i64) = vec_new()\n"
                                  "    let i = 0\n"
                                  "    while i < n {\n"
                                  "        vec_push(xs, i)\n"
                                  "        i = i + 1\n"
                                  "    }\n"
                                  "    return xs\n"
                                  "}\n"
                                  "fn main() {\n"
                                  "    let xs: Vec(i64) = fill(40)\n"
                                  "    xs = fill(2)\n"
                                  "    print(vec_len(xs))\n"
                                  "    let s: String = string_new()\n"
                                  "    string_push(s, \"longer text\")\n"
                                  "    s = string_new()\n"
                                  "    string_push(s, \"s\")\n"
                                  "    print(string_text(s))\n"
                                  "}\n")
        self.assertEqual(result.stdout.splitlines(), ["2", "s"])
        self.assertEqual(live_arrays(result.stderr), 0, result.stderr)

    def test_a_moved_binding_inside_a_nested_block_is_released_by_the_receiver(self):
        result = self.run_program("fn main() {\n"
                                  "    let xs: Vec(i64) = vec_new()\n"
                                  "    vec_push(xs, 7)\n"
                                  "    if vec_len(xs) == 1 {\n"
                                  "        let inner: Vec(i64) = xs\n"
                                  "        vec_push(inner, 8)\n"
                                  "        print(vec_len(inner))\n"
                                  "    }\n"
                                  "    print(1)\n"
                                  "}\n")
        self.assertEqual(result.stdout.splitlines(), ["2", "1"])
        self.assertEqual(live_arrays(result.stderr), 0, result.stderr)

    def test_moves_and_returns_under_the_sanitizers(self):
        """The control that carries the memory claim: a double release, a read of
        a released handle or a leaked buffer is a sanitizer report."""
        result = self.run_program("fn label(n: i64) -> String {\n"
                                           "    let s: String = string_new()\n"
                                           "    string_push(s, str_from_i64(n))\n"
                                           "    string_push(s, \":\")\n"
                                           "    string_push(s, str_replace(\"a-b\", \"-\", \"+\"))\n"
                                           "    return s\n"
                                           "}\n"
                                           "fn grow(n: i64) -> Vec(i64) {\n"
                                           "    let xs: Vec(i64) = vec_new()\n"
                                           "    let i = 0\n"
                                           "    while i < n {\n"
                                           "        vec_push(xs, i)\n"
                                           "        i = i + 1\n"
                                           "    }\n"
                                           "    return xs\n"
                                           "}\n"
                                           "fn main() {\n"
                                           "    let round = 0\n"
                                           "    while round < 200 {\n"
                                           "        let text: String = label(round)\n"
                                           "        let moved: String = text\n"
                                           "        let xs: Vec(i64) = grow(8)\n"
                                           "        let ys: Vec(i64) = xs\n"
                                           "        ys = grow(3)\n"
                                           "        print(string_len(moved) + vec_len(ys))\n"
                                           "        round = round + 1\n"
                                           "    }\n"
                                           "}\n", sanitize=True)
        self.assertEqual(len(result.stdout.splitlines()), 200)
        self.assertEqual(live_arrays(result.stderr), 0, result.stderr)


class ArenaRegionTests(unittest.TestCase):
    """The region is the measurable part: the same loop, bounded bytes."""

    def run_program(self, text: str) -> subprocess.CompletedProcess:
        result = compile_and_run("edition 2;\n" + text)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    BUILDING_LOOP = ("fn width(n: i64) -> i64 {\n"
                     "    let s: String = string_new()\n"
                     "    let i = 0\n"
                     "    while i < 8 {\n"
                     "        string_push(s, str_from_i64(n))\n"
                     "        i = i + 1\n"
                     "    }\n"
                     "    return string_len(s)\n"
                     "}\n")

    def test_a_frame_that_builds_text_gets_its_region_back(self):
        once = self.run_program(self.BUILDING_LOOP +
                                "fn main() { print(width(7)) }\n")
        many = self.run_program(self.BUILDING_LOOP +
                                "fn main() {\n"
                                "    let total = 0\n"
                                "    let i = 0\n"
                                "    while i < 2000 {\n"
                                "        total = total + width(i)\n"
                                "        i = i + 1\n"
                                "    }\n"
                                "    print(total)\n"
                                "}\n")
        self.assertEqual(once.stdout, "8\n", once.stderr)
        # 2 000 calls, each building eight pieces of text, report no live bytes:
        # every frame gave its region back. Without a region the figure is the
        # sum of all of them, which is what the next test pins.
        self.assertEqual(arena_bytes(many.stderr), (0, 0), many.stderr)

    def test_text_that_can_escape_keeps_its_chunks(self):
        """A function that returns `str` is not given a region: the caller reads
        what it built, so the chunks live until exit. The price is visible."""
        result = self.run_program("fn label(n: i64) -> str {\n"
                                  "    return str_concat(\"n=\", str_from_i64(n))\n"
                                  "}\n"
                                  "fn main() {\n"
                                  "    let i = 0\n"
                                  "    while i < 500 {\n"
                                  "        if bytes_len(label(i)) == 0 { print(0) }\n"
                                  "        i = i + 1\n"
                                  "    }\n"
                                  "    print(1)\n"
                                  "}\n")
        self.assertEqual(result.stdout, "1\n")
        live, chunks = arena_bytes(result.stderr)
        self.assertGreater(live, 0, result.stderr)
        self.assertGreaterEqual(chunks, 1, result.stderr)

    def test_text_written_through_a_mut_parameter_survives_the_call(self):
        """`mut Vec(str)` is the second way out, and the rule sees it: the frame
        keeps its chunks, so the field the caller reads is still valid."""
        result = self.run_program("fn add(parts: mut Vec(str), n: i64) {\n"
                                  "    vec_push(parts, str_concat(\"p\", str_from_i64(n)))\n"
                                  "}\n"
                                  "fn main() {\n"
                                  "    let parts: Vec(str) = vec_new()\n"
                                  "    let i = 0\n"
                                  "    while i < 3 {\n"
                                  "        add(parts, i)\n"
                                  "        i = i + 1\n"
                                  "    }\n"
                                  "    print(vec_len(parts))\n"
                                  "    print(vec_get(parts, 0))\n"
                                  "    print(vec_get(parts, 2))\n"
                                  "}\n")
        self.assertEqual(result.stdout.splitlines(), ["3", "p0", "p2"])
        self.assertGreater(arena_bytes(result.stderr)[0], 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
