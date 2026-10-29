"""The `collections` module: the layer above one element at a time (PLAN.md N12h).

Every public name of this module is exercised by the standard library's own
self-test, because none of them needs a file, a command line or a grant — which
makes this file the place for the properties a single call cannot show:

* **filling a container appends, it does not replace.** `keys_of` and
  `elements_of` write into a vector the caller owns, so what the caller put there
  first has to survive; a module that cleared it would be impossible to use in a
  loop.
* **a scratch collection belongs to the caller.** `count_distinct` leaves the
  values in the set it was given, so a second call answers the same number
  rather than twice the count — the set is a parameter, not a hidden cache.
* **the arithmetic is checked.** `sum_of` adds with the checked `+`, so a sum
  that does not fit stops the program (`i64 overflow in +`) instead of wrapping
  round; that is the difference between a sum and a guess.
* **the iteration order is the container's, and it is stable.** A table and a set
  are iterated in their own order, which is the same order twice in one build for
  the same insertions; nothing here claims it is sorted, and the tests assert
  stability rather than a particular sequence.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "tests"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import grants  # noqa: E402

CC = shutil.which("cc")


def program(body: str) -> str:
    """A small edition-2 program that imports `collections`."""
    return ("edition 2;\n\nimport collections;\n\nfn main() {\n"
            + "".join(f"    {line}\n" for line in body.strip().splitlines())
            + "}\n")


def run(body: str) -> subprocess.CompletedProcess:
    return grants.run(program(body))


class ReadingTests(unittest.TestCase):
    """The pure four: sum, count, containment."""

    def test_the_sum_is_checked(self):
        result = run("""
            let values: Vec(i64) = vec_new()
            vec_push(values, 9223372036854775807)
            vec_push(values, 1)
            print(collections::sum_of(values))
            print(1)
        """)
        self.assertEqual(result.returncode, 1)
        self.assertIn("i64 overflow in +", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_counting_and_containment_agree_with_each_other(self):
        result = run("""
            let values: Vec(i64) = vec_new()
            vec_push(values, 1)
            vec_push(values, 2)
            vec_push(values, 1)
            print(collections::count_of(values, 1))
            let both: [i64] = [1, 2]
            let one: [i64] = [1]
            let missing: [i64] = [1, 9]
            print(collections::contains_all(values, both))
            print(collections::contains_all(values, one))
            print(collections::contains_all(values, missing))
            print(collections::has_any(values, missing))
        """)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(),
                         ["2", "true", "true", "false", "true"])

    def test_an_empty_vector_and_an_empty_wanted_list(self):
        result = run("""
            let empty: Vec(i64) = vec_new()
            let none: [i64] = []
            print(collections::sum_of(empty))
            print(collections::count_of(empty, 0))
            print(collections::contains_all(empty, none))
            print(collections::has_any(empty, none))
            let values: Vec(i64) = vec_new()
            vec_push(values, 1)
            print(collections::contains_all(values, none))
            print(collections::has_any(values, none))
        """)
        self.assertEqual(result.returncode, 0, result.stderr)
        # "every one of nothing is present" is true; "any of nothing" is not.
        self.assertEqual(result.stdout.splitlines(),
                         ["0", "0", "true", "false", "true", "false"])


class FillingTests(unittest.TestCase):
    """The three that write into a container the caller owns."""

    def test_keys_are_appended_not_replaced(self):
        result = run("""
            let table: HashMap(str, i64) = map_new()
            map_set(table, "a", 1)
            map_set(table, "b", 2)
            let keys: Vec(str) = vec_new()
            vec_push(keys, "kept")
            collections::keys_of(table, keys)
            print(vec_len(keys))
            print(vec_get(keys, 0))
        """)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["3", "kept"])

    def test_elements_are_appended_after_removals(self):
        result = run("""
            let items: HashSet(i64) = set_new()
            set_add(items, 4)
            set_add(items, 5)
            set_remove(items, 4)
            let listed: Vec(i64) = vec_new()
            collections::elements_of(items, listed)
            print(vec_len(listed))
            print(vec_get(listed, 0))
        """)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["1", "5"])

    def test_the_scratch_set_keeps_its_values(self):
        """A second call must answer the same number, not twice the count."""
        result = run("""
            let values: Vec(i64) = vec_new()
            vec_push(values, 3)
            vec_push(values, 1)
            vec_push(values, 3)
            let seen: HashSet(i64) = set_new()
            print(collections::count_distinct(values, seen))
            print(collections::count_distinct(values, seen))
            print(set_len(seen))
        """)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["2", "2", "2"])

    def test_a_set_that_already_held_values_counts_them(self):
        result = run("""
            let values: Vec(i64) = vec_new()
            vec_push(values, 7)
            let seen: HashSet(i64) = set_new()
            set_add(seen, 7)
            set_add(seen, 8)
            print(collections::count_distinct(values, seen))
        """)
        self.assertEqual(result.returncode, 0, result.stderr)
        # The contract says `set_len(seen)` after the call, not "how many were new".
        self.assertEqual(result.stdout.splitlines(), ["2"])


class OrderTests(unittest.TestCase):
    """The order is the container's, and the same twice."""

    def test_two_listings_of_one_table_agree(self):
        result = run("""
            let table: HashMap(str, i64) = map_new()
            map_set(table, "alpha", 1)
            map_set(table, "beta", 2)
            map_set(table, "gamma", 3)
            let first: Vec(str) = vec_new()
            collections::keys_of(table, first)
            let second: Vec(str) = vec_new()
            collections::keys_of(table, second)
            print(vec_len(first) == vec_len(second))
            let i = 0
            let same = true
            while i < vec_len(first) {
                if vec_get(first, i) != vec_get(second, i) {
                    same = false
                }
                i = i + 1
            }
            print(same)
        """)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["true", "true"])

    def test_the_same_insertions_give_the_same_order(self):
        """Stability across two separately built tables, in one process."""
        result = run("""
            let one: HashSet(i64) = set_new()
            set_add(one, 5)
            set_add(one, 6)
            set_add(one, 7)
            let two: HashSet(i64) = set_new()
            set_add(two, 5)
            set_add(two, 6)
            set_add(two, 7)
            let first: Vec(i64) = vec_new()
            collections::elements_of(one, first)
            let second: Vec(i64) = vec_new()
            collections::elements_of(two, second)
            let i = 0
            let same = true
            while i < vec_len(first) {
                if vec_get(first, i) != vec_get(second, i) {
                    same = false
                }
                i = i + 1
            }
            print(same)
        """)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["true"])


if __name__ == "__main__":
    unittest.main()
