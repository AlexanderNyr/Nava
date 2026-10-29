"""`nava.toml`: the project manifest (PLAN.md N13a).

The manifest is the first thing in the phase that is not a Nava program, so this
file is organised around what a manifest has to be able to promise before the
lockfile can stand on it:

* **It is read strictly.** Three tables, four value shapes, and everything else
  refused with a code and a line: an unknown table, an unknown field, a repeated
  key, an inline table, an unquoted word. A key that is skipped silently is a
  setting its author believes is in force, so nothing is skipped.
* **A typo is an error, not a default.** `edtion = 2` is `E0704` rather than a
  missing `edition`, and a missing `package.name` is `E0703`. The two failures
  have different repairs, so they have different codes.
* **Two names are checked against the world.** A project called `math`, and a
  dependency called `math`, are both `E0708`: the standard library already has
  that name and the loader refuses the shadowing later anyway (`E0360`).
* **The edition is checked against the entry file.** A manifest saying
  `edition = 2` over a file that does not declare it is `E0706`, because only one
  of the two is read by the compiler and a manifest that cannot be wrong is
  decoration.
* **The same bytes give the same report.** Dependencies are reported sorted by
  name, nothing depends on the clock, and the JSON document is compared byte for
  byte between two runs — the reproducibility this phase is about starts with the
  compiler not inventing an order.

The versions and constraints are unit-tested directly as well, because a
constraint matcher is arithmetic and arithmetic is cheaper to check at the edges
(`^1.2.3` includes `1.9.0` and excludes `2.0.0`) than through a build.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "tests"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from nava_lang import manifest  # noqa: E402
from nava_lang.model import NavaError  # noqa: E402

NAVA = [sys.executable, str(ROOT / "nava")]

#: A manifest that is correct in every respect, so a case can change one line.
VALID = """\
# demo project
[package]
name = "demo"
version = "0.1.0"
edition = 2

