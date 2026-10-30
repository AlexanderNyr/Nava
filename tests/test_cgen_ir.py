"""The C backend as a second reader of the shared IR (PLAN.md N14d).

PLAN.md records N14 as *one IR, two backends*. N14c gave the IR arrays; this
increment makes the C backend **consume** it, which is the claim the milestones were
named for and the reason the differential is worth anything: the two backends no
longer disagree about what a program means by construction, because they read the
same lowering.

The gate is three-way and it is the only gate that matters here: the same program,
printed from the IR, printed by the old AST walk (`NAVA_C_FROM_IR=0`) and compiled by
the native backend, compared on stdout, stderr and exit status. A fourth test asserts
that the IR path is not *silently* skipped — every fixture in the subset must carry the
marker — because a fallback nobody notices is a test that passes without testing
anything.
"""
from __future__ import annotations

import os
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from nava_lang import cgen, cgen_ir                      # noqa: E402
from nava_lang.cli import load_program                   # noqa: E402
from nava_lang.diagnostics import NavaError              # noqa: E402
from nava_lang.native import ir, lower                   # noqa: E402

NAVA = [sys.executable, str(ROOT / "nava")]
FIXTURES = ROOT / "tests" / "fixtures" / "positive"

#: The fixtures the IR can express today. Every one of them must take the IR path:
#: this list is the "not silently skipped" half of the gate.
IN_THE_SUBSET = ("arrays_basic", "arrays_borrow", "arrays_sieve", "arrays_sort",
                 "for_loops", "scoping", "short_circuit", "while_recheck", "arithmetic",
                 "functions", "hello", "exit_code", "runtime_div_zero",
                 "runtime_overflow", "structs_basic", "structs_copy", "strings",
                 "numerical", "fmt_table", "read_i64", "runtime_bad_input",
                 "enums_basic", "enums_state", "defer_labels", "results_basic",
                 "results_chain", "slices_basic", "struct_arrays", "floats",
                 "map_basics", "set_basics")
#: Programs the IR expresses but this printer does not print — a narrower gap than a
#: refusal, and named separately so that neither becomes the other by accident.
#: `self_test` lowers since a function became a value; what it still needs is the
#: `String` builder (`string_new`/`string_push`/`string_text`), `str_split` and the
#: three `clock_*` builtins.
LOWERED_NOT_PRINTED = ("self_test",)
#: Programs whose C must still come from the AST walk, because the lowering refuses
#: them by name (`E0901`). Empty: the lowering now accepts every fixture, which is
#: what makes the second list the only gap left.
OUTSIDE_THE_SUBSET = ()
MARKER = "A lowering of the shared IR"


def program_of(source: str, path: pathlib.Path | None = None):
    return load_program(source, path=path)


def fixture(name: str) -> pathlib.Path:
    return FIXTURES / f"{name}.nv"


def lowered(name: str):
    """The fixture's IR module, or None if the lowering refused it."""
    path = fixture(name)
    program = program_of(path.read_text(encoding="utf-8"), path=path)
    try:
        return lower.lower(program)
    except NavaError as error:
        if getattr(error, "code", None) == "E0901":
            return None
        raise


def build(source: pathlib.Path, output: pathlib.Path, from_ir: bool):
    """Build through the CLI, choosing which of the C backend's paths prints it."""
    environment = {**os.environ}
    if from_ir:
        environment.pop("NAVA_C_FROM_IR", None)
    else:
        environment["NAVA_C_FROM_IR"] = "0"
    result = subprocess.run([*NAVA, "build", str(source), "-o", str(output)],
                            capture_output=True, text=True, timeout=300,
                            env=environment)
    if result.returncode != 0:
        raise AssertionError(f"the C backend failed to build {source.name}: "
                             f"{result.stderr}")
    return output


