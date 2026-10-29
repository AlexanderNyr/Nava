"""Building a program out of vendored packages (PLAN.md N13c).

N13b made a lockfile that describes a graph; this file is about the build reading
it. Three claims, in the order they can be broken:

* **A package's module is a module, and it can import another package's module.**
  `import json;` in the entry file finds `vendor/json/json.nv` because `nava.lock`
  names `json`, and `json.nv` finds `util` because the lockfile names that too.
  There is no search path to configure and no build script to write.
* **The lockfile is the boundary, not a suggestion.** A `vendor/` directory the
  lockfile does not name is not a dependency: a module it provides is `E0810`, and
  the message names the package and says to add the constraint and run
  `nava lock`. That is the gate's sentence — *no transitive dependency can appear
  that the lockfile does not name* — turned into a refusal at the place a program
  can break it.
* **A name means one thing.** A local file wins over a package's file (the
  documented order), a package's file may not shadow a standard module (`E0360`),
  and two packages may not provide the same module name (`E0811`) — the compiler
  does not decide by directory order.

The build record is checked too: `nava build --project` prints which packages
contributed a module and `--format json` carries the same list, so "what is in this
binary" is answerable from the build rather than from a reading of the sources.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "tests"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

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

JSON_MANIFEST = """\
[package]
name = "json"
version = "1.2.3"
edition = 2

[dependencies]
util = "^0.2.0"
"""

JSON_MODULE = """\
edition 2;

import util;

