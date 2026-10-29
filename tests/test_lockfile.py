"""`nava.lock`: the resolved graph, by digest (PLAN.md N13b).

The phase's gate for packages is a sentence about trust: *a lockfile reproduces a
build on another machine; a tampered package fails the build*. This file is
organised around the three things that sentence needs, in the order they have to
hold:

1. **Identity is bytes.** `digest_of` is a function of the files and nothing else —
   not of the clock, not of the order a directory listing happens to produce, not
   of where the directory sits. `DigestTests` pins that from both sides: the same
   tree in two places has one digest, and one flipped byte has another.
2. **Resolution is a walk, and every way it fails is named.** A missing package is
   `E0805`, a version outside its constraint is `E0806` — including a constraint
   placed by a package rather than by the project — a cycle is `E0807` with the
   cycle written out, and a directory that is not a package is `E0808`.
3. **The lockfile is checked, not trusted.** `CheckTests` walks the tamper list the
   phase asks for: one flipped byte in a vendored package, a digest edited by hand,
   a version that moved, a dependency list that moved, an entry nothing needs, an
   entry that went missing. Each is its own code, because "the lockfile was edited"
   and "the package was edited" are different accidents.

The *second checkout* case is tested the way it can be tested honestly inside one
process: the whole project is copied to a fresh temporary directory — a different
absolute path, a different inode for every file — and the lockfile written by the
first copy is checked against it. A digest that depended on a path would fail there.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "tests"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from nava_lang import lockfile, manifest  # noqa: E402
from nava_lang.model import NavaError  # noqa: E402

NAVA = [sys.executable, str(ROOT / "nava")]

PROJECT = """\
[package]
name = "demo"
version = "0.1.0"
edition = 2

[build]
entry = "src/main.nv"

