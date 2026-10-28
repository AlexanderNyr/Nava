"""Edition 2, part five: `defer`, labelled loops and `pub` (INDEPENDENCE.md N10e).

Three constructs that all decide *where control leaves a scope*, so each claim
is tested by watching a program, not by reading the generated C:

1. **A deferred block runs on every way out of its block** — normal completion,
   `return`, and a `break`/`continue` that leaves it — in reverse declaration
   order, and before the block releases its arrays.
2. **A label reaches the loop it names**, from any depth, and stops there: the
   loops between the jump and the target are left, not continued.
3. **`pub` decides what a module exports**, and only for a module that opted into
   edition 2: an edition-1 module keeps exporting everything it always did.
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

from nava_lang.cli import compile_source  # noqa: E402
from nava_lang.model import NavaError  # noqa: E402

CC = shutil.which("cc")
MODULES = ROOT / "tests/fixtures/modules"


def run_nava(text: str, root: str | None = None) -> subprocess.CompletedProcess:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "program.nv"
        path.write_text(text, encoding="utf-8")
        command = [sys.executable, str(ROOT / "nava"), "run", str(path)]
        if root is not None:
            command += ["--root", root]
        return subprocess.run(command, capture_output=True, text=True, timeout=120)


class DeferFrontendTests(unittest.TestCase):
    def assert_code(self, text: str, code: str, message: str | None = None) -> NavaError:
        with self.assertRaises(NavaError) as caught:
            compile_source(text)
        self.assertEqual(caught.exception.code, code, caught.exception.message)
        if message is not None:
            self.assertIn(message, caught.exception.message)
        return caught.exception

    def test_a_defer_needs_edition_two(self):
        self.assert_code("fn main() { defer { print(1) } }", "E0372", "deferred")

    def test_a_deferred_block_cannot_leave_itself(self):
        self.assert_code("edition 2;\nfn f() -> i64 {\n    defer { return 0 }\n"
                         "    return 1\n}\nfn main() { print(f()) }\n",
                         "E0430", "cannot return")
        self.assert_code("edition 2;\nfn main() {\n    while true {\n"
                         "        defer { break }\n    }\n}\n",
                         "E0430", "cannot `break`")
        self.assert_code("edition 2;\nfn main() {\n    while true {\n"
                         "        defer { continue }\n    }\n}\n",
                         "E0430", "cannot `continue`")
        self.assert_code("edition 2;\nfn f() -> Result(i64, str) {\n"
                         "    return Result(i64, str)::Ok(1)\n}\nfn main() {\n"
                         "    defer { let v = f()? }\n}\n".replace("f()?", "f()?"),
                         "E0430")

    def test_a_label_must_name_a_loop(self):
        self.assert_code("edition 2;\nfn main() {\n    outer: let n = 1\n    print(n)\n}\n",
                         "E0432", "loop")

    def test_a_jump_must_name_a_loop_it_can_reach(self):
        self.assert_code("edition 2;\nfn main() {\n    for i in 0..3 {\n"
                         "        break outer\n    }\n}\n",
                         "E0431", "no enclosing loop carries the label")

    def test_labels_need_edition_two(self):
        self.assert_code("fn main() {\n    outer: for i in 0..3 {\n"
                         "        break outer\n    }\n}\n", "E0372", "label")

    def test_only_a_function_can_be_public(self):
        self.assert_code("edition 2;\npub struct Point { x: i64 }\nfn main() { print(1) }\n",
                         "E0434", "only a function")

    def test_a_public_function_needs_edition_two(self):
        self.assert_code("pub fn step(n: i64) -> i64 { return n }\n"
                         "fn main() { print(step(1)) }\n", "E0372", "public")

    def test_a_deferred_block_is_a_block(self):
        self.assert_code("edition 2;\nfn main() {\n    defer print(1)\n}\n",
                         "E0201", "expected '{' after 'defer'")


@unittest.skipUnless(CC, "a C compiler is required to observe deferred execution")
class DeferRuntimeTests(unittest.TestCase):
    def test_defers_run_last_first_and_before_the_release(self):
        result = run_nava("""edition 2;

fn main() {
    let values: [i64] = [1, 2, 3]
    defer { print(len(values)) }
    defer { print(values[2]) }
    print(0)
}
""")
        self.assertEqual(result.returncode, 0, result.stderr)
        # Reverse order, and the array is still readable inside the defers.
        self.assertEqual(result.stdout, "0\n3\n3\n")

    def test_a_defer_runs_on_return(self):
        result = run_nava("""edition 2;

fn finish(n: i64) -> i64 {
    defer { print(0 - 1) }
    if n > 0 { return n }
    return 0
}

fn main() {
    print(finish(7))
    print(finish(0 - 1))
}
""")
        self.assertEqual(result.returncode, 0, result.stderr)
        # The value is computed first, then the block runs, then the caller sees
        # the value: `-1`, `7`, `-1`, `0`.
        self.assertEqual(result.stdout, "-1\n7\n-1\n0\n")

    def test_a_defer_runs_on_break_and_continue(self):
        result = run_nava("""edition 2;

