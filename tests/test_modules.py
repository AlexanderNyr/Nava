"""Modules: resolution, diagnostics and multi-file programs (PLAN.md phase N6).

The gate has five parts:

1. **Resolution** — a module is one file, found next to the entry file or in
   the packaged standard library, with no configurable search path; `--root`
   moves the project boundary.
2. **Diagnostics** — a missing, duplicated, cyclic, escaping, shadowing or
   version-mismatched import, `main` in a module, an unqualified call into
   another module and a qualified call without an import each produce their own
   code, at the location in the file that caused them.
3. **Multi-file programs** — real programs are compiled, checked, built and run
   from a clean directory, and the generated C is one translation unit.
4. **Fixtures** — `tests/fixtures/modules.json` drives the error, warning and
   run cases, so a case cannot be silently dropped.
5. **Capability of the boundary** — an import cannot escape the directory it
   was resolved in, even through a symbolic link.
"""
from __future__ import annotations

import contextlib
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nava_lang import modules  # noqa: E402
from nava_lang.cli import compile_source  # noqa: E402
from nava_lang.diagnostics import format_human  # noqa: E402
from nava_lang.model import NavaError  # noqa: E402

CC = shutil.which("cc")
FIXTURES = ROOT / "tests/fixtures/modules"
MODULE_CASES = ROOT / "tests/fixtures/modules.json"


@contextlib.contextmanager
def project(files: dict[str, str]):
    """A temporary project directory populated with `files`."""
    with tempfile.TemporaryDirectory() as temp:
        root = pathlib.Path(temp)
        for name, text in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        yield root


def nava(*arguments, stdin: str = "", timeout: int = 120):
    return subprocess.run([sys.executable, str(ROOT / "nava"), *arguments],
                          input=stdin, capture_output=True, text=True, timeout=timeout)


def error_for(root: pathlib.Path, entry: str = "main.nv", **options) -> NavaError:
    try:
        compile_source((root / entry).read_text(encoding="utf-8"),
                       path=root / entry, **options)
    except NavaError as exc:
        return exc
    raise AssertionError("expected a NavaError, but the project compiled")