pub fn quote(text: str) -> str {
    return str_concat(str_concat("\\"", text), "\\"")
}

pub fn doubled(value: i64) -> i64 {
    return util::twice(value)
}
"""

UTIL_MANIFEST = '[package]\nname = "util"\nversion = "0.2.1"\nedition = 2\n'
UTIL_MODULE = "edition 2;\n\npub fn twice(x: i64) -> i64 {\n    return x * 2\n}\n"


def write_project(root: pathlib.Path, *, lock: bool = True,
                  main: str = MAIN, project: str = PROJECT,
                  json_manifest: str = JSON_MANIFEST,
                  json_module: str = JSON_MODULE) -> pathlib.Path:
    """The demo project: a `json` package that depends on `util`."""
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "src" / "main.nv").write_text(main, encoding="utf-8")
    (root / "nava.toml").write_text(project, encoding="utf-8")
    for name, manifest_text, module_text in (
            ("json", json_manifest, json_module), ("util", UTIL_MANIFEST, UTIL_MODULE)):
        directory = root / "vendor" / name
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "nava.toml").write_text(manifest_text, encoding="utf-8")
        (directory / f"{name}.nv").write_text(module_text, encoding="utf-8")
    if lock:
        result = subprocess.run([*NAVA, "lock"], cwd=str(root), capture_output=True,
                                text=True, timeout=120)
        assert result.returncode == 0, result.stderr
    return root


def relock(root: pathlib.Path) -> None:
    """Write the lockfile again, after a test has changed a vendored package.

    Several cases edit a package and then ask about a *different* rule; without this
    the digest check would answer first, which it is supposed to do.
    """
    result = subprocess.run([*NAVA, "lock"], cwd=str(root), capture_output=True,
                            text=True, timeout=120)
    assert result.returncode == 0, result.stderr


def run_nava(*arguments: str, cwd) -> subprocess.CompletedProcess:
    return subprocess.run([*NAVA, *arguments], capture_output=True, text=True,
                          cwd=str(cwd), timeout=300)


class PackageModuleTests(unittest.TestCase):
    """A locked package's modules are part of the program."""

    def test_a_module_from_a_locked_package_is_importable_and_runs(self):
        with self._project() as root:
            result = run_nava("run", "--project", cwd=root)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ['"hi"', "42"])

    def test_a_package_module_may_import_another_package_module(self):
        """`json` uses `util`: the transitive module is found through the lockfile."""
        with self._project() as root:
            checked = run_nava("check", "--project", cwd=root)
        self.assertEqual(checked.returncode, 0, checked.stderr)

    def test_the_build_record_names_the_packages_that_contributed(self):
        with self._project() as root:
            result = run_nava("build", "--project", "-o", str(root / "prog"), cwd=root)
            binary = subprocess.run([str(root / "prog")], capture_output=True, text=True,
                                    timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Packages: json, util (from nava.lock)", result.stdout)
        self.assertEqual(binary.stdout.splitlines(), ['"hi"', "42"])

    def test_the_json_build_record_carries_the_packages_and_the_network_mode(self):
        with self._project() as root:
            result = run_nava("--format", "json", "build", "--project",
                              "-o", str(root / "prog"), cwd=root)
            locked = (root / "nava.lock").read_text(encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        document = json.loads(result.stdout)
        self.assertEqual(document["packages"], ["json", "util"])
        self.assertEqual(document["network"], "offline")
        for name in document["packages"]:
            self.assertIn(f'name = "{name}"', locked)

    def test_a_project_with_no_packages_records_none(self):
        with self._project() as root:
            (root / "src" / "main.nv").write_text(
                "edition 2;\n\nfn main() {\n    print(7)\n}\n", encoding="utf-8")
            (root / "nava.toml").write_text(
                PROJECT[:PROJECT.index("[dependencies]")], encoding="utf-8")
            (root / "nava.lock").unlink()
            result = run_nava("build", "--project", "-o", str(root / "prog"), cwd=root)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("Packages:", result.stdout)
        self.assertIn("Built:", result.stdout)

    def _project(self, **kwargs):
        import contextlib
        import tempfile

        @contextlib.contextmanager
        def manager():
            with tempfile.TemporaryDirectory() as directory:
                yield write_project(pathlib.Path(directory), **kwargs)
        return manager()


class BoundaryTests(unittest.TestCase):
    """What is not a dependency, and what a name may not do."""

    def test_a_module_from_a_package_the_lockfile_does_not_name_is_e0810(self):
        with self._project(lock=False) as root:
            result = run_nava("check", "--project", cwd=root)
        self.assertEqual(result.returncode, 2)
        self.assertIn("E0810", result.stderr)
        self.assertIn("module 'json' is provided by the package 'json'", result.stderr)
        self.assertIn("nava lock", result.stderr)

    def test_a_module_nobody_provides_lists_where_it_looked(self):
        with self._project() as root:
            (root / "src" / "main.nv").write_text(
                "edition 2;\n\nimport nosuch;\n\nfn main() {\n    print(1)\n}\n",
                encoding="utf-8")
            result = run_nava("check", "--project", cwd=root)
        self.assertEqual(result.returncode, 2)
        self.assertIn("E0352", result.stderr)
        self.assertIn("module 'nosuch' not found", result.stderr)
        self.assertIn("vendor/json/nosuch.nv", result.stderr)

    def test_a_tampered_package_fails_the_build_and_not_only_the_lock_check(self):
        """The phase's package gate, as a build gate: bytes are checked before compiling."""
        with self._project() as root:
            target = root / "vendor" / "util" / "util.nv"
            target.write_text(target.read_text(encoding="utf-8") + "# one more byte\n",
                              encoding="utf-8")
            checked = run_nava("check", "--project", cwd=root)
            built = run_nava("build", "--project", "-o", str(root / "prog"), cwd=root)
        for result in (checked, built):
            self.assertEqual(result.returncode, 2)
            self.assertIn("E0804", result.stderr)
            self.assertIn("util does not match its digest", result.stderr)

    def test_a_local_module_wins_over_a_package_module(self):
        """The documented order: the project's own file first."""
        with self._project() as root:
            (root / "json.nv").write_text(
                "edition 2;\n\npub fn quote(text: str) -> str {\n    return \"local\"\n}\n\n"
                "pub fn doubled(value: i64) -> i64 {\n    return 0\n}\n", encoding="utf-8")
            (root / "src" / "main.nv").write_text(
                "edition 2;\n\nimport json;\n\nfn main() {\n    print(json::quote(\"x\"))\n}\n",
                encoding="utf-8")
            (root / "nava.toml").write_text(
                """[package]
name = "demo"
version = "0.1.0"
edition = 2

[build]
entry = "src/main.nv"
""", encoding="utf-8")
            (root / "nava.lock").unlink()
            result = run_nava("run", "--project", cwd=root)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "local")

    def test_a_package_module_may_not_shadow_a_standard_module(self):
        with self._project() as root:
            (root / "vendor" / "json" / "math.nv").write_text(
                "edition 2;\n\npub fn abs(x: i64) -> i64 {\n    return x\n}\n",
                encoding="utf-8")
            relock(root)
            (root / "src" / "main.nv").write_text(
                "edition 2;\n\nimport math;\n\nfn main() {\n    print(math::abs(-1))\n}\n",
                encoding="utf-8")
            result = run_nava("check", "--project", cwd=root)
        self.assertEqual(result.returncode, 2)
        self.assertIn("E0360", result.stderr)
        self.assertIn("package 'json' provides module 'math', which is a standard module",
                      result.stderr)

    def test_two_packages_may_not_provide_the_same_module(self):
        with self._project() as root:
            (root / "vendor" / "util" / "json.nv").write_text(
                "edition 2;\n\npub fn twice(x: i64) -> i64 {\n    return x * 3\n}\n",
                encoding="utf-8")
            relock(root)
            result = run_nava("check", "--project", cwd=root)
        self.assertEqual(result.returncode, 2)
        self.assertIn("E0811", result.stderr)
        self.assertIn("module 'json' is provided by two packages", result.stderr)
        self.assertIn("vendor/json and vendor/util", result.stderr)

    def test_the_loader_reads_the_lockfile_rather_than_the_directory(self):
        """A second, unlocked `vendor/` directory changes nothing but the message."""
        with self._project() as root:
            extra = root / "vendor" / "extra"
            extra.mkdir()
            (extra / "nava.toml").write_text(
                '[package]\nname = "extra"\nversion = "1.0.0"\nedition = 2\n',
                encoding="utf-8")
            (extra / "extra.nv").write_text(
                "edition 2;\n\npub fn one() -> i64 {\n    return 1\n}\n", encoding="utf-8")
            good = run_nava("check", "--project", cwd=root)
            (root / "src" / "main.nv").write_text(
                "edition 2;\n\nimport extra;\n\nfn main() {\n    print(extra::one())\n}\n",
                encoding="utf-8")
            bad = run_nava("check", "--project", cwd=root)
        self.assertEqual(good.returncode, 0, good.stderr)
        self.assertEqual(bad.returncode, 2)
        self.assertIn("E0810", bad.stderr)
        self.assertIn("the package 'extra'", bad.stderr)

    def _project(self, **kwargs):
        import contextlib
        import tempfile

        @contextlib.contextmanager
        def manager():
            with tempfile.TemporaryDirectory() as directory:
                yield write_project(pathlib.Path(directory), **kwargs)
        return manager()


class NetworkTests(unittest.TestCase):
    """Offline is the only mode, and the option that asks for the other one says so."""

    def test_network_is_refused_with_its_own_code(self):
        with self._project() as root:
            result = run_nava("check", "--project", "--network", cwd=root)
        self.assertEqual(result.returncode, 2)
        self.assertIn("E0812", result.stderr)
        self.assertIn("--network is not available", result.stderr)
        self.assertIn("offline is the only mode", result.stderr)

    def test_network_is_refused_before_anything_is_read(self):
        """Even with no manifest at all, the answer is about the option, not the tree."""
        with self._project() as root:
            empty = root / "empty"
            empty.mkdir()
            result = run_nava("check", "--project", "--network", cwd=empty)
        self.assertEqual(result.returncode, 2)
        self.assertIn("E0812", result.stderr)

    def test_offline_is_accepted_and_recorded(self):
        with self._project() as root:
            result = run_nava("--format", "json", "check", "--project", "--offline",
                              cwd=root)
        self.assertEqual(result.returncode, 0, result.stderr)
        document = json.loads(result.stdout)
        self.assertEqual(document["network"], "offline")
        self.assertEqual(document["packages"], ["json", "util"])

    def _project(self):
        import contextlib
        import tempfile

        @contextlib.contextmanager
        def manager():
            with tempfile.TemporaryDirectory() as directory:
                yield write_project(pathlib.Path(directory))
        return manager()


if __name__ == "__main__":
    unittest.main()
