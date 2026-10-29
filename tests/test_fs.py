"""The `fs` module and the three filesystem builtins behind it (PLAN.md N12f).

`fs` is split along one line, and this file tests both sides of it:

* the **text** half (`basename`, `dirname`, `path_join`, `extension`, `stem`,
  `is_absolute`) is arithmetic on a `str` and is `pure: yes`; the standard
  library's own self-test covers it, and the cases here are the ones that pin the
  edges — a trailing slash, a leading dot, a name made only of a dot.
* the **filesystem** half (`exists`, `size`, `entries`) is a wrapper over the
  builtins `path_exists`, `file_size` and `list_dir`, which are read-grant
  operations. Their tests need a granted directory, which is why they live here
  rather than in the self-test, and why `tests/test_self_test.py` lists them in
  `TESTED_ELSEWHERE`.

Three claims are under test:

1. **The listing is ordered by bytes.** `list_dir` sorts, so two runs on the same
   directory produce the same sequence — an unordered `readdir` is a
   non-determinism a program cannot see until it runs on another machine.
2. **Asking is an effect, and it is gated.** A question about a path outside the
   grants stops the program rather than answering `false`, and a program with no
   grant is refused with the documented message. `exists` is not a way to probe
   the host.
3. **A missing path is an answer, a missing directory is an error.**
   `path_exists` answers `false` — that is what the question means — while
   `file_size` and `list_dir` stop the program, because there is nothing to
   report.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "tests"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import grants  # noqa: E402

#: Grants for the cases that ask about their own directory and nothing else.
READ = {"read": ["."]}
BOTH = {"read": ["."], "write": ["."]}


def program(body: str) -> str:
    """A small edition-2 program that imports `fs`, so a case reads as its body."""
    return ("edition 2;\n\nimport fs;\n\nfn main() {\n"
            + "".join(f"    {line}\n" for line in body.strip().splitlines())
            + "}\n")


class ExistenceTests(unittest.TestCase):
    """`fs::exists` / `path_exists`: a question with two answers, both answers."""

    def test_a_file_is_there_and_a_missing_name_is_not(self):
        result = grants.run(
            program("""
                print(fs::exists("data.txt"))
                print(fs::exists("missing.txt"))
                print(path_exists("data.txt"))
                print(path_exists("missing.txt"))
            """),
            files={"data.txt": "x"},
            capabilities=READ)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["true", "false", "true", "false"])

    def test_a_directory_is_there_too(self):
        result = grants.run(
            program("""
                print(fs::exists("sub"))
                print(fs::exists("sub/inside.txt"))
            """),
            files={"sub/inside.txt": "x"},
            capabilities=READ)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["true", "true"])

    def test_a_path_whose_parent_is_missing_is_simply_not_there(self):
        """The question has an answer; a missing directory is not a crash."""
        result = grants.run(
            program("""
                print(fs::exists("no_dir/child.txt"))
            """),
            capabilities=READ)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["false"])

    def test_a_question_about_a_path_outside_the_grants_is_refused(self):
        """`exists` answers "no", it does not leak "no because you may not look"."""
        result = grants.run(
            program("""
                print(fs::exists("../outside.txt"))
                print(1)
            """),
            capabilities=READ)
        self.assertEqual(result.returncode, 1)
        self.assertIn("path_exists denied: '../outside.txt' is outside every permitted directory",
                      result.stderr)
        self.assertEqual(result.stdout, "")

    def test_a_question_without_a_grant_is_refused(self):
        result = grants.run(
            program("""
                print(fs::exists("data.txt"))
            """),
            files={"data.txt": "x"})
        self.assertEqual(result.returncode, 1)
        self.assertIn("path_exists denied: no permitted directory was configured",
                      result.stderr)


class SizeTests(unittest.TestCase):
    """`fs::size` / `file_size`: bytes, exactly the length `read_file` returns."""

    def test_the_size_is_the_number_of_bytes(self):
        result = grants.run(
            program("""
                print(fs::size("a.txt"))
                print(fs::size("b.txt"))
                print(fs::size("empty.txt"))
                print(bytes_len(read_file("a.txt")))
            """),
            files={"a.txt": "hello", "b.txt": "a" * 1000, "empty.txt": ""},
            capabilities=READ)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["5", "1000", "0", "5"])

    def test_a_missing_file_stops_the_program(self):
        result = grants.run(
            program("""
                print(fs::size("missing.txt"))
                print(1)
            """),
            capabilities=READ)
        self.assertEqual(result.returncode, 1)
        self.assertIn("cannot open 'missing.txt': No such file or directory", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_a_directory_is_not_a_file(self):
        result = grants.run(
            program("""
                print(fs::size("sub"))
            """),
            files={"sub/inside.txt": "x"},
            capabilities=READ)
        self.assertEqual(result.returncode, 1)
        self.assertIn("file_size: 'sub' is not a regular file", result.stderr)


class ListingTests(unittest.TestCase):
    """`fs::entries` / `list_dir`: names, in byte order, appended to a vector."""

    def test_the_names_come_back_in_byte_order(self):
        result = grants.run(
            program("""
                let names: Vec(str) = vec_new()
                fs::entries(".", names)
                print(vec_len(names))
                let i = 0
                while i < vec_len(names) {
                    print(vec_get(names, i))
                    i = i + 1
                }
            """),
            files={"b.txt": "", "a.txt": "", "C.txt": "", "sub/e.txt": ""},
            capabilities=READ)
        self.assertEqual(result.returncode, 0, result.stderr)
        # Byte order: the uppercase name sorts before the lowercase ones, and a
        # directory is an entry like any other.
        self.assertEqual(result.stdout.splitlines(),
                         ["4", "C.txt", "a.txt", "b.txt", "sub"])

    def test_dot_and_dot_dot_are_not_entries(self):
        result = grants.run(
            program("""
                let names: Vec(str) = vec_new()
                fs::entries(".", names)
                let i = 0
                let dots = 0
                while i < vec_len(names) {
                    if vec_get(names, i) == "." || vec_get(names, i) == ".." {
                        dots = dots + 1
                    }
                    i = i + 1
                }
                print(dots)
            """),
            files={"a.txt": ""},
            capabilities=READ)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["0"])

    def test_the_entries_are_appended_to_what_the_vector_held(self):
        result = grants.run(
            program("""
                let names: Vec(str) = vec_new()
                vec_push(names, "kept")
                fs::entries(".", names)
                print(vec_get(names, 0))
                print(vec_len(names))
            """),
            files={"a.txt": ""},
            capabilities=READ)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["kept", "2"])

    def test_two_listings_agree(self):
        """Determinism: the same directory answers the same way twice."""
        result = grants.run(
            program("""
                let first: Vec(str) = vec_new()
                fs::entries(".", first)
                let second: Vec(str) = vec_new()
                fs::entries(".", second)
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
            """),
            files={"x.txt": "", "y.txt": "", "z.txt": "", "d/w.txt": ""},
            capabilities=READ)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["true", "true"])

    def test_a_file_where_a_directory_was_expected_is_reported(self):
        result = grants.run(
            program("""
                let names: Vec(str) = vec_new()
                fs::entries("a.txt", names)
            """),
            files={"a.txt": ""},
            capabilities=READ)
        self.assertEqual(result.returncode, 1)
        self.assertIn("list_dir: 'a.txt' is not a directory", result.stderr)

    def test_a_missing_directory_is_reported(self):
        result = grants.run(
            program("""
                let names: Vec(str) = vec_new()
                fs::entries("no_such_dir", names)
            """),
            capabilities=READ)
        self.assertEqual(result.returncode, 1)
        self.assertIn("cannot open 'no_such_dir': No such file or directory", result.stderr)


class PathTextTests(unittest.TestCase):
    """The pure half, on the edges a table of examples cannot all hold."""

    def run_paths(self, body: str):
        return grants.run(program(body))

    def test_the_edges_of_basename_and_dirname(self):
        result = self.run_paths("""
            print(fs::basename("/a/b.txt"))
            print(fs::basename("a/b/"))
            print(fs::dirname("/a/b.txt"))
            print(fs::dirname("a"))
            print(fs::dirname("/"))
            print(fs::dirname("a/b/"))
        """)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(),
                         ["b.txt", "", "/a", ".", "/", "a/b"])

    def test_join_never_doubles_a_separator(self):
        result = self.run_paths("""
            print(fs::path_join("a/", "/b"))
            print(fs::path_join("a/", "b"))
            print(fs::path_join("a", "/b"))
            print(fs::path_join("", ""))
            print(fs::path_join("a", ".."))
        """)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["a/b", "a/b", "a/b", "", "a/.."])

    def test_extension_and_stem_agree_with_each_other(self):
        result = self.run_paths("""
            print(fs::extension("a/.hidden"))
            print(fs::stem("a/.hidden"))
            print(fs::extension("a/name."))
            print(fs::stem("a/name."))
            print(fs::stem("/"))
        """)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["", ".hidden", "", "name.", ""])

    def test_a_path_built_by_join_reads_back_the_same(self):
        """The pairing that makes the two functions worth having together."""
        result = self.run_paths("""
            let joined = fs::path_join(fs::dirname("a/b/c.txt"), fs::basename("a/b/c.txt"))
            print(joined)
            print(joined == "a/b/c.txt")
        """)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["a/b/c.txt", "true"])


class IsolationTests(unittest.TestCase):
    """The same refusal through the command line, with the jail asked for."""

    def nava(self, *arguments):
        return subprocess.run([sys.executable, str(ROOT / "nava"), *arguments],
                              capture_output=True, text=True, timeout=180)

    def test_asking_about_a_path_outside_the_grant_is_refused_by_the_cli(self):
        """The builtin inherits the capability model on the real command path."""
        source = program("""
            print(fs::exists("inside/data.txt"))
            print(fs::exists("outside/data.txt"))
        """)
        with grants.workspace({
            "main.nv": source,
            "inside/data.txt": "visible",
            "outside/data.txt": "hidden",
        }) as root:
            result = self.nava("run", "--isolation", "off", "--workdir", str(root),
                               "--allow-read", "inside", str(root / "main.nv"))
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual(result.stdout, "true\n")
        self.assertIn("path_exists denied: 'outside/data.txt' is outside every "
                      "permitted directory", result.stderr)


if __name__ == "__main__":
    unittest.main()
