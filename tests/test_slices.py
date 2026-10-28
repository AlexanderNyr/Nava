"""Edition 2, part four: arrays of structs and slices (INDEPENDENCE.md N10d).

Two additions, and each has a property that only a running program can show:

1. **An array of structs copies values.** The element is a struct, so what comes
   out of `points[0]` must be a copy: writing a field of that copy must not
   touch the array, and `points[0] = p` must replace the element.
2. **A slice is a view, and the bounds are checked.** `a[i..j]` does not copy:
   it is a borrowed handle into the array's storage, it is read-only, it is
   never freed by the block that binds it, and `0 <= i <= j <= len` is enforced
   at run time with two distinct messages (a bad start, and a bad end) so a
   failed slice cannot be mistaken for a failed index.
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

from nava_lang.cli import compile_source, frontend  # noqa: E402
from nava_lang.model import NavaError  # noqa: E402

CC = shutil.which("cc")

POINT = """edition 2;

struct Point { x: i64, y: i64 }

"""


def run_nava(text: str) -> subprocess.CompletedProcess:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "program.nv"
        path.write_text(text, encoding="utf-8")
        return subprocess.run([sys.executable, str(ROOT / "nava"), "run", str(path)],
                              capture_output=True, text=True, timeout=120)


class SliceFrontendTests(unittest.TestCase):
    def assert_code(self, text: str, code: str, message: str | None = None) -> NavaError:
        with self.assertRaises(NavaError) as caught:
            compile_source(text)
        self.assertEqual(caught.exception.code, code, caught.exception.message)
        if message is not None:
            self.assertIn(message, caught.exception.message)
        return caught.exception

    def test_a_slice_needs_edition_two(self):
        self.assert_code("fn main() {\n    let a: [i64] = [1, 2]\n    let s = a[0..1]\n"
                         "    print(len(s))\n}\n", "E0372", "slice")

    def test_the_bounds_must_be_i64(self):
        text = ("edition 2;\nfn main() {\n    let a: [i64] = [1, 2]\n"
                "    let s = a[true..1]\n    print(len(s))\n}\n")
        self.assert_code(text, "E0420", "start of a slice must be i64")
        text = ("edition 2;\nfn main() {\n    let a: [i64] = [1, 2]\n"
                "    let s = a[0..1.5]\n    print(len(s))\n}\n")
        self.assert_code(text, "E0420", "end of a slice must be i64")

    def test_only_an_array_can_be_sliced(self):
        text = ("edition 2;\nstruct P { x: i64 }\nfn main() {\n    let p = P(x: 1)\n"
                "    let s = p[0..1]\n    print(len(s))\n}\n")
        self.assert_code(text, "E0421", "only an array can be sliced")

    def test_a_slice_is_read_only(self):
        text = ("edition 2;\nfn main() {\n    let a: [i64] = [1, 2, 3]\n"
                "    let s = a[0..2]\n    s[0] = 9\n    print(s[0])\n}\n")
        self.assert_code(text, "E0422", "read-only")

    def test_a_slice_cannot_become_a_mutable_borrow(self):
        text = POINT + """fn move_first(points: mut [Point]) {
    points[0] = Point(x: 1, y: 2)
}

fn main() {
    let points: [Point] = [Point(x: 0, y: 0)]
    let view = points[0..1]
    move_first(view)
    print(points[0].x)
}
"""
        self.assert_code(text, "E0423", "borrow cannot be handed on")

    def test_an_array_of_enums_is_a_value_array(self):
        # N11a: an enum is a value with no owner, so an array of them is an
        # array of plain values — the array is the only owner.
        text = ("edition 2;\nenum Shape { Circle(i64), Dot }\nfn main() {\n"
                "    let shapes: [Shape] = [Shape::Dot]\n    print(len(shapes))\n}\n")
        self.assertTrue(compile_source(text).code)
        # `f64` and `void` stay shape errors: no declared name could make them
        # structs, so they are refused before a symbol table exists.
        self.assert_code("fn main() { let a: [f64] = [1.5] }", "E0206", "got 'f64'")
        self.assert_code("fn main() { let a: [void] = [] }", "E0206", "got 'void'")
        self.assert_code("fn main() { let a: [[i64]] = [] }", "E0206", "another array")

    def test_an_unknown_element_name_is_reported_as_an_unknown_type(self):
        text = ("edition 2;\nfn main() {\n    let a: [Missing] = []\n    print(len(a))\n}\n")
        self.assert_code(text, "E0203", "unknown type")

    def test_struct_arrays_keep_the_array_rules(self):
        # An array is still not copyable, not returnable, not printable.
        self.assert_code(POINT + """fn main() {
    let points: [Point] = [Point(x: 1, y: 2)]
    let copy = points
    print(copy[0].x)
}
""", "E0331")
        self.assert_code(POINT + """fn make() -> [Point] {
    return [Point(x: 1, y: 2)]
}

