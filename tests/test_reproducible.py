"""Reproducible builds: records, and proving two builds are one build (PLAN.md N13d).

The phase's gate is a sentence about a second checkout on a clean host, and the only
honest way to test it inside one process is to make "two checkouts" concrete: the
project is copied to a second directory with a different absolute path, built there,
and the two records are compared. What has to hold:

* **The same tree gives the same binary.** `nava reproduce` builds twice in two
  temporary directories and compares the records — source digests, the generated C,
  the binary, the toolchain and `SOURCE_DATE_EPOCH`. A single differing byte is
  reported with its name, not as an unexplained second hash.
* **The record has no clock and no path in it.** The same project in two checkouts
  produces the same record bytes, because the record names modules rather than
  files and writes its maps sorted. That is what makes a record worth committing
  next to a release.
* **`SOURCE_DATE_EPOCH` is the one clock a build may see**, and a value that is not
  a number is refused (`E0813`) by every command that produces a binary — not only
  by the one that writes a record, because a build that cannot pin time should not
  quietly look as if it did.
* **A different toolchain is a toolchain difference.** The phase's negative control
  says a build with a different toolchain must *record* the difference rather than
  silently produce a second hash; `compare` reports such a difference first, before
  the digests it caused, and a test asserts that order.

The compiler's own output is part of the claim: the generated C for one project in
two checkouts is compared byte for byte, so "deterministic builds" is not only a
property of the C compiler's behaviour that its flags happen to give us.
"""

from __future__ import annotations

import json
import os
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

from nava_lang import reproducible  # noqa: E402
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

MAIN = """\
edition 2;

import json;

fn main() {
    print(json::quote("hi"))
    print(json::doubled(21))
}
"""


