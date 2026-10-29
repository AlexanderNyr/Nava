"""N11, part seven: an element of an array or a vector may own storage.

`[String]`, `Vec(String)`, `[Bag]` and `Vec(Bag)` used to be refused (`E0426` /
`E0427`): an element is read by value on every access, so an element that owns a
buffer would have to be copied on every read and released on every overwrite, and
until now those copies were shallow. N11g makes them deep, and the claim has
three parts.

**An element copy is deep and the container owns it.** `vec_push(v, s)` and
`xs[0] = s` copy the element in, so growing `s` afterwards leaves the copy alone;
the container releases every element it still holds when it is released, and
`_copy` copies elementwise, so a whole container can be stored in a value.

**A read copies and the copy is released.** `xs[0]`, `vec_get(v, 0)` and the
variable of `for x in v` hand back a copy that owns its own storage; the copy is
released when the statement ends, or by the block that bound it, or by the turn
of the loop it belongs to. That is what the live-array counter and
AddressSanitizer are here to check: a shallow copy would double-free, and a
missing release would leak.

**Nesting is still a shape rule.** `[[i64]]`, `[Vec(i64)]`, `Vec(Vec(i64))` and
`Vec([i64])` stay `E0206`: Nava holds one collection at a time. That rule is
about what a collection may *contain* as a shape, and it outlives the ownership
rule that changed here.
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

SLOT = ("enum Slot {\n"
        "    Empty,\n"
        "    Full(Bag)\n"
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


class ElementFrontendTests(unittest.TestCase):
    """What the generated C does with an element that owns storage."""

    def assert_code(self, text: str, code: str, message: str | None = None) -> NavaError:
        with self.assertRaises(NavaError) as caught:
            compile_source(text)
        self.assertEqual(caught.exception.code, code, caught.exception.message)
        if message is not None:
            self.assertIn(message, caught.exception.message)
        return caught.exception

    def test_an_array_of_strings_copies_elements_in_and_releases_them(self):
        source = ("edition 2;\n"
                  "fn main() {\n"
                  "    let a: String = string_new()\n"
                  "    string_push(a, \"a\")\n"
                  "    let words: [String] = [a, a]\n"
                  "    print(len(words))\n"
                  "    print(string_text(words[0]))\n"
                  "}\n")
        code = frontend(source)
        self.assertIn("result.data[at] = nv_string_copy(", code)
        self.assertIn("nv_string_free(&array->data[at]);", code)

    def test_a_vector_of_strings_copies_an_element_in_on_push_and_set(self):
        source = ("edition 2;\n"
                  "fn main() {\n"
                  "    let v: Vec(String) = vec_new()\n"
                  "    let a: String = string_new()\n"
                  "    vec_push(v, a)\n"
                  "    vec_set(v, 0, a)\n"
                  "    print(vec_len(v))\n"
                  "}\n")
        code = frontend(source)
        self.assertIn("vector->data[vector->len] = nv_string_copy(value);", code)
        self.assertIn("nv_string_free(&vector->data[at]);", code)
        self.assertIn("vector->data[at] = nv_string_copy(value);", code)
        self.assertIn("copy->data[at] = nv_string_copy(source->data[at]);", code)

    def test_a_read_of_an_element_is_a_copy_that_the_statement_releases(self):
        source = ("edition 2;\n"
                  "fn main() {\n"
                  "    let a: String = string_new()\n"
                  "    let words: [String] = [a, a]\n"
                  "    print(string_len(words[1]))\n"
                  "}\n")
        code = frontend(source)
        self.assertIn("nv_string_copy(nv_arr_String_get(", code)
        # The copy belongs to the reader: the statement that made it releases it.
        self.assertIn("nv_string_free(&", code)

    def test_a_slot_is_released_before_it_is_overwritten(self):
        source = ("edition 2;\n"
                  "fn main() {\n"
                  "    let a: String = string_new()\n"
                  "    let words: [String] = [a]\n"
                  "    words[0] = a\n"
                  "    print(len(words))\n"
                  "}\n")
        code = frontend(source)
        release = code.index("static inline void nv_arr_String_set")
        body = code[release:code.index("static void nv_arr_String_free")]
        self.assertLess(body.index("nv_string_free(&array->data[at]);"),
                        body.index("nv_string_copy(value)"))

    def test_a_holder_of_storage_is_an_element(self):
        source = ("edition 2;\n" + BAG +
                  "fn main() {\n"
                  "    let bags: [Bag] = []\n"
                  "    print(len(bags))\n"
                  "    let more: Vec(Bag) = vec_new()\n"
                  "    print(vec_len(more))\n"
                  "}\n")
        code = frontend(source)
        self.assertIn("result.data[at] = nv_copy_nv_struct_Bag(items[at]);", code)
        self.assertIn("nv_release_nv_struct_Bag(&array->data[at]);", code)
        self.assertIn("vector->data[vector->len] = nv_copy_nv_struct_Bag(value);", code)
        self.assertIn("nv_release_nv_struct_Bag(&vector->data[at]);", code)

    def test_an_enum_element_is_copied_and_released(self):
        source = ("edition 2;\n" + BAG + SLOT +
                  "fn main() {\n"
                  "    let slots: Vec(Slot) = vec_new()\n"
                  "    print(vec_len(slots))\n"
                  "}\n")
        code = frontend(source)
        self.assertIn("nv_copy_nv_enum_Slot(value)", code)
        self.assertIn("nv_release_nv_enum_Slot(&vector->data[at]);", code)

    def test_a_loop_variable_is_a_copy_released_with_its_turn(self):
        source = ("edition 2;\n"
                  "fn main() {\n"
                  "    let a: String = string_new()\n"
                  "    let words: [String] = [a]\n"
                  "    for word in words {\n"
                  "        print(string_len(word))\n"
                  "    }\n"
                  "}\n")
        code = frontend(source)
        loop = code.index("for (;")
        body = code[loop:code.index("nv_arr_String_free", loop)]
        self.assertIn("nv_string_copy(nv_arr_String_get(", body)
        self.assertIn("nv_string_free(&", body)

    def test_a_zeroed_slot_is_not_a_value_of_an_owning_element(self):
        """`array_zeros` needs an element whose zero value *is* a value.

        An all-zero `nv_string` is a null buffer, which is not an empty string,
        so the rule that refused `[str]` refuses `[String]` for the same reason —
        and an array of owning elements is therefore always built from values,
        never from zeros.
        """
        self.assert_code("edition 2;\n"
                         "fn main() {\n"
                         "    let words: [String] = array_zeros(3)\n"
                         "    print(string_len(words[0]))\n"
                         "}\n", "E0425", "String is String, which has no zero value")

    def test_a_collection_is_still_not_an_element(self):
        """The nested-shape rule and the ownership rule are different rules."""
        for source in ("edition 2;\nfn main() {\n"
                       "    let rows: Vec(Vec(i64)) = vec_new()\n"
                       "    print(vec_len(rows))\n}\n",
                       "edition 2;\nfn main() {\n"
                       "    let rows: Vec([i64]) = vec_new()\n"
                       "    print(vec_len(rows))\n}\n",
                       "edition 2;\nfn main() {\n"
                       "    let rows: [[i64]] = []\n"
                       "    print(len(rows))\n}\n",
                       "edition 2;\nfn main() {\n"
                       "    let rows: [Vec(i64)] = []\n"
                       "    print(len(rows))\n}\n"):
            with self.subTest(source=source.splitlines()[3].strip()):
                self.assert_code(source, "E0206")

    def test_the_honest_message_survives_for_an_unknown_element(self):
        self.assert_code("edition 2;\nfn main() {\n    let a: [Nope] = []\n"
                         "    print(len(a))\n}\n", "E0203", "unknown type")


class ElementRuntimeTests(unittest.TestCase):
    """What running the program says about the buffers and about the leaks."""

    def test_a_stored_string_is_independent_of_its_source(self):
        source = ("edition 2;\n"
                  "fn main() {\n"
                  "    let a: String = string_new()\n"
                  "    string_push(a, \"one\")\n"
                  "    let words: [String] = [a]\n"
                  "    string_push(a, \"!\")\n"
                  "    print(string_text(words[0]))\n"
                  "    print(string_text(a))\n"
                  "    print(len(words))\n"
                  "}\n")
        result = compile_and_run(source)
        self.assertEqual(result.stdout.split(), ["one", "one!", "1"])
        self.assertEqual(live_arrays(result.stderr), 0)

    def test_a_write_to_a_slot_releases_what_it_held(self):
        source = ("edition 2;\n"
                  "fn main() {\n"
                  "    let a: String = string_new()\n"
                  "    string_push(a, \"a\")\n"
                  "    let b: String = string_new()\n"
                  "    string_push(b, \"b\")\n"
                  "    let words: [String] = [a, b]\n"
                  "    let i: i64 = 0\n"
                  "    while i < 20 {\n"
                  "        let fresh: String = string_new()\n"
                  "        string_push(fresh, \"z\")\n"
                  "        words[0] = fresh\n"
                  "        i = i + 1\n"
                  "    }\n"
                  "    print(string_text(words[0]))\n"
                  "    print(string_text(words[1]))\n"
                  "}\n")
        result = compile_and_run(source, sanitize=True)
        self.assertEqual(result.stdout.split(), ["z", "b"], result.stderr)
        self.assertEqual(live_arrays(result.stderr), 0)
        self.assertNotIn("AddressSanitizer", result.stderr)

    def test_a_vector_of_strings_pushes_reads_and_pops(self):
        source = ("edition 2;\n"
                  "fn main() {\n"
                  "    let v: Vec(String) = vec_new()\n"
                  "    let i: i64 = 0\n"
                  "    while i < 4 {\n"
                  "        let s: String = string_new()\n"
                  "        string_push(s, \"x\")\n"
                  "        vec_push(v, s)\n"
                  "        i = i + 1\n"
                  "    }\n"
                  "    print(vec_len(v))\n"
                  "    let first: String = vec_get(v, 0)\n"
                  "    print(string_len(first))\n"
                  "    let popped = vec_pop(v)\n"
                  "    match popped {\n"
                  "        Option(String)::Some(got) => { print(string_text(got)) }\n"
                  "        Option(String)::None => { print(\"none\") }\n"
                  "    }\n"
                  "    print(vec_len(v))\n"
                  "}\n")
        result = compile_and_run(source)
        self.assertEqual(result.stdout.split(), ["4", "1", "x", "3"])
        self.assertEqual(live_arrays(result.stderr), 0)

    def test_a_loop_copies_each_element(self):
        """Changing the loop variable cannot reach back into the container."""
        source = ("edition 2;\n"
                  "fn main() {\n"
                  "    let a: String = string_new()\n"
                  "    string_push(a, \"keep\")\n"
                  "    let words: [String] = [a, a]\n"
                  "    for word in words {\n"
                  "        string_push(word, \"!\")\n"
                  "        print(string_text(word))\n"
                  "    }\n"
                  "    print(string_text(words[0]))\n"
                  "}\n")
        result = compile_and_run(source)
        self.assertEqual(result.stdout.split(), ["keep!", "keep!", "keep"])
        self.assertEqual(live_arrays(result.stderr), 0)

    def test_an_array_of_bags_is_released_with_its_elements(self):
        source = ("edition 2;\n" + BAG +
                  "fn make(n: i64) -> Bag {\n"
                  "    let xs: Vec(i64) = vec_new()\n"
                  "    vec_push(xs, n)\n"
                  "    let s: String = string_new()\n"
                  "    string_push(s, \"b\")\n"
                  "    return Bag(items: xs, text: s)\n"
                  "}\n"
                  "fn main() {\n"
                  "    let i: i64 = 0\n"
                  "    let total: i64 = 0\n"
                  "    while i < 50 {\n"
                  "        let bags: [Bag] = [make(i)]\n"
                  "        let stolen: Bag = bags[0]\n"
                  "        total = total + vec_len(stolen.items)\n"
                  "        i = i + 1\n"
                  "    }\n"
                  "    print(total)\n"
                  "}\n")
        result = compile_and_run(source, sanitize=True)
        self.assertEqual(result.stdout.split(), ["50"], result.stderr)
        self.assertEqual(live_arrays(result.stderr), 0)
        self.assertNotIn("AddressSanitizer", result.stderr)

    def test_elements_under_the_sanitizers(self):
        """Two hundred rounds of build, store, read, overwrite and release."""
        source = ("edition 2;\n" + BAG + SLOT +
                  "fn make(n: i64) -> Bag {\n"
                  "    let xs: Vec(i64) = vec_new()\n"
                  "    vec_push(xs, n)\n"
                  "    let s: String = string_new()\n"
                  "    string_push(s, \"b\")\n"
                  "    return Bag(items: xs, text: s)\n"
                  "}\n"
                  "fn main() {\n"
                  "    let slots: Vec(Slot) = vec_new()\n"
                  "    let round: i64 = 0\n"
                  "    let seen: i64 = 0\n"
                  "    while round < 200 {\n"
                  "        let bags: [Bag] = [make(round)]\n"
                  "        bags[0] = make(round)\n"
                  "        vec_push(slots, Slot::Full(bags[0]))\n"
                  "        for slot in slots {\n"
                  "            match slot {\n"
                  "                Slot::Empty => { seen = seen + 0 }\n"
                  "                Slot::Full(inner) => { seen = seen + vec_len(inner.items) }\n"
                  "            }\n"
                  "        }\n"
                  "        round = round + 1\n"
                  "    }\n"
                  "    print(seen)\n"
                  "}\n")
        result = compile_and_run(source, sanitize=True)
        self.assertEqual(result.stdout.split(), ["20100"], result.stderr)
        self.assertEqual(live_arrays(result.stderr), 0)
        self.assertNotIn("AddressSanitizer", result.stderr)
        self.assertNotIn("runtime error", result.stderr)


if __name__ == "__main__":
    unittest.main()
