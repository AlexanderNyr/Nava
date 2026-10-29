"""The native backend: one translation unit, two code generators (PLAN.md N14a).

Every claim this phase makes is a comparison, because the phase adds a second way to
compile the same program and the only interesting question is whether the two agree:

* **The output is the same.** A table of programs is built twice — once through C
  with a C compiler, once through the native backend into an ELF file with no C
  compiler at all — and the two binaries are run and compared on stdout, stderr and
  exit status. Traps are part of the comparison: an overflow is a message and a
  status, so a backend that computes a wrapped result fails here rather than in a
  hand-written expectation.
* **The IR is well-formed.** Values are defined before they are used, every use is
  dominated by its definition, every jump passes exactly one argument per block
  parameter, and every block ends in exactly one terminator. `dump()` is what a
  reader and a `diff` both see, so it is also asserted to be stable.
* **What the backend cannot compile, it refuses.** `E0901` names the construct and
  the function, `E0903` names the option: no program is silently compiled into
  something that does not mean what it says.
* **The file is a static executable.** `ET_EXEC`, one executable segment and one
  writable one, no interpreter, and the same bytes on every build — a backend that
  put a clock or a path in its output would not be reproducible, which is the other
  half of why N12's gate is still open.
* **No C toolchain is involved.** A native build is run with an empty `PATH`; if
  anything in the path reached for `cc`, that test fails.
"""

from __future__ import annotations

import os
import pathlib
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "tests"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from nava_lang.model import NavaError  # noqa: E402
from nava_lang.native import ir, lower  # noqa: E402
from nava_lang.parser import parse  # noqa: E402

NAVA = [sys.executable, str(ROOT / "nava")]

#: Programs the two backends must agree about. Every construct the native backend
#: claims support for appears in at least one of them: `i64`, `bool`, `str` literals,
#: arithmetic, comparisons, `&&`/`||`, `if`/`else`, `while`, `for … in a..b`,
#: `break`, `continue`, labelled jumps, calls (including recursion) and `print`.
PROGRAMS = {
    "arith_and_control": """\
fn main() -> i64 {
    let a: bool = true
    let b: bool = false
    print(a && b)
    print(a || b)
    print(!a)
    print(a == b)
    print(a != b)
    let n: i64 = 0
    while n < 5 {
        if n == 3 { break }
        print(n)
        n = n + 1
    }
    print(n)
    return 0
}
""",
    "calls_and_text": """\
fn fact(n: i64) -> i64 {
    if n <= 1 { return 1 }
    return n * fact(n - 1)
}

fn six(a: i64, b: i64, c: i64, d: i64, e: i64, f: i64) -> i64 {
    return a + b + c + d + e + f
}

fn main() -> i64 {
    print(fact(10))
    print(six(1, 2, 3, 4, 5, 6))
    print("tab\\there\\nnewline \\\\ \\"quoted\\"")
    print("Привет, мир")
    return 7
}
""",
    "labelled_loops": """\
edition 2;
fn main() -> i64 {
    let total: i64 = 0
    outer: for i in 0..5 {
        for j in 0..5 {
            if j == 3 { continue outer }
            if i == 4 { break outer }
            total = total + i * 10 + j
        }
    }
    print(total)
    let x: i64 = 100
    if x > 50 {
        let y: i64 = x * 2
        print(y)
    } else {
        print(-1)
    }
    print(x)
    return 0
}
""",
    "signed_arithmetic": """\
fn sign(n: i64) -> i64 {
    if n < 0 { return -1 }
    if n > 0 { return 1 }
    return 0
}

fn main() -> i64 {
    for i in -2..3 {
        print(sign(i))
    }
    print(-9223372036854775807 % 3)
    print(9223372036854775807 % 7)
    print(0 / 5)
    print(0 % 5)
    let flag: bool = false
    while !flag {
        print("once")
        flag = true
    }
    return 300
}
""",
    "loop_carried_values": """\
fn fib(n: i64) -> i64 {
    let a: i64 = 0
    let b: i64 = 1
    let i: i64 = 0
    while i < n {
        let t: i64 = a + b
        a = b
        b = t
        i = i + 1
    }
    return a
}

fn main() -> i64 {
    let i: i64 = 0
    while i < 10 {
        print(fib(i))
        i = i + 1
    }
    return -1
}
""",
    "bool_parameters": """\
fn both(x: bool, y: bool) -> bool {
    return x && y
}

fn either(x: bool, y: bool) -> bool {
    return x || y
}

fn main() -> i64 {
    print(both(true, false))
    print(either(false, true))
    print(either(both(true, true), false))
    let even: i64 = 0
    for i in 0..6 {
        if i % 2 == 0 { even = even + 1 }
    }
    print(even)
    return 0
}
""",
}