[build]
entry = "main.nv"
"""

PROGRAM = "edition 2;\n\nfn main() {\n    print(42)\n}\n"


def write_project(directory: pathlib.Path, text: str = VALID,
                  program: str = PROGRAM, entry: str = "main.nv") -> pathlib.Path:
    """Write a manifest and its entry file into `directory`."""
    target = directory / entry
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(program, encoding="utf-8")
    path = directory / manifest.MANIFEST_NAME
    path.write_text(text, encoding="utf-8")
    return path


def code_of(text: str) -> str:
    """The diagnostic code `from_text` raises for `text`."""
    with tempfile.TemporaryDirectory() as directory:
        path = pathlib.Path(directory) / manifest.MANIFEST_NAME
        try:
            manifest.from_text(text, path)
        except NavaError as error:
            return error.code
    return ""


def message_of(text: str) -> str:
    with tempfile.TemporaryDirectory() as directory:
        path = pathlib.Path(directory) / manifest.MANIFEST_NAME
        try:
            manifest.from_text(text, path)
        except NavaError as error:
            return error.message
    return ""


class ParsingTests(unittest.TestCase):
    """The subset of TOML the manifest accepts, and the shapes it refuses."""

    def test_a_valid_manifest_is_read_whole(self):
        with tempfile.TemporaryDirectory() as directory:
            path = write_project(pathlib.Path(directory))
            result = manifest.read(path)
        self.assertEqual(result.name, "demo")
        self.assertEqual(str(result.version), "0.1.0")
        self.assertEqual(result.edition, 2)
        self.assertEqual(result.entry, "main.nv")
        self.assertEqual(result.dependencies, [])

    def test_comments_blank_lines_and_the_default_entry(self):
        text = ('# leading comment\n\n[package]\nname = "demo"  # trailing\n'
                'version = "1.0.0"\nedition = 1\n')
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / manifest.MANIFEST_NAME
            path.write_text(text, encoding="utf-8")
            (pathlib.Path(directory) / "main.nv").write_text("fn main() { print(1) }\n",
                                                             encoding="utf-8")
            result = manifest.read(path)
        self.assertEqual(result.name, "demo")
        self.assertEqual(result.entry, "main.nv", "the entry defaults to main.nv")

    def test_dependencies_keep_the_order_written_and_report_sorted(self):
        text = VALID + '\n[dependencies]\nzeta = "^1.0.0"\nalpha = "~2.1.0"\n'
        result = manifest.from_text(text)
        self.assertEqual([item.name for item in result.dependencies], ["zeta", "alpha"])
        self.assertEqual([item.name for item in result.sorted_dependencies()],
                         ["alpha", "zeta"])
        report = manifest.summarize(result)
        self.assertIn("dependencies 2\n  alpha ~2.1.0\n  zeta ^1.0.0\n", report)

    def test_an_unknown_table_is_refused(self):
        self.assertEqual(code_of(VALID + "\n[scripts]\nrun = \"x\"\n"), "E0704")

    def test_an_unknown_field_is_refused_and_lists_the_known_ones(self):
        self.assertEqual(code_of(VALID.replace("edition = 2", "edtion = 2")), "E0704")
        message = message_of(VALID.replace("edition = 2", "edtion = 2"))
        self.assertIn("unknown field package.edtion", message)
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / manifest.MANIFEST_NAME
            path.write_text(VALID.replace("edition = 2", "edtion = 2"), encoding="utf-8")
            try:
                manifest.from_text(VALID.replace("edition = 2", "edtion = 2"), path)
            except NavaError as error:
                self.assertIn("edition", error.hint or "")
                self.assertEqual(error.diagnostic.loc.line, 5)

    def test_a_repeated_key_is_refused(self):
        self.assertEqual(code_of(VALID.replace('name = "demo"',
                                               'name = "demo"\nname = "other"')), "E0702")

    def test_a_key_before_any_table_is_refused(self):
        self.assertEqual(code_of('name = "demo"\n' + VALID), "E0702")

    def test_shapes_outside_the_subset_are_refused(self):
        for text in (
            VALID.replace('name = "demo"', "name = demo"),
            VALID.replace('name = "demo"', 'name = "demo" extra'),
            VALID.replace('name = "demo"', 'name = "de\\qmo"'),
            VALID.replace('edition = 2', "edition = 2.5"),
            VALID + '\n[dependencies]\njson = { version = "1.0.0" }\n',
            VALID + '\n[dependencies]\njson = ["1.0.0", "2.0.0"]\n',
            '[package]\nname = "demo\n',
            '[package\nname = "demo"\n',
            'package\n',
        ):
            with self.subTest(text=text):
                self.assertEqual(code_of(text), "E0702")

    def test_a_field_of_the_wrong_shape_is_refused(self):
        self.assertEqual(code_of(VALID.replace("edition = 2", 'edition = "2"')), "E0702")
        self.assertEqual(code_of(VALID.replace('name = "demo"', "name = 2")), "E0702")

    def test_a_missing_required_field_is_its_own_code(self):
        self.assertEqual(code_of(VALID.replace('name = "demo"\n', "")), "E0703")
        self.assertEqual(code_of("[package]\nname = \"demo\"\n"), "E0703")


class NameAndVersionTests(unittest.TestCase):
    """The two fields that are checked against the world rather than the file."""

    def test_a_package_may_not_take_a_standard_module_name(self):
        error = None
        try:
            manifest.from_text(VALID.replace('name = "demo"', 'name = "math"'))
        except NavaError as caught:
            error = caught
        self.assertIsNotNone(error)
        self.assertEqual(error.code, "E0708")
        self.assertIn("shadows a standard-library module", error.message)
        self.assertIn("math", error.hint or "")

    def test_a_dependency_may_not_take_one_either(self):
        text = VALID + '\n[dependencies]\nmath = "^1.0.0"\n'
        self.assertEqual(code_of(text), "E0708")

    def test_a_name_is_a_lower_case_identifier(self):
        for name in ("Demo", "my-project", "1demo", "_demo", "de mo", "démo"):
            with self.subTest(name=name):
                self.assertEqual(code_of(VALID.replace('name = "demo"', f'name = "{name}"')),
                                 "E0709")
        self.assertEqual(code_of(VALID.replace('name = "demo"',
                                               f'name = "{"a" * (manifest.MAX_NAME_LENGTH + 1)}"')),
                         "E0709")

    def test_a_version_is_three_numbers(self):
        for version in ("1", "1.2", "1.2.3.4", "01.2.3", "v1.2.3", "1.2.x", ""):
            with self.subTest(version=version):
                self.assertEqual(code_of(VALID.replace('version = "0.1.0"',
                                                       f'version = "{version}"')), "E0705")

    def test_constraints_match_what_semantic_versioning_says(self):
        cases = [
            ("^1.2.3", "1.2.3", True), ("^1.2.3", "1.9.0", True),
            ("^1.2.3", "2.0.0", False), ("^1.2.3", "1.2.2", False),
            ("~1.2.3", "1.2.9", True), ("~1.2.3", "1.3.0", False),
            ("~1.2.3", "1.2.2", False), ("=1.2.3", "1.2.3", True),
            ("=1.2.3", "1.2.4", False), ("1.2.3", "1.2.3", True),
            ("1.2.3", "1.2.4", False), ("*", "0.0.1", True),
            ("*", "99.0.0", True),
        ]
        for text, version, expected in cases:
            with self.subTest(constraint=text, version=version):
                constraint = manifest.parse_constraint(text)
                self.assertEqual(constraint.matches(manifest.parse_version(version)),
                                 expected)

    def test_a_constraint_needs_a_version_after_its_operator(self):
        self.assertEqual(code_of(VALID + '\n[dependencies]\njson = "^"\n'), "E0705")
        self.assertEqual(code_of(VALID + '\n[dependencies]\njson = "^1.2"\n'), "E0705")

    def test_an_edition_this_compiler_does_not_have_is_refused(self):
        self.assertEqual(code_of(VALID.replace("edition = 2", "edition = 3")), "E0706")
        self.assertEqual(code_of(VALID.replace("edition = 2", "edition = 0")), "E0706")


class ProjectTests(unittest.TestCase):
    """What `load` checks against the entry file, and where it looks for one."""

    def test_the_entry_must_exist(self):
        with tempfile.TemporaryDirectory() as directory:
            write_project(pathlib.Path(directory), VALID.replace('entry = "main.nv"',
                                                                 'entry = "src/main.nv"'),
                          entry="other.nv")
            with self.assertRaises(NavaError) as caught:
                manifest.resolve(directory)
        self.assertEqual(caught.exception.code, "E0707")

    def test_the_entry_may_not_leave_the_project(self):
        for entry in ("../main.nv", "/etc/passwd", "src/../../main.nv", ""):
            with self.subTest(entry=entry):
                text = VALID.replace('entry = "main.nv"', f'entry = "{entry}"')
                self.assertEqual(code_of(text), "E0707")

    def test_the_manifest_and_the_entry_must_agree_about_the_edition(self):
        with tempfile.TemporaryDirectory() as directory:
            write_project(pathlib.Path(directory),
                          VALID.replace("edition = 2", "edition = 1"),
                          program=PROGRAM)
            with self.assertRaises(NavaError) as caught:
                manifest.resolve(directory)
        self.assertEqual(caught.exception.code, "E0706")
        self.assertIn("declares edition 1", caught.exception.message)

        with tempfile.TemporaryDirectory() as directory:
            write_project(pathlib.Path(directory), VALID,
                          program="fn main() {\n    print(42)\n}\n")
            with self.assertRaises(NavaError) as caught:
                manifest.resolve(directory)
        self.assertEqual(caught.exception.code, "E0706")
        self.assertIn("declares edition 1", caught.exception.message)

    def test_the_manifest_is_found_in_a_parent_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            write_project(root, VALID.replace('entry = "main.nv"', 'entry = "src/main.nv"'),
                          entry="src/main.nv")
            (root / "src" / "deep").mkdir()
            found = manifest.find(root / "src" / "deep")
            self.assertEqual(found, (root / manifest.MANIFEST_NAME).resolve())
            self.assertEqual(manifest.resolve(root / "src" / "deep").name, "demo")

    def test_no_manifest_anywhere_is_its_own_code(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(NavaError) as caught:
                manifest.find(directory)
        self.assertEqual(caught.exception.code, "E0701")
        self.assertIn("or in any directory above it", caught.exception.message)


class CommandTests(unittest.TestCase):
    """`nava project` and `--project` on the commands that compile."""

    def run_nava(self, *arguments: str, cwd: str) -> subprocess.CompletedProcess:
        return subprocess.run([*NAVA, *arguments], capture_output=True, text=True,
                              cwd=cwd, timeout=120)

    def test_the_project_command_describes_the_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            write_project(pathlib.Path(directory),
                          VALID + '\n[dependencies]\nzeta = "^1.0.0"\nalpha = "~2.1.0"\n')
            result = self.run_nava("project", cwd=directory)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout,
                         "project demo 0.1.0\nedition 2\nentry main.nv\n"
                         "dependencies 2\n  alpha ~2.1.0\n  zeta ^1.0.0\n")

    def test_the_json_report_is_byte_identical_between_two_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            write_project(pathlib.Path(directory),
                          VALID + '\n[dependencies]\nzeta = "^1.0.0"\nalpha = "~2.1.0"\n')
            first = self.run_nava("--format", "json", "project", cwd=directory)
            second = self.run_nava("--format", "json", "project", cwd=directory)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(first.stdout, second.stdout)
        document = json.loads(first.stdout)
        self.assertEqual(document["status"], "ok")
        self.assertEqual(document["dependencies"], {"alpha": "~2.1.0", "zeta": "^1.0.0"})

    def test_a_broken_manifest_is_reported_with_its_code(self):
        with tempfile.TemporaryDirectory() as directory:
            write_project(pathlib.Path(directory), VALID.replace('name = "demo"',
                                                                 'name = "Math"'))
            result = self.run_nava("--format", "json", "project", cwd=directory)
        self.assertEqual(result.returncode, 2)
        document = json.loads(result.stdout)
        self.assertEqual(document["diagnostics"][0]["code"], "E0709")
        self.assertEqual(document["status"], "error")

    def test_without_a_manifest_the_command_says_where_it_looked(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_nava("project", cwd=directory)
        self.assertEqual(result.returncode, 2)
        self.assertIn("nava: no nava.toml in", result.stderr)

    def test_the_compiling_commands_take_their_entry_from_the_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            write_project(root, VALID.replace('entry = "main.nv"', 'entry = "src/prog.nv"'),
                          entry="src/prog.nv", program=PROGRAM)
            built = self.run_nava("build", "--project", "-o", str(root / "prog"), cwd=directory)
            ran = self.run_nava("run", "--project", cwd=directory)
            checked = self.run_nava("check", "--project", cwd=directory)
        self.assertEqual(built.returncode, 0, built.stderr)
        self.assertIn("Built:", built.stdout)
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertEqual(ran.stdout.strip(), "42")
        self.assertEqual(checked.returncode, 0, checked.stderr)
        self.assertIn("src/prog.nv", checked.stdout)

    def test_a_source_and_a_project_together_are_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            write_project(pathlib.Path(directory))
            result = self.run_nava("check", "main.nv", "--project", cwd=directory)
        self.assertEqual(result.returncode, 2)
        self.assertIn("E0509", result.stderr)
        self.assertIn("not both", result.stderr)

    def test_neither_a_source_nor_a_project_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_nava("check", cwd=directory)
        self.assertEqual(result.returncode, 2)
        self.assertIn("E0509", result.stderr)
        self.assertIn("no source file", result.stderr)


if __name__ == "__main__":
    unittest.main()