def build_native(source: pathlib.Path, output: pathlib.Path) -> pathlib.Path:
    result = subprocess.run([*NAVA, "build", str(source), "-o", str(output),
                             "--backend", "native"],
                            capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise AssertionError(f"the native backend failed to build {source.name}: "
                             f"{result.stderr}")
    return output


def run_binary(path: pathlib.Path) -> tuple[str, str, int]:
    result = subprocess.run([str(path)], capture_output=True, text=True, timeout=300)
    return result.stdout, result.stderr, result.returncode


def emitted(source: str, from_ir: bool = True, path=None) -> str:
    """The C text of a program, through one of the two paths."""
    environment = {"NAVA_C_FROM_IR": "0"} if not from_ir else {}
    with mock.patch.dict(os.environ, environment, clear=False):
        if not from_ir:
            os.environ["NAVA_C_FROM_IR"] = "0"
        else:
            os.environ.pop("NAVA_C_FROM_IR", None)
        return cgen.generate(program_of(source, path=path))


class CFromIRTests(unittest.TestCase):
    """The IR is what the C backend reads, for everything it can express."""

    def test_every_program_in_the_subset_is_printed_from_the_ir(self):
        for name in IN_THE_SUBSET:
            with self.subTest(fixture=name):
                module = lowered(name)
                self.assertIsNotNone(module, f"{name} was refused by the lowering")
                self.assertTrue(cgen_ir.supports(module),
                                f"{name} lowers to IR the C printer cannot print")
                text = emitted(fixture(name).read_text(encoding="utf-8"),
                               path=fixture(name))
                self.assertIn(MARKER, text,
                              f"{name} was printed by the AST walk, not from the IR")

    def test_a_program_outside_the_subset_still_comes_from_the_ast(self):
        """A refusal is a gap in one lowering, not a program that cannot be built."""
        for name in OUTSIDE_THE_SUBSET:
            with self.subTest(fixture=name):
                self.assertIsNone(lowered(name),
                                  f"{name} lowers now; move it to IN_THE_SUBSET")
                text = emitted(fixture(name).read_text(encoding="utf-8"),
                               path=fixture(name))
                self.assertNotIn(MARKER, text)

    def test_every_lowered_program_named_unprintable_says_why(self):
        """A gap is a named instruction, not a program that quietly takes one path.

        `LOWERED_NOT_PRINTED` is the plan for this printer; an entry without a
        reason would be a gap nobody could close.
        """
        for name in LOWERED_NOT_PRINTED:
            with self.subTest(fixture=name):
                path = fixture(name)
                program = program_of(path.read_text(encoding="utf-8"), path=path)
                reason = cgen_ir.unprintable(lower.lower(program),
                                             cgen.function_types(program))
                self.assertIsNotNone(reason, f"{name} is printable from the IR now")

    def test_a_program_the_ir_expresses_but_this_printer_does_not(self):
        """The gap between the two halves of the boundary is stated, not implied.

        A module that lowers is not the same as a module this printer can print, and a
        list for each keeps a construct from moving between them unnoticed.
        """
        for name in LOWERED_NOT_PRINTED:
            with self.subTest(fixture=name):
                module = lowered(name)
                self.assertIsNotNone(module, f"{name} no longer lowers")
                self.assertFalse(cgen_ir.supports(module),
                                 f"{name} is printable now; move it to IN_THE_SUBSET")
                text = emitted(fixture(name).read_text(encoding="utf-8"),
                               path=fixture(name))
                self.assertNotIn(MARKER, text)

    def test_the_names_in_the_artefact_do_not_depend_on_the_path(self):
        """A function's C name is the program's, so a crash report means one thing.

        The IR carries only reachable functions, so the AST path may define more of them
        than the IR does; what must not differ is the *name* of a function both printed.
        """
        names = re.compile(r"^static [^\n]*?\b(nv_fn_\d+)\(", re.MULTILINE)
        for name in ("structs_basic", "arrays_basic"):
            with self.subTest(fixture=name):
                source = fixture(name).read_text(encoding="utf-8")
                from_ir = set(names.findall(emitted(source, from_ir=True)))
                from_ast = set(names.findall(emitted(source, from_ir=False)))
                self.assertTrue(from_ir, f"{name} printed no function from the IR")
                self.assertLessEqual(from_ir, from_ast,
                                     f"{name} names a function the AST path does not")

    def test_the_three_paths_agree_on_output_stderr_and_status(self):
        programs = {name: fixture(name) for name in
                    ("arrays_basic", "arrays_sort", "for_loops", "short_circuit",
                     "exit_code", "runtime_div_zero", "runtime_overflow",
                     "structs_basic", "structs_copy")}
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            for name, source in programs.items():
                with self.subTest(fixture=name):
                    from_ir = run_binary(build(source, directory / f"{name}-ir", True))
                    from_ast = run_binary(build(source, directory / f"{name}-ast", False))
                    native = run_binary(build_native(source, directory / f"{name}-native"))
                    self.assertEqual(from_ir, from_ast,
                                     "the two C paths disagree about the program")
                    self.assertEqual(from_ir, native,
                                     "the C backend and the native backend disagree")

    def test_the_body_says_it_came_from_the_ir(self):
        text = emitted(fixture("arrays_sort").read_text(encoding="utf-8"))
        self.assertIn(MARKER, text)
        # One comment per function, naming the link-time symbol, so a crash report and
        # the emitted source speak about the same thing.
        self.assertIn("/* nava.entry.main */", text)
        self.assertIn("/* nava.entry.sort */", text)

    def test_every_function_is_defined_once_under_the_name_the_ast_path_used(self):
        text = emitted(fixture("arrays_sort").read_text(encoding="utf-8"))
        definitions = re.findall(r"^static .*?(nv_fn_\d+)\(.*\) \{$", text, re.MULTILINE)
        self.assertEqual(len(definitions), len(set(definitions)),
                         "a function is defined twice")
        module = lowered("arrays_sort")
        self.assertEqual(len(definitions), len(module.functions),
                         "one definition per function of the program")
        self.assertIn("nv_fn_0", definitions)

    def test_the_printer_is_deterministic(self):
        source = fixture("arrays_borrow").read_text(encoding="utf-8")
        self.assertEqual(emitted(source), emitted(source))

    def test_array_access_goes_through_the_checked_helpers(self):
        text = emitted(fixture("arrays_basic").read_text(encoding="utf-8"))
        body = text.split(MARKER, 1)[1]
        self.assertIn("nv_arr_i64_get(", body)
        self.assertIn("nv_arr_i64_set(", body)
        self.assertIn("nv_check_index(", body)
        self.assertIn("nv_arr_i64_free(", body)
        raw = [line.strip() for line in body.splitlines()
               if ".data[" in line and "nv_check_index" not in line]
        self.assertEqual(raw, [], "the IR body indexes a buffer without the check")

    def test_the_index_is_checked_before_the_value_is_evaluated(self):
        """The order the language states, and the order both backends keep.

        `a[1] = mark()` must not call `mark`: the index is validated first. The IR
        says so with an instruction of its own (`check`), so the printer cannot lose
        the order by choosing a different evaluation order for the store.
        """
        source = """\
fn mark() -> i64 {
    print("must not print")
    return 0
}

fn main() {
    let a: [i64] = [1]
    a[1] = mark()
    print("unreachable")
}
"""
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            path = directory / "order.nv"
            path.write_text(source, encoding="utf-8")
            from_ir = run_binary(build(path, directory / "ir", True))
            from_ast = run_binary(build(path, directory / "ast", False))
            native = run_binary(build_native(path, directory / "native"))
        self.assertEqual(from_ir[0], "", "the value expression ran before the check")
        self.assertEqual(from_ir[2], 1)
        self.assertIn("array index 1 is out of bounds for length 1", from_ir[1])
        self.assertEqual(from_ir, from_ast)
        self.assertEqual(from_ir, native)

    def test_the_ir_states_the_check_before_the_store(self):
        module = lowered("arrays_basic")
        main = next(function for function in module.functions if function.name == "main")
        instructions = [instruction for block in main.blocks
                        for instruction in block.instructions]
        checks = [index for index, instruction in enumerate(instructions)
                  if isinstance(instruction, ir.CheckIndex)]
        stores = [index for index, instruction in enumerate(instructions)
                  if isinstance(instruction, ir.IndexStore)]
        self.assertTrue(checks, "no check in a program that writes through an index")
        self.assertLess(checks[0], stores[0])

    def test_the_escape_hatch_asks_for_the_old_path(self):
        source = fixture("arrays_basic").read_text(encoding="utf-8")
        self.assertNotIn(MARKER, emitted(source, from_ir=False))

    def test_a_str_comparison_prints_as_strcmp_and_never_as_pointer_equality(self):
        """`==` on two `str`s means text. Since N14f the IR can say so.

        It says it with `TextCompare`, which the C printer turns into `strcmp(...) == 0`
        — so a string comparison is never `v1 == v2` on two `const char *`, which is a
        question about addresses and not about text. The IR keeps the instruction rather
        than a `Cmp` because the two backends' string representations differ and neither
        may be tempted to compare the representations.
        """
        source = 'fn main() -> i64 {\n    print("a" == "a")\n    return 0\n}\n'
        module = lower.lower(program_of(source))
        instructions = [item for function in module.functions
                        for block in function.blocks for item in block.instructions]
        self.assertTrue(any(isinstance(item, ir.TextCompare) for item in instructions))
        self.assertFalse(any(isinstance(item, ir.Cmp) for item in instructions),
                         "a text comparison was stated as a pointer comparison")
        text = emitted(source)
        self.assertIn(MARKER, text)
        self.assertIn("strcmp(", text)
        # A `==` between two locals would be the pointer comparison this instruction
        # exists to prevent.
        self.assertIsNone(re.search(r"v\d+ == v\d+;", text))

    def test_an_enum_is_declared_with_the_name_the_checker_gave_it(self):
        """The IR carries the written type (`Shape`); the C name is the checker's.

        `nv_enum_Shape` for a declared enum and `nv_enum_Result_i64_str` for an
        instantiation, both from one rule (`checker.mangle`), so a crash report or a
        symbol in the artefact means the same thing whichever path printed the file.
        And a `match` is printed as what it is: a comparison against the tag.
        """
        source = fixture("enums_basic").read_text(encoding="utf-8")
        text = emitted(source, path=fixture("enums_basic"))
        self.assertIn(MARKER, text)
        self.assertIn("typedef struct nv_enum_Shape {", text)
        self.assertIn("int32_t _tag;", text)
        self.assertIn("._tag", text)
        self.assertIn("._payload._v_Circle._f0", text)
        self.assertIn("._payload = { ._v_Circle = { ._f0 = v", text)

    def test_a_generic_with_one_instantiation_is_printed_from_the_ir(self):
        source = ("edition 2;\n"
                  "fn identity<T>(x: T) -> T { return x }\n"
                  "fn main() -> i64 {\n    print(identity(7))\n    return 0\n}\n")
        self.assertIn(MARKER, emitted(source))

    def test_two_instantiations_are_refused_by_name_rather_than_confused(self):
        """An instantiation carries no type argument in its name, so two would share
        one symbol: a refusal now, not a wrong binary later."""
        source = ("edition 2;\n"
                  "fn identity<T>(x: T) -> T { return x }\n"
                  "fn main() -> i64 {\n"
                  "    print(identity(1))\n    print(identity(true))\n    return 0\n}\n")
        with self.assertRaises(NavaError) as raised:
            lower.lower(program_of(source))
        self.assertEqual(raised.exception.code, "E0901")
        self.assertIn("more than one instantiation", str(raised.exception))
        self.assertNotIn(MARKER, emitted(source))


if __name__ == "__main__":
    unittest.main()
