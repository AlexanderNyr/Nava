"""N11, part six: a value that owns storage can be *inside* another value.

`Vec(T)` and `String` used to be the two types that could not be stored: a field
is copied with its value and a payload with its value, and neither has a shallow
copy that would keep the program correct. This increment gives both a **deep**
copy — a new buffer with the bytes copied into it — so a field, a payload and a
type argument can hold one. The claim is one sentence, and it has two halves:

**The holder owns its own bytes.** `Bag(items: xs)` allocates a second buffer;
pushing to `xs` afterwards leaves `bag.items` alone, and releasing the bag leaves
`xs` alone. That is asserted from both sides, and AddressSanitizer plus the live
array counter are what make the claim about memory rather than about eyes.

**Storing is not the same as being an element.** An array element and a vector
element are read *by value* on every access, so an element that owns storage
would have to be copied on every read and released on every overwrite; that is
the element work of N11g, which is where an element that owns storage — a
`String`, or a struct that merely *contains* a `Vec` or a `String` — became legal.

What the sort of value a field is by itself is unchanged: reading `bag.items` is
a borrow, so it cannot be moved out (`E0428`), and a mutating call needs a
writable local, so `vec_push(bag.items, 1)` is `E0444` — the same rule the array
field has had since N11b.
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

BAG = ("struct Bag {\n"
       "    items: Vec(i64),\n"
       "    text: String\n"
       "}\n")

SHELF = ("struct Shelf {\n"
         "    bag: Bag,\n"
         "    count: i64\n"
         "}\n")


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


class StorageFrontendTests(unittest.TestCase):
    """What a field, a payload and a type argument may now hold."""

    def assert_code(self, text: str, code: str, message: str | None = None) -> NavaError:
        with self.assertRaises(NavaError) as caught:
            compile_source(text)
        self.assertEqual(caught.exception.code, code, caught.exception.message)
        if message is not None:
            self.assertIn(message, caught.exception.message)
        return caught.exception

    def test_a_field_holds_a_vector_and_a_string(self):
        """The struct's own helpers copy both, deeply, and release both."""
        source = ("edition 2;\n" + BAG +
                  "fn main() {\n"
                  "    let xs: Vec(i64) = vec_new()\n"
                  "    vec_push(xs, 1)\n"
                  "    let s: String = string_new()\n"
                  "    string_push(s, \"hi\")\n"
                  "    let bag: Bag = Bag(items: xs, text: s)\n"
                  "    print(vec_len(bag.items))\n"
                  "    print(string_text(bag.text))\n"
                  "}\n")
        code = frontend(source)
        self.assertIn("nv_vec_i64_copy(", code)
        self.assertIn("nv_string_copy(", code)
        self.assertIn("static nv_struct_Bag nv_copy_nv_struct_Bag(", code)
        self.assertIn("static void nv_release_nv_struct_Bag(", code)

    def test_a_nested_field_copies_both_levels(self):
        source = ("edition 2;\n" + BAG + SHELF +
                  "fn main() {\n"
                  "    let xs: Vec(i64) = vec_new()\n"
                  "    let s: String = string_new()\n"
                  "    let bag: Bag = Bag(items: xs, text: s)\n"
                  "    let shelf: Shelf = Shelf(bag: bag, count: 1)\n"
                  "    print(vec_len(shelf.bag.items))\n"
                  "}\n")
        compile_source(source)
        self.assertIn("nv_copy_nv_struct_Bag(value.bag)", frontend(source))

    def test_a_payload_holds_a_vector_and_a_string(self):
        source = ("edition 2;\n" + BAG +
                  "enum Slot {\n"
                  "    Empty,\n"
                  "    Full(Bag),\n"
                  "    Text(String)\n"
                  "}\n"
                  "fn main() {\n"
                  "    let xs: Vec(i64) = vec_new()\n"
                  "    let s: String = string_new()\n"
                  "    let bag: Bag = Bag(items: xs, text: s)\n"
                  "    let slot: Slot = Slot::Full(bag)\n"
                  "    match slot {\n"
                  "        Slot::Full(inner) => { print(vec_len(inner.items)) }\n"
                  "        Slot::Text(name) => { print(string_len(name)) }\n"
                  "        Slot::Empty => { print(0) }\n"
                  "    }\n"
                  "}\n")
        compile_source(source)
        code = frontend(source)
        self.assertIn("case", code)
        self.assertIn("nv_copy_nv_enum_Slot(", code)

    def test_a_type_argument_may_be_an_owning_type(self):
        source = ("edition 2;\n"
                  "struct Pair<T> { first: T, second: T }\n"
                  "fn main() {\n"
                  "    let a: String = string_new()\n"
                  "    let b: String = string_new()\n"
                  "    let p = Pair(String)(first: a, second: b)\n"
                  "    print(string_len(p.first) + string_len(p.second))\n"
                  "    let xs: Vec(i64) = vec_new()\n"
                  "    let o: Option(Vec(i64)) = Option(Vec(i64))::Some(xs)\n"
                  "    match o {\n"
                  "        Option(Vec(i64))::Some(inner) => { print(vec_len(inner)) }\n"
                  "        Option(Vec(i64))::None => { print(-1) }\n"
                  "    }\n"
                  "}\n")
        compile_source(source)
        self.assertIn("nv_copy_nv_struct_Pair_String(", frontend(source))

    def test_a_holder_of_storage_may_be_an_element(self):
        """A struct that holds a `Vec` and a `String` is an element of its own."""
        for source in ("edition 2;\n" + BAG +
                       "fn main() {\n"
                       "    let bags: [Bag] = []\n"
                       "    print(len(bags))\n"
                       "}\n",
                       "edition 2;\n" + BAG +
                       "fn main() {\n"
                       "    let bags: Vec(Bag) = vec_new()\n"
                       "    print(vec_len(bags))\n"
                       "}\n"):
            with self.subTest(source=source.splitlines()[-3].strip()):
                self.assertTrue(compile_source(source).code)

    def test_a_field_is_read_but_never_moved_out(self):
        """A field read is a borrow: the field keeps its buffer."""
        for field, declared in (("items", "Vec(i64)"), ("text", "String")):
            with self.subTest(field=field):
                error = self.assert_code("edition 2;\n" + BAG +
                                         "fn main() {\n"
                                         "    let xs: Vec(i64) = vec_new()\n"
                                         "    let s: String = string_new()\n"
                                         "    let bag: Bag = Bag(items: xs, text: s)\n"
                                         f"    let stolen: {declared} = bag.{field}\n"
                                         "    print(1)\n"
                                         "}\n", "E0428")
                self.assertIn("cannot be copied", error.message)

    def test_a_mutating_call_on_a_field_needs_a_local(self):
        """`vec_push` and `string_push` take a writable local or a `mut`
        parameter; a field is neither, exactly as with an array field."""
        self.assert_code("edition 2;\n" + BAG +
                         "fn main() {\n"
                         "    let xs: Vec(i64) = vec_new()\n"
                         "    let s: String = string_new()\n"
                         "    let bag: Bag = Bag(items: xs, text: s)\n"
                         "    vec_push(bag.items, 1)\n"
                         "}\n", "E0444")
        self.assert_code("edition 2;\n" + BAG +
                         "fn main() {\n"
                         "    let xs: Vec(i64) = vec_new()\n"
                         "    let s: String = string_new()\n"
                         "    let bag: Bag = Bag(items: xs, text: s)\n"
                         "    string_push(bag.text, \"x\")\n"
                         "}\n", "E0444")

    def test_an_owning_field_needs_edition_two(self):
        self.assert_code("edition 1;\n"
                         "struct Bag {\n"
                         "    items: Vec(i64)\n"
                         "}\n"
                         "fn main() { print(1) }\n", "E0372")

    def test_a_struct_that_contains_itself_through_a_field_is_still_refused(self):
        self.assert_code("edition 2;\n"
                         "struct Loop {\n"
                         "    next: Loop\n"
                         "}\n"
                         "fn main() { print(1) }\n", "E0377")


