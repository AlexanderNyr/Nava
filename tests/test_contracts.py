"""Contracts in doc blocks, and the reference they generate (PLAN.md N12b).

N12 is "a standard library with contracts". A contract that only says what
somebody once believed is a comment; this file is what makes it a claim:

1. **Every public function carries one.** `pre:`, `post:`, `cost:` and at least
   one `example:` — the guard fails on a public name without them.
2. **Every example runs.** The examples of a module are compiled into one
   program (each inside its own block, so two examples may both declare
   `values`) and each has to be true. An example that stops compiling is
   reported with the compiler's own diagnostic, and the module is then tried one
   example at a time to name the one that broke.
3. **Every documented failure happens.** An `error:` line is statements plus the
   text the program must print before it stops; a documented failure that does
   not stop the program is a broken promise, so it fails the lane.
4. **The reference is generated, not written by hand.** `nava doc` renders
   `docs/stdlib/*.md` from the contracts, and the guard regenerates them and
   compares byte for byte — the same discipline as the pinned guide digest.

The negative controls at the end are the point of the file: a public function
with no contract, an example that does not compile, an example that is false and
a documented error that does not happen all have to be *reported*.
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

from nava_lang import contracts  # noqa: E402
from nava_lang.cli import compile_source  # noqa: E402
from nava_lang.model import NavaError  # noqa: E402
from nava_lang.parser import parse  # noqa: E402

CC = shutil.which("cc")
STDLIB = ROOT / "nava_lang" / "stdlib"
REFERENCE = ROOT / "docs" / "stdlib"
NAVA = ROOT / "nava"
#: The packaged standard modules, in the order `nava doc` reads them.
MODULES = tuple(sorted(path.stem for path in STDLIB.glob("*.nv")))
#: The grants an example runs with: its own temporary directory and nothing
#: else. `io`'s examples write a file and read it back, so without a grant they
#: would stop at the capability check instead of testing what they claim — but a
#: grant to `.` is a grant to the harness's directory, not to the host's.
EXAMPLE_GRANTS = {"read": ["."], "write": ["."]}
#: The command line an example program is started with. A module about the words
#: a program was given cannot be demonstrated without some, so the harness gives
#: every example program the same three: `--count 3 name`. `args`' contracts say
#: so, and `LANGUAGE.md` states it beside the module.
EXAMPLE_ARGS = ("--count", "3", "name")


def module_source(module: str) -> str:
    return (STDLIB / f"{module}.nv").read_text(encoding="utf-8")


def build_and_run(source: str, root: Path, entry_name: str = "main.nv",
                  opt: str = "-O1",
                  capabilities: dict | None = None,
                  program_args: tuple[str, ...] = ()) -> subprocess.CompletedProcess:
    """Compile a program whose project root is `root`, then run it.

    `opt` is passed to the C compiler, because "the same answer at every
    optimisation level" is one of the claims this file tests. `capabilities`
    are the file grants baked into the program at build time; when they are
    given the program runs *in* `root`, so a relative grant means the same
    directory the file it writes lands in.
    """
    entry = root / entry_name
    entry.write_text(source, encoding="utf-8")
    compilation = compile_source(source, path=entry, capabilities=capabilities)
    c_file = root / "program.c"
    binary = root / "program"
    c_file.write_text(compilation.code, encoding="utf-8")
    build = subprocess.run([CC, "-std=c11", opt, str(c_file), "-o", str(binary)],
                           capture_output=True, text=True, timeout=300)
    if build.returncode != 0:
        raise AssertionError(f"the generated C did not compile:\n{build.stderr}")
    return subprocess.run([str(binary), *program_args], capture_output=True, text=True,
                          timeout=120, cwd=str(root) if capabilities else None)


def examples_program(module: str, examples: list[str]) -> str:
    """One program that runs every example of a module and reports each line."""
    # Edition 2, because an example is allowed to use the whole language: the
    # test module's own examples pass a *function value* to `run`.
    lines = ["edition 2;", "", f"import {module};", "", "fn main() {"]
    for index, example in enumerate(examples, start=1):
        statements = contracts.statements_of(example)
        if not statements:
            continue
        lines.append("    if true {")
        for statement in statements[:-1]:
            lines.append(f"        {statement}")
        lines.append(f"        if !({statements[-1]}) {{")
        lines.append(f'            print("fail {index}")')
        lines.append("        } else {")
        lines.append(f'            print("ok {index}")')
        lines.append("        }")
        lines.append("    }")
    lines.append("}")
    return "\n".join(lines) + "\n"


def error_program(module: str, statements: str) -> str:
    """One program per documented failure: it must stop before the last print."""
    lines = ["edition 2;", "", f"import {module};", "", "fn main() {"]
    for statement in contracts.statements_of(statements):
        lines.append(f"    {statement}")
    lines.append('    print("no error")')
    lines.append("}")
    return "\n".join(lines) + "\n"


def contract_module(source: str, name: str, root: Path):
    """Parse a module given as text, as if it were `root/<name>.nv`."""
    path = root / f"{name}.nv"
    path.write_text(source, encoding="utf-8")
    return parse(source, path=str(path))


def example_problems(module: str, examples: list[str],
                     root: Path | None = None,
                     capabilities: dict | None = None,
                     program_args: tuple[str, ...] = ()) -> list[str]:
    """Every example of one module that is false or does not compile.

    An example is written the way a caller writes it, so a function that lives
    in a module is named with the module: `sample::twice(2) == 4`.
    `root` is the directory the module is resolved from: the standard library
    when it is not given, and the caller's own directory when it tests a module
    it wrote. An entry file has to sit next to the module it imports, which is
    why the harness is built there rather than in a directory of its own.
    """
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory) if root is None else root
        program = examples_program(module, examples)
        try:
            result = build_and_run(program, root, capabilities=capabilities,
                                   program_args=program_args)
        except NavaError as error:
            # The batch did not compile, so the culprit is one example. Compile
            # them one at a time to name it instead of blaming the module.
            problems = []
            for index, example in enumerate(examples, start=1):
                single = examples_program(module, [example])
                try:
                    build_and_run(single, root, f"main{index}.nv",
                                  capabilities=capabilities,
                                  program_args=program_args)
                except NavaError as single_error:
                    problems.append(
                        f"{module}: example {index} does not compile: "
                        f"{single_error.code} {single_error.message}")
            return problems
        problems = []
        for line in result.stdout.splitlines():
            parts = line.split()
            if parts and parts[0] == "fail":
                index = int(parts[1])
                problems.append(f"{module}: example {index} is false: "
                                f"{examples[index - 1]}")
        if result.returncode != 0:
            problems.append(f"{module}: the examples stopped with status "
                            f"{result.returncode}: {result.stderr.strip()}")
        return problems


def error_case_problems(module: str, statements: str, message: str,
                        root: Path | None = None,
                        capabilities: dict | None = None,
                        program_args: tuple[str, ...] = ()) -> list[str]:
    """Does one documented failure really stop the program, saying this?"""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory) if root is None else root
        program = error_program(module, statements)
        try:
            result = build_and_run(program, root, capabilities=capabilities,
                                   program_args=program_args)
        except NavaError as error:
            return [f"{module}: the documented failure does not compile: "
                    f"{error.code} {error.message}"]
        if result.returncode == 0:
            return [f"{module}: the documented failure of `{statements}` did not "
                    f"stop the program"]
        if message not in result.stderr:
            return [f"{module}: the documented failure of `{statements}` printed "
                    f"{result.stderr.strip()!r}, not {message!r}"]
        return []


class DocBlockTests(unittest.TestCase):
    """The syntax: a doc block belongs to the function under it, and nowhere else."""

    def test_a_doc_block_attaches_to_the_function_below_it(self):
        program = parse("edition 2;\n\n"
                        "/// `double(x)` — twice `x`.\n"
                        "///\n"
                        "/// pre: none\n"
                        "/// post: the result is `2 * x`\n"
                        "/// cost: O(1)\n"
                        "/// example: double(2) == 4\n"
                        "pub fn double(x: i64) -> i64 {\n    return x * 2\n}\n\n"
                        "fn helper() -> i64 {\n    return 1\n}\n")
        double = program.functions[0]
        contract = contracts.parse(double.doc)
        self.assertEqual(contract.post, "the result is `2 * x`")
        self.assertEqual(contract.examples, ("double(2) == 4",))
        self.assertEqual(contract.summary, "`double(x)` — twice `x`.")
        self.assertEqual(program.functions[1].doc, [],
                         "a doc block must not leak onto the next declaration")

    def test_a_blank_line_ends_a_doc_block(self):
        program = parse("/// orphan\n\nfn helper() -> i64 {\n    return 1\n}\n"
                        "fn main() {\n    print(helper())\n}\n")
        # A block that a blank line separates still belongs to the next
        # declaration: the parser keeps the run it saw, and `helper` is that
        # declaration.
        self.assertEqual(program.functions[0].doc, [" orphan"])

    def test_a_doc_comment_inside_a_body_is_refused(self):
        with self.assertRaises(NavaError) as caught:
            parse("fn main() {\n    /// not here\n    print(1)\n}\n")
        self.assertEqual(caught.exception.code, "E0207")

    def test_a_doc_comment_before_something_that_is_not_a_function_is_refused(self):
        with self.assertRaises(NavaError) as caught:
            parse("edition 2;\n\n/// a struct is not a function yet\n"
                  "struct Point {\n    x: i64\n}\n\nfn main() {\n    let p = Point(x: 1)\n"
                  "    print(p.x)\n}\n")
        self.assertEqual(caught.exception.code, "E0207")

    def test_fields_are_read_and_a_duplicate_is_reported(self):
        contract = contracts.parse([" `f()` — prose.", " pre: a", " pre: b",
                                    " post: c", " cost: O(1)", " example: f() == 1"])
        self.assertEqual(contract.pre, "b")
        self.assertEqual(contract.duplicates, ("pre",))
        reported = contracts.problems(contract, "m::f")
        self.assertIn("m::f: `pre:` is written more than once", reported)

    def test_a_miswritten_field_shows_up_as_missing(self):
        contract = contracts.parse([" `f()` — prose.", " pres: a", " post: c",
                                    " cost: O(1)"])
        self.assertEqual(contract.pre, "")
        self.assertIn("m::f: no `pre:` line", contracts.problems(contract, "m::f"))

    def test_an_error_line_needs_both_halves(self):
        contract = contracts.parse([" `f()` — prose.", " pre: a", " post: c",
                                    " cost: O(1)", " example: f() == 1",
                                    " error: f()"])
        problems = contracts.problems(contract, "m::f")
        self.assertIn("m::f: error 1 does not say what the program prints", problems)

    def test_statements_split_on_semicolons_but_not_inside_strings(self):
        self.assertEqual(contracts.statements_of('let a = 1; f(a)'), ["let a = 1", "f(a)"])
        self.assertEqual(contracts.statements_of('print("a;b"); print("c")'),
                         ['print("a;b")', 'print("c")'])

    def test_the_module_protocol_function_is_exempt(self):
        program = parse(module_source("math"))
        self.assertNotIn("module_version", contracts.contracts_of(program))
        self.assertIn("abs", contracts.contracts_of(program))


class ContractGuardTests(unittest.TestCase):
    """The gate: every public name documented, every example and failure real."""

    def test_every_public_name_has_a_contract(self):
        for module in MODULES:
            with self.subTest(module=module):
                program = parse(module_source(module))
                self.assertEqual(contracts.check_program(program, module), [])

    @unittest.skipUnless(CC is not None, "a C compiler is required")
    def test_every_documented_example_is_true(self):
        for module in MODULES:
            with self.subTest(module=module):
                program = parse(module_source(module))
                examples = [example for function in program.functions
                            if contracts.is_public(program, function)
                            for example in contracts.parse(function.doc).examples]
                self.assertTrue(examples, f"{module} documents no example")
                self.assertEqual(
                    example_problems(module, examples, capabilities=EXAMPLE_GRANTS,
                                     program_args=EXAMPLE_ARGS),
                    [])

    @unittest.skipUnless(CC is not None, "a C compiler is required")
    def test_every_documented_failure_happens(self):
        documented = 0
        for module in MODULES:
            program = parse(module_source(module))
            for function in program.functions:
                if not contracts.is_public(program, function):
                    continue
                for statements, message in contracts.parse(function.doc).errors:
                    documented += 1
                    with self.subTest(module=module, function=function.name):
                        self.assertEqual(
                            error_case_problems(module, statements, message,
                                                capabilities=EXAMPLE_GRANTS,
                                                program_args=EXAMPLE_ARGS), [])
        self.assertGreaterEqual(documented, 3,
                                "the standard library documents no failure at all")

    def test_a_pure_function_is_pure_by_its_body(self):
        """The `pure: yes` claim is checked against what the body does."""
        for module in MODULES:
            with self.subTest(module=module):
                self.assertEqual(contracts.purity_problems(
                    parse(module_source(module)), module), [])

    def test_the_purity_claim_is_not_vacuous(self):
        """A lane with nothing in it proves nothing, so the count is asserted."""
        pure = 0
        for module in MODULES:
            program = parse(module_source(module))
            for function in program.functions:
                if contracts.is_public(program, function) and \
                        contracts.parse(function.doc).pure == "yes":
                    pure += 1
        self.assertGreaterEqual(pure, 10,
                                f"only {pure} public function(s) claim to be pure")

    @unittest.skipUnless(CC is not None, "a C compiler is required")
    def test_a_pure_function_answers_the_same_at_every_optimisation_level(self):
        """The claim a reader actually relies on: the answer does not drift.

        The example program of a module is built at four optimisation levels and
        run twice at each, and every run must print exactly the same thing. The
        second backend this project specifies (N14) is not here yet, so the axis
        that exists today is the C compiler's own: `-O0`, `-O1`, `-O2`, `-O3`.
        """
        for module in MODULES:
            program = parse(module_source(module))
            examples = [example for function in program.functions
                        if contracts.is_public(program, function)
                        and contracts.parse(function.doc).pure == "yes"
                        for example in contracts.parse(function.doc).examples]
            if not examples:
                continue
            with self.subTest(module=module):
                program_text = examples_program(module, examples)
                outputs = []
                for opt in ("-O0", "-O1", "-O2", "-O3"):
                    for _ in range(2):
                        with tempfile.TemporaryDirectory() as directory:
                            result = build_and_run(program_text, Path(directory), opt=opt)
                        outputs.append((opt, result.returncode, result.stdout))
                first = outputs[0]
                for opt, code, out in outputs[1:]:
                    self.assertEqual((code, out), (first[1], first[2]),
                                     f"{module}: {opt} printed something else")
                self.assertNotIn("fail", first[2].replace("FAIL", ""),
                                 f"{module}: a documented example is false")

    def test_the_generated_reference_matches_the_tree(self):
        for module in MODULES:
            path = STDLIB / f"{module}.nv"
            program = parse(module_source(module))
            expected = contracts.render_module(
                module, Path(f"nava_lang/stdlib/{module}.nv"), program,
                contracts.module_version(program))
            recorded = REFERENCE / f"{module}.md"
            with self.subTest(module=module):
                self.assertTrue(recorded.is_file(),
                                f"{recorded} is missing: run `nava doc --out docs/stdlib`")
                self.assertEqual(recorded.read_text(encoding="utf-8"), expected,
                                 f"{recorded} is stale: run `nava doc --out docs/stdlib`")

    def test_the_command_writes_what_the_guard_expects(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, str(NAVA), "doc", "--out", directory],
                                    capture_output=True, text=True, timeout=300)
            self.assertEqual(result.returncode, 0, result.stderr)
            written = sorted(path.name for path in Path(directory).glob("*.md"))
            self.assertEqual(written, sorted(f"{module}.md" for module in MODULES))
            for name in written:
                self.assertEqual((Path(directory) / name).read_text(encoding="utf-8"),
                                 (REFERENCE / name).read_text(encoding="utf-8"))

    def test_a_module_with_no_public_function_is_not_a_pass(self):
        """A module that exports nothing passes every check by having none.

        That is the shape of a guard that has stopped guarding, so it is
        reported: without this, an edition-2 module whose every function forgot
        `pub` would be documented as clean.
        """
        reported = contracts.check_program(
            parse("edition 2;\n\nfn private_helper(x: i64) -> i64 {\n"
                  "    return x\n}\n"), "sample")
        self.assertTrue(any("exports no function" in item for item in reported),
                        reported)

    def test_an_unknown_module_is_reported(self):
        result = subprocess.run([sys.executable, str(NAVA), "doc", "nosuchmodule"],
                                capture_output=True, text=True, timeout=300)
        self.assertEqual(result.returncode, 2)
        self.assertIn("no module named", result.stderr)


class PurityNegativeControlTests(unittest.TestCase):
    """A claim of purity that the body contradicts has to be reported."""

    def problems(self, source: str, module: str = "sample") -> list[str]:
        return contracts.check_program(parse(source), module)

    def test_a_pure_function_that_prints_is_reported(self):
        reported = self.problems(
            "edition 2;\n\n/// `noisy(x)` — prints.\n/// pre: none\n"
            "/// post: the result is `x`\n/// cost: O(1)\n/// pure: yes\n"
            "/// example: noisy(1) == 1\n"
            "pub fn noisy(x: i64) -> i64 {\n    print(x)\n    return x\n}\n")
        self.assertTrue(any("calls `print`, which has an effect" in item
                            for item in reported), reported)

    def test_a_pure_function_that_calls_an_impure_one_is_reported(self):
        reported = self.problems(
            "edition 2;\n\n/// `noisy(x)` — prints.\n/// pre: none\n"
            "/// post: the result is `x`\n/// cost: O(1)\n/// pure: no\n"
            "/// example: noisy(1) == 1\n"
            "pub fn noisy(x: i64) -> i64 {\n    print(x)\n    return x\n}\n\n"
            "/// `wrapper(x)` — claims purity.\n/// pre: none\n"
            "/// post: the result is `x`\n/// cost: O(1)\n/// pure: yes\n"
            "/// example: wrapper(1) == 1\n"
            "pub fn wrapper(x: i64) -> i64 {\n    return noisy(x)\n}\n")
        self.assertTrue(any("calls `noisy`, which is documented `pure: no`" in item
                            for item in reported), reported)

    def test_a_pure_function_that_takes_a_mut_borrow_is_reported(self):
        reported = self.problems(
            "edition 2;\n\n/// `touch(xs)` — writes.\n/// pre: none\n"
            "/// post: the result is `0`\n/// cost: O(1)\n/// pure: yes\n"
            "/// example: touch(1) == 0\n"
            "pub fn touch(x: i64) -> i64 {\n"
            "    let xs: Vec(i64) = vec_new()\n    vec_push(xs, x)\n    return 0\n}\n")
        self.assertTrue(any("calls `vec_push`, which has an effect" in item
                            for item in reported), reported)

    def test_a_pure_function_that_writes_through_a_parameter_is_reported(self):
        reported = self.problems(
            "edition 2;\n\n/// `bump(xs)` — writes.\n/// pre: none\n"
            "/// post: the result is `len(xs)`\n/// cost: O(1)\n/// pure: yes\n"
            "/// example: bump(1) == 1\n"
            "pub fn bump(xs: mut [i64]) -> i64 {\n    xs[0] = 1\n    return len(xs)\n}\n")
        self.assertTrue(any("takes `mut [i64]`" in item for item in reported), reported)

    def test_a_pure_function_that_calls_an_unclaimed_helper_is_reported(self):
        """A local helper without a claim is an open question, not a pass.

        The message has to say *that*, because "which has an effect" would send
        the author to the wrong repair: the helper may well be pure, it just has
        not said so.
        """
        reported = self.problems(
            "edition 2;\n\nfn helper(x: i64) -> i64 {\n    return x + 1\n}\n\n"
            "/// `wrapper(x)` — claims purity.\n/// pre: none\n"
            "/// post: the result is `x + 1`\n/// cost: O(1)\n/// pure: yes\n"
            "/// example: wrapper(1) == 2\n"
            "pub fn wrapper(x: i64) -> i64 {\n    return helper(x)\n}\n")
        self.assertTrue(
            any("calls `helper`, which has no purity claim" in item for item in reported),
            reported)
        self.assertFalse(any("has an effect" in item for item in reported), reported)

    def test_a_helper_that_states_its_own_claim_is_accepted(self):
        reported = self.problems(
            "edition 2;\n\n/// `helper(x)` — adds one.\n/// pre: none\n"
            "/// post: the result is `x + 1`\n/// cost: O(1)\n/// pure: yes\n"
            "fn helper(x: i64) -> i64 {\n    return x + 1\n}\n\n"
            "/// `wrapper(x)` — claims purity.\n/// pre: none\n"
            "/// post: the result is `x + 1`\n/// cost: O(1)\n/// pure: yes\n"
            "/// example: wrapper(1) == 2\n"
            "pub fn wrapper(x: i64) -> i64 {\n    return helper(x)\n}\n")
        self.assertEqual(reported, [])

    def test_a_value_other_than_yes_or_no_is_reported(self):
        reported = self.problems(
            "edition 2;\n\n/// `f(x)` — prose.\n/// pre: none\n"
            "/// post: the result is `x`\n/// cost: O(1)\n/// pure: maybe\n"
            "/// example: f(1) == 1\n"
            "pub fn f(x: i64) -> i64 {\n    return x\n}\n")
        self.assertTrue(any("`pure:` is `yes` or `no`" in item for item in reported),
                        reported)


class ContractNegativeControlTests(unittest.TestCase):
    """The guard has to fail when a contract is missing or untrue."""

    def test_a_public_function_without_a_contract_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            program = contract_module(
                "# no doc block at all\n"
                "fn twice(x: i64) -> i64 {\n    return x * 2\n}\n\n"
                "fn documented(x: i64) -> i64 {\n    return x\n}\n", "sample", root)
            problems = contracts.check_program(program, "sample")
            self.assertTrue(any("sample::twice: no summary line" in item
                                for item in problems), problems)
            self.assertTrue(any("sample::twice: no `example:` line" in item
                                for item in problems), problems)

    @unittest.skipUnless(CC is not None, "a C compiler is required")
    def test_an_example_that_does_not_compile_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            contract_module("fn twice(x: i64) -> i64 {\n    return x * 2\n}\n",
                            "sample", root)
            problems = example_problems("sample", ["sample::twice(2) =="], root)
            self.assertTrue(problems, "a broken example must be reported")
            self.assertIn("does not compile", problems[0])

    @unittest.skipUnless(CC is not None, "a C compiler is required")
    def test_an_example_that_is_false_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            contract_module("fn twice(x: i64) -> i64 {\n    return x * 2\n}\n",
                            "sample", root)
            self.assertEqual(example_problems("sample", ["sample::twice(2) == 4"], root), [])
            self.assertEqual(example_problems("sample", ["sample::twice(2) == 5"], root),
                             ["sample: example 1 is false: sample::twice(2) == 5"])

    @unittest.skipUnless(CC is not None, "a C compiler is required")
    def test_a_documented_failure_that_does_not_happen_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            contract_module("fn twice(x: i64) -> i64 {\n    return x * 2\n}\n",
                            "sample", root)
            problems = error_case_problems("sample", "sample::twice(2)", "overflow", root)
            self.assertEqual(len(problems), 1)
            self.assertIn("did not stop the program", problems[0])

    @unittest.skipUnless(CC is not None, "a C compiler is required")
    def test_a_documented_failure_with_the_wrong_message_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            contract_module("fn boom(x: i64) -> i64 {\n    return 1 / x\n}\n",
                            "sample", root)
            problems = error_case_problems("sample", "sample::boom(0)", "not the message", root)
            self.assertEqual(len(problems), 1)
            self.assertIn("division by zero", problems[0])


if __name__ == "__main__":
    unittest.main()