[dependencies]
json = "^1.2.3"
"""

PROGRAM = "edition 2;\n\nfn main() {\n    print(1)\n}\n"


def write_tree(root: pathlib.Path, *, json_version: str = "1.2.3",
               util_version: str = "0.2.1", requires: str = 'util = "^0.2.0"',
               project: str = PROJECT, with_util: bool = True) -> pathlib.Path:
    """A project with two vendored packages: `json`, which needs `util`."""
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "src" / "main.nv").write_text(PROGRAM, encoding="utf-8")
    (root / manifest.MANIFEST_NAME).write_text(project, encoding="utf-8")
    json_dir = root / "vendor" / "json"
    json_dir.mkdir(parents=True, exist_ok=True)
    (json_dir / manifest.MANIFEST_NAME).write_text(
        f'[package]\nname = "json"\nversion = "{json_version}"\nedition = 2\n\n'
        f'[dependencies]\n{requires}\n' if requires else
        f'[package]\nname = "json"\nversion = "{json_version}"\nedition = 2\n',
        encoding="utf-8")
    (json_dir / "json.nv").write_text(
        'edition 2;\n\npub fn quote(text: str) -> str {\n'
        '    return str_concat(str_concat("\\"", text), "\\"")\n}\n', encoding="utf-8")
    if with_util:
        util_dir = root / "vendor" / "util"
        util_dir.mkdir(parents=True, exist_ok=True)
        (util_dir / manifest.MANIFEST_NAME).write_text(
            f'[package]\nname = "util"\nversion = "{util_version}"\nedition = 2\n',
            encoding="utf-8")
        (util_dir / "util.nv").write_text(
            "edition 2;\n\npub fn twice(x: i64) -> i64 {\n    return x * 2\n}\n",
            encoding="utf-8")
    return root


def frozen(root: pathlib.Path):
    """A manifest object for `root`, without the entry-file checks of `read`."""
    return manifest.from_text((root / manifest.MANIFEST_NAME).read_text(encoding="utf-8"),
                              root / manifest.MANIFEST_NAME)


def code_of(action) -> str:
    try:
        action()
    except NavaError as error:
        return error.code
    return ""


class DigestTests(unittest.TestCase):
    """A package is what it contains."""

    def test_the_same_tree_in_two_places_has_one_digest(self):
        with tempfile.TemporaryDirectory() as one, tempfile.TemporaryDirectory() as two:
            first = write_tree(pathlib.Path(one) / "json")
            second = write_tree(pathlib.Path(two) / "deeper" / "json")
            self.assertEqual(lockfile.digest_of(first / "vendor" / "json"),
                             lockfile.digest_of(second / "vendor" / "json"))

    def test_one_flipped_byte_is_a_different_package(self):
        with tempfile.TemporaryDirectory() as directory:
            root = write_tree(pathlib.Path(directory))
            before = lockfile.digest_of(root / "vendor" / "util")
            target = root / "vendor" / "util" / "util.nv"
            target.write_text(target.read_text(encoding="utf-8").replace("2", "3"),
                              encoding="utf-8")
            after = lockfile.digest_of(root / "vendor" / "util")
        self.assertNotEqual(before, after)
        self.assertTrue(before.startswith("sha256:"))
        self.assertEqual(len(before), len("sha256:") + 64)

    def test_a_rename_changes_the_digest_as_much_as_a_byte(self):
        with tempfile.TemporaryDirectory() as directory:
            root = write_tree(pathlib.Path(directory))
            util = root / "vendor" / "util"
            before = lockfile.digest_of(util)
            (util / "util.nv").rename(util / "helper.nv")
            self.assertNotEqual(before, lockfile.digest_of(util))

    def test_the_order_files_were_written_in_does_not_matter(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / "b.nv").write_text("b\n", encoding="utf-8")
            (root / "a.nv").write_text("a\n", encoding="utf-8")
            first = lockfile.digest_of(root)
            (root / "a.nv").unlink()
            (root / "a.nv").write_text("a\n", encoding="utf-8")
            self.assertEqual(first, lockfile.digest_of(root))

    def test_a_symbolic_link_is_refused_rather_than_followed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / "real.nv").write_text("x\n", encoding="utf-8")
            (root / "link.nv").symlink_to(root / "real.nv")
            with self.assertRaises(NavaError) as caught:
                lockfile.digest_of(root)
        self.assertEqual(caught.exception.code, "E0808")
        self.assertIn("symbolic link", caught.exception.message)

    def test_an_empty_package_still_has_a_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            digest = lockfile.digest_of(pathlib.Path(directory))
        self.assertEqual(digest, "sha256:" + __import__("hashlib").sha256(b"E\x00").hexdigest())


class ResolutionTests(unittest.TestCase):
    """The walk, and the ways it stops."""

    def test_a_transitive_dependency_is_resolved_and_sorted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = write_tree(pathlib.Path(directory))
            resolution = lockfile.resolve(frozen(root))
        self.assertEqual([item.name for item in resolution.packages], ["json", "util"])
        self.assertEqual(resolution.package("json").requires, ["util"])
        self.assertEqual(str(resolution.package("util").version), "0.2.1")
        self.assertEqual(resolution.package("json").source, "vendor/json")
        self.assertEqual(resolution.project, "demo")

    def test_a_package_that_is_not_vendored_is_named_with_who_wanted_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = write_tree(pathlib.Path(directory), with_util=False)
            error = None
            try:
                lockfile.resolve(frozen(root))
            except NavaError as caught:
                error = caught
        self.assertEqual(error.code, "E0805")
        self.assertIn("json requires 'util'", error.message)
        self.assertIn("vendor/util does not exist", error.message)

    def test_a_version_outside_the_project_constraint_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = write_tree(pathlib.Path(directory), json_version="2.0.0")
            error = None
            try:
                lockfile.resolve(frozen(root))
            except NavaError as caught:
                error = caught
        self.assertEqual(error.code, "E0806")
        self.assertIn("json 2.0.0 is outside ^1.2.3", error.message)
        self.assertIn("required by the project", error.message)

    def test_a_version_outside_a_package_constraint_is_refused_too(self):
        """The constraint a package places counts as much as the project's."""
        with tempfile.TemporaryDirectory() as directory:
            root = write_tree(pathlib.Path(directory), util_version="0.3.0")
            error = None
            try:
                lockfile.resolve(frozen(root))
            except NavaError as caught:
                error = caught
        self.assertEqual(error.code, "E0806")
        self.assertIn("util 0.3.0 is outside ^0.2.0", error.message)
        self.assertIn("required by json", error.message)

    def test_a_cycle_is_refused_with_the_cycle_written_out(self):
        with tempfile.TemporaryDirectory() as directory:
            root = write_tree(pathlib.Path(directory))
            util = root / "vendor" / "util" / manifest.MANIFEST_NAME
            util.write_text(util.read_text(encoding="utf-8") +
                            '\n[dependencies]\njson = "^1.2.3"\n', encoding="utf-8")
            error = None
            try:
                lockfile.resolve(frozen(root))
            except NavaError as caught:
                error = caught
        self.assertEqual(error.code, "E0807")
        self.assertIn("json -> util -> json", error.message)

    def test_a_directory_that_is_not_a_package_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = write_tree(pathlib.Path(directory))
            (root / "vendor" / "util" / manifest.MANIFEST_NAME).unlink()
            error = None
            try:
                lockfile.resolve(frozen(root))
            except NavaError as caught:
                error = caught
        self.assertEqual(error.code, "E0808")
        self.assertIn("is not a package", error.message)

    def test_a_package_vendored_under_the_wrong_name_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = write_tree(pathlib.Path(directory))
            target = root / "vendor" / "util" / manifest.MANIFEST_NAME
            target.write_text(target.read_text(encoding="utf-8").replace(
                'name = "util"', 'name = "helper"'), encoding="utf-8")
            error = None
            try:
                lockfile.resolve(frozen(root))
            except NavaError as caught:
                error = caught
        self.assertEqual(error.code, "E0808")
        self.assertIn("declares the name 'helper'", error.message)

    def test_a_package_may_not_shadow_a_standard_module(self):
        """The manifest's rule, applied to a package that is vendored."""
        with tempfile.TemporaryDirectory() as directory:
            root = write_tree(pathlib.Path(directory))
            math = root / "vendor" / "math"
            math.mkdir()
            (math / manifest.MANIFEST_NAME).write_text(
                '[package]\nname = "math"\nversion = "1.0.0"\nedition = 2\n',
                encoding="utf-8")
            (root / manifest.MANIFEST_NAME).write_text(
                PROJECT + 'math = "^1.0.0"\n', encoding="utf-8")
            self.assertEqual(code_of(lambda: lockfile.resolve(frozen(root))), "E0708")

    def test_a_package_with_no_version_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = write_tree(pathlib.Path(directory))
            target = root / "vendor" / "util" / manifest.MANIFEST_NAME
            target.write_text('[package]\nname = "util"\nedition = 2\n', encoding="utf-8")
            self.assertEqual(code_of(lambda: lockfile.resolve(frozen(root))), "E0703")