class StorageRuntimeTests(unittest.TestCase):
    """The copies at run time: two holders, two buffers, one release each."""

    def run_program(self, text: str, sanitize: bool = False) -> subprocess.CompletedProcess:
        result = compile_and_run("edition 2;\n" + text, sanitize=sanitize)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def test_a_stored_vector_is_a_copy_and_the_source_goes_on(self):
        result = self.run_program(BAG +
                                  "fn main() {\n"
                                  "    let xs: Vec(i64) = vec_new()\n"
                                  "    vec_push(xs, 1)\n"
                                  "    let s: String = string_new()\n"
                                  "    string_push(s, \"a\")\n"
                                  "    let bag: Bag = Bag(items: xs, text: s)\n"
                                  "    vec_push(xs, 2)\n"
                                  "    string_push(s, \"b\")\n"
                                  "    print(vec_len(bag.items))\n"
                                  "    print(vec_get(bag.items, 0))\n"
                                  "    print(string_text(bag.text))\n"
                                  "    print(vec_len(xs))\n"
                                  "    print(string_text(s))\n"
                                  "}\n")
        self.assertEqual(result.stdout.splitlines(), ["1", "1", "a", "2", "ab"])
        self.assertEqual(live_arrays(result.stderr), 0, result.stderr)

    def test_a_field_write_replaces_and_releases_the_old_buffer(self):
        result = self.run_program(BAG +
                                  "fn main() {\n"
                                  "    let xs: Vec(i64) = vec_new()\n"
                                  "    vec_push(xs, 1)\n"
                                  "    let s: String = string_new()\n"
                                  "    string_push(s, \"old\")\n"
                                  "    let bag: Bag = Bag(items: xs, text: s)\n"
                                  "    let other: Vec(i64) = vec_new()\n"
                                  "    vec_push(other, 7)\n"
                                  "    vec_push(other, 8)\n"
                                  "    bag.items = other\n"
                                  "    let fresh: String = string_new()\n"
                                  "    string_push(fresh, \"new\")\n"
                                  "    bag.text = fresh\n"
                                  "    print(vec_len(bag.items))\n"
                                  "    print(string_text(bag.text))\n"
                                  "    print(vec_len(other))\n"
                                  "}\n")
        self.assertEqual(result.stdout.splitlines(), ["2", "new", "2"])
        self.assertEqual(live_arrays(result.stderr), 0, result.stderr)

    def test_a_stored_value_travels_through_functions_and_keeps_its_storage(self):
        result = self.run_program(BAG + SHELF +
                                  "fn build(n: i64) -> Shelf {\n"
                                  "    let xs: Vec(i64) = vec_new()\n"
                                  "    vec_push(xs, n)\n"
                                  "    let s: String = string_new()\n"
                                  "    string_push(s, str_from_i64(n))\n"
                                  "    let bag: Bag = Bag(items: xs, text: s)\n"
                                  "    return Shelf(bag: bag, count: n)\n"
                                  "}\n"
                                  "fn main() {\n"
                                  "    let a: Shelf = build(1)\n"
                                  "    let b: Shelf = build(2)\n"
                                  "    print(vec_get(a.bag.items, 0) + vec_get(b.bag.items, 0))\n"
                                  "    print(string_text(a.bag.text))\n"
                                  "    print(string_text(b.bag.text))\n"
                                  "}\n")
        self.assertEqual(result.stdout.splitlines(), ["3", "1", "2"])
        self.assertEqual(live_arrays(result.stderr), 0, result.stderr)

    def test_a_payload_binding_is_a_copy_of_its_own(self):
        result = self.run_program(BAG +
                                  "enum Slot {\n"
                                  "    Empty,\n"
                                  "    Full(Bag)\n"
                                  "}\n"
                                  "fn main() {\n"
                                  "    let xs: Vec(i64) = vec_new()\n"
                                  "    vec_push(xs, 1)\n"
                                  "    let s: String = string_new()\n"
                                  "    string_push(s, \"keep\")\n"
                                  "    let bag: Bag = Bag(items: xs, text: s)\n"
                                  "    let slot: Slot = Slot::Full(bag)\n"
                                  "    vec_push(xs, 9)\n"
                                  "    match slot {\n"
                                  "        Slot::Full(inner) => {\n"
                                  "            print(vec_len(inner.items))\n"
                                  "            print(string_text(inner.text))\n"
                                  "        }\n"
                                  "        Slot::Empty => { print(0) }\n"
                                  "    }\n"
                                  "}\n")
        self.assertEqual(result.stdout.splitlines(), ["1", "keep"])
        self.assertEqual(live_arrays(result.stderr), 0, result.stderr)

    def test_a_type_argument_value_is_independent(self):
        result = self.run_program("struct Pair<T> { first: T, second: T }\n"
                                  "fn main() {\n"
                                  "    let a: String = string_new()\n"
                                  "    string_push(a, \"left\")\n"
                                  "    let b: String = string_new()\n"
                                  "    string_push(b, \"right\")\n"
                                  "    let p = Pair(String)(first: a, second: b)\n"
                                  "    string_push(a, \"!\")\n"
                                  "    print(string_text(p.first))\n"
                                  "    print(string_text(a))\n"
                                  "    print(string_text(p.second))\n"
                                  "}\n")
        self.assertEqual(result.stdout.splitlines(), ["left", "left!", "right"])
        self.assertEqual(live_arrays(result.stderr), 0, result.stderr)

    def test_a_value_that_owns_fields_is_released_with_its_block(self):
        result = self.run_program(BAG + SHELF +
                                  "fn spin(n: i64) -> i64 {\n"
                                  "    let round = 0\n"
                                  "    let total = 0\n"
                                  "    while round < n {\n"
                                  "        let xs: Vec(i64) = vec_new()\n"
                                  "        vec_push(xs, round)\n"
                                  "        let s: String = string_new()\n"
                                  "        string_push(s, str_from_i64(round))\n"
                                  "        let bag: Bag = Bag(items: xs, text: s)\n"
                                  "        let shelf: Shelf = Shelf(bag: bag, count: round)\n"
                                  "        total = total + vec_len(shelf.bag.items)\n"
                                  "        round = round + 1\n"
                                  "    }\n"
                                  "    return total\n"
                                  "}\n"
                                  "fn main() { print(spin(50)) }\n")
        self.assertEqual(result.stdout.strip(), "50")
        self.assertEqual(live_arrays(result.stderr), 0, result.stderr)

    def test_stored_values_under_the_sanitizers(self):
        """The control that carries the memory claim: a shallow copy or a second
        release of the same buffer is an AddressSanitizer report."""
        result = self.run_program(BAG + SHELF +
                                  "fn build(n: i64) -> Shelf {\n"
                                  "    let xs: Vec(i64) = vec_new()\n"
                                  "    let i = 0\n"
                                  "    while i < 4 {\n"
                                  "        vec_push(xs, i)\n"
                                  "        i = i + 1\n"
                                  "    }\n"
                                  "    let s: String = string_new()\n"
                                  "    string_push(s, str_from_i64(n))\n"
                                  "    string_push(s, str_replace(\"x-y\", \"-\", \"+\"))\n"
                                  "    let bag: Bag = Bag(items: xs, text: s)\n"
                                  "    return Shelf(bag: bag, count: n)\n"
                                  "}\n"
                                  "fn main() {\n"
                                  "    let round = 0\n"
                                  "    while round < 200 {\n"
                                  "        let shelf: Shelf = build(round)\n"
                                  "        let copy: Shelf = shelf\n"
                                  "        let slot: Option(Bag) = Option(Bag)::Some(copy.bag)\n"
                                  "        print(vec_len(shelf.bag.items) + string_len(shelf.bag.text))\n"
                                  "        match slot {\n"
                                  "            Option(Bag)::Some(inner) => { print(vec_len(inner.items)) }\n"
                                  "            Option(Bag)::None => { print(-1) }\n"
                                  "        }\n"
                                  "        round = round + 1\n"
                                  "    }\n"
                                  "}\n", sanitize=True)
        self.assertEqual(len(result.stdout.splitlines()), 400)
        self.assertEqual(live_arrays(result.stderr), 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