class ModuleFrontendTests(unittest.TestCase):
    def test_import_must_precede_every_function(self):
        with project({"main.nv": "fn main() { print(1) }\nimport math\n"}) as root:
            error = error_for(root)
        self.assertEqual(error.code, "E0354")
        self.assertEqual((error.loc.line, error.loc.column), (2, 1))
        self.assertIn("before every function", error.message)

    def test_missing_module_lists_both_search_locations(self):
        with project({"main.nv": "import nosuch\n\nfn main() { print(1) }\n"}) as root:
            error = error_for(root)
        self.assertEqual(error.code, "E0352")
        self.assertIn("'nosuch' not found", error.message)
        self.assertIn(str(root / "nosuch.nv"), error.hint)
        self.assertIn("nava_lang/stdlib/nosuch.nv", error.hint)

    def test_module_imported_twice_is_an_error(self):
        with project({
            "main.nv": "import math\nimport math\n\nfn main() { print(math::abs(-1)) }\n",
        }) as root:
            error = error_for(root)
        self.assertEqual(error.code, "E0355")
        self.assertEqual((error.loc.line, error.loc.column), (2, 8))

    def test_a_cycle_names_every_module_and_its_location(self):
        with project({
            "main.nv": "import alpha\n\nfn main() { print(alpha::value()) }\n",
            "alpha.nv": "import beta\n\nfn value() -> i64 { return beta::value() }\n",
            "beta.nv": "import alpha\n\nfn value() -> i64 { return alpha::value() }\n",
        }) as root:
            error = error_for(root)
        self.assertEqual(error.code, "E0353")
        self.assertIn("alpha -> beta -> alpha", error.message)
        # The location is in the file whose import closes the cycle.
        self.assertTrue(error.loc.path.endswith("beta.nv"), error.loc.path)
        self.assertEqual((error.loc.line, error.loc.column), (1, 8))

    def test_a_module_must_not_define_main(self):
        with project({
            "main.nv": "import helper\n\nfn main() { print(helper::value()) }\n",
            "helper.nv": "fn value() -> i64 { return 1 }\n\nfn main() { print(2) }\n",
        }) as root:
            error = error_for(root)
        self.assertEqual(error.code, "E0357")
        self.assertIn("must not define fn main", error.message)

    def test_an_unqualified_call_into_another_module_names_the_fix(self):
        with project({
            "main.nv": "import math\n\nfn main() { print(abs(-5)) }\n",
        }) as root:
            error = error_for(root)
        self.assertEqual(error.code, "E0358")
        self.assertIn("defined in module 'math'", error.message)
        self.assertEqual(error.hint, "call it as math::abs")

    def test_a_qualified_call_without_an_import_is_reported(self):
        with project({"main.nv": "fn main() { print(math::abs(-5)) }\n"}) as root:
            error = error_for(root)
        self.assertEqual(error.code, "E0359")
        self.assertIn("module 'math' is not imported", error.message)
        self.assertIn("import math", error.hint)

    def test_a_local_module_may_not_shadow_a_standard_module(self):
        with project({
            "main.nv": "import math\n\nfn main() { print(math::abs(-5)) }\n",
            "math.nv": "fn abs(value: i64) -> i64 { return value }\n",
        }) as root:
            error = error_for(root)
        self.assertEqual(error.code, "E0360")
        self.assertIn("shadows a standard module", error.message)
        # The hint must not contain a machine-specific absolute path.
        self.assertNotIn("/", error.hint)

    def test_the_standard_library_version_is_checked(self):
        with tempfile.TemporaryDirectory() as temp:
            stdlib = pathlib.Path(temp) / "stdlib"
            shutil.copytree(modules.stdlib_path(), stdlib)
            math = stdlib / "math.nv"
            math.write_text(math.read_text(encoding="utf-8").replace("return 1",
                                                                     "return 2", 1),
                            encoding="utf-8")
            with project({"main.nv": "import math\n\nfn main() { print(math::abs(-1)) }\n"}) as root:
                error = error_for(root, stdlib_root=stdlib)
        self.assertEqual(error.code, "E0361")
        self.assertIn("declares version 2", error.message)
        self.assertIn(f"standard library version {modules.STDLIB_VERSION}", error.hint)

    def test_a_standard_module_must_declare_its_version(self):
        with tempfile.TemporaryDirectory() as temp:
            stdlib = pathlib.Path(temp) / "stdlib"
            shutil.copytree(modules.stdlib_path(), stdlib)
            (stdlib / "extra.nv").write_text("fn twice(x: i64) -> i64 { return x * 2 }\n",
                                            encoding="utf-8")
            with project({"main.nv": "import extra\n\nfn main() { print(extra::twice(2)) }\n"}) as root:
                error = error_for(root, stdlib_root=stdlib)
        self.assertEqual(error.code, "E0361")
        self.assertIn("does not declare fn module_version", error.message)

    def test_an_import_cannot_escape_the_project_boundary(self):
        with tempfile.TemporaryDirectory() as temp:
            outside = pathlib.Path(temp) / "outside"
            outside.mkdir()
            (outside / "hidden.nv").write_text("fn value() -> i64 { return 1 }\n",
                                              encoding="utf-8")
            root = outside / "project"
            root.mkdir()
            (root / "hidden.nv").symlink_to(outside / "hidden.nv")
            (root / "main.nv").write_text(
                "import hidden\n\nfn main() { print(hidden::value()) }\n", encoding="utf-8")
            error = error_for(root)
        self.assertEqual(error.code, "E0356")
        self.assertIn("resolves outside the project boundary", error.message)
        self.assertIn(str(outside / "hidden.nv"), error.hint)

    def test_unknown_function_in_a_module(self):
        with project({"main.nv": "import math\n\nfn main() { print(math::nosuch(1)) }\n"}) as root:
            error = error_for(root)
        self.assertEqual(error.code, "E0325")
        self.assertIn("module 'math' has no function 'nosuch'", error.message)

    def test_a_builtin_cannot_be_reached_through_a_module(self):
        with project({"main.nv": "import math\n\nfn main() { math::print(1) }\n"}) as root:
            error = error_for(root)
        self.assertEqual(error.code, "E0325")
        self.assertIn("has no function 'print'", error.message)

    def test_an_unused_import_is_a_warning_not_an_error(self):
        with project({"main.nv": "import math\n\nfn main() { print(42) }\n"}) as root:
            compilation = compile_source((root / "main.nv").read_text(encoding="utf-8"),
                                         path=root / "main.nv")
        self.assertEqual([warning.code for warning in compilation.warnings], ["W0002"])
        self.assertIn("unused import 'math'", compilation.warnings[0].message)

    def test_diagnostics_carry_the_path_of_the_file_they_come_from(self):
        with project({
            "main.nv": "import broken\n\nfn main() { print(broken::value()) }\n",
            "broken.nv": "fn value() -> i64 {\n    return true\n}\n",
        }) as root:
            error = error_for(root)
        self.assertTrue(error.loc.path.endswith("broken.nv"), error.loc.path)
        rendered = format_human(str(root / "main.nv"), error.diagnostic)
        self.assertTrue(rendered.startswith(str(root / "broken.nv")), rendered)

    def test_an_in_memory_source_cannot_resolve_an_import(self):
        error = None
        try:
            compile_source("import math\n\nfn main() { print(math::abs(-1)) }\n")
        except NavaError as caught:
            error = caught
        self.assertIsNotNone(error)
        self.assertEqual(error.code, "E0352")
        self.assertIn("compile a file", error.hint)

    def test_a_module_may_import_another_module(self):
        with project({
            "main.nv": "import outer\n\nfn main() { print(outer::value()) }\n",
            "outer.nv": "import inner\n\nfn value() -> i64 { return inner::value() + 1 }\n",
            "inner.nv": "fn value() -> i64 { return 41 }\n",
        }) as root:
            compilation = compile_source((root / "main.nv").read_text(encoding="utf-8"),
                                         path=root / "main.nv")
        self.assertEqual(compilation.warnings, [])
        self.assertEqual(compilation.code.count("int main(void)"), 1)

    def test_the_project_root_can_be_moved(self):
        files = {
            "app/main.nv": "import shared\n\nfn main() { print(shared::value()) }\n",
            "lib/shared.nv": "fn value() -> i64 { return 7 }\n",
        }
        with project(files) as root:
            moved = root / "lib"
            compilation = compile_source(
                (root / "app/main.nv").read_text(encoding="utf-8"),
                path=root / "app/main.nv", root=moved)
            self.assertIn("nv_fn_", compilation.code)
            # With the default root the module is not next to the entry file.
            error = error_for(root, entry="app/main.nv")
        self.assertEqual(error.code, "E0352")