#: Traps: the same message, the same status, from both backends.
TRAPS = {
    "overflow_add": "let a: i64 = 9223372036854775807\n    print(a + 1)",
    "overflow_sub": "let a: i64 = -9223372036854775808\n    print(a - 1)",
    "overflow_mul": "let a: i64 = 4611686018427387904\n    print(a * 2)",
    "overflow_neg": "let a: i64 = -9223372036854775808\n    print(-a)",
    "division_by_zero": "let a: i64 = 1\n    let b: i64 = 0\n    print(a / b)",
    "remainder_by_zero": "let a: i64 = 1\n    let b: i64 = 0\n    print(a % b)",
    "division_overflow": "let a: i64 = -9223372036854775808\n    let b: i64 = -1\n    print(a / b)",
}


def write_source(directory: pathlib.Path, name: str, source: str) -> pathlib.Path:
    path = directory / f"{name}.nv"
    path.write_text(source, encoding="utf-8")
    return path


def run(command, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, text=True, timeout=120, **kwargs)


def build_both(source: pathlib.Path, directory: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    """Build one source with the C backend and with the native backend."""
    c_binary = directory / f"{source.stem}-c"
    native_binary = directory / f"{source.stem}-native"
    result = run([*NAVA, "build", str(source), "-o", str(c_binary)])
    if result.returncode != 0:
        raise AssertionError(f"the C backend failed to build {source.name}: {result.stderr}")
    result = run([*NAVA, "build", str(source), "-o", str(native_binary),
                  "--backend", "native"])
    if result.returncode != 0:
        raise AssertionError(
            f"the native backend failed to build {source.name}: {result.stderr}")
    return c_binary, native_binary


def run_binary(path: pathlib.Path) -> tuple[str, str, int]:
    result = subprocess.run([str(path)], capture_output=True, text=True, timeout=120)
    return result.stdout, result.stderr, result.returncode


def lower_source(source: str) -> ir.Module:
    return lower.lower(parse(source))


def operands(instruction) -> list[str]:
    """The values an instruction reads, in the order the IR defines them."""
    if isinstance(instruction, (ir.Bin, ir.Cmp)):
        return [instruction.left, instruction.right]
    if isinstance(instruction, (ir.Neg, ir.Not, ir.Copy, ir.Print)):
        return [instruction.operand]
    if isinstance(instruction, ir.Call):
        return list(instruction.args)
    if isinstance(instruction, ir.Jump):
        return list(instruction.args)
    if isinstance(instruction, ir.Branch):
        return [instruction.condition, *instruction.then_args, *instruction.else_args]
    if isinstance(instruction, ir.Ret):
        return [] if instruction.operand is None else [instruction.operand]
    if isinstance(instruction, (ir.Const, ir.Str)):
        return []
    raise AssertionError(f"unknown IR instruction {instruction!r}")


def defined(instruction) -> str | None:
    """The value an instruction defines, if it defines one."""
    if isinstance(instruction, (ir.Bin, ir.Neg, ir.Not, ir.Cmp, ir.Copy, ir.Str, ir.Const)):
        return instruction.value
    if isinstance(instruction, ir.Call) and instruction.returns != "void":
        return instruction.value
    return None


def successors(block: ir.Block) -> list[str]:
    terminator = block.terminator
    if isinstance(terminator, ir.Jump):
        return [terminator.target]
    if isinstance(terminator, ir.Branch):
        return [terminator.then_target, terminator.else_target]
    return []


def dominators(function: ir.Function) -> dict[str, set[str]]:
    """The dominator set of every block: the classic fixed point over the CFG."""
    blocks = {block.label: block for block in function.blocks}
    entry = function.blocks[0].label
    everything = set(blocks)
    result = {label: ({label} if label == entry else set(everything)) for label in blocks}
    changed = True
    while changed:
        changed = False
        for label in blocks:
            if label == entry:
                continue
            predecessors = [other for other in blocks if label in successors(blocks[other])]
            common = set(everything)
            for predecessor in predecessors:
                common &= result[predecessor]
            updated = {label} | common
            if updated != result[label]:
                result[label] = updated
                changed = True
    return result


class NativeDifferentialTests(unittest.TestCase):
    """One program, two backends, one answer."""

    def test_each_program_prints_the_same_through_both_backends(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            for name, source in PROGRAMS.items():
                with self.subTest(program=name):
                    path = write_source(directory, name, source)
                    c_binary, native_binary = build_both(path, directory)
                    self.assertEqual(run_binary(native_binary), run_binary(c_binary),
                                     f"{name}: the two backends disagree")

    def test_each_trap_is_the_same_message_and_status(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            for name, body in TRAPS.items():
                with self.subTest(trap=name):
                    source = f"fn main() -> i64 {{\n    {body}\n    return 0\n}}\n"
                    path = write_source(directory, name, source)
                    c_binary, native_binary = build_both(path, directory)
                    c_result = run_binary(c_binary)
                    native_result = run_binary(native_binary)
                    self.assertEqual(native_result, c_result,
                                     f"{name}: the two backends disagree")
                    self.assertEqual(native_result[2], 1, f"{name}: a trap exits with 1")
                    self.assertIn("nava runtime error: ", native_result[1])

    def test_the_exit_status_is_the_return_value_modulo_256(self):
        statuses = {"zero": (0, 0), "hundred": (100, 100), "over": (300, 44),
                    "negative": (-1, 255), "min": (-9223372036854775808, 0)}
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            for name, (value, expected) in statuses.items():
                with self.subTest(value=value):
                    source = f"fn main() -> i64 {{\n    return {value}\n}}\n"
                    path = write_source(directory, name, source)
                    c_binary, native_binary = build_both(path, directory)
                    self.assertEqual(run_binary(native_binary)[2], expected)
                    self.assertEqual(run_binary(native_binary), run_binary(c_binary))


class NativeIRTests(unittest.TestCase):
    """The IR the second backend consumes has to be well-formed on its own."""

    def test_every_value_is_used_only_where_its_definition_dominates(self):
        for name, source in PROGRAMS.items():
            with self.subTest(program=name):
                module = lower_source(source)
                for function in module.functions:
                    dominance = dominators(function)
                    entry = function.blocks[0].label
                    # A function's parameters are the entry block's incoming values.
                    defining_block = {value: entry for value, _typ in function.params}
                    for block in function.blocks:
                        for value, _typ in block.params:
                            defining_block[value] = block.label
                        for instruction in block.instructions:
                            value = defined(instruction)
                            if value is not None:
                                defining_block[value] = block.label
                    for block in function.blocks:
                        local = {value for value, _typ in block.params}
                        if block.label == entry:
                            local |= {value for value, _typ in function.params}
                        for instruction in block.instructions:
                            for operand in operands(instruction):
                                if operand not in defining_block:
                                    self.fail(f"'{operand}' is used but never defined")
                                where = defining_block[operand]
                                if where == block.label or operand in local:
                                    continue
                                self.assertIn(
                                    where, dominance[block.label],
                                    f"'{operand}' is defined in ^{where}, which does not "
                                    f"dominate ^{block.label}",
                                )
                            value = defined(instruction)
                            if value is not None:
                                local.add(value)

    def test_each_jump_passes_one_value_per_block_parameter(self):
        for name, source in PROGRAMS.items():
            with self.subTest(program=name):
                module = lower_source(source)
                for function in module.functions:
                    parameters = {block.label: block.params for block in function.blocks}
                    for block in function.blocks:
                        terminator = block.terminator
                        edges = []
                        if isinstance(terminator, ir.Jump):
                            edges.append((terminator.target, terminator.args))
                        elif isinstance(terminator, ir.Branch):
                            edges.append((terminator.then_target, terminator.then_args))
                            edges.append((terminator.else_target, terminator.else_args))
                        for target, arguments in edges:
                            self.assertEqual(
                                len(arguments), len(parameters[target]),
                                f"^{block.label} passes {len(arguments)} value(s) to "
                                f"^{target}, which has {len(parameters[target])} "
                                f"parameter(s)",
                            )

    def test_every_block_ends_in_exactly_one_terminator(self):
        for name, source in PROGRAMS.items():
            with self.subTest(program=name):
                module = lower_source(source)
                for function in module.functions:
                    for block in function.blocks:
                        terminators = [instruction for instruction in block.instructions
                                       if isinstance(instruction, ir.TERMINATORS)]
                        self.assertEqual(len(terminators), 1,
                                         f"^{block.label} has {len(terminators)} terminators")
                        self.assertIs(terminators[-1], block.instructions[-1],
                                      f"^{block.label} continues after its terminator")

    def test_a_branch_and_a_loop_header_pass_their_values_explicitly(self):
        """A join is a block parameter, not a phi and not a mutation of a slot."""
        module = lower_source(PROGRAMS["arith_and_control"])
        main = module.function("main")
        headers = [block for block in main.blocks if block.label.startswith("while_head")]
        self.assertEqual(len(headers), 1)
        self.assertEqual([typ for _value, typ in headers[0].params], ["i64"])
        joins = [block for block in main.blocks if block.label.startswith("if_join")]
        self.assertTrue(all(block.params == [] for block in joins))

    def test_the_short_circuit_is_a_branch_so_the_right_side_does_not_run(self):
        module = lower_source(PROGRAMS["arith_and_control"])
        main = module.function("main")
        branches = [instruction for block in main.blocks
                    for instruction in block.instructions
                    if isinstance(instruction, ir.Branch)]
        self.assertGreaterEqual(len(branches), 4,
                                "&& and || should each add a branch")
        # A side effect on the right is the observable difference: printing inside
        # `&&` must not happen when the left side is false.
        source = """\
fn say(x: bool) -> bool {
    print("evaluated")
    return x
}

fn main() -> i64 {
    print(say(false) && say(true))
    print(say(false) || say(true))
    return 0
}
"""
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            path = write_source(directory, "short", source)
            c_binary, native_binary = build_both(path, directory)
            self.assertEqual(run_binary(native_binary), run_binary(c_binary))
            self.assertEqual(run_binary(native_binary)[0],
                             "evaluated\nfalse\nevaluated\nevaluated\ntrue\n")

    def test_the_dump_is_stable(self):
        first = ir.dump(lower_source(PROGRAMS["calls_and_text"]))
        second = ir.dump(lower_source(PROGRAMS["calls_and_text"]))
        self.assertEqual(first, second, "the IR dump is not deterministic")
        self.assertTrue(first.startswith("fn @fact(0: i64) -> i64:\n"))
        # Every line is a function header, a block header or an instruction: a
        # placeholder or a stray blank line would make the dump useless for a diff.
        for line in first.splitlines():
            self.assertTrue(line.startswith(("fn ", "  ^", "    ")), line)


class NativeRefusalTests(unittest.TestCase):
    """What the backend does not implement is refused by name, not approximated."""

    #: One program per construct the backend refuses, with the name the diagnostic
    #: has to use. Each is a whole program that the checker accepts: the refusal has
    #: to come from the backend, not from a syntax error before it.
    CONSTRUCTS = {
        "a floating-point literal": """\
fn main() -> i64 {
    let x: f64 = 1.5
    return 0
}
""",
        "an array": """\
fn main() -> i64 {
    let a: [i64] = [1, 2, 3]
    return 0
}
""",
        "a struct": """\
edition 2;
struct Point {
    x: i64,
    y: i64
}

fn main() -> i64 {
    let p: Point = Point(x: 1, y: 2)
    print(1)
    return 0
}
""",
        "an enum variant": """\
edition 2;
enum Shape {
    Dot,
    Circle(i64)
}

fn main() -> i64 {
    let s: Shape = Shape::Circle(2)
    print(1)
    return 0
}
""",
        "a defer": """\
edition 2;
fn main() -> i64 {
    defer {
        print(1)
    }
    return 0
}
""",
    }

    def test_a_construct_is_refused_by_name(self):
        for description, source in self.CONSTRUCTS.items():
            with self.subTest(construct=description):
                with self.assertRaises(NavaError) as caught:
                    lower_source(source)
                self.assertEqual(caught.exception.code, "E0901")
                self.assertIn(description, caught.exception.message)
                self.assertIn("(in 'main')", caught.exception.message)
                self.assertIn("--backend native", caught.exception.hint)

    def test_the_c_backend_compiles_every_program_the_native_one_refuses(self):
        """A refusal is a gap in one backend, not a claim that the program is wrong."""
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            for index, (description, source) in enumerate(self.CONSTRUCTS.items()):
                with self.subTest(construct=description):
                    path = write_source(directory, f"refused{index}", source)
                    native_result = run([*NAVA, "build", str(path),
                                         "-o", str(directory / f"n{index}"),
                                         "--backend", "native"])
                    self.assertEqual(native_result.returncode, 2)
                    self.assertIn("E0901", native_result.stderr)
                    c_result = run([*NAVA, "build", str(path),
                                    "-o", str(directory / f"c{index}")])
                    self.assertEqual(c_result.returncode, 0, c_result.stderr)

    def test_a_call_with_more_than_six_arguments_is_refused(self):
        source = """\
fn many(a: i64, b: i64, c: i64, d: i64, e: i64, f: i64, g: i64) -> i64 {
    return a + b + c + d + e + f + g
}

fn main() -> i64 {
    return many(1, 2, 3, 4, 5, 6, 7)
}
"""
        with tempfile.TemporaryDirectory() as temp:
            path = write_source(pathlib.Path(temp), "many", source)
            result = run([*NAVA, "build", str(path), "-o", str(path.with_suffix(".bin")),
                          "--backend", "native"])
        self.assertEqual(result.returncode, 2)
        self.assertIn("E0901", result.stderr)
        self.assertIn("more than 6 arguments", result.stderr)

    def test_an_unknown_option_is_refused_rather_than_ignored(self):
        source = "fn main() -> i64 {\n    return 0\n}\n"
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            path = write_source(directory, "empty", source)
            for option in (["--cc", "cc"], ["--opt", "2"], ["--record", "r.json"]):
                with self.subTest(option=option[0]):
                    result = run([*NAVA, "build", str(path), "-o", str(directory / "x"),
                                  "--backend", "native", *option])
                    self.assertEqual(result.returncode, 2)
                    self.assertIn("E0903", result.stderr)
                    self.assertIn(option[0], result.stderr)
                    self.assertFalse((directory / "x").exists(),
                                     "a refused build must not write a binary")


class NativeELFTests(unittest.TestCase):
    """The file the backend writes: static, two segments, and the same every time."""

    def build(self, source: str, directory: pathlib.Path, name: str = "program") -> bytes:
        path = write_source(directory, name, source)
        result = run([*NAVA, "build", str(path), "-o", str(directory / name),
                      "--backend", "native"])
        self.assertEqual(result.returncode, 0, result.stderr)
        return (directory / name).read_bytes()

    def test_the_file_is_a_static_x86_64_executable(self):
        with tempfile.TemporaryDirectory() as temp:
            image = self.build(PROGRAMS["calls_and_text"], pathlib.Path(temp))
        self.assertEqual(image[:4], b"\x7fELF")
        self.assertEqual(image[4], 2, "a 64-bit ELF file")
        self.assertEqual(image[5], 1, "little endian")
        (e_type, e_machine, e_version, e_entry, e_phoff, _e_shoff,
         _flags, _ehsize, phentsize, phnum) = struct.unpack_from("<HHIQQQIHHH", image, 16)
        self.assertEqual(e_type, 2, "ET_EXEC: position-dependent, no loader relocation")
        self.assertEqual(e_machine, 62, "x86-64")
        self.assertEqual(e_version, 1)
        self.assertEqual((e_phoff, phentsize, phnum), (64, 56, 2))
        self.assertEqual(e_entry, 0x400000 + 176, "the entry point is the start of .text")
        segments = [struct.unpack_from("<IIQQQQQQ", image, e_phoff + index * phentsize)
                    for index in range(phnum)]
        for segment in segments:
            self.assertEqual(segment[0], 1, "PT_LOAD")
        text, data = segments
        self.assertEqual(text[1], 5, "the text segment is readable and executable")
        self.assertEqual(data[1], 6, "the data segment is readable and writable")
        self.assertNotEqual(text[4], data[4], "the two segments are mapped apart")
        self.assertEqual(data[2] % 0x1000, 0, "the data segment starts on a page")
        self.assertEqual(data[2] % 0x1000, data[3] % 0x1000, "offset matches address")
        self.assertNotIn(b"ld-linux", image, "there is no dynamic loader")

    def test_two_builds_of_one_source_are_the_same_bytes(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            one = self.build(PROGRAMS["labelled_loops"], pathlib.Path(first), "one")
            two = self.build(PROGRAMS["labelled_loops"], pathlib.Path(second), "two")
        self.assertEqual(one, two, "the native backend put something variable in the file")

    def test_the_binary_is_marked_executable_and_can_be_run_directly(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            self.build(PROGRAMS["arith_and_control"], directory, "run")
            binary = directory / "run"
            self.assertTrue(os.access(binary, os.X_OK), "the output is not executable")
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)


class NativeCommandTests(unittest.TestCase):
    """The two commands that expose the backend."""

    def test_build_needs_no_c_compiler_on_the_path(self):
        """The phase's point: an empty `PATH` is enough, because nothing is spawned."""
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            empty = directory / "empty"
            empty.mkdir()
            path = write_source(directory, "solo", "fn main() -> i64 {\n    print(1)\n    return 0\n}\n")
            environment = {"PATH": str(empty), "HOME": str(directory),
                           "LC_ALL": "C.UTF-8"}
            result = subprocess.run([sys.executable, str(ROOT / "nava"), "build",
                                     str(path), "-o", str(directory / "solo"),
                                     "--backend", "native"],
                                    capture_output=True, text=True, timeout=120,
                                    env=environment)
            self.assertEqual(result.returncode, 0, result.stderr)
            ran = subprocess.run([str(directory / "solo")], capture_output=True,
                                 text=True, timeout=60)
        self.assertEqual((ran.stdout, ran.returncode), ("1\n", 0))

    def test_emit_ir_writes_the_same_text_to_stdout_and_to_a_file(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            path = write_source(directory, "loops", PROGRAMS["loop_carried_values"])
            printed = run([*NAVA, "emit-ir", str(path)])
            written = run([*NAVA, "emit-ir", str(path), "-o", str(directory / "out.ir")])
            self.assertEqual(printed.returncode, 0, printed.stderr)
            self.assertEqual(written.returncode, 0, written.stderr)
            self.assertEqual(printed.stdout, (directory / "out.ir").read_text(encoding="utf-8"))
        self.assertIn("fn @fib(0: i64) -> i64:", printed.stdout)
        self.assertIn("^while_head", printed.stdout)
        self.assertIn("jump ^while_head", printed.stdout)

    def test_emit_ir_refuses_a_construct_it_cannot_represent(self):
        with tempfile.TemporaryDirectory() as temp:
            path = write_source(pathlib.Path(temp), "float",
                                "fn main() -> i64 {\n    let x: f64 = 1.5\n    return 0\n}\n")
            result = run([*NAVA, "emit-ir", str(path)])
        self.assertEqual(result.returncode, 2)
        self.assertIn("E0901", result.stderr)
        self.assertIn("a floating-point literal", result.stderr)
        self.assertEqual(result.stdout, "", "a refusal writes no IR")

    def test_the_json_report_names_the_backend(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            path = write_source(directory, "json", "fn main() -> i64 {\n    return 0\n}\n")
            result = run([*NAVA, "--format", "json", "build", str(path),
                          "-o", str(directory / "json"), "--backend", "native"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"backend": "native"', result.stdout)


class NativeRuntimeTests(unittest.TestCase):
    """The runtime is code too: the conversion and the writing are asserted directly."""

    def run_program(self, body: str) -> str:
        source = f"fn main() -> i64 {{\n{body}\n    return 0\n}}\n"
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            path = write_source(directory, "probe", source)
            result = run([*NAVA, "build", str(path), "-o", str(directory / "probe"),
                          "--backend", "native"])
            self.assertEqual(result.returncode, 0, result.stderr)
            return subprocess.run([str(directory / "probe")], capture_output=True,
                                  text=True, timeout=60).stdout

    def test_integers_are_written_in_decimal_including_the_extremes(self):
        output = self.run_program(
            "    print(0)\n    print(1)\n    print(-1)\n"
            "    print(9223372036854775807)\n    print(-9223372036854775807 - 1)\n"
            "    print(123456789)\n    print(-9876543210)\n")
        self.assertEqual(output, "0\n1\n-1\n9223372036854775807\n"
                                 "-9223372036854775808\n123456789\n-9876543210\n")

    def test_a_string_is_written_byte_for_byte_and_then_a_newline(self):
        output = self.run_program(
            '    print("")\n    print("Привет, мир")\n    print("a\\tb")\n    print("two\\nlines")\n')
        self.assertEqual(output, "\nПривет, мир\na\tb\ntwo\nlines\n")

    def test_a_bool_prints_as_true_or_false(self):
        output = self.run_program("    print(true)\n    print(false)\n    print(1 < 2)\n")
        self.assertEqual(output, "true\nfalse\ntrue\n")


if __name__ == "__main__":
    unittest.main()