fn main() {
    let a = make()
    print(a[0].x)
}
""", "E0330", "cannot return an array")

    def test_a_slice_composes(self):
        text = ("edition 2;\nfn main() {\n    let a: [i64] = [1, 2, 3, 4]\n"
                "    let inner = a[1..3]\n    let tail = inner[1..2]\n"
                "    print(tail[0])\n}\n")
        self.assertTrue(compile_source(text).code)


@unittest.skipUnless(CC, "a C compiler is required to observe element copies")
class SliceRuntimeTests(unittest.TestCase):
    def test_a_slice_is_a_view_and_not_a_copy(self):
        result = run_nava(POINT + """fn main() {
    let points: [Point] = [Point(x: 1, y: 2), Point(x: 3, y: 4)]
    let view = points[0..2]
    print(len(view))
    print(view[1].y)
    print(len(points))
}
""")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "2\n4\n2\n")

    def test_a_struct_element_read_is_a_copy(self):
        result = run_nava(POINT + """fn main() {
    let points: [Point] = [Point(x: 1, y: 2)]
    let p = points[0]
    p.x = 99
    print(p.x)
    print(points[0].x)
}
""")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "99\n1\n", "the element must not be aliased")

    def test_an_element_write_replaces_the_whole_struct(self):
        result = run_nava(POINT + """fn main() {
    let points: [Point] = [Point(x: 1, y: 2), Point(x: 3, y: 4)]
    let replacement = Point(x: 7, y: 8)
    points[1] = replacement
    print(points[1].x)
    print(points[1].y)
}
""")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "7\n8\n")

    def test_zeroed_struct_arrays_are_all_zero(self):
        result = run_nava(POINT + """fn main() {
    let points: [Point] = array_zeros(3)
    print(len(points))
    print(points[2].x)
    print(points[2].y)
}
""")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "3\n0\n0\n")

    def test_a_bad_slice_start_is_reported_as_a_start(self):
        for body, message in (
            ("let a: [i64] = [1, 2, 3]; let s = a[0 - 1..2]; print(len(s))",
             "slice start -1 is out of range for length 3"),
            ("let a: [i64] = [1, 2, 3]; let s = a[4..4]; print(len(s))",
             "slice start 4 is out of range for length 3"),
        ):
            with self.subTest(body=body):
                result = run_nava(f"edition 2;\nfn main() {{ {body} }}")
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, "", "a failed slice must not print a value")
                self.assertIn("nava runtime error:", result.stderr)
                self.assertIn(message, result.stderr)

    def test_a_bad_slice_end_is_reported_as_an_end(self):
        for body, message in (
            ("let a: [i64] = [1, 2, 3]; let s = a[2..1]; print(len(s))",
             "slice end 1 is out of range for start 2 and length 3"),
            ("let a: [i64] = [1, 2, 3]; let s = a[0..4]; print(len(s))",
             "slice end 4 is out of range for start 0 and length 3"),
            ("let a: [Point] = []; let s = a[0..1]; print(len(s))",
             "slice end 1 is out of range for start 0 and length 0"),
        ):
            with self.subTest(body=body):
                source = f"edition 2;\nstruct Point {{ x: i64, y: i64 }}\nfn main() {{ {body} }}"
                result = run_nava(source)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, "")
                self.assertIn(message, result.stderr)

    def test_the_bounds_are_evaluated_left_to_right(self):
        # Nava documents left-to-right evaluation; a slice must not depend on the
        # order a C compiler happens to pick for two arguments.
        result = run_nava("""edition 2;

fn mark(label: i64) -> i64 {
    print(label)
    return label
}

fn main() {
    let a: [i64] = [1, 2, 3]
    let s = a[mark(1)..mark(2)]
    print(len(s))
}
""")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "1\n2\n1\n")

    def test_a_view_sees_a_write_through_the_owning_array(self):
        result = run_nava(POINT + """fn main() {
    let points: [Point] = [Point(x: 1, y: 2), Point(x: 3, y: 4), Point(x: 5, y: 6)]
    let view = points[0..3]
    let changed = points[1]
    changed.x = 99
    points[1] = changed
    print(view[1].x)
}
""")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "99\n",
                         "a slice is a view: it must see the owner's write")

    def test_a_for_loop_can_iterate_a_slice(self):
        result = run_nava(POINT + """fn mark(label: i64) -> i64 {
    print(label)
    return label
}

fn main() {
    let points: [Point] = [Point(x: 1, y: 2), Point(x: 3, y: 4), Point(x: 5, y: 6)]
    let sum = 0
    for point in points[mark(1)..mark(3)] {
        sum = sum + point.x
    }
    print(sum)
}
""")
        self.assertEqual(result.returncode, 0, result.stderr)
        # The bounds are evaluated once, before the first turn, so `1` and `3`
        # are printed once each; the slice is points[1..3], so the sum is 3 + 5.
        self.assertEqual(result.stdout, "1\n3\n8\n")

    def test_a_slice_does_not_release_the_storage_it_views(self):
        # The instrumentation counts arrays still owned at exit. A borrowed view
        # freed with its block would either crash or decrement the counter; if
        # the array itself were leaked, the count would be 1.
        source = """edition 2;

fn main() {
    let a: [i64] = [1, 2, 3]
    let s = a[1..3]
    print(s[0])
    print(len(a))
}
"""
        with tempfile.TemporaryDirectory() as directory:
            c_file = Path(directory) / "program.c"
            binary = Path(directory) / "program"
            c_file.write_text(frontend(source), encoding="utf-8")
            build = subprocess.run([CC, "-std=c11", "-O1", "-DNAVA_TRACK_ALLOCS",
                                    str(c_file), "-o", str(binary)],
                                   capture_output=True, text=True, timeout=300)
            self.assertEqual(build.returncode, 0, build.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "2\n3\n")
        self.assertIn("nava: live arrays at exit: 0", result.stderr)

if __name__ == "__main__":
    unittest.main()