@unittest.skipUnless(CC, "a C compiler is required for the module runtime tests")
class ModuleRuntimeTests(unittest.TestCase):
    def test_the_multi_file_example_runs(self):
        expected = "3\n7\n19\n23\n42\n88\ntrue\n182\n2\n30.333333333333332\ntrue\n6\n"
        result = nava("run", "--isolation", "off", str(ROOT / "examples/modules/main.nv"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, expected)

    def test_check_build_and_run_agree(self):
        fixture = FIXTURES / "multi_file"
        checked = nava("check", str(fixture / "main.nv"))
        self.assertEqual(checked.returncode, 0, checked.stderr)
        self.assertIn("OK:", checked.stdout)
        with tempfile.TemporaryDirectory() as temp:
            binary = pathlib.Path(temp) / "program"
            built = nava("build", str(fixture / "main.nv"), "-o", str(binary))
            self.assertEqual(built.returncode, 0, built.stderr)
            run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(run.stdout, "22\n22\n9\n")

    def test_the_generated_c_is_one_translation_unit(self):
        fixture = FIXTURES / "multi_file"
        with tempfile.TemporaryDirectory() as temp:
            output = pathlib.Path(temp) / "program.c"
            emitted = nava("emit-c", str(fixture / "main.nv"), "-o", str(output))
            self.assertEqual(emitted.returncode, 0, emitted.stderr)
            code = output.read_text(encoding="utf-8")
            built = subprocess.run([CC, "-std=c11", "-pedantic-errors", "-O2",
                                    str(output), "-o", str(pathlib.Path(temp) / "program")],
                                   capture_output=True, text=True, timeout=300)
        self.assertEqual(built.returncode, 0, built.stderr)
        self.assertEqual(code.count("int main(void)"), 1)
        # Every module function is emitted exactly once, as a static function:
        # the C entry point is the only way into the whole program.
        definitions = [line for line in code.splitlines()
                       if line.startswith("static ") and line.endswith("{")
                       and "nv_fn_" in line]
        self.assertEqual(len(definitions), 4, definitions)
        self.assertEqual(len({line.split("(")[0] for line in definitions}), 4)

    def test_module_fixture_cases(self):
        cases = json.loads(MODULE_CASES.read_text(encoding="utf-8"))["cases"]
        on_disk = {item.name for item in FIXTURES.iterdir() if item.is_dir()}
        self.assertEqual(on_disk, {case["dir"] for case in cases},
                         "every module fixture directory needs a manifest entry")
        for case in cases:
            with self.subTest(case=case["dir"]):
                entry = FIXTURES / case["dir"] / case["entry"]
                if case["kind"] == "run":
                    result = nava("run", "--isolation", "off", str(entry), stdin=case["stdin"])
                    self.assertEqual(result.returncode, case["exit_code"], result.stderr)
                    self.assertEqual(result.stdout, case["stdout"])
                    continue
                result = nava("--format", "json", "check", str(entry))
                document = json.loads(result.stdout)
                self.assertEqual(len(document["diagnostics"]), 1, document)
                diagnostic = document["diagnostics"][0]
                self.assertEqual(diagnostic["code"], case["code"])
                self.assertTrue(diagnostic["path"].endswith(case["file"]), diagnostic["path"])
                self.assertEqual((diagnostic["range"]["start"]["line"],
                                  diagnostic["range"]["start"]["column"]),
                                 (case["line"], case["column"]))
                self.assertEqual(diagnostic["message"], case["message"])
                self.assertEqual(diagnostic.get("hint"), case["hint"])
                self.assertEqual(result.returncode, 0 if case["kind"] == "warning" else 2)
                if case["kind"] == "warning":
                    self.assertEqual(diagnostic["severity"], "warning")
                    self.assertEqual(document["status"], "ok")
                    # In human mode the same warning is rendered on stderr.
                    human = nava("check", str(entry))
                    self.assertEqual(human.returncode, 0, human.stderr)
                    self.assertIn("warning[" + case["code"] + "]", human.stderr)


if __name__ == "__main__":
    unittest.main()