def write_project(root: pathlib.Path) -> pathlib.Path:
    """The demo project from N13c: a `json` package that depends on `util`."""
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "src" / "main.nv").write_text(MAIN, encoding="utf-8")
    (root / "nava.toml").write_text(PROJECT, encoding="utf-8")
    (root / "vendor" / "json").mkdir(parents=True, exist_ok=True)
    (root / "vendor" / "json" / "nava.toml").write_text(
        '[package]\nname = "json"\nversion = "1.2.3"\nedition = 2\n\n'
        '[dependencies]\nutil = "^0.2.0"\n', encoding="utf-8")
    (root / "vendor" / "json" / "json.nv").write_text(
        "edition 2;\n\nimport util;\n\npub fn quote(text: str) -> str {\n"
        '    return str_concat(str_concat("\\"", text), "\\"")\n}\n\n'
        "pub fn doubled(value: i64) -> i64 {\n    return util::twice(value)\n}\n",
        encoding="utf-8")
    (root / "vendor" / "util").mkdir(parents=True, exist_ok=True)
    (root / "vendor" / "util" / "nava.toml").write_text(
        '[package]\nname = "util"\nversion = "0.2.1"\nedition = 2\n', encoding="utf-8")
    (root / "vendor" / "util" / "util.nv").write_text(
        "edition 2;\n\npub fn twice(x: i64) -> i64 {\n    return x * 2\n}\n",
        encoding="utf-8")
    result = subprocess.run([*NAVA, "lock"], cwd=str(root), capture_output=True,
                            text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    return root


def run_nava(*arguments: str, cwd, env: dict | None = None) -> subprocess.CompletedProcess:
    environment = dict(os.environ)
    environment.pop(reproducible.EPOCH_VARIABLE, None)
    environment.update(env or {})
    return subprocess.run([*NAVA, *arguments], capture_output=True, text=True,
                          cwd=str(cwd), timeout=600, env=environment)


class ReproduceTests(unittest.TestCase):
    """Two builds, one binary."""

    def test_two_builds_of_one_source_are_one_binary(self):
        with self._project() as root:
            result = run_nava("reproduce", "src/main.nv", "--root", str(root), cwd=root)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertRegex(result.stdout.strip(),
                         r"^(reproducible over 2 builds: )sha256:[0-9a-f]{64}$")

    def test_more_runs_are_compared_with_the_first(self):
        with self._project() as root:
            result = run_nava("reproduce", "src/main.nv", "--root", str(root),
                              "--runs", "3", cwd=root)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("over 3 builds", result.stdout)

    def test_the_record_carries_every_source_and_no_time_of_its_own(self):
        with self._project() as root:
            built = run_nava("build", "--project", "-o", str(root / "prog"),
                             "--record", str(root / "record.json"), cwd=root)
            text = (root / "record.json").read_text(encoding="utf-8")
            document = json.loads(text)
        self.assertEqual(built.returncode, 0, built.stderr)
        self.assertEqual(sorted(document["sources"]), ["entry", "json", "util"])
        self.assertEqual(document["packages"], ["json", "util"])
        self.assertIsNone(document["source_date_epoch"])
        self.assertTrue(document["toolchain"]["cc_version"])
        # No path of this machine is in the record: that is what makes it portable
        # enough to compare with somebody else's.
        self.assertNotIn(str(root), text)
        self.assertNotIn("timestamp", text)

    def test_a_second_checkout_produces_the_same_record_and_binary(self):
        with tempfile.TemporaryDirectory() as one, tempfile.TemporaryDirectory() as two:
            first = write_project(pathlib.Path(one) / "demo")
            second = pathlib.Path(two) / "elsewhere" / "demo"
            shutil.copytree(first, second)
            for root in (first, second):
                result = run_nava("build", "--project", "-o", str(root / "prog"),
                                  "--record", str(root / "record.json"), cwd=root)
                self.assertEqual(result.returncode, 0, result.stderr)
            first_record = (first / "record.json").read_text(encoding="utf-8")
            second_record = (second / "record.json").read_text(encoding="utf-8")
            first_binary = reproducible.sha256_file(first / "prog")
            second_binary = reproducible.sha256_file(second / "prog")
            across = run_nava("reproduce", "src/main.nv", "--root", str(second),
                              "--record", str(first / "record.json"), cwd=second)
        self.assertEqual(first_record, second_record,
                         "the same tree in two checkouts wrote two different records")
        self.assertEqual(first_binary, second_binary)
        self.assertEqual(across.returncode, 0, across.stderr)

    def test_the_generated_c_is_the_same_in_two_checkouts(self):
        with tempfile.TemporaryDirectory() as one, tempfile.TemporaryDirectory() as two:
            first = write_project(pathlib.Path(one) / "demo")
            second = pathlib.Path(two) / "further" / "demo"
            shutil.copytree(first, second)
            left = run_nava("emit-c", "src/main.nv", "--root", str(first), cwd=first)
            right = run_nava("emit-c", "src/main.nv", "--root", str(second), cwd=second)
        self.assertEqual(left.returncode, 0, left.stderr)
        self.assertEqual(left.stdout, right.stdout)
        self.assertNotIn(str(first), left.stdout)

    def _project(self):
        import contextlib

        @contextlib.contextmanager
        def manager():
            with tempfile.TemporaryDirectory() as directory:
                yield write_project(pathlib.Path(directory))
        return manager()


class DifferenceTests(unittest.TestCase):
    """What a difference is called when it is found."""

    def test_a_modified_source_is_named(self):
        with self._project() as root:
            run_nava("build", "--project", "-o", str(root / "prog"),
                     "--record", str(root / "record.json"), cwd=root)
            (root / "src" / "main.nv").write_text(
                "edition 2;\n\nfn main() {\n    print(2)\n}\n", encoding="utf-8")
            result = run_nava("reproduce", "src/main.nv", "--root", str(root),
                              "--record", str(root / "record.json"), cwd=root)
        self.assertEqual(result.returncode, 2)
        self.assertIn("E0814", result.stderr)
        self.assertIn("source entry:", result.stderr)
        self.assertIn("source json:", result.stderr)

    def test_source_date_epoch_is_recorded_and_a_change_to_it_is_named(self):
        with self._project() as root:
            pinned = run_nava("build", "--project", "-o", str(root / "prog"),
                              "--record", str(root / "pinned.json"),
                              env={reproducible.EPOCH_VARIABLE: "1700000000"}, cwd=root)
            document = json.loads((root / "pinned.json").read_text(encoding="utf-8"))
            unpinned = run_nava("reproduce", "src/main.nv", "--root", str(root),
                                "--record", str(root / "pinned.json"), cwd=root)
        self.assertEqual(pinned.returncode, 0, pinned.stderr)
        self.assertEqual(document["source_date_epoch"], 1700000000)
        self.assertEqual(unpinned.returncode, 2)
        self.assertIn("E0814", unpinned.stderr)
        self.assertIn("SOURCE_DATE_EPOCH: 1700000000 != None", unpinned.stderr)

    def test_a_different_toolchain_is_a_toolchain_difference_first(self):
        """The phase's negative control: recorded, named, and named first."""
        with self._project() as root:
            run_nava("build", "--project", "-o", str(root / "prog"),
                     "--record", str(root / "record.json"), cwd=root)
            # `cc` and the compiler it stands for are the same program here, but the
            # command that ran is part of the toolchain's identity, and a record that
            # does not say which command it ran cannot be compared with one that does.
            result = run_nava("reproduce", "src/main.nv", "--root", str(root),
                              "--cc", "/usr/bin/cc", "--record", str(root / "record.json"),
                              cwd=root)
        self.assertEqual(result.returncode, 2)
        self.assertIn("E0814", result.stderr)
        lines = [line.strip() for line in result.stderr.splitlines() if line.strip().startswith(("toolchain", "source", "c_digest", "binary_digest"))]
        self.assertTrue(lines, result.stderr)
        self.assertTrue(lines[0].startswith("toolchain.cc_command:"), lines[0])

    def test_a_record_that_is_not_a_record_is_refused(self):
        for text in ('{"format": 2}', '{"format": 1}', "not json at all", '{"format": 1, "toolchain": {}}'):
            with self.subTest(text=text):
                with self.assertRaises(NavaError) as caught:
                    reproducible.parse(text)
                self.assertEqual(caught.exception.code, "E0815")

    def test_the_environment_variable_is_validated(self):
        for value in ("abc", "-1", "1.5", " 12x"):
            with self.subTest(value=value):
                environment = os.environ.copy()
                environment[reproducible.EPOCH_VARIABLE] = value
                previous = os.environ.get(reproducible.EPOCH_VARIABLE)
                os.environ[reproducible.EPOCH_VARIABLE] = value
                try:
                    with self.assertRaises(NavaError) as caught:
                        reproducible.source_date_epoch()
                    self.assertEqual(caught.exception.code, "E0813")
                finally:
                    if previous is None:
                        os.environ.pop(reproducible.EPOCH_VARIABLE, None)
                    else:
                        os.environ[reproducible.EPOCH_VARIABLE] = previous

    def test_a_build_refuses_a_broken_epoch_even_without_a_record(self):
        with self._project() as root:
            result = run_nava("build", "--project", "-o", str(root / "prog"),
                              env={reproducible.EPOCH_VARIABLE: "yesterday"}, cwd=root)
        self.assertEqual(result.returncode, 2)
        self.assertIn("E0813", result.stderr)
        self.assertIn("is not a whole non-negative number", result.stderr)

    def _project(self):
        import contextlib

        @contextlib.contextmanager
        def manager():
            with tempfile.TemporaryDirectory() as directory:
                yield write_project(pathlib.Path(directory))
        return manager()


class CompareTests(unittest.TestCase):
    """`compare` as a function: the empty answer, and the order of the others."""

    def record(self, **changes):
        tool = reproducible.Toolchain(cc_command="cc", cc_version="cc 1.0",
                                      cc_path="/usr/bin/cc", ld_version="ld 1.0",
                                      platform="linux", machine="x86_64")
        values = dict(source_digest="sha256:a", c_digest="sha256:b",
                      binary_digest="sha256:c", toolchain=tool,
                      source_date_epoch=None, sources={"entry": "sha256:d"},
                      packages=["json"])
        values.update(changes)
        return reproducible.BuildRecord(**values)

    def test_equal_records_differ_in_nothing(self):
        self.assertEqual(reproducible.compare(self.record(), self.record()), [])

    def test_every_difference_is_named_and_the_toolchain_comes_first(self):
        other_tool = reproducible.Toolchain(cc_command="clang", cc_version="clang 18",
                                            cc_path="/usr/bin/clang", ld_version="ld 2.0",
                                            platform="linux", machine="aarch64")
        other = self.record(toolchain=other_tool, binary_digest="sha256:z",
                            c_digest="sha256:y", sources={"entry": "sha256:q"})
        differences = reproducible.compare(self.record(), other)
        self.assertTrue(differences[0].startswith("toolchain.cc_command:"), differences)
        self.assertIn("toolchain.machine: 'x86_64' != 'aarch64'", differences)
        self.assertIn("source entry: sha256:d != sha256:q", differences)
        self.assertIn("c_digest: sha256:b != sha256:y", differences)
        self.assertIn("binary_digest: sha256:c != sha256:z", differences)

    def test_the_rendered_record_is_stable(self):
        first = reproducible.render(self.record())
        second = reproducible.render(self.record())
        self.assertEqual(first, second)
        self.assertNotIn(str(ROOT), first)
        parsed = reproducible.parse(first)
        self.assertEqual(reproducible.compare(parsed, self.record()), [])


if __name__ == "__main__":
    unittest.main()