fn main() {
    let i = 0
    while i < 3 {
        defer { print(100 + i) }
        if i == 1 {
            i = i + 1
            continue
        }
        if i == 2 { break }
        print(i)
        i = i + 1
    }
}
""")
        self.assertEqual(result.returncode, 0, result.stderr)
        # The deferred block reads `i` when it runs, not when it was registered:
        # `0` is printed by the body of the first turn, whose defer then sees
        # `i == 1`; the `continue` turn raises `i` to 2 and prints 102 on the way
        # out, and the `break` turn prints 102 too because `i` is still 2.
        self.assertEqual(result.stdout, "0\n101\n102\n102\n")

    def test_a_defer_runs_per_iteration(self):
        result = run_nava("""edition 2;

fn main() {
    for i in 0..3 {
        defer { print(i) }
    }
    print(9)
}
""")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "0\n1\n2\n9\n")

    def test_a_deferred_block_can_declare_and_release_its_own_array(self):
        result = run_nava("""edition 2;

fn main() {
    defer {
        let scratch: [i64] = [4, 5]
        print(len(scratch))
    }
    print(1)
}
""")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "1\n2\n")


@unittest.skipUnless(CC, "a C compiler is required to observe labelled jumps")
class LabelledLoopRuntimeTests(unittest.TestCase):
    def test_a_label_leaves_every_loop_above_the_target(self):
        result = run_nava("""edition 2;

fn main() {
    outer: for i in 0..3 {
        for j in 0..3 {
            if j == 1 { continue outer }
            if i == 1 { break outer }
            print(i * 10 + j)
        }
    }
    print(9)
}
""")
        self.assertEqual(result.returncode, 0, result.stderr)
        # i = 0 prints 0 and then restarts the outer loop; i = 1 breaks out.
        self.assertEqual(result.stdout, "0\n9\n")

    def test_a_labelled_continue_runs_the_increment(self):
        result = run_nava("""edition 2;

fn main() {
    let total = 0
    outer: for i in 0..4 {
        if i == 2 { continue outer }
        total = total + i
    }
    print(total)
}
""")
        self.assertEqual(result.returncode, 0, result.stderr)
        # 0 + 1 + 3; a `continue` that skipped the increment would hang.
        self.assertEqual(result.stdout, "4\n")

    def test_a_label_works_on_a_while_loop_and_over_an_array(self):
        result = run_nava("""edition 2;

fn main() {
    let values: [i64] = [1, 2, 3, 4]
    let i = 0
    scan: while i < 4 {
        for value in values {
            if value == 3 { i = 4 }
            if i == 4 { break scan }
            print(value)
        }
    }
    print(7)
}
""")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "1\n2\n7\n")

    def test_a_nested_loop_may_reuse_a_label_and_the_innermost_wins(self):
        result = run_nava("""edition 2;

fn main() {
    outer: for a in 0..2 {
        inner: for b in 0..3 {
            if b == 1 { break inner }
            print(a * 10 + b)
        }
    }
    outer: for a in 0..2 {
        outer: for b in 0..3 {
            if b == 1 { break outer }
            print(100 + a * 10 + b)
        }
    }
}
""")
        self.assertEqual(result.returncode, 0, result.stderr)
        # The first pair of loops breaks the inner one each turn (0, 10), the
        # second pair reuses `outer` and the innermost loop with that name wins.
        self.assertEqual(result.stdout, "0\n10\n100\n110\n")

    def test_a_labelled_jump_releases_the_arrays_it_leaves(self):
        result = run_nava("""edition 2;

fn main() {
    outer: for i in 0..2 {
        let scratch: [i64] = [i, i, i]
        for j in 0..2 {
            let inner: [i64] = [j]
            if j == 1 { continue outer }
            print(len(scratch) + len(inner))
        }
    }
}
""")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "4\n4\n")


class PublicFrontendTests(unittest.TestCase):
    def nava(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(ROOT / "nava"), *args],
                              capture_output=True, text=True, timeout=120)

    def test_a_private_function_cannot_be_called_from_another_module(self):
        result = self.nava("check", str(MODULES / "private_module/main.nv"))
        self.assertEqual(result.returncode, 2)
        self.assertIn("E0433", result.stderr)
        self.assertIn("tools::hidden is not public", result.stderr)

    def test_a_public_function_can_use_a_private_one(self):
        result = self.nava("run", "--isolation", "off",
                           str(MODULES / "public_module/main.nv"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "8\n22\n")

    def test_an_edition_one_module_still_exports_everything(self):
        # The multi-file fixture is edition 1: it declares no `pub` and every
        # function stays visible, which is the edition-1 contract.
        result = self.nava("run", "--isolation", "off",
                           str(MODULES / "multi_file/main.nv"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("22", result.stdout)


if __name__ == "__main__":
    unittest.main()
