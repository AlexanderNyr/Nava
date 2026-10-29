"""The `io` module against real files and real grants (PLAN.md N12e).

The module is small on purpose, and this file is where its file-touching half is
tested: `lines_of`, `count_lines`, `first_line` and `join_lines` are exercised by
the standard library's own self-test, because they need nothing but memory, and
`read_lines`, `write_lines` and `append_line` are here because a test for them
needs something the language does not hand out for free — a directory the
program is allowed to use.

Three claims are under test, and each has a control that would fail without it:

* **The round trip is exact.** `write_lines` puts a newline after every line and
  `read_lines` drops the one empty line that a final newline leaves, so a list of
  lines comes back as it went in — and a file that genuinely ends with an empty
  line keeps it, because only *one* is dropped.
* **The capability model still decides.** A program with no grant is refused with
  the message `LANGUAGE.md` documents, and a program with a read grant cannot
  write. The module is not a way around the model: it is a caller of it.
* **A failure is a failure.** A missing file stops the program with
  `cannot open '<path>': No such file or directory` rather than returning an
  empty string that a caller would have to guess about.

`tests/grants.py` builds each case in a temporary tree, compiles with the grants
baked in and runs the program with that tree as its working directory, which is
what makes a relative grant mean the same thing here as it does in a shell.
"""

from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "tests"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import grants  # noqa: E402

#: Grants for the cases that are allowed to touch their own directory.
BOTH = {"read": ["."], "write": ["."]}


def program(body: str) -> str:
    """A small edition-2 program that imports `io`, so a case reads as its body."""
    return ("edition 2;\n\nimport io;\n\nfn main() {\n"
            + "".join(f"    {line}\n" for line in body.strip().splitlines())
            + "}\n")


class RoundTripTests(unittest.TestCase):
    """What `write_lines` writes, `read_lines` reads back."""

    def test_lines_come_back_as_they_went_in(self):
        result = grants.run(
            program("""
                let written: [str] = ["alpha", "beta", "gamma"]
                io::write_lines("notes.txt", written)
                let lines: Vec(str) = vec_new()
                io::read_lines("notes.txt", lines)
                print(vec_len(lines))
                print(vec_get(lines, 0))
                print(vec_get(lines, 2))
            """),
            capabilities=BOTH)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["3", "alpha", "gamma"])

    def test_the_file_ends_with_a_newline(self):
        result = grants.run(
            program("""
                let written: [str] = ["a", "b"]
                io::write_lines("written.txt", written)
                print(bytes_len(read_file("written.txt")))
                print(read_file("written.txt") == "a\\nb\\n")
            """),
            capabilities=BOTH)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["4", "true"])

    def test_an_empty_array_writes_an_empty_file(self):
        result = grants.run(
            program("""
                let nothing: [str] = []
                io::write_lines("empty.txt", nothing)
                print(bytes_len(read_file("empty.txt")))
                let lines: Vec(str) = vec_new()
                io::read_lines("empty.txt", lines)
                print(vec_len(lines))
            """),
            capabilities=BOTH)
        self.assertEqual(result.returncode, 0, result.stderr)
        # An empty file is one empty line, not zero lines: that is what splitting
        # on newlines means, and the two functions agree about it.
        self.assertEqual(result.stdout.splitlines(), ["0", "1"])

    def test_only_one_trailing_empty_line_is_dropped(self):
        """A file that really ends with an empty line keeps it.

        The rule is "drop the one empty field a final newline produces", not
        "drop trailing empty lines": `"a\\n\\n"` is the lines `a` and an empty one.
        """
        result = grants.run(
            program("""
                write_file("gaps.txt", "a\\n\\n")
                let lines: Vec(str) = vec_new()
                io::read_lines("gaps.txt", lines)
                print(vec_len(lines))
                print(bytes_len(vec_get(lines, 1)))
            """),
            capabilities=BOTH)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["2", "0"])

    def test_lines_are_appended_to_what_the_vector_held(self):
        result = grants.run(
            program("""
                write_file("one.txt", "x\\n")
                let lines: Vec(str) = vec_new()
                vec_push(lines, "kept")
                io::read_lines("one.txt", lines)
                print(vec_len(lines))
                print(vec_get(lines, 0))
                print(vec_get(lines, 1))
            """),
            capabilities=BOTH)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["2", "kept", "x"])

    def test_append_line_adds_one_line_at_the_end(self):
        result = grants.run(
            program("""
                let first: [str] = ["a"]
                io::write_lines("log.txt", first)
                io::append_line("log.txt", "b")
                io::append_line("log.txt", "c")
                print(read_file("log.txt") == "a\\nb\\nc\\n")
                print(bytes_len(read_file("log.txt")))
            """),
            capabilities=BOTH)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["true", "6"])

    def test_append_line_starts_from_an_empty_file(self):
        result = grants.run(
            program("""
                let nothing: [str] = []
                io::write_lines("tail.txt", nothing)
                io::append_line("tail.txt", "x")
                print(read_file("tail.txt") == "x\\n")
            """),
            capabilities=BOTH)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["true"])


class CapabilityTests(unittest.TestCase):
    """The module inherits the capability model; it does not widen it."""

    def test_reading_without_a_grant_is_denied(self):
        result = grants.run(
            program("""
                let lines: Vec(str) = vec_new()
                io::read_lines("data.txt", lines)
                print(vec_len(lines))
            """),
            files={"data.txt": "secret\n"})
        self.assertEqual(result.returncode, 1)
        self.assertIn("read_file denied: no permitted directory was configured",
                      result.stderr)
        self.assertEqual(result.stdout, "")

    def test_a_read_grant_does_not_allow_writing(self):
        result = grants.run(
            program("""
                let written: [str] = ["a"]
                io::write_lines("out.txt", written)
                print(1)
            """),
            capabilities={"read": ["."]})
        self.assertEqual(result.returncode, 1)
        self.assertIn("write_file denied", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_a_write_grant_does_not_allow_reading(self):
        result = grants.run(
            program("""
                let lines: Vec(str) = vec_new()
                io::read_lines("data.txt", lines)
                print(vec_len(lines))
            """),
            files={"data.txt": "a\n"},
            capabilities={"write": ["."]})
        self.assertEqual(result.returncode, 1)
        self.assertIn("read_file denied", result.stderr)


class FailureTests(unittest.TestCase):
    """A missing file is a stopped program, not an empty result."""

    def test_a_missing_file_is_reported(self):
        result = grants.run(
            program("""
                let lines: Vec(str) = vec_new()
                io::read_lines("no_such_file.txt", lines)
                print(vec_len(lines))
            """),
            capabilities=BOTH)
        self.assertEqual(result.returncode, 1)
        self.assertIn("cannot open 'no_such_file.txt': No such file or directory",
                      result.stderr)
        self.assertEqual(result.stdout, "")

    def test_appending_to_a_missing_file_is_reported(self):
        """`append_line` reads first, so it inherits the same refusal."""
        result = grants.run(
            program("""
                io::append_line("never_created.txt", "x")
                print(1)
            """),
            capabilities=BOTH)
        self.assertEqual(result.returncode, 1)
        self.assertIn("cannot open 'never_created.txt'", result.stderr)


if __name__ == "__main__":
    unittest.main()
