"""N12, part one: `HashMap(K, V)` and `HashSet(T)`.

The audit asks for collections, and a map is the one container a program cannot
build out of the two it already has: a linear search through a vector is the work
a table exists to remove. N12a adds both, with one interface shape, and the claim
has three parts.

**The types are written once, in the annotation.** `let m: HashMap(i64, str) =
map_new()` is the only place a map learns its key and value types, so a missing
annotation is refused (`E0448`) instead of being guessed. A key is a value with a
defined hash — `i64`, `bool`, `str`, `String` — and everything else is `E0447`;
the reason it is closed is that iteration order is observable, so two programs
that insert the same keys must hash them the same way forever.

**The table owns a copy of every key and value.** `map_set` copies in, an
overwrite releases the slot it replaces, `map_remove` and `map_clear` release
what they drop, and `map_get` hands back a copy inside its `Option`. That is the
element rule of N11g applied to a second container, and the live-allocation
counter and AddressSanitizer are here to check it: a shallow copy would
double-free and a missing release would leak.

**A map and a set are moved, never copied.** `let b: HashMap(i64, str) = a`,
`b = a` and `return a` hand the table on and leave `a` dead (`E0445`); a parameter
is a borrow, so returning one is `E0446`. Nothing in this file claims a map is
copied, because no program can do it.
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
        return subprocess.run([str(binary)], capture_output=True, text=True, timeout=120)


def live_arrays(stderr: str) -> int:
    for line in stderr.splitlines():
        if line.startswith("nava: live arrays at exit:"):
            return int(line.rsplit(":", 1)[1])
    raise AssertionError(f"no live-array report in:\n{stderr}")


def error_of(text: str) -> NavaError:
    try:
        compile_source(text)
    except NavaError as error:
        return error
    raise AssertionError("the program compiled but should not have")


class TableFrontendTests(unittest.TestCase):
    """What a map compiles to, and what the checker refuses before that."""

    def test_one_table_per_instance_is_emitted(self):
        c = frontend("edition 2;\n\nfn main() {\n"
                     "    let m: HashMap(i64, str) = map_new()\n"
                     "    map_set(m, 1, \"one\")\n"
                     "    match map_get(m, 1) {\n"
                     "        Option(str)::Some(value) => { print(value) }\n"
                     "        Option(str)::None => { print(\"none\") }\n"
                     "    }\n}\n")
        self.assertIn("nv_map_slot_i64_str", c)
        for helper in ("_new", "_find", "_reserve", "_set", "_get", "_contains",
                       "_remove", "_clear", "_free", "_copy"):
            with self.subTest(helper=helper):
                self.assertIn(f"nv_map_i64_str{helper}(", c)

    def test_a_set_uses_the_same_probe_without_a_value(self):
        c = frontend("edition 2;\n\nfn main() {\n"
                     "    let s: HashSet(i64) = set_new()\n"
                     "    set_add(s, 1)\n"
                     "    print(set_len(s))\n}\n")
        self.assertIn("nv_set_slot_i64", c)
        self.assertIn("nv_set_i64_add(", c)
        self.assertNotIn("nv_set_i64_set(", c)
        self.assertNotIn("value;", c.split("nv_set_i64_add(")[0].split("typedef struct")[-1])

    def test_a_stored_value_is_copied_in_and_released(self):
        """`map_set` copies, and the table frees what it still holds."""
        c = frontend("edition 2;\n\nfn main() {\n"
                     "    let m: HashMap(i64, String) = map_new()\n"
                     "    print(map_len(m))\n}\n")
        set_body = c.split("nv_map_i64_String_set(")[1].split("\n}\n")[0]
        self.assertIn("nv_string_copy(", set_body)
        self.assertIn("nv_string_free(", set_body)
        free_body = c.split("nv_map_i64_String_free(nv_map_i64_String **slot) {")[1]
        self.assertIn("nv_string_free(", free_body)
        self.assertIn("nv_map_i64_String_release_slots(", free_body)

    def test_a_struct_value_is_copied_with_its_own_helpers(self):
        c = frontend("edition 2;\n\n" + BAG +
                     "\nfn main() {\n"
                     "    let m: HashMap(str, Bag) = map_new()\n"
                     "    print(map_len(m))\n}\n")
        body = c.split("nv_map_str_Bag_set(")[1].split("\n}\n")[0]
        self.assertIn("nv_copy_nv_struct_Bag(", body)
        self.assertIn("nv_release_nv_struct_Bag(", body)

    def test_a_key_must_be_a_value_with_a_hash(self):
        cases = {
            "f64": "edition 2;\n\nfn main() {\n"
                   "    let m: HashMap(f64, i64) = map_new()\n"
                   "    print(map_len(m))\n}\n",
            "struct": "edition 2;\n\nstruct Point {\n    x: i64,\n    y: i64\n}\n\n"
                      "fn main() {\n"
                      "    let m: HashMap(Point, i64) = map_new()\n"
                      "    print(map_len(m))\n}\n",
        }
        for name, source in cases.items():
            with self.subTest(key=name):
                error = error_of(source)
                self.assertEqual(error.code, "E0447")
                self.assertIn("cannot be hashed", error.message)
        error = error_of("edition 2;\n\nfn main() {\n"
                         "    let s: HashSet(String) = set_new()\n"
                         "    set_add(s, \"x\")\n"
                         "    print(set_len(s))\n}\n")
        # `str` is the hashable spelling of text; a `String` set wants a String.
        self.assertEqual(error.code, "E0327")

    def test_the_types_must_be_written_out(self):
        for name in ("map_new", "set_new"):
            with self.subTest(call=name):
                error = error_of("edition 2;\n\nfn main() {\n"
                                 f"    let x = {name}()\n"
                                 "    print(1)\n}\n")
                self.assertEqual(error.code, "E0448")
                self.assertIn("needs to know the type", error.message)

    def test_a_collection_is_not_a_value_or_an_element(self):
        cases = {
            "Vec(HashMap(i64, i64))": "edition 2;\n\nfn main() {\n"
                                      "    let tables: Vec(HashMap(i64, i64)) = vec_new()\n"
                                      "    print(vec_len(tables))\n}\n",
            "HashMap(str, [i64])": "edition 2;\n\nfn main() {\n"
                                   "    let m: HashMap(str, [i64]) = map_new()\n"
                                   "    print(map_len(m))\n}\n",
            "HashMap(str, HashMap(i64, i64))": "edition 2;\n\nfn main() {\n"
                                               "    let m: HashMap(str, HashMap(i64, i64))"
                                               " = map_new()\n"
                                               "    print(map_len(m))\n}\n",
        }
        for name, source in cases.items():
            with self.subTest(nested=name):
                error = error_of(source)
                self.assertEqual(error.code, "E0206")
                self.assertIn("nested collections", error.hint)

    def test_a_mutating_call_needs_a_writable_target(self):
        for call, head in (("map_set(m, 1, 2)", "fn add(m: HashMap(i64, i64))"),
                           ("map_clear(m)", "fn add(m: HashMap(i64, i64))"),
                           ("set_add(s, 1)", "fn add(s: HashSet(i64))")):
            with self.subTest(call=call):
                source = ("edition 2;\n\n" + head + " {\n"
                          f"    {call}\n}}\n\n"
                          "fn main() {\n"
                          "    let m: HashMap(i64, i64) = map_new()\n"
                          "    let s: HashSet(i64) = set_new()\n"
                          "    print(map_len(m) + set_len(s))\n}\n")
                error = error_of(source)
                self.assertEqual(error.code, "E0444")
                self.assertIn("must be a writable local", error.message)

    def test_a_map_is_moved_not_copied(self):
        error = error_of("edition 2;\n\nfn main() {\n"
                         "    let a: HashMap(i64, i64) = map_new()\n"
                         "    let b: HashMap(i64, i64) = a\n"
                         "    print(map_len(a) + map_len(b))\n}\n")
        self.assertEqual(error.code, "E0445")
        error = error_of("edition 2;\n\nfn take(m: HashMap(i64, i64)) "
                         "-> HashMap(i64, i64) {\n    return m\n}\n\n"
                         "fn main() {\n"
                         "    let m: HashMap(i64, i64) = map_new()\n"
                         "    print(map_len(take(m)))\n}\n")
        self.assertEqual(error.code, "E0446")

    def test_a_map_loop_needs_a_name(self):
        error = error_of("edition 2;\n\nfn build() -> HashMap(i64, i64) {\n"
                         "    let m: HashMap(i64, i64) = map_new()\n"
                         "    return m\n}\n\n"
                         "fn main() {\n"
                         "    for key in build() { print(key) }\n}\n")
        self.assertEqual(error.code, "E0350")
        self.assertIn("by name", error.message)

    def test_a_map_and_a_set_are_not_printable(self):
        for source in ("edition 2;\n\nfn main() {\n"
                       "    let m: HashMap(i64, i64) = map_new()\n"
                       "    print(m)\n}\n",
                       "edition 2;\n\nfn main() {\n"
                       "    let s: HashSet(i64) = set_new()\n"
                       "    print(s)\n}\n"):
            with self.subTest(program=source.splitlines()[-2].strip()):
                self.assertEqual(error_of(source).code, "E0323")

    def test_map_get_is_total_and_returns_an_option(self):
        c = frontend("edition 2;\n\nfn main() {\n"
                     "    let m: HashMap(i64, i64) = map_new()\n"
                     "    match map_get(m, 1) {\n"
                     "        Option(i64)::Some(value) => { print(value) }\n"
                     "        Option(i64)::None => { print(-1) }\n"
                     "    }\n}\n")
        self.assertIn("nv_enum_Option_i64", c)


@unittest.skipUnless(CC is not None, "a C compiler is required")
class TableRuntimeTests(unittest.TestCase):
    """Running a table: growth, removal, ownership and iteration."""

    def test_entries_survive_growth_and_removal(self):
        source = ("edition 2;\n\n"
                  "fn fill(m: mut HashMap(str, i64), n: i64) {\n"
                  "    let i: i64 = 0\n"
                  "    while i < n {\n"
                  "        map_set(m, str_from_i64(i), i)\n"
                  "        i = i + 1\n"
                  "    }\n"
                  "}\n\n"
                  "fn main() {\n"
                  "    let m: HashMap(str, i64) = map_new()\n"
                  "    fill(m, 200)\n"
                  "    print(map_len(m))\n"
                  "    let i: i64 = 0\n"
                  "    let gone: i64 = 0\n"
                  "    while i < 100 {\n"
                  "        if map_remove(m, str_from_i64(i)) { gone = gone + 1 }\n"
                  "        i = i + 1\n"
                  "    }\n"
                  "    print(gone)\n"
                  "    print(map_len(m))\n"
                  "    let again: i64 = 0\n"
                  "    while again < 100 {\n"
                  "        map_set(m, str_from_i64(again), again)\n"
                  "        again = again + 1\n"
                  "    }\n"
                  "    print(map_len(m))\n"
                  "    match map_get(m, \"42\") {\n"
                  "        Option(i64)::Some(value) => { print(value) }\n"
                  "        Option(i64)::None => { print(-1) }\n"
                  "    }\n}\n")
        result = compile_and_run(source)
        self.assertEqual(result.stdout, "200\n100\n100\n200\n42\n")
        self.assertEqual(live_arrays(result.stderr), 0)

    def test_a_stored_value_is_a_copy(self):
        source = ("edition 2;\n\nfn main() {\n"
                  "    let m: HashMap(i64, String) = map_new()\n"
                  "    let s: String = string_new()\n"
                  "    string_push(s, \"v\")\n"
                  "    map_set(m, 1, s)\n"
                  "    string_push(s, \"!\")\n"
                  "    print(string_text(s))\n"
                  "    match map_get(m, 1) {\n"
                  "        Option(String)::Some(value) => { print(string_text(value)) }\n"
                  "        Option(String)::None => { print(\"none\") }\n"
                  "    }\n"
                  "    let i: i64 = 0\n"
                  "    while i < 16 {\n"
                  "        let other: String = string_new()\n"
                  "        string_push(other, str_from_i64(i))\n"
                  "        map_set(m, 1, other)\n"
                  "        i = i + 1\n"
                  "    }\n"
                  "    match map_get(m, 1) {\n"
                  "        Option(String)::Some(value) => { print(string_text(value)) }\n"
                  "        Option(String)::None => { print(\"none\") }\n"
                  "    }\n}\n")
        result = compile_and_run(source)
        self.assertEqual(result.stdout, "v!\nv\n15\n")
        self.assertEqual(live_arrays(result.stderr), 0)

    def test_a_string_key_is_copied(self):
        source = ("edition 2;\n\nfn main() {\n"
                  "    let m: HashMap(String, i64) = map_new()\n"
                  "    let key: String = string_new()\n"
                  "    string_push(key, \"k\")\n"
                  "    map_set(m, key, 7)\n"
                  "    string_push(key, \"!\")\n"
                  "    print(map_contains(m, key))\n"
                  "    let probe: String = string_new()\n"
                  "    string_push(probe, \"k\")\n"
                  "    print(map_contains(m, probe))\n"
                  "    match map_get(m, probe) {\n"
                  "        Option(i64)::Some(value) => { print(value) }\n"
                  "        Option(i64)::None => { print(-1) }\n"
                  "    }\n}\n")
        result = compile_and_run(source)
        self.assertEqual(result.stdout, "false\ntrue\n7\n")
        self.assertEqual(live_arrays(result.stderr), 0)

    def test_iteration_visits_every_entry_once(self):
        source = ("edition 2;\n\nfn main() {\n"
                  "    let seen: HashSet(i64) = set_new()\n"
                  "    let i: i64 = 0\n"
                  "    while i < 64 {\n"
                  "        set_add(seen, i)\n"
                  "        i = i + 1\n"
                  "    }\n"
                  "    let count: i64 = 0\n"
                  "    let total: i64 = 0\n"
                  "    for x in seen {\n"
                  "        count = count + 1\n"
                  "        total = total + x\n"
                  "    }\n"
                  "    print(count)\n"
                  "    print(total)\n"
                  "    let m: HashMap(i64, i64) = map_new()\n"
                  "    let j: i64 = 0\n"
                  "    while j < 16 {\n"
                  "        map_set(m, j, j * 2)\n"
                  "        j = j + 1\n"
                  "    }\n"
                  "    let keys: i64 = 0\n"
                  "    for key in m { keys = keys + key }\n"
                  "    print(keys)\n}\n")
        result = compile_and_run(source)
        self.assertEqual(result.stdout, "64\n2016\n120\n")
        self.assertEqual(live_arrays(result.stderr), 0)

    def test_a_map_of_structs_copies_in_and_out(self):
        source = ("edition 2;\n\n" + BAG +
                  "\nfn main() {\n"
                  "    let m: HashMap(str, Bag) = map_new()\n"
                  "    let xs: Vec(i64) = vec_new()\n"
                  "    vec_push(xs, 3)\n"
                  "    let s: String = string_new()\n"
                  "    string_push(s, \"b\")\n"
                  "    map_set(m, \"one\", Bag(items: xs, text: s))\n"
                  "    vec_push(xs, 4)\n"
                  "    string_push(s, \"!\")\n"
                  "    match map_get(m, \"one\") {\n"
                  "        Option(Bag)::Some(bag) => {\n"
                  "            print(vec_len(bag.items))\n"
                  "            print(string_text(bag.text))\n"
                  "        }\n"
                  "        Option(Bag)::None => { print(-1) }\n"
                  "    }\n"
                  "    print(vec_len(xs))\n"
                  "    print(string_text(s))\n"
                  "    map_clear(m)\n"
                  "    print(map_len(m))\n}\n")
        result = compile_and_run(source)
        self.assertEqual(result.stdout, "1\nb\n2\nb!\n0\n")
        self.assertEqual(live_arrays(result.stderr), 0)

    def test_a_struct_field_may_hold_a_map(self):
        source = ("edition 2;\n\n"
                  "struct Registry {\n"
                  "    counts: HashMap(str, i64),\n"
                  "    tags: HashSet(i64)\n"
                  "}\n\n"
                  "fn main() {\n"
                  "    let counts: HashMap(str, i64) = map_new()\n"
                  "    map_set(counts, \"a\", 1)\n"
                  "    let tags: HashSet(i64) = set_new()\n"
                  "    set_add(tags, 2)\n"
                  "    let registry: Registry = Registry(counts: counts, tags: tags)\n"
                  "    let copy: Registry = Registry(counts: registry.counts, tags: registry.tags)\n"
                  "    print(map_len(registry.counts))\n"
                  "    print(set_len(copy.tags))\n"
                  "    print(map_len(counts))\n"
                  "    print(set_len(tags))\n}\n")
        result = compile_and_run(source)
        self.assertEqual(result.stdout, "1\n1\n1\n1\n")
        self.assertEqual(live_arrays(result.stderr), 0)

    def test_a_returned_map_keeps_its_keys(self):
        """Returning a map keeps the arena text of its keys alive too."""
        source = ("edition 2;\n\n"
                  "fn build(n: i64) -> HashMap(str, i64) {\n"
                  "    let m: HashMap(str, i64) = map_new()\n"
                  "    let i: i64 = 0\n"
                  "    while i < n {\n"
                  "        map_set(m, str_from_i64(i), i)\n"
                  "        i = i + 1\n"
                  "    }\n"
                  "    return m\n}\n\n"
                  "fn main() {\n"
                  "    let m: HashMap(str, i64) = build(32)\n"
                  "    let total: i64 = 0\n"
                  "    for key in m { total = total + bytes_len(key) }\n"
                  "    print(map_len(m))\n"
                  "    print(total)\n"
                  "    print(map_contains(m, \"17\"))\n}\n")
        result = compile_and_run(source)
        self.assertEqual(result.stdout, "32\n54\ntrue\n")
        self.assertEqual(live_arrays(result.stderr), 0)

    def test_a_set_deduplicates_owning_elements(self):
        source = ("edition 2;\n\nfn main() {\n"
                  "    let tags: HashSet(String) = set_new()\n"
                  "    let i: i64 = 0\n"
                  "    while i < 8 {\n"
                  "        let tag: String = string_new()\n"
                  "        string_push(tag, \"x\")\n"
                  "        set_add(tags, tag)\n"
                  "        i = i + 1\n"
                  "    }\n"
                  "    print(set_len(tags))\n"
                  "    let removed: i64 = 0\n"
                  "    let j: i64 = 0\n"
                  "    while j < 8 {\n"
                  "        let probe: String = string_new()\n"
                  "        string_push(probe, \"x\")\n"
                  "        if set_remove(tags, probe) { removed = removed + 1 }\n"
                  "        j = j + 1\n"
                  "    }\n"
                  "    print(removed)\n"
                  "    print(set_len(tags))\n}\n")
        result = compile_and_run(source)
        self.assertEqual(result.stdout, "1\n1\n0\n")
        self.assertEqual(live_arrays(result.stderr), 0)

    def test_tables_under_the_sanitizers(self):
        """A table that rehashes, removes and clears, with AddressSanitizer on."""
        source = ("edition 2;\n\n" + BAG + "\n" + SLOT +
                  "\nfn make(n: i64) -> Slot {\n"
                  "    let xs: Vec(i64) = vec_new()\n"
                  "    vec_push(xs, n)\n"
                  "    let s: String = string_new()\n"
                  "    string_push(s, \"b\")\n"
                  "    return Slot::Full(Bag(items: xs, text: s))\n}\n\n"
                  "fn main() {\n"
                  "    let slots: HashMap(str, Slot) = map_new()\n"
                  "    let i: i64 = 0\n"
                  "    while i < 200 {\n"
                  "        map_set(slots, str_from_i64(i), make(i))\n"
                  "        i = i + 1\n"
                  "    }\n"
                  "    print(map_len(slots))\n"
                  "    let total: i64 = 0\n"
                  "    for key in slots {\n"
                  "        match map_get(slots, key) {\n"
                  "            Option(Slot)::Some(slot) => {\n"
                  "                match slot {\n"
                  "                    Slot::Full(bag) => { total = total + vec_len(bag.items) }\n"
                  "                    Slot::Empty => { total = total + 0 }\n"
                  "                }\n"
                  "            }\n"
                  "            Option(Slot)::None => { total = total + 0 }\n"
                  "        }\n"
                  "    }\n"
                  "    print(total)\n"
                  "    let j: i64 = 0\n"
                  "    let gone: i64 = 0\n"
                  "    while j < 100 {\n"
                  "        if map_remove(slots, str_from_i64(j)) { gone = gone + 1 }\n"
                  "        j = j + 1\n"
                  "    }\n"
                  "    print(gone)\n"
                  "    print(map_len(slots))\n"
                  "    map_clear(slots)\n"
                  "    print(map_len(slots))\n}\n")
        result = compile_and_run(source, sanitize=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "200\n200\n100\n100\n0\n")
        self.assertEqual(live_arrays(result.stderr), 0)


if __name__ == "__main__":
    unittest.main()