class LockfileFileTests(unittest.TestCase):
    """Writing the file, and refusing a file that was not written by us."""

    def resolve(self, directory: pathlib.Path):
        return lockfile.resolve(frozen(directory))

    def test_the_text_is_the_same_bytes_twice(self):
        with tempfile.TemporaryDirectory() as directory:
            root = write_tree(pathlib.Path(directory))
            first = lockfile.render(self.resolve(root))
            second = lockfile.render(self.resolve(root))
        self.assertEqual(first, second)
        self.assertIn('[lock]\nformat = 1\nproject = "demo"\nversion = "0.1.0"', first)
        self.assertIn('source = "vendor/json"', first)
        self.assertIn('requires = ["util"]', first)
        self.assertIn('requires = []', first)

    def test_a_written_file_reads_back_to_the_same_resolution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = write_tree(pathlib.Path(directory))
            resolution = self.resolve(root)
            path = lockfile.write(resolution, root / lockfile.LOCK_NAME)
            parsed = lockfile.read(path)
        self.assertEqual(parsed.project, resolution.project)
        self.assertEqual([item.line() for item in parsed.packages],
                         [item.line() for item in resolution.packages])
        self.assertEqual([item.requires for item in parsed.packages],
                         [item.requires for item in resolution.packages])

    def test_the_reader_refuses_what_it_did_not_write(self):
        valid = ('[lock]\nformat = 1\nproject = "demo"\nversion = "0.1.0"\n\n'
                 '[[package]]\nname = "json"\nversion = "1.2.3"\n'
                 'digest = "sha256:' + "a" * 64 + '"\nsource = "vendor/json"\n'
                 'requires = []\n')
        cases = {
            "no header": ('[[package]]\nname = "json"\nversion = "1.2.3"\n'
                          'digest = "sha256:' + "a" * 64 + '"\n'),
            "wrong format": valid.replace("format = 1", "format = 2"),
            "unknown field": valid.replace("[lock]", "[lock]\nauthor = \"me\""),
            "bad digest": valid.replace("sha256:" + "a" * 64, "sha256:abc"),
            "unsorted requires": valid.replace('requires = []', 'requires = ["b", "a"]'),
            "single bracket package": valid.replace("[[package]]", "[package]"),
            "package twice": valid + "\n" + valid[valid.index("[[package]]"):],
            "missing digest": valid.replace('digest = "sha256:' + "a" * 64 + '"\n', ""),
            "no name": valid.replace('name = "json"\n', ""),
        }
        for label, text in cases.items():
            with self.subTest(case=label):
                with self.assertRaises(NavaError) as caught:
                    lockfile.parse(text)
                self.assertIn(caught.exception.code, ("E0702", "E0802"))
        self.assertEqual(code_of(lambda: lockfile.parse(valid)), "")

    def test_a_missing_lockfile_is_its_own_code(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(code_of(lambda: lockfile.read(pathlib.Path(directory) /
                                                           lockfile.LOCK_NAME)), "E0801")


class CheckTests(unittest.TestCase):
    """The tamper list the phase asks for, one code per accident."""

    def locked(self, directory: pathlib.Path):
        root = write_tree(directory)
        project = frozen(root)
        resolution = lockfile.resolve(project)
        path = lockfile.write(resolution, root / lockfile.LOCK_NAME)
        return root, project, path

    def test_an_untouched_project_checks_clean(self):
        with tempfile.TemporaryDirectory() as directory:
            root, project, path = self.locked(pathlib.Path(directory))
            lockfile.check(project, lockfile.read(path))

    def test_one_flipped_byte_in_a_vendored_package_is_e0804(self):
        with tempfile.TemporaryDirectory() as directory:
            root, project, path = self.locked(pathlib.Path(directory))
            target = root / "vendor" / "json" / "json.nv"
            target.write_text(target.read_text(encoding="utf-8") + "# one more byte\n",
                              encoding="utf-8")
            error = None
            try:
                lockfile.check(project, lockfile.read(path))
            except NavaError as caught:
                error = caught
        self.assertEqual(error.code, "E0804")
        self.assertIn("json does not match its digest", error.message)
        self.assertIn("restore the package", error.hint)

    def test_a_digest_edited_by_hand_is_e0804_too(self):
        with tempfile.TemporaryDirectory() as directory:
            root, project, path = self.locked(pathlib.Path(directory))
            text = path.read_text(encoding="utf-8")
            digest = lockfile.digest_of(root / "vendor" / "util")
            path.write_text(text.replace(digest, "sha256:" + "0" * 64), encoding="utf-8")
            self.assertEqual(code_of(lambda: lockfile.check(project, lockfile.read(path))),
                             "E0804")

    def test_a_version_that_moved_is_e0803(self):
        with tempfile.TemporaryDirectory() as directory:
            root, project, path = self.locked(pathlib.Path(directory))
            target = root / "vendor" / "util" / manifest.MANIFEST_NAME
            target.write_text(target.read_text(encoding="utf-8").replace("0.2.1", "0.2.2"),
                              encoding="utf-8")
            error = None
            try:
                lockfile.check(project, lockfile.read(path))
            except NavaError as caught:
                error = caught
        self.assertEqual(error.code, "E0803")
        self.assertIn("locks util 0.2.1", error.message)
        self.assertIn("holds 0.2.2", error.message)

    def test_a_dependency_list_that_moved_is_e0803(self):
        with tempfile.TemporaryDirectory() as directory:
            root, project, path = self.locked(pathlib.Path(directory))
            target = root / "vendor" / "json" / manifest.MANIFEST_NAME
            target.write_text(target.read_text(encoding="utf-8").replace(
                '[dependencies]\nutil = "^0.2.0"', "\n"), encoding="utf-8")
            error = None
            try:
                lockfile.check(project, lockfile.read(path))
            except NavaError as caught:
                error = caught
        self.assertEqual(error.code, "E0803")
        self.assertIn("says json requires ['util']", error.message)

    def test_an_entry_nothing_needs_is_e0803(self):
        with tempfile.TemporaryDirectory() as directory:
            root, project, path = self.locked(pathlib.Path(directory))
            path.write_text(path.read_text(encoding="utf-8") +
                            f'\n[[package]]\nname = "ghost"\nversion = "1.0.0"\n'
                            f'digest = "sha256:{"0" * 64}"\nsource = "vendor/ghost"\n'
                            f'requires = []\n', encoding="utf-8")
            error = None
            try:
                lockfile.check(project, lockfile.read(path))
            except NavaError as caught:
                error = caught
        self.assertEqual(error.code, "E0803")
        self.assertIn("names ghost 1.0.0", error.message)
        self.assertIn("does not depend on", error.message)

    def test_an_entry_that_went_missing_is_e0803(self):
        with tempfile.TemporaryDirectory() as directory:
            root, project, path = self.locked(pathlib.Path(directory))
            text = path.read_text(encoding="utf-8")
            head = text[:text.index("[[package]]")]
            only_json = text[text.index("[[package]]"):text.index("[[package]]", text.index("[[package]]") + 1)]
            path.write_text(head + only_json, encoding="utf-8")
            error = None
            try:
                lockfile.check(project, lockfile.read(path))
            except NavaError as caught:
                error = caught
        self.assertEqual(error.code, "E0803")
        self.assertIn("does not name util 0.2.1", error.message)

    def test_a_lockfile_for_another_project_is_e0803(self):
        with tempfile.TemporaryDirectory() as directory:
            root, project, path = self.locked(pathlib.Path(directory))
            path.write_text(path.read_text(encoding="utf-8").replace(
                'version = "0.1.0"', 'version = "0.2.0"'), encoding="utf-8")
            error = None
            try:
                lockfile.check(project, lockfile.read(path))
            except NavaError as caught:
                error = caught
        self.assertEqual(error.code, "E0803")
        self.assertIn("describes demo 0.2.0", error.message)

    def test_a_second_checkout_checks_clean(self):
        """A different absolute path, different inodes, the same lockfile."""
        with tempfile.TemporaryDirectory() as one, tempfile.TemporaryDirectory() as two:
            root, project, path = self.locked(pathlib.Path(one))
            copy = pathlib.Path(two) / "elsewhere" / "demo"
            shutil.copytree(root, copy)
            lockfile.check(frozen(copy), lockfile.read(copy / lockfile.LOCK_NAME))
            self.assertEqual(lockfile.render(lockfile.resolve(frozen(copy))),
                             lockfile.render(lockfile.resolve(project)))


class GraphTests(unittest.TestCase):
    """The graph is printed, and it is the lockfile's answer rather than a second one."""

    def test_the_graph_names_every_package_with_its_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = write_tree(pathlib.Path(directory))
            project = frozen(root)
            resolution = lockfile.resolve(project)
            text = lockfile.graph(resolution, project)
        lines = text.splitlines()
        self.assertEqual(lines[0], "demo 0.1.0")
        self.assertIn("json 1.2.3 sha256:", lines[1])
        self.assertIn("└── util 0.2.1 sha256:", lines[2])
        self.assertEqual(text.count("sha256:"), 2)

    def test_the_graph_is_the_same_twice(self):
        with tempfile.TemporaryDirectory() as directory:
            root = write_tree(pathlib.Path(directory))
            project = frozen(root)
            one = lockfile.graph(lockfile.resolve(project), project)
            two = lockfile.graph(lockfile.resolve(project), project)
        self.assertEqual(one, two)


class LockCommandTests(unittest.TestCase):
    """`nava lock` and `nava lock --check` as a caller sees them."""

    def run_nava(self, *arguments: str, cwd: str) -> subprocess.CompletedProcess:
        return subprocess.run([*NAVA, *arguments], capture_output=True, text=True,
                              cwd=cwd, timeout=120)

    def test_the_command_writes_the_file_and_prints_the_graph(self):
        with tempfile.TemporaryDirectory() as directory:
            root = write_tree(pathlib.Path(directory))
            result = self.run_nava("lock", cwd=str(root))
            written = (root / lockfile.LOCK_NAME).read_text(encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.startswith("demo 0.1.0\n"))
        self.assertIn("Wrote:", result.stdout)
        self.assertIn('[[package]]\nname = "json"', written)
        self.assertIn('requires = ["util"]', written)

    def test_the_file_is_the_same_bytes_when_written_twice(self):
        with tempfile.TemporaryDirectory() as directory:
            root = write_tree(pathlib.Path(directory))
            self.run_nava("lock", cwd=str(root))
            first = (root / lockfile.LOCK_NAME).read_bytes()
            self.run_nava("lock", cwd=str(root))
            second = (root / lockfile.LOCK_NAME).read_bytes()
        self.assertEqual(first, second)

    def test_check_reports_ok_and_then_catches_a_tampered_package(self):
        with tempfile.TemporaryDirectory() as directory:
            root = write_tree(pathlib.Path(directory))
            self.run_nava("lock", cwd=str(root))
            good = self.run_nava("lock", "--check", cwd=str(root))
            target = root / "vendor" / "util" / "util.nv"
            target.write_text(target.read_text(encoding="utf-8").replace("2", "3"),
                              encoding="utf-8")
            bad = self.run_nava("lock", "--check", cwd=str(root))
        self.assertEqual(good.returncode, 0, good.stderr)
        self.assertIn("OK: nava.lock describes demo 0.1.0 (2 packages)", good.stdout)
        self.assertEqual(bad.returncode, 2)
        self.assertIn("E0804", bad.stderr)
        self.assertIn("does not match its digest", bad.stderr)

    def test_the_json_report_carries_every_package(self):
        with tempfile.TemporaryDirectory() as directory:
            root = write_tree(pathlib.Path(directory))
            result = self.run_nava("--format", "json", "lock", cwd=str(root))
        self.assertEqual(result.returncode, 0, result.stderr)
        document = json.loads(result.stdout)
        self.assertEqual([item["name"] for item in document["packages"]], ["json", "util"])
        self.assertTrue(all(item["digest"].startswith("sha256:")
                            for item in document["packages"]))
        self.assertEqual(document["packages"][0]["requires"], ["util"])

    def test_checking_without_a_lockfile_says_to_write_one(self):
        with tempfile.TemporaryDirectory() as directory:
            root = write_tree(pathlib.Path(directory))
            result = self.run_nava("lock", "--check", cwd=str(root))
        self.assertEqual(result.returncode, 2)
        self.assertIn("E0801", result.stderr)
        self.assertIn("nava lock", result.stderr)


if __name__ == "__main__":
    unittest.main()
