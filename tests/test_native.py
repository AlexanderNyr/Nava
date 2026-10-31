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
* **An array is one instruction set.** N14c makes arrays a first-class part of the
  IR — a literal, a zeroed one, a checked read, a checked write, a length and a
  release — and the differential runs the same array programs through both
  backends, including the traps: a bounds check is a message and a status, so a
  backend that forgot the check fails here instead of in a comment.
* **A function is a value.** N14l makes a declared function something a program can
  pass, store, return and call through: one word, the address of its code, and no
  closure. The differential runs the same programs through both backends, and the
  structural half of the claim is that a value and a direct call name one symbol —
  the same address — which is what makes the two ways of reaching a function
  interchangeable.
* **No C toolchain is involved.** A native build is run with an empty `PATH`; if
  anything in the path reached for `cc`, that test fails.
"""

from __future__ import annotations

import os
import pathlib
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "tests"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from nava_lang.checker import check  # noqa: E402
from nava_lang.cli import load_program  # noqa: E402
from nava_lang.model import NavaError  # noqa: E402
from nava_lang.native import elf64, ir, linker, lower, reader, x86, x86_64  # noqa: E402
from nava_lang.native.unit import Relocation, Symbol, Unit  # noqa: E402
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
    "arrays_and_loops": """\
fn total(a: [i64]) -> i64 {
    let sum = 0
    for value in a {
        sum = sum + value
    }
    return sum
}

fn fill(a: mut [i64], value: i64) {
    let i = 0
    while i < len(a) {
        a[i] = value
        i = i + 1
    }
}

fn main() -> i64 {
    let a: [i64] = [1, 2, 3]
    print(len(a))
    print(total(a))
    a[1] = a[0] + a[2]
    print(a[1])
    fill(a, 5)
    print(total(a))
    let zeros: [i64] = array_zeros(3)
    print(len(zeros))
    fill(zeros, 2)
    print(total(zeros))
    for value in a {
        print(value)
    }
    let words: [str] = ["alpha", "beta"]
    print(words[1])
    let flags: [bool] = [true, false]
    print(flags[1])
    print(len([7, 8, 9]))
    print(total([10, 20]))
    return 5
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
    #: N14l: a function as a value, in the corpus the IR is checked against. The
    #: well-formedness walk reads it as any other program, so a `funcref` that
    #: defined no value or a call that did not read its callee would fail here.
    "function_value": """\
edition 2;
fn twice(x: i64) -> i64 {
    return x * 2
}

fn triple(x: i64) -> i64 {
    return x * 3
}

fn apply(f: fn(i64) -> i64, x: i64) -> i64 {
    return f(x)
}

fn main() -> i64 {
    let f: fn(i64) -> i64 = twice
    let i = 0
    let total = 0
    while i < 5 {
        if i % 2 == 0 { f = triple } else { f = twice }
        total = total + f(i)
        i = i + 1
    }
    print(total)
    print(apply(twice, 21))
    print(apply(triple, apply(twice, 1)))
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
    # Arrays (N14c): the bounds check is the C runtime's, word for word.
    "array_index": "let a: [i64] = [1, 2, 3]\n    let i: i64 = 7\n    print(a[i])",
    "array_index_negative": "let a: [i64] = [1, 2, 3]\n    let i: i64 = 0 - 1\n    a[i] = 5",
    "array_length_negative": "let n: i64 = 0 - 1\n    let a: [i64] = array_zeros(n)",
    "array_length_limit": "let a: [i64] = array_zeros(4194305)",
}


def fixture_text(name: str) -> str:
    """The source of one positive fixture, by name."""
    return (ROOT / "tests" / "fixtures" / "positive" / f"{name}.nv").read_text(encoding="utf-8")


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


def checked(source: str):
    """A parsed and checked program, which is what a backend consumes."""
    program = parse(source)
    check(program)
    return program


def lower_source(source: str) -> ir.Module:
    return lower.lower(checked(source))


def units_of(source: str, sources: dict | None = None):
    """The units of one program: one per module, then the runtime."""
    return x86_64.units(lower_source(source), sources)


def operands(instruction) -> list[str]:
    """The values an instruction reads, in the order the IR defines them."""
    if isinstance(instruction, (ir.Bin, ir.Cmp)):
        return [instruction.left, instruction.right]
    if isinstance(instruction, (ir.Neg, ir.Not, ir.Copy, ir.Print)):
        return [instruction.operand]
    if isinstance(instruction, ir.Call):
        # A call through a value reads the value: it is the last thing the call
        # needs, after the arguments, and the backend loads it last.
        if instruction.callee is not None:
            return [*instruction.args, instruction.callee]
        return list(instruction.args)
    if isinstance(instruction, ir.Jump):
        return list(instruction.args)
    if isinstance(instruction, ir.Branch):
        return [instruction.condition, *instruction.then_args, *instruction.else_args]
    if isinstance(instruction, ir.Ret):
        return [] if instruction.operand is None else [instruction.operand]
    if isinstance(instruction, (ir.Const, ir.Str)):
        return []
    if isinstance(instruction, ir.FuncRef):
        return []                                   # it names a function, not a value
    if isinstance(instruction, ir.ArrayLiteral):
        return list(instruction.items)
    if isinstance(instruction, ir.ArrayZeros):
        return [instruction.count]
    if isinstance(instruction, ir.Index):
        return [instruction.base, instruction.index]
    if isinstance(instruction, ir.CheckIndex):
        return [instruction.base, instruction.index]
    if isinstance(instruction, ir.IndexStore):
        return [instruction.base, instruction.index, instruction.value]
    if isinstance(instruction, ir.Length):
        return [instruction.base]
    if isinstance(instruction, ir.Release):
        return [instruction.operand]
    if isinstance(instruction, ir.SlotLoad):
        return []
    if isinstance(instruction, ir.SlotStore):
        return [instruction.operand]
    if isinstance(instruction, ir.StructNew):
        return [value for _name, value in instruction.fields]
    if isinstance(instruction, ir.Field):
        return [instruction.base]
    if isinstance(instruction, ir.FieldStore):
        return [instruction.operand]
    if isinstance(instruction, ir.Builtin):
        return list(instruction.args)
    raise AssertionError(f"unknown IR instruction {instruction!r}")


def defined(instruction) -> str | None:
    """The value an instruction defines, if it defines one."""
    if isinstance(instruction, (ir.Bin, ir.Neg, ir.Not, ir.Cmp, ir.Copy, ir.Str, ir.Const,
                                ir.ArrayLiteral, ir.ArrayZeros, ir.Index, ir.Length,
                                ir.SlotLoad, ir.StructNew, ir.Field, ir.FuncRef)):
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
        "a 'defer' whose body assigns a variable": """\
edition 2;
fn main() -> i64 {
    let n = 0
    defer {
        n = 1
    }
    print(n)
    return 0
}
""",
        "a '?' that forwards Result(bool, str) into a function returning Result(i64, str)": """\
edition 2;
fn small() -> Result(bool, str) {
    return Result(bool, str)::Ok(true)
}

fn outer() -> Result(i64, str) {
    let flag = small()?
    print(flag)
    return Result(i64, str)::Ok(1)
}

fn main() {
    match outer() {
        Result(i64, str)::Ok(v) => { print(v) }
        Result(i64, str)::Err(e) => { print(e) }
    }
}
""",
        "a payload of type Point in 'Holder::Full'": """\
edition 2;
struct Point {
    x: i64
}

enum Holder {
    Empty,
    Full(Point)
}

fn main() -> i64 {
    let h: Holder = Holder::Full(Point(x: 1))
    print(1)
    return 0
}
""",
        "a field 'xs' of type [i64]": """\
edition 2;
struct Bag {
    count: i64,
    xs: [i64]
}

fn main() -> i64 {
    let b: Bag = Bag(count: 1, xs: [1, 2])
    print(b.count)
    return 0
}
""",
    }

    #: Programs the IR expresses and this backend refuses at code generation, because
    #: the builtin is a runtime routine the native runtime does not have yet. The
    #: refusal is `E0901` with the builtin's own name, from the generator and not from
    #: the lowering: the IR says what the program means, and the backend says what it
    #: cannot compile.
    CODEGEN_REFUSALS = {
        "builtin 'chars_len'": """\
fn main() -> i64 {
    print(chars_len("abc"))
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
                self.assertIn("(in '", caught.exception.message)
                self.assertIn("--backend native", caught.exception.hint)

    def test_a_routine_the_runtime_lacks_is_refused_at_code_generation(self):
        """The IR states the program; the generator names what it cannot compile.

        A `Builtin` is an instruction, so a builtin the runtime lacks is not a hole in
        the lowering: the closing of that gap is the increment that adds the routine.
        """
        for description, source in self.CODEGEN_REFUSALS.items():
            with self.subTest(refusal=description):
                module = lower_source(source)
                with self.assertRaises(NavaError) as caught:
                    x86_64.units(module)
                self.assertEqual(caught.exception.code, "E0901")
                self.assertIn(description, caught.exception.message)
                self.assertIn("--backend native", caught.exception.hint)
                if description.startswith("builtin "):
                    names = [instruction.name for function in module.functions
                             for block in function.blocks
                             for instruction in block.instructions
                             if isinstance(instruction, ir.Builtin)]
                    self.assertEqual(names, [description.split(" ", 1)[1].strip("'")])

    def test_the_c_backend_compiles_every_program_the_native_one_refuses(self):
        """A refusal is a gap in one backend, not a claim that the program is wrong."""
        cases = {**self.CONSTRUCTS, **self.CODEGEN_REFUSALS}
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            for index, (description, source) in enumerate(cases.items()):
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


class NativeArrayTests(unittest.TestCase):
    """Arrays: one instruction set, two lowerings, one answer (PLAN.md N14c).

    The claim is narrow and checkable. An array is a **value of the IR** — a literal,
    `array_zeros`, a checked read, a checked write, a length and a release — so both
    backends consume the same six instructions, and the differential, the traps and
    the fixtures all compare what the two produce. Ownership is not a comment: which
    block releases which array is decided by the lowering and stated in the IR, and
    the tests below read it there.
    """

    #: Every fixture whose arrays the native backend runs today.
    FIXTURES = ("arrays_basic", "arrays_borrow", "arrays_sort", "for_loops")

    def test_every_array_fixture_builds_and_runs_through_both_backends(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            for name in self.FIXTURES:
                with self.subTest(fixture=name):
                    source = ROOT / "tests" / "fixtures" / "positive" / f"{name}.nv"
                    c_binary, native_binary = build_both(source, directory)
                    self.assertEqual(run_binary(native_binary), run_binary(c_binary),
                                     f"{name}: the two backends disagree")

    def test_the_ir_states_every_array_operation_the_language_has(self):
        module = lower_source(PROGRAMS["arrays_and_loops"])
        kinds = {type(instruction).__name__
                 for function in module.functions for block in function.blocks
                 for instruction in block.instructions}
        for name in ("ArrayLiteral", "ArrayZeros", "Index", "IndexStore", "Length",
                     "Release"):
            self.assertIn(name, kinds, f"the IR never states {name}")

    def test_the_block_that_declared_an_array_releases_it_last_declared_first(self):
        """Ownership is the lowering's decision, so it is read out of the IR."""
        module = lower_source("""\
fn main() -> i64 {
    let first: [i64] = [1]
    let second: [i64] = [2, 3]
    print(len(first) + len(second))
    return 0
}
""")
        main = next(function for function in module.functions if function.name == "main")
        instructions = [instruction for block in main.blocks
                        for instruction in block.instructions]
        literals = [instruction.value for instruction in instructions
                    if isinstance(instruction, ir.ArrayLiteral)]
        releases = [instruction.operand for instruction in instructions
                    if isinstance(instruction, ir.Release)]
        self.assertEqual(len(literals), 2)
        self.assertEqual(releases, list(reversed(literals)),
                         "an array is released by the block that owns it, last first")
        self.assertIsInstance(instructions[-1], ir.Ret,
                              "the releases come before the return, so the array is "
                              "still alive while the returned value is computed")

    def test_a_temporary_array_is_released_when_its_statement_ends(self):
        module = lower_source("""\
fn main() -> i64 {
    print(len([7, 8, 9]))
    print(1)
    return 0
}
""")
        main = next(function for function in module.functions if function.name == "main")
        instructions = [instruction for block in main.blocks
                        for instruction in block.instructions]
        release = next(index for index, instruction in enumerate(instructions)
                       if isinstance(instruction, ir.Release))
        last_print = max(index for index, instruction in enumerate(instructions)
                         if isinstance(instruction, ir.Print))
        self.assertLess(release, last_print,
                        "an array nobody binds belongs to the statement that built it")

    def test_an_array_parameter_is_borrowed_and_never_released(self):
        module = lower_source(PROGRAMS["arrays_and_loops"])
        for name in ("total", "fill"):
            with self.subTest(function=name):
                function = next(item for item in module.functions
                                if item.name == name)
                releases = [instruction for block in function.blocks
                            for instruction in block.instructions
                            if isinstance(instruction, ir.Release)]
                self.assertEqual(releases, [],
                                 f"{name} borrows its array; the caller owns it")

    def test_a_while_whose_condition_short_circuits_leaves_the_loop(self):
        """The regression N14c found: the back edge landed in the `&&` join block.

        `while j >= 0 && a[j] > key` produced a back edge into the join, whose single
        parameter is a `bool`; the loop then ran on a number that was never zero. The
        program below is the insertion sort's inner loop, and it terminates.
        """
        source = """\
fn sort(a: mut [i64]) {
    let i = 1
    while i < len(a) {
        let key = a[i]
        let j = i - 1
        while j >= 0 && a[j] > key {
            a[j + 1] = a[j]
            j = j - 1
        }
        a[j + 1] = key
        i = i + 1
    }
}

fn main() -> i64 {
    let a: [i64] = [3, 1, 2]
    sort(a)
    for value in a {
        print(value)
    }
    return 0
}
"""
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            path = write_source(directory, "short_circuit_loop", source)
            c_binary, native_binary = build_both(path, directory)
            native_run = subprocess.run([str(native_binary)], capture_output=True,
                                        text=True, timeout=30)
            c_run = subprocess.run([str(c_binary)], capture_output=True, text=True,
                                   timeout=30)
        self.assertEqual((native_run.stdout, native_run.returncode),
                         (c_run.stdout, c_run.returncode))
        self.assertEqual(native_run.stdout, "1\n2\n3\n")

    def test_a_bounds_trap_traces_like_any_other_trap(self):
        """A trap says where it happened, and is silent about that when not asked."""
        source = """\
fn read_at(a: [i64], index: i64) -> i64 {
    return a[index]
}

fn main() -> i64 {
    let a: [i64] = [1, 2, 3]
    print(read_at(a, 5))
    return 0
}
"""
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            path = write_source(directory, "traced", source)
            _, native_binary = build_both(path, directory)
            plain = subprocess.run([str(native_binary)], capture_output=True, text=True,
                                   timeout=30)
            traced = subprocess.run([str(native_binary)], capture_output=True, text=True,
                                    timeout=30, env={**os.environ, "NAVA_TRACE": "1"})
        self.assertEqual(plain.returncode, 1)
        self.assertEqual(
            plain.stderr,
            "nava runtime error: array index 5 is out of bounds for length 3\n",
            "without NAVA_TRACE a trap is exactly the message the C backend prints")
        self.assertIn("  at nava.entry.read_at (traced.nv:2)\n", traced.stderr)
        self.assertIn("  at nava.entry.main (traced.nv:7)\n", traced.stderr)

    def test_the_release_instruction_costs_nothing_in_the_native_backend(self):
        """The arena never gives bytes back, so `release` encodes as nothing.

        That is a decision with a cost, so it is measured rather than asserted: a
        build of a program with arrays contains no `free` and no `munmap`, and the
        *whole* text of two programs that differ only in their arrays need not be
        compared — what is compared is that the file is still an executable.
        """
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            path = write_source(directory, "released", PROGRAMS["arrays_and_loops"])
            _, native_binary = build_both(path, directory)
            parsed = reader.read(native_binary)
        symbols = [symbol.name for symbol in parsed.symbols]
        self.assertNotIn("free", symbols)
        self.assertNotIn("malloc", symbols)
        self.assertIn("nv_array_new", symbols)


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
        parsed = reader.read(image)
        self.assertEqual(image[:6], b"\x7fELF\x02\x01")
        header = parsed.header
        self.assertEqual((header.type, header.machine, header.version), (2, 62, 1),
                         "ET_EXEC, x86-64, current version")
        self.assertEqual((header.ehsize, header.phentsize, header.shentsize),
                         (64, 56, 64))
        self.assertEqual(header.phnum, 3, "text, read-only data, writable data")
        entry = parsed.symbol("_start")
        self.assertEqual(header.entry, entry.value, "the entry point is `_start`")
        segments = parsed.segments
        self.assertEqual([segment.type for segment in segments], [1, 1, 1],
                         "every segment is PT_LOAD")
        self.assertEqual([segment.permissions for segment in segments], ["RX", "R", "RW"])
        for segment in segments:
            self.assertEqual(segment.offset % 0x1000, segment.address % 0x1000,
                             "a segment's file offset agrees with its address")
            self.assertEqual(segment.memsz, segment.filesz,
                             "nothing is zero-filled at load time")
        self.assertNotEqual(segments[0].address, segments[2].address,
                            "text and data are mapped apart")
        self.assertNotIn(b"ld-linux", image, "there is no dynamic loader")
        self.assertNotIn(b".interp", image, "there is no interpreter segment")

    def test_the_sections_and_the_symbol_table_say_what_was_linked(self):
        """A file that runs but cannot be read back is a file nobody can debug."""
        with tempfile.TemporaryDirectory() as temp:
            image = self.build(PROGRAMS["calls_and_text"], pathlib.Path(temp))
        parsed = reader.read(image)
        names = [section.name for section in parsed.sections]
        self.assertEqual(names, ["", ".text", ".rodata", ".nava.lines", ".data",
                                 ".symtab", ".strtab", ".shstrtab"])
        for name in (".text", ".rodata", ".data"):
            section = parsed.section(name)
            self.assertGreater(section.size, 0, f"{name} is empty")
            self.assertEqual(section.offset, section.address - elf64.BASE,
                             "a loadable section's offset is its address minus BASE")
        self.assertEqual(parsed.section(".text").flags & (elf64.SHF_ALLOC |
                                                          elf64.SHF_EXECINSTR),
                         elf64.SHF_ALLOC | elf64.SHF_EXECINSTR)
        self.assertTrue(parsed.section(".data").flags & elf64.SHF_WRITE)
        self.assertEqual(parsed.symbols[0].name, "",
                         "the symbol table starts with the null symbol, or nm sees none")
        self.assertEqual([symbol.name for symbol in parsed.symbols[:1]], [""])
        exported = {symbol.name: symbol for symbol in parsed.symbols
                    if symbol.binding == "global"}
        self.assertIn("_start", exported)
        self.assertIn("nava.entry.main", exported)
        self.assertIn("nv_trace_table", exported)
        for name in ("nava.entry.main", "_start", "nv_print_i64"):
            symbol = parsed.symbol(name)
            self.assertEqual(symbol.kind, "func", f"{name} is a function symbol")
            self.assertGreater(symbol.size, 0, f"{name} has a size")
            self.assertEqual(symbol.section, 1, f"{name} lives in .text")

    def test_the_line_table_names_every_function_of_the_program(self):
        with tempfile.TemporaryDirectory() as temp:
            image = self.build(PROGRAMS["calls_and_text"], pathlib.Path(temp), "lines")
        parsed = reader.read(image)
        functions = [record.name for record in parsed.lines]
        self.assertEqual(functions, ["nava.entry.fact", "nava.entry.six",
                                     "nava.entry.main"],
                         "the line table names every function, in source order")
        for record in parsed.lines:
            self.assertEqual(record.file, "lines.nv",
                             "a line table names the file, never the path")
            offsets = [at for at, _line in record.lines]
            self.assertEqual(offsets, sorted(offsets), "line entries ascend")
            for at, line in record.lines:
                self.assertGreaterEqual(at, record.start)
                self.assertLess(at, record.end)
                self.assertGreater(line, 0)
        self.assertEqual(parsed.symbolise(parsed.symbol("nava.entry.six").value),
                         "nava.entry.six (lines.nv:6)")

    def test_two_builds_of_one_source_are_the_same_bytes(self):
        """The same file, built twice in two directories, is one file.

        The *name* of the source is in the binary and is meant to be there: a line
        table that could not name the file would be a line table nobody can act on.
        The directory is not, and that is the claim: a build that embedded its own
        working directory would make two checkouts of one tree two different
        binaries.
        """
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            one = self.build(PROGRAMS["labelled_loops"], pathlib.Path(first), "same")
            two = self.build(PROGRAMS["labelled_loops"], pathlib.Path(second), "same")
        self.assertEqual(one, two, "the native backend put something variable in the file")
        self.assertIn(b"same.nv", one, "the file's name reaches the line table")
        self.assertNotIn(first.encode(), one, "the build directory reached the file")
        self.assertNotIn(second.encode(), two, "the build directory reached the file")

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
        """A struct field that owns storage: still refused, by name, with no IR written."""
        with tempfile.TemporaryDirectory() as temp:
            path = write_source(pathlib.Path(temp), "owning",
                                "edition 2;\nstruct Bag {\n    count: i64,\n"
                                "    xs: [i64]\n}\n\nfn main() -> i64 {\n"
                                "    let b: Bag = Bag(count: 1, xs: [1, 2])\n"
                                "    print(b.count)\n    return 0\n}\n")
            result = run([*NAVA, "emit-ir", str(path)])
        self.assertEqual(result.returncode, 2)
        self.assertIn("E0901", result.stderr)
        self.assertIn("a field 'xs' of type [i64]", result.stderr)
        self.assertEqual(result.stdout, "", "a refusal writes no IR")

    def test_emit_ir_states_a_struct_and_an_f64_rather_than_refusing(self):
        """The two types N14e adds to the IR: a layout, and an f64 that is not printed."""
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            structs = write_source(directory, "structs",
                                   "edition 2;\nstruct Point {\n    x: i64,\n    y: i64\n}\n\n"
                                   "fn main() -> i64 {\n    let p: Point = Point(x: 1, y: 2)\n"
                                   "    print(p.x)\n    return 0\n}\n")
            result = run([*NAVA, "emit-ir", str(structs)])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("struct Point {x: i64, y: i64}", result.stdout)
            self.assertIn("= struct Point {x: %", result.stdout)
            self.assertIn("= field Point %", result.stdout)
            float_path = write_source(directory, "float",
                                      "fn main() -> i64 {\n    let x: f64 = 1.5\n"
                                      "    if x > 1.0 { print(1) }\n    return 0\n}\n")
            result = run([*NAVA, "emit-ir", str(float_path)])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("= const f64 1.5", result.stdout)
            self.assertIn("= cmp gt f64 %", result.stdout)

    def test_run_compiles_with_the_native_backend_when_asked(self):
        """`run` runs the same program twice, and the two ways must agree."""
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            path = write_source(directory, "both", PROGRAMS["calls_and_text"])
            native = run([*NAVA, "run", str(path), "--backend", "native"])
            through_c = run([*NAVA, "run", str(path)])
        self.assertEqual(native.returncode, 7, native.stderr)
        self.assertEqual(native.stdout, through_c.stdout)
        self.assertEqual(native.returncode, through_c.returncode,
                         "the program's own exit status, whichever backend built it")
        self.assertEqual(native.stdout, "3628800\n21\ntab\there\n"
                                        "newline \\ \"quoted\"\nПривет, мир\n")

    def test_run_refuses_the_c_build_options_with_the_native_backend(self):
        with tempfile.TemporaryDirectory() as temp:
            path = write_source(pathlib.Path(temp), "solo",
                                "fn main() -> i64 {\n    return 0\n}\n")
            for option in (["--cc", "cc"], ["--opt", "2"]):
                with self.subTest(option=option[0]):
                    result = run([*NAVA, "run", str(path), "--backend", "native", *option])
                    self.assertEqual(result.returncode, 2)
                    self.assertIn("E0903", result.stderr)
                    self.assertIn("`nava run`", result.stderr,
                                  "the hint names the command it was given to")

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


#: A two-file program, for the linker: every function is called through its module.
MODULES = {
    "helper.nv": """\
fn double(value: i64) -> i64 {
    return value * 2
}

fn label() -> str {
    return "helper"
}
""",
    "main.nv": """\
import helper

fn main() -> i64 {
    print(helper::label())
    print(helper::double(21))
    return helper::double(3)
}
""",
}

#: A trap two frames below `main`, for the trace: the line of every frame is known.
TRACE_PROGRAM = """\
fn scale(x: i64) -> i64 {
    return 100 / x
}

fn report(x: i64) -> i64 {
    return scale(x)
}

fn main() -> i64 {
    print("before")
    let value = report(0)
    print(value)
    return 0
}
"""


class NativeUnitTests(unittest.TestCase):
    """A unit states what it defines and what it leaves to the linker."""

    def test_there_is_one_unit_per_module_and_one_for_the_runtime(self):
        units = units_of(PROGRAMS["calls_and_text"])
        self.assertEqual([unit.name for unit in units], ["module.entry", "runtime"],
                         "the entry module first, then the runtime")

    def test_a_function_is_a_global_symbol_and_everything_else_is_private(self):
        module = units_of(PROGRAMS["calls_and_text"])[0]
        exported = {symbol.name for symbol in module.symbols if not symbol.local}
        self.assertEqual(exported, {"nava.entry.main", "nava.entry.fact",
                                    "nava.entry.six"},
                         "a function's link-time name is qualified by its module")
        private = [symbol.name for symbol in module.symbols if symbol.local]
        self.assertTrue(private, "a unit has labels that are not exported")
        for name in private:
            self.assertRegex(name, r"^module\.entry\.(text|rodata|data)\.",
                             "a private label is qualified by its unit and its section")

    def test_a_reference_every_unit_cannot_resolve_is_a_relocation(self):
        module = units_of(PROGRAMS["calls_and_text"])[0]
        targets = {(relocation.symbol, relocation.kind)
                   for relocation in module.relocations}
        self.assertIn(("nv_print_i64", "pc32"),
                      targets, "a call into the runtime is the linker's job")
        self.assertTrue(any(symbol.startswith("module.entry.rodata.") and kind == "pc32"
                            for symbol, kind in targets),
                        "a string literal is a displacement into this unit's .rodata")

    def test_every_call_is_a_relocation_because_a_call_names_its_callee(self):
        """Inside one unit a call *could* be a distance; it is a name instead.

        A name is what the linker resolves and what `nm` prints, and one rule for
        every call is worth the four bytes the linker has to fill in. Branches inside
        a function stay distances, because a block label is not a name.
        """
        module = units_of(PROGRAMS["calls_and_text"])[0]
        relocated = {relocation.symbol for relocation in module.relocations}
        self.assertIn("nava.entry.fact", relocated, "a recursive call names the callee")
        self.assertIn("nava.entry.six", relocated)
        labels = {relocation.symbol for relocation in module.relocations
                  if relocation.symbol.startswith("module.entry.text.")}
        self.assertEqual(labels, set(),
                         "a block label is a distance inside one function, never a "
                         "name the linker has to resolve")

    def test_every_function_records_its_extent_and_its_lines_in_order(self):
        for unit in units_of(PROGRAMS["calls_and_text"]):
            for function in unit.functions:
                with self.subTest(function=function.symbol):
                    self.assertLess(function.start, function.end)
                    self.assertTrue(function.lines, "a function without lines")
                    offsets = [entry.offset for entry in function.lines]
                    self.assertEqual(offsets, sorted(offsets), "line entries ascend")
                    self.assertEqual(len(offsets), len(set(offsets)),
                                     "one entry per address")
                    for entry in function.lines:
                        self.assertGreaterEqual(entry.offset, function.start)
                        self.assertLess(entry.offset, function.end)
                        self.assertGreater(entry.line, 0)
                    self.assertTrue(function.file.endswith(".nv"),
                                    "a line table names a file, not a path")
                    self.assertNotIn(os.sep, function.file)


class NativeLinkerTests(unittest.TestCase):
    """The linker is the part a two-file program needs, so it is tested alone."""

    def test_the_line_table_is_the_same_size_in_both_passes(self):
        """A size that depends on an address would make the second pass move it."""
        units = units_of(PROGRAMS["calls_and_text"])
        size_only = linker.build_lines(units)
        filled = linker.build_lines(units, lambda unit, offset: offset)
        self.assertEqual(len(size_only), len(filled))
        self.assertEqual(size_only, bytearray(len(size_only)),
                         "the size pass writes no content")

    def test_the_image_names_its_sections_its_entry_point_and_its_symbols(self):
        image = linker.link(units_of(PROGRAMS["calls_and_text"]))
        self.assertEqual([section.name for section in image.sections],
                         [".text", ".rodata", ".nava.lines", ".data"])
        self.assertEqual(image.address("_start"), image.entry)
        self.assertLess(image.address("nava.entry.main"), image.address("_start"),
                        "the entry module is laid out before the runtime")
        self.assertGreater(image.section(".rodata").address,
                           image.section(".text").address)
        self.assertGreater(image.section(".data").address,
                           image.section(".rodata").address)

    def test_an_undefined_symbol_is_refused_by_name(self):
        unit = Unit("probe", text=b"\x90" * 8,
                    symbols=[Symbol("_start", "text", 0)],
                    relocations=[Relocation("text", 4, "nobody.defines.this")])
        with self.assertRaises(linker.LinkError) as caught:
            linker.link([unit])
        self.assertIn("nobody.defines.this", str(caught.exception))
        self.assertEqual(caught.exception.missing, ["nobody.defines.this"])

    def test_a_missing_entry_point_is_refused(self):
        with self.assertRaises(linker.LinkError) as caught:
            linker.link(units_of(PROGRAMS["calls_and_text"]), entry_symbol="no.such.thing")
        self.assertEqual(caught.exception.missing, ["no.such.thing"])

    def test_two_units_with_one_name_are_refused(self):
        unit = Unit("same", text=b"\x90" * 8, symbols=[Symbol("_start", "text", 0)])
        with self.assertRaises(linker.LinkError) as caught:
            linker.link([unit, Unit("same", text=b"", symbols=[])])
        self.assertIn("same", str(caught.exception))


class NativeModuleTests(unittest.TestCase):
    """Two files, two units, one executable — the reason the linker exists."""

    def write_program(self, directory: pathlib.Path) -> pathlib.Path:
        for name, source in MODULES.items():
            (directory / name).write_text(source, encoding="utf-8")
        return directory / "main.nv"

    def test_a_program_in_two_files_prints_the_same_through_both_backends(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            path = self.write_program(directory)
            built = run([*NAVA, "build", str(path), "-o", str(directory / "native"),
                         "--backend", "native"])
            self.assertEqual(built.returncode, 0, built.stderr)
            compiled = run([*NAVA, "build", str(path), "-o", str(directory / "c")])
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            native = run_binary(directory / "native")
            through_c = run_binary(directory / "c")
        self.assertEqual(native, ("helper\n42\n", "", 6))
        self.assertEqual(native, through_c,
                         "the two backends disagree about a two-file program")

    def test_each_module_keeps_its_own_symbols(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            path = self.write_program(directory)
            program = load_program(path.read_text(encoding="utf-8"), path=path)
            units = x86_64.units(lower.lower(program))
        self.assertEqual([unit.name for unit in units],
                         ["module.entry", "module.helper", "runtime"])
        exported = {symbol.name
                    for unit in units[:2] for symbol in unit.symbols if not symbol.local}
        self.assertEqual(exported, {"nava.entry.main", "nava.helper.double",
                                    "nava.helper.label"},
                         "a function's name says which module defined it")
        entry = next(unit for unit in units if unit.name == "module.entry")
        called = {relocation.symbol for relocation in entry.relocations}
        self.assertIn("nava.helper.double", called, "a call across modules is a name")
        self.assertIn("nava.helper.label", called)
        helper = next(unit for unit in units if unit.name == "module.helper")
        outside = {relocation.symbol for relocation in helper.relocations
                   if not relocation.symbol.startswith("module.helper.")}
        self.assertEqual(outside, {"nv_fail"},
                         "a checked multiply reaches the trap path and nothing else: "
                         "the module names no other module, and the runtime names it")

    def test_a_line_table_names_the_file_each_function_came_from(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            path = self.write_program(directory)
            built = run([*NAVA, "build", str(path), "-o", str(directory / "native"),
                         "--backend", "native"])
            self.assertEqual(built.returncode, 0, built.stderr)
            parsed = reader.read(directory / "native")
        files = {record.name: record.file for record in parsed.lines}
        self.assertEqual(files["nava.entry.main"], "main.nv")
        self.assertEqual(files["nava.helper.double"], "helper.nv")


class NativeReaderTests(unittest.TestCase):
    """Our reader, checked against the reader everybody else uses."""

    def build(self, source: str, directory: pathlib.Path, name: str = "program") -> pathlib.Path:
        path = write_source(directory, name, source)
        result = run([*NAVA, "build", str(path), "-o", str(directory / name),
                      "--backend", "native"])
        self.assertEqual(result.returncode, 0, result.stderr)
        return directory / name

    def tool(self, name: str) -> str:
        found = shutil.which(name)
        if found is None:
            self.fail(f"{name} is required to cross-check the ELF writer: a file the "
                      f"tools cannot read is a file nobody can debug")
        return found

    def test_the_reader_and_readelf_agree_about_the_header_and_the_segments(self):
        with tempfile.TemporaryDirectory() as temp:
            binary = self.build(PROGRAMS["calls_and_text"], pathlib.Path(temp))
            text = run([self.tool("readelf"), "-W", "-h", "-l", str(binary)]).stdout
            parsed = reader.read(binary)
        entry = re.search(r"Entry point address:\s+(0x[0-9a-f]+)", text)
        self.assertIsNotNone(entry, text)
        self.assertEqual(int(entry.group(1), 16), parsed.header.entry)
        programs = re.search(r"Number of program headers:\s+(\d+)", text)
        self.assertEqual(int(programs.group(1)), len(parsed.segments))
        pattern = re.compile(
            r"^\s+LOAD\s+(0x[0-9a-f]+)\s+(0x[0-9a-f]+)\s+(0x[0-9a-f]+)\s+"
            r"(0x[0-9a-f]+)\s+(0x[0-9a-f]+)\s+(\S+(?:\s\S)?)\s+(0x[0-9a-f]+)\s*$")
        loads = [match for match in (pattern.match(line) for line in text.splitlines())
                 if match is not None]
        self.assertEqual(len(loads), len(parsed.segments), text)
        for load, segment in zip(loads, parsed.segments):
            self.assertEqual(int(load.group(1), 16), segment.offset)
            self.assertEqual(int(load.group(2), 16), segment.address)
            self.assertEqual(int(load.group(4), 16), segment.filesz)
            self.assertEqual(int(load.group(7), 16), segment.alignment)
            flags = load.group(6)
            for letter, bit in (("R", 4), ("W", 2), ("E", 1)):
                self.assertEqual(letter in flags, bool(segment.flags & bit),
                                 f"{letter} disagrees for {load.group(0)}")

    def test_the_reader_and_nm_agree_about_every_symbol(self):
        with tempfile.TemporaryDirectory() as temp:
            binary = self.build(PROGRAMS["calls_and_text"], pathlib.Path(temp))
            text = run([self.tool("nm"), str(binary)])
            self.assertEqual(text.returncode, 0, "nm could not read the symbol table")
            parsed = reader.read(binary)
        listed = {}
        for line in text.stdout.splitlines():
            parts = line.split()
            if len(parts) == 3:
                listed[parts[2]] = (int(parts[0], 16), parts[1])
        named = {symbol.name: symbol for symbol in parsed.symbols if symbol.name}
        self.assertEqual(set(listed), set(named),
                         "our reader and nm disagree about which symbols exist")
        for name, (value, kind) in listed.items():
            symbol = named[name]
            self.assertEqual(symbol.value, value, name)
            # `nm` prints a capital for a global and a lower-case letter for a local,
            # so the letter's case and our binding are two accounts of the same fact.
            self.assertEqual(kind.isupper(), symbol.binding == "global", name)
            section = parsed.sections[symbol.section].name
            expected = {".text": "T", ".rodata": "R", ".data": "D",
                        ".nava.lines": "R"}[section]
            self.assertEqual(kind.upper(), expected,
                             f"{name} is in {section}, so nm's letter is wrong")

    def test_the_reader_names_the_function_and_the_line_of_an_address(self):
        with tempfile.TemporaryDirectory() as temp:
            binary = self.build(PROGRAMS["calls_and_text"], pathlib.Path(temp), "named")
            parsed = reader.read(binary)
        main = parsed.symbol("nava.entry.main")
        self.assertEqual(parsed.symbolise(main.value), "nava.entry.main (named.nv:10)")
        self.assertIsNone(parsed.symbolise(parsed.symbol("nv_trace_table").value),
                          "a data address belongs to no function")
        record = parsed.function_at(main.value)
        self.assertEqual((record.start, record.end, record.file),
                         (main.value, main.value + main.size, "named.nv"))

    def test_a_truncated_file_is_rejected_rather_than_guessed_at(self):
        with tempfile.TemporaryDirectory() as temp:
            binary = self.build(PROGRAMS["calls_and_text"], pathlib.Path(temp))
            image = binary.read_bytes()
        for length in (0, 8, 40, 100, len(image) // 2, len(image) - 1):
            with self.subTest(length=length):
                with self.assertRaises(reader.Malformed):
                    reader.read(image[:length])

    def test_something_that_is_not_an_executable_is_rejected(self):
        for payload in (b"", b"not an elf at all", b"\x7fELF" + bytes(8),
                        b"\x7fELF\x01\x01\x01\x00" + bytes(56)):
            with self.subTest(payload=payload[:8]):
                with self.assertRaises(reader.Malformed):
                    reader.read(payload)

    def test_the_reader_reads_what_our_writer_wrote_and_nothing_else(self):
        """The negative control for the reader: a plausible header, a false body."""
        with tempfile.TemporaryDirectory() as temp:
            binary = self.build(PROGRAMS["calls_and_text"], pathlib.Path(temp))
            image = bytearray(binary.read_bytes())
        parsed = reader.read(bytes(image))
        lines = parsed.section(".nava.lines")
        bad_magic = bytearray(image)
        bad_magic[lines.offset] = 0
        with self.assertRaises(reader.Malformed):
            reader.read(bytes(bad_magic))
        bad_section = bytearray(image)
        bad_section[parsed.header.shoff + 64 + 32:parsed.header.shoff + 64 + 40] = (
            (1 << 62).to_bytes(8, "little"))
        with self.assertRaises(reader.Malformed):
            reader.read(bytes(bad_section))


class NativeTraceTests(unittest.TestCase):
    """The trace: a symbolised stack, and silence when it is not asked for."""

    def build(self, source: str, directory: pathlib.Path) -> pathlib.Path:
        path = write_source(directory, "probe", source)
        result = run([*NAVA, "build", str(path), "-o", str(directory / "probe"),
                      "--backend", "native"])
        self.assertEqual(result.returncode, 0, result.stderr)
        return directory / "probe"

    def test_without_the_variable_a_trap_prints_exactly_the_message(self):
        with tempfile.TemporaryDirectory() as temp:
            binary = self.build(TRACE_PROGRAM, pathlib.Path(temp))
            result = subprocess.run([str(binary)], capture_output=True, text=True,
                                    timeout=60, env={"PATH": "/usr/bin:/bin"})
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "before\n")
        self.assertEqual(result.stderr, "nava runtime error: division by zero\n",
                         "the trace must be off by default, or the differential "
                         "would compare two different outputs")

    def test_the_variable_adds_a_frame_for_every_caller_from_the_innermost_out(self):
        with tempfile.TemporaryDirectory() as temp:
            binary = self.build(TRACE_PROGRAM, pathlib.Path(temp))
            result = subprocess.run([str(binary)], capture_output=True, text=True,
                                    timeout=60,
                                    env={"PATH": "/usr/bin:/bin", "NAVA_TRACE": "1"})
            parsed = reader.read(binary)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "before\n")
        lines = result.stderr.splitlines()
        self.assertEqual(lines[0], "nava runtime error: division by zero")
        self.assertEqual(lines[1:], [
            "  at nava.entry.scale (probe.nv:2)",
            "  at nava.entry.report (probe.nv:6)",
            "  at nava.entry.main (probe.nv:11)",
        ], "the trace walks the frame chain from the failing call outwards")
        # The trace is not asserted from memory: every line it names is a line the
        # line table maps, and the frame it starts with is a function of the program.
        mapped = {}
        for record in parsed.lines:
            mapped[record.name] = {line for _address, line in record.lines}
        for frame in lines[1:]:
            match = re.match(r"  at (\S+) \((.+):(\d+)\)$", frame)
            self.assertIsNotNone(match, frame)
            name, file, line = match.groups()
            self.assertIn(name, mapped, f"{name} is not a function of the program")
            self.assertEqual(file, "probe.nv")
            self.assertIn(int(line), mapped[name],
                          f"{name} has no line {line} in its line table")

    def test_a_trace_stops_at_the_bottom_of_the_stack(self):
        """A failing `main` is two frames: the program's own, and nothing invented."""
        source = "fn main() -> i64 {\n    let zero: i64 = 0\n    return 1 / zero\n}\n"
        with tempfile.TemporaryDirectory() as temp:
            binary = self.build(source, pathlib.Path(temp))
            result = subprocess.run([str(binary)], capture_output=True, text=True,
                                    timeout=60,
                                    env={"PATH": "/usr/bin:/bin", "NAVA_TRACE": "1"})
        self.assertEqual(result.stderr.splitlines()[1:],
                         ["  at nava.entry.main (probe.nv:3)"])


class NativeStructTests(unittest.TestCase):
    """A struct is a value with fields at fixed positions (PLAN.md N14e).

    Two claims, both comparisons rather than expectations: the two backends agree on a
    program that copies a struct and writes through a nested path, and an `f64` in a
    field or a comparison means what IEEE says it means on either side.
    """

    #: A struct stored, passed, copied and written into — through a nested path, which
    #: is the write a record with identity would get wrong.
    COPY_PROGRAM = """\
edition 2;

struct Point {
    x: i64,
    y: i64
}

struct Segment {
    from: Point,
    to: Point
}

fn moved(p: Point) -> i64 {
    p.x = p.x + 100
    return p.x
}

fn main() {
    let a = Point(x: 1, y: 2)
    print(a.x)
    print(moved(a))
    print(a.x)
    let s = Segment(from: a, to: Point(x: 3, y: 4))
    s.to.y = 40
    print(s.to.y)
    print(s.from.x)
    print(a.y)
}
"""

    def build(self, source: str, directory: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
        path = write_source(directory, "structs", source)
        return build_both(path, directory)

    def test_a_struct_is_copied_where_it_is_stored(self):
        with tempfile.TemporaryDirectory() as temp:
            c_binary, native_binary = self.build(self.COPY_PROGRAM, pathlib.Path(temp))
            c_out = run([str(c_binary)])
            native_out = run([str(native_binary)])
        self.assertEqual((c_out.stdout, c_out.stderr, c_out.returncode),
                         (native_out.stdout, native_out.stderr, native_out.returncode))
        # The numbers are the language's, not the backend's: `moved` cannot change the
        # caller's `a`, and a nested write in `s` cannot change `a` either.
        self.assertEqual(c_out.stdout, "1\n101\n1\n40\n1\n2\n")

    def test_a_str_field_is_a_string_like_any_other(self):
        source = """\
edition 2;

struct Card {
    title: str,
    number: i64
}

fn rename(c: Card) -> str {
    c.title = "other"
    return c.title
}

fn main() {
    let c = Card(title: "hi", number: 3)
    print(c.title)
    print(rename(c))
    print(c.title)
    print(c.number)
}
"""
        with tempfile.TemporaryDirectory() as temp:
            c_binary, native_binary = self.build(source, pathlib.Path(temp))
            c_out = run([str(c_binary)])
            native_out = run([str(native_binary)])
        self.assertEqual(c_out.stdout, native_out.stdout)
        self.assertEqual(native_out.stdout, "hi\nother\nhi\n3\n")

    def test_the_module_states_every_layout_it_lays_out(self):
        """A layout is a fact about the program: the IR carries it, the backend reads it."""
        source = pathlib.Path("tests/fixtures/positive/structs_basic.nv").read_text(encoding="utf-8")
        module = lower_source(source)
        self.assertEqual(module.structs["Point"], (("x", "i64"), ("y", "i64")))
        self.assertEqual(module.structs["Segment"],
                         (("from", "Point"), ("to", "Point")))
        # A struct the program declares and never builds is not laid out: a layout the
        # backend does not need is a layout it should not have to agree about.
        self.assertEqual(sorted(module.structs), ["Point", "Segment"])

    def test_a_struct_value_lives_in_a_cell_and_nowhere_else(self):
        module = lower_source(self.COPY_PROGRAM)
        main = module.function("main")
        cells = {cell.name: cell.typ for cell in main.slots}
        self.assertEqual(cells, {"c0": "Point", "c1": "Segment"})
        instructions = [instruction for block in main.blocks
                        for instruction in block.instructions]
        # `a` and `s` are storage, and each is stored into once. A construction that is
        # only read — `Point(x: 3, y: 4)` — is a value and never becomes a cell.
        self.assertEqual(sum(isinstance(item, ir.SlotStore) for item in instructions), 2)
        writes = [item for item in instructions if isinstance(item, ir.FieldStore)]
        self.assertEqual([item.path for item in writes], [("to", "y")])


class NativeFloatTests(unittest.TestCase):
    """`f64` arithmetic and comparison, which is what the IR can state without printing.

    The decimal form of an `f64` is the C runtime's `snprintf`; the native backend does
    not have that conversion yet, so it refuses to print one and these programs observe
    their floats through comparisons instead — which is exactly the half of `f64` the
    language's `if` and `while` can reach.
    """

    PROGRAM = """\
fn main() {
    let a = 1.5
    let b = 2.5
    if a < b { print(1) }
    if b - a == 1.0 { print(2) }
    if a * b > 3.0 { print(3) }
    if -a == 0.0 - 1.5 { print(4) }
    let nan = 0.0 / 0.0
    print(nan == nan)
    print(nan != nan)
    print(nan < 1.0)
    print(nan >= 1.0)
    let inf = 1.0 / 0.0
    print(inf > 1.0)
    print(-inf < 0.0)
}
"""

    def test_float_arithmetic_and_comparison_agree_with_the_c_backend(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            path = write_source(directory, "floats", self.PROGRAM)
            c_binary, native_binary = build_both(path, directory)
            c_out = run([str(c_binary)])
            native_out = run([str(native_binary)])
        self.assertEqual((c_out.stdout, c_out.stderr, c_out.returncode),
                         (native_out.stdout, native_out.stderr, native_out.returncode))
        # `nan` compares unordered: false for `==`, `<` and `>=`, true for `!=`.
        self.assertEqual(native_out.stdout.splitlines(),
                         ["1", "2", "3", "4", "false", "true", "false", "false",
                          "true", "true"])

    def test_an_f64_crosses_a_call_as_its_bits(self):
        source = """\
fn mean(a: f64, b: f64) -> f64 {
    return (a + b) / 2.0
}

fn main() {
    if mean(1.0, 2.0) == 1.5 { print(1) }
    if mean(0.0, 0.0) == 0.0 { print(2) }
}
"""
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            path = write_source(directory, "mean", source)
            c_binary, native_binary = build_both(path, directory)
            c_out = run([str(c_binary)])
            native_out = run([str(native_binary)])
        self.assertEqual(c_out.stdout, native_out.stdout)
        self.assertEqual(native_out.stdout, "1\n2\n")

    def test_a_float_constant_is_its_bits_and_the_dump_says_so(self):
        module = lower_source("fn main() -> i64 {\n    let x: f64 = 1.5\n"
                              "    if x > 1.0 { print(1) }\n    return 0\n}\n")
        dump = ir.dump(module)
        self.assertIn("= const f64 1.5", dump)
        self.assertIn("= cmp gt f64", dump)
        # The instruction says which unit does the work; an `i64` comparison does not
        # gain the word, so a reader can tell the two apart in the dump.
        integer = ir.dump(lower_source("fn main() -> i64 {\n    if 1 < 2 { print(1) }\n"
                                       "    return 0\n}\n"))
        self.assertIn("= cmp lt i64", integer)


class NativeInputTestsOrdering(unittest.TestCase):
    """`print` before a `read` reaches the reader in that order on both backends.

    The C runtime flushes standard output before it reads, because its reader is buffered;
    the native backend writes each line as a syscall and buffers nothing. The two must
    still agree about what the program's output is, and a prompt printed before a read is
    the case that would show it if they did not.
    """

    def test_a_line_printed_before_a_read_comes_first(self):
        source = """\
fn main() {
    print(1)
    let n = read_i64()
    print(2)
    print(n)
}
"""
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            path = write_source(directory, "interleave", source)
            c_binary, native_binary = build_both(path, directory)
            c_out = subprocess.run([str(c_binary)], input="5\n", capture_output=True,
                                   text=True, timeout=120)
            native_out = subprocess.run([str(native_binary)], input="5\n",
                                        capture_output=True, text=True, timeout=120)
        self.assertEqual((c_out.stdout, c_out.stderr, c_out.returncode),
                         (native_out.stdout, native_out.stderr, native_out.returncode))
        self.assertEqual(native_out.stdout, "1\n2\n5\n")


class NativeTextTests(unittest.TestCase):
    """Text through one IR: a comparison of content, and the four string operations.

    A native `str` is a pointer to `[length][bytes]` and the C backend's is a NUL-
    terminated `char *`; the IR says only what the language says, so each backend keeps
    its own representation and the differential is what holds them together.
    """

    PROGRAM = """\
edition 2;

fn shown(text: str) -> i64 {
    return bytes_len(text)
}

fn main() {
    let joined = str_concat("abc", "def")
    print(joined)
    print(shown(joined))
    print(str_from_i64(-42))
    print(str_slice("hello world", 6, 11))
    print(byte_at("AZ", 0))
    print(joined == "abcdef")
    print(joined != "abcdef")
    print("" == "")
    print(str_concat("", "") == "")
    let empty = ""
    print(bytes_len(empty))
}
"""

    def build(self, source: str, directory: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
        path = write_source(directory, "text", source)
        return build_both(path, directory)

    def test_text_operations_agree_with_the_c_backend(self):
        with tempfile.TemporaryDirectory() as temp:
            c_binary, native_binary = self.build(self.PROGRAM, pathlib.Path(temp))
            c_out = run([str(c_binary)])
            native_out = run([str(native_binary)])
        self.assertEqual((c_out.stdout, c_out.stderr, c_out.returncode),
                         (native_out.stdout, native_out.stderr, native_out.returncode))
        self.assertEqual(native_out.stdout.splitlines(),
                         ["abcdef", "6", "-42", "world", "65", "true", "false", "true",
                          "true", "0"])

    def test_a_string_comparison_is_text_and_not_storage(self):
        """Two strings built separately are equal when they say the same thing.

        The comparison is `nv_str_cmp` over the bytes, so `str_concat("a", "b")` and
        `str_concat("ab", "")` are one answer, which is what a pointer comparison would
        have got wrong.
        """
        source = """\
fn main() {
    let a = str_concat("a", "b")
    let b = str_concat("ab", "")
    print(a == b)
    print(a == "ab")
    print("ab" == a)
    print(a != str_concat("a", "c"))
}
"""
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            path = write_source(directory, "compare", source)
            c_binary, native_binary = build_both(path, directory)
            c_out = run([str(c_binary)])
            native_out = run([str(native_binary)])
        self.assertEqual(c_out.stdout, native_out.stdout)
        self.assertEqual(native_out.stdout, "true\ntrue\ntrue\ntrue\n")

    def test_a_byte_index_out_of_bounds_is_the_c_runtime_message(self):
        source = """\
fn main() {
    print(byte_at("hi", 5))
}
"""
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            path = write_source(directory, "byte", source)
            c_binary, native_binary = build_both(path, directory)
            c_out = run([str(c_binary)])
            native_out = run([str(native_binary)])
        self.assertEqual((c_out.stdout, c_out.stderr, c_out.returncode),
                         (native_out.stdout, native_out.stderr, native_out.returncode))
        self.assertEqual(native_out.stderr.strip(),
                         "nava runtime error: byte index 5 is out of bounds for a "
                         "string of 2 bytes")

    def test_a_slice_out_of_bounds_names_three_numbers(self):
        source = """\
fn main() {
    print(str_slice("hello", 3, 9))
}
"""
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            path = write_source(directory, "slice", source)
            c_binary, native_binary = build_both(path, directory)
            c_out = run([str(c_binary)])
            native_out = run([str(native_binary)])
        self.assertEqual((c_out.stdout, c_out.stderr, c_out.returncode),
                         (native_out.stdout, native_out.stderr, native_out.returncode))
        self.assertEqual(native_out.stderr.strip(),
                         "nava runtime error: string slice 3..9 is out of bounds for a "
                         "string of 5 bytes")

    def test_the_text_of_a_number_is_the_text_printed_str_alone(self):
        """`str_from_i64` and `print` share one routine, so they cannot disagree."""
        source = """\
fn main() {
    print(str_from_i64(0))
    print(str_from_i64(-9223372036854775807 - 1))
    print(str_from_i64(9223372036854775807))
    print(bytes_len(str_from_i64(-9223372036854775807 - 1)))
}
"""
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            path = write_source(directory, "digits", source)
            c_binary, native_binary = build_both(path, directory)
            c_out = run([str(c_binary)])
            native_out = run([str(native_binary)])
        self.assertEqual(c_out.stdout, native_out.stdout)
        self.assertEqual(native_out.stdout.splitlines(),
                         ["0", "-9223372036854775808", "9223372036854775807", "20"])


class NativeInputTests(unittest.TestCase):
    """`read_i64` reads standard input, and refuses it the way the C runtime does."""

    def build(self, directory: pathlib.Path):
        source = "fn main() {\n    print(read_i64())\n}\n"
        path = write_source(directory, "read", source)
        return build_both(path, directory)

    def outcome(self, binary: pathlib.Path, text: str) -> tuple[str, str, int]:
        result = subprocess.run([str(binary)], input=text, capture_output=True,
                                text=True, timeout=120)
        return result.stdout, result.stderr, result.returncode

    def test_a_token_is_read_and_converted(self):
        with tempfile.TemporaryDirectory() as temp:
            c_binary, native_binary = self.build(pathlib.Path(temp))
            for text in ("42", "-7", "+5", "  13  ", "0\n"):
                with self.subTest(input=text):
                    self.assertEqual(self.outcome(c_binary, text),
                                     self.outcome(native_binary, text))
            self.assertEqual(self.outcome(native_binary, "  -7 ")[0], "-7\n")

    def test_the_two_extremes_are_read_exactly(self):
        with tempfile.TemporaryDirectory() as temp:
            c_binary, native_binary = self.build(pathlib.Path(temp))
            for text in ("9223372036854775807", "-9223372036854775808"):
                with self.subTest(input=text):
                    self.assertEqual(self.outcome(c_binary, text),
                                     self.outcome(native_binary, text))
                    self.assertEqual(self.outcome(native_binary, text)[0], text + "\n")

    def test_every_refusal_is_the_c_runtime_message_and_status(self):
        """Four ways to be wrong, one message each, and the same on both backends."""
        cases = {
            "": "expected an i64, reached end of input",
            "abc": "expected a decimal i64",
            "12x": "invalid character in i64 input",
            "99999999999999999999": "input outside i64 range",
            "9223372036854775808": "input outside i64 range",
        }
        with tempfile.TemporaryDirectory() as temp:
            c_binary, native_binary = self.build(pathlib.Path(temp))
            for text, message in cases.items():
                with self.subTest(input=text):
                    c_out = self.outcome(c_binary, text)
                    native_out = self.outcome(native_binary, text)
                    self.assertEqual(c_out, native_out)
                    self.assertEqual(native_out[0], "", "a refusal prints nothing")
                    self.assertEqual(native_out[2], 1)
                    self.assertEqual(native_out[1].strip(),
                                     "nava runtime error: " + message)


class NativeEnumTests(unittest.TestCase):
    """An enum is a tag and what the variant carries, and a `match` reads the tag.

    The value lives in an arena record whose first word is the tag; a pattern binds the
    words after it. The C backend keeps a `struct` with a tag and a union, so the two
    representations differ while the behaviour does not — which is the point of stating
    the tag and the payload as instructions rather than leaving them to a printer.
    """

    PROGRAM = """\
edition 2;

enum Shape {
    Dot,
    Circle(i64),
    Rect(i64, i64)
}

fn area(s: Shape) -> i64 {
    match s {
        Shape::Dot => { return 0 }
        Shape::Circle(r) => { return 3 * r * r }
        Shape::Rect(w, h) => { return w * h }
    }
}

fn describe(s: Shape, tag: i64) -> i64 {
    let extra = 0
    match s {
        Shape::Circle(r) => { extra = r * 10 }
        _ => { extra = 0 - 1 }
    }
    return extra + tag
}

fn main() {
    print(area(Shape::Dot))
    print(area(Shape::Circle(2)))
    print(area(Shape::Rect(3, 4)))
    print(describe(Shape::Circle(7), 1))
    print(describe(Shape::Rect(1, 1), 2))
}
"""

    def test_an_enum_and_a_match_agree_with_the_c_backend(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            path = write_source(directory, "enum", self.PROGRAM)
            c_binary, native_binary = build_both(path, directory)
            c_out = run([str(c_binary)])
            native_out = run([str(native_binary)])
        self.assertEqual((c_out.stdout, c_out.stderr, c_out.returncode),
                         (native_out.stdout, native_out.stderr, native_out.returncode))
        # `describe(Rect(1, 1), 2)` takes the wildcard arm, which answers -1, so the
        # sum is 1: the arm that does not match is the one that runs.
        self.assertEqual(native_out.stdout.splitlines(), ["0", "12", "12", "71", "1"])

    def test_a_match_through_a_parameter_sees_the_tag_it_was_given(self):
        """The tag travels with the value: it is in the record, not in the call site."""
        source = """\
edition 2;

enum Kind {
    A(i64),
    B(str),
    Empty
}

fn say(k: Kind) {
    match k {
        Kind::A(n) => { print(n) }
        Kind::B(text) => { print(text) }
        Kind::Empty => { print("empty") }
    }
}

fn main() {
    say(Kind::A(5))
    say(Kind::B("bee"))
    say(Kind::Empty)
}
"""
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            path = write_source(directory, "payload", source)
            c_binary, native_binary = build_both(path, directory)
            c_out = run([str(c_binary)])
            native_out = run([str(native_binary)])
        self.assertEqual((c_out.stdout, c_out.stderr, c_out.returncode),
                         (native_out.stdout, native_out.stderr, native_out.returncode))
        self.assertEqual(native_out.stdout, "5\nbee\nempty\n")

    def test_a_match_inside_a_loop_reads_the_value_it_was_given_each_time(self):
        source = """\
edition 2;

enum Step {
    Add(i64),
    Stop
}

fn main() {
    let total = 0
    let i = 0
    while i < 5 {
        let step: Step = Step::Add(i * 2)
        match step {
            Step::Add(n) => { total = total + n }
            Step::Stop => { total = 0 }
        }
        i = i + 1
    }
    print(total)
}
"""
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            path = write_source(directory, "loop", source)
            c_binary, native_binary = build_both(path, directory)
            c_out = run([str(c_binary)])
            native_out = run([str(native_binary)])
        self.assertEqual(c_out.stdout, native_out.stdout)
        self.assertEqual(native_out.stdout, "20\n")

    def test_the_ir_states_the_tag_and_the_layouts(self):
        """`emit-ir` shows a tag comparison against a constant and the enum's layout."""
        with tempfile.TemporaryDirectory() as temp:
            path = write_source(pathlib.Path(temp), "enum", self.PROGRAM)
            result = run([*NAVA, "emit-ir", str(path)])
        self.assertEqual(result.returncode, 0, result.stderr)
        text = result.stdout
        self.assertIn("enum Shape {Dot(), Circle(i64), Rect(i64, i64)}", text)
        self.assertIn("= tag %", text)
        self.assertIn("= payload %", text)
        self.assertIn("= variant Shape::Circle(%", text)
        # A match compares the tag with the variant's position, so the constant is in
        # the IR and no backend looks a name up at run time.
        self.assertIn("= const i64 1", text)

    def test_a_payload_the_backend_cannot_hold_is_refused_by_name(self):
        """A struct payload waits for the copy a struct value needs inside a record."""
        source = """\
edition 2;

struct Point {
    x: i64
}

enum Holder {
    Empty,
    Full(Point)
}

fn main() {
    let h: Holder = Holder::Full(Point(x: 1))
    print(1)
}
"""
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            path = write_source(directory, "payload", source)
            native = run([*NAVA, "run", str(path), "--backend", "native"])
            c_build = run([*NAVA, "build", str(path), "-o", str(directory / "c")])
        self.assertEqual(native.returncode, 2)
        self.assertIn("E0901", native.stderr)
        self.assertIn("a payload of type Point in 'Holder::Full'", native.stderr)
        self.assertEqual(c_build.returncode, 0,
                         "the C backend builds what the native one refuses")


class NativeReturnPathTests(unittest.TestCase):
    """A function whose every path returns is a function with nothing after the choice.

    Before N14g the lowering refused it: the block after an `if`/`match` had no
    terminator, and the function-level check read that as a path that does not return.
    The IR now says `unreachable`, which is what the program proved.
    """

    def test_an_if_where_both_halves_return_lowers(self):
        source = """\
fn pick(n: i64) -> i64 {
    if n > 0 {
        return 1
    } else {
        return 2
    }
}

fn main() {
    print(pick(1))
    print(pick(0 - 1))
}
"""
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            path = write_source(directory, "pick", source)
            c_binary, native_binary = build_both(path, directory)
            c_out = run([str(c_binary)])
            native_out = run([str(native_binary)])
        self.assertEqual(c_out.stdout, native_out.stdout)
        self.assertEqual(native_out.stdout, "1\n2\n")

    def test_the_block_after_a_finished_choice_is_stated_unreachable(self):
        with tempfile.TemporaryDirectory() as temp:
            path = write_source(pathlib.Path(temp), "pick",
                                "fn pick(n: i64) -> i64 {\n"
                                "    if n > 0 {\n        return 1\n    } else {\n"
                                "        return 2\n    }\n}\n\n"
                                "fn main() {\n    print(pick(1))\n}\n")
            result = run([*NAVA, "emit-ir", str(path)])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("unreachable", result.stdout)

    def test_a_stmt_after_a_finished_match_is_not_emitted(self):
        """Unreachable code after the join is not lowered at all, on either backend."""
        source = """\
edition 2;

enum Flag {
    On,
    Off
}

fn value(f: Flag) -> i64 {
    match f {
        Flag::On => { return 1 }
        Flag::Off => { return 0 }
    }
}

fn main() {
    print(value(Flag::On))
    print(value(Flag::Off))
}
"""
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            path = write_source(directory, "flags", source)
            c_binary, native_binary = build_both(path, directory)
            self.assertEqual(run([str(c_binary)]).stdout,
                             run([str(native_binary)]).stdout)
            self.assertEqual(run([str(native_binary)]).stdout, "1\n0\n")


class NativeDeferTests(unittest.TestCase):
    """`defer` runs where the block is left, last registered first.

    A deferred body is lowered at each exit rather than where it is written, so it sees
    the values its scope holds when it runs — which is what the C backend's deferred
    block does, and the reason the two agree about `defer { print(i) }` in a loop.
    """

    def test_a_defer_runs_on_the_fall_through_path_last_registered_first(self):
        source = """\
edition 2;

fn main() {
    print(1)
    defer { print(2) }
    defer { print(3) }
    print(4)
}
"""
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            path = write_source(directory, "fall", source)
            c_binary, native_binary = build_both(path, directory)
            c_out = run([str(c_binary)])
            native_out = run([str(native_binary)])
        self.assertEqual(c_out.stdout, native_out.stdout)
        self.assertEqual(native_out.stdout, "1\n4\n3\n2\n")

    def test_a_defer_runs_before_the_arrays_of_its_scope_are_given_back(self):
        """A deferred block may still use what its scope owns: it runs first."""
        source = """\
edition 2;

fn probe(n: i64) -> i64 {
    let a: [i64] = [7, 8, 9]
    defer { print(len(a)) }
    defer { print(0 - len(a)) }
    if n > 0 {
        defer { print(555) }
        return a[0] + n
    }
    return 0
}

fn main() {
    print(probe(1))
    print(probe(0 - 1))
}
"""
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            path = write_source(directory, "arrays", source)
            c_binary, native_binary = build_both(path, directory)
            c_out = run([str(c_binary)])
            native_out = run([str(native_binary)])
        self.assertEqual((c_out.stdout, c_out.stderr, c_out.returncode),
                         (native_out.stdout, native_out.stderr, native_out.returncode))
        # The inner `if` block's defer, then the two of the function body, then the
        # returned value; the second call skips the inner block and answers 0.
        self.assertEqual(native_out.stdout.splitlines(),
                         ["555", "-3", "3", "8", "-3", "3", "0"])

    def test_a_defer_in_a_loop_sees_the_values_of_the_iteration_that_ends(self):
        """The fixture's `defer { print(100 + i) }` prints the *incremented* `i`."""
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            path = write_source(directory, "loop",
                                fixture_text("defer_labels"))
            c_binary, native_binary = build_both(path, directory)
            c_out = run([str(c_binary)])
            native_out = run([str(native_binary)])
        self.assertEqual(c_out.stdout, native_out.stdout)
        self.assertEqual(native_out.stdout.splitlines(),
                         ["-1", "1", "1", "0", "101", "1", "102"])

    def test_a_defer_whose_body_assigns_is_refused_by_name(self):
        source = """\
edition 2;

fn main() {
    let n = 0
    defer { n = 1 }
    print(n)
}
"""
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            path = write_source(directory, "assign", source)
            native = run([*NAVA, "run", str(path), "--backend", "native"])
            c_build = run([*NAVA, "build", str(path), "-o", str(directory / "c")])
        self.assertEqual(native.returncode, 2)
        self.assertIn("E0901", native.stderr)
        self.assertIn("a 'defer' whose body assigns a variable", native.stderr)
        self.assertEqual(c_build.returncode, 0)


class NativeSliceTests(unittest.TestCase):
    """A slice is a view of an array, and both backends answer with one.

    The claim is the one the C backend makes with `nv_arr_T_slice`: a slice is two words
    — a length and a pointer into somebody else's elements — checked against the parent
    by `nv_check_slice`'s two questions, in its order and with its words. Nothing is
    copied, so writing through the parent is visible through the view and a slice of a
    thousand elements costs two words; the binding that holds a slice owns nothing, so
    the parent's release is the only release.
    """

    def test_the_fixture_agrees_with_the_c_backend(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            path = write_source(directory, "slices", fixture_text("slices_basic"))
            c_binary, native_binary = build_both(path, directory)
            c_out = run([str(c_binary)])
            native_out = run([str(native_binary)])
        self.assertEqual((c_out.stdout, c_out.stderr, c_out.returncode),
                         (native_out.stdout, native_out.stderr, native_out.returncode))
        self.assertEqual(native_out.stdout.splitlines(),
                         ["2", "20", "30", "30", "1", "9", "b"])

    def test_a_slice_is_a_view_and_not_a_copy(self):
        """The view reads the parent's elements, and the two bound pairs may be equal."""
        source = """\
edition 2;

fn main() {
    let values: [i64] = [10, 20, 30, 40]
    let part = values[1..3]
    print(len(part))
    print(part[0])
    let empty = values[2..2]
    print(len(empty))
    let all = values[0..4]
    print(len(all))
    let text: [str] = ["a", "b", "c"]
    let letters = text[1..3]
    print(letters[0])
}
"""
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            path = write_source(directory, "view", source)
            c_binary, native_binary = build_both(path, directory)
            c_out = run([str(c_binary)])
            native_out = run([str(native_binary)])
        self.assertEqual(c_out.stdout, native_out.stdout)
        self.assertEqual(native_out.stdout.splitlines(),
                         ["2", "20", "0", "4", "b"])

    def test_the_bounds_are_checked_and_the_message_is_the_c_one(self):
        """Both out-of-range bounds stop the program with the runtime's own words."""
        cases = {
            "end": ("let a: [i64] = [1, 2, 3]\n    print(len(a[2..5]))",
                    "nava runtime error: slice end 5 is out of range for start 2 "
                    "and length 3"),
            "start": ("let a: [i64] = [1, 2, 3]\n    print(len(a[0 - 1..2]))",
                      "nava runtime error: slice start -1 is out of range for length 3"),
            "reversed": ("let a: [i64] = [1, 2, 3]\n    print(len(a[2..1]))",
                         "nava runtime error: slice end 1 is out of range for start 2 "
                         "and length 3"),
        }
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            for name, (body, message) in cases.items():
                with self.subTest(bound=name):
                    source = f"edition 2;\n\nfn main() {{\n    {body}\n}}\n"
                    path = write_source(directory, name, source)
                    c_binary, native_binary = build_both(path, directory)
                    c_out = run([str(c_binary)])
                    native_out = run([str(native_binary)])
                    self.assertEqual((c_out.stdout, c_out.stderr, c_out.returncode),
                                     (native_out.stdout, native_out.stderr,
                                      native_out.returncode))
                    self.assertEqual(native_out.returncode, 1)
                    self.assertEqual(native_out.stderr, message + "\n")

    def test_the_lowering_states_the_view_once(self):
        """`slice` is an instruction: values in, a value out, no memory traffic."""
        module = lower_source("""\
edition 2;

fn main() {
    let a: [i64] = [1, 2, 3]
    let part = a[0..2]
    print(len(part))
}
""")
        slices = [instruction for function in module.functions
                  for block in function.blocks
                  for instruction in block.instructions
                  if isinstance(instruction, ir.Slice)]
        self.assertEqual(len(slices), 1)
        self.assertEqual(slices[0].typ, "[i64]")
        self.assertIn("= slice %", ir.dump(module))


class NativeStructArrayTests(unittest.TestCase):
    """An array whose elements are structs: the same six instructions, one word each.

    A struct on this backend is the address of its record, so the element of the array is
    that address and the array still holds one word per element. What that costs is
    stated and tested: a struct that enters an array is **copied**, so two elements named
    from one record stay two, exactly as C copies a struct into its element.
    """

    def test_the_fixture_agrees_with_the_c_backend(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            path = write_source(directory, "points", fixture_text("struct_arrays"))
            c_binary, native_binary = build_both(path, directory)
            c_out = run([str(c_binary)])
            native_out = run([str(native_binary)])
        self.assertEqual((c_out.stdout, c_out.stderr, c_out.returncode),
                         (native_out.stdout, native_out.stderr, native_out.returncode))
        self.assertEqual(native_out.stdout.splitlines(), ["21", "3", "4", "0"])

    def test_an_element_is_copied_as_it_enters_the_array(self):
        """A record written twice, then changed, is two elements and not one."""
        source = """\
edition 2;

struct Point {
    x: i64,
    y: i64
}

fn main() {
    let one = Point(x: 1, y: 2)
    let twos: [Point] = [one, one]
    one.x = 99
    print(twos[0].x)
    print(twos[1].x)
    print(one.x)
    let filled: [Point] = [Point(x: 0, y: 0)]
    let copy = filled[0]
    copy.y = 7
    print(filled[0].y)
    print(copy.y)
}
"""
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            path = write_source(directory, "copy", source)
            c_binary, native_binary = build_both(path, directory)
            c_out = run([str(c_binary)])
            native_out = run([str(native_binary)])
        self.assertEqual((c_out.stdout, c_out.returncode),
                         (native_out.stdout, native_out.returncode))
        self.assertEqual(native_out.stdout.splitlines(), ["1", "1", "99", "0", "7"])

    def test_a_zeroed_array_of_structs_holds_a_zeroed_record_each(self):
        """`array_zeros` of a struct element fills with records, not with nothing.

        An element is an address, so an array allocated by `calloc` would be a row of
        null pointers and reading a field through one would read unmapped memory. The
        fill is one zeroed record per slot, which is what the first read below proves.
        """
        source = """\
edition 2;

struct Counter {
    hits: i64
}

fn main() {
    let zeros: [Counter] = array_zeros(2)
    print(zeros[0].hits)
    print(zeros[1].hits)
    zeros[1] = Counter(hits: 5)
    print(zeros[1].hits)
    print(zeros[0].hits)
}
"""
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            path = write_source(directory, "zeroed", source)
            c_binary, native_binary = build_both(path, directory)
            c_out = run([str(c_binary)])
            native_out = run([str(native_binary)])
        self.assertEqual((c_out.stdout, c_out.returncode),
                         (native_out.stdout, native_out.returncode))
        self.assertEqual(native_out.stdout.splitlines(), ["0", "0", "5", "0"])

    def test_an_element_written_into_the_array_is_copied_too(self):
        """`array[1] = one` copies, so changing `one` afterwards leaves the element."""
        source = """\
edition 2;

struct Point {
    x: i64,
    y: i64
}

fn main() {
    let points: [Point] = array_zeros(2)
    let one = Point(x: 1, y: 2)
    points[1] = one
    one.x = 42
    print(points[1].x)
    print(one.x)
    let view = points[0..2]
    print(len(view))
    print(view[1].y)
}
"""
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            path = write_source(directory, "store", source)
            c_binary, native_binary = build_both(path, directory)
            c_out = run([str(c_binary)])
            native_out = run([str(native_binary)])
        self.assertEqual(c_out.stdout, native_out.stdout)
        self.assertEqual(native_out.stdout.splitlines(), ["1", "42", "2", "2"])

    def test_an_element_is_one_word_in_the_lowering(self):
        """The array's instructions are the array's: only the element type differs."""
        module = lower_source("""\
edition 2;

struct Point {
    x: i64,
    y: i64
}

fn main() {
    let points: [Point] = [Point(x: 1, y: 2)]
    print(points[0].x)
    print(len(points))
}
""")
        instructions = [instruction for function in module.functions
                        for block in function.blocks for instruction in block.instructions]
        literals = [one for one in instructions if isinstance(one, ir.ArrayLiteral)]
        self.assertEqual([one.typ for one in literals], ["[Point]"])
        self.assertEqual([one.typ for one in instructions if isinstance(one, ir.Index)],
                         ["Point"])
        self.assertTrue(any(isinstance(one, ir.Length) for one in instructions))

class NativeTryTests(unittest.TestCase):
    """`?` forwards the failure variant, and leaves the function the honest way.

    The failure path is a `return` of the value itself, after the scopes it leaves have
    run their `defer` bodies and given back their arrays — so a function with a `?` and
    a `defer` does both, in the language's order.
    """

    def test_propagation_agrees_with_the_c_backend(self):
        source = """\
edition 2;

fn small(n: i64) -> Result(i64, str) {
    if n > 2 { return Result(i64, str)::Err("big") }
    return Result(i64, str)::Ok(n)
}

fn total(a: i64, b: i64) -> Result(i64, str) {
    let x = small(a)?
    let y = small(b)?
    return Result(i64, str)::Ok(x + y)
}

fn report(r: Result(i64, str)) -> i64 {
    match r {
        Result(i64, str)::Ok(v) => { return v }
        Result(i64, str)::Err(e) => { return 0 - 1 }
    }
}

fn main() {
    print(report(total(1, 2)))
    print(report(total(2, 2)))
    print(report(total(1, 0 - 1)))
}
"""
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            path = write_source(directory, "prop", source)
            c_binary, native_binary = build_both(path, directory)
            c_out = run([str(c_binary)])
            native_out = run([str(native_binary)])
        self.assertEqual((c_out.stdout, c_out.stderr, c_out.returncode),
                         (native_out.stdout, native_out.stderr, native_out.returncode))
        # `small(1) + small(2)` is 3, `small(2) + small(2)` is 4 and the third call
        # propagates a failure; the report of a failure is -1 and not the sum.
        self.assertEqual(native_out.stdout.splitlines(), ["3", "4", "0"])

    def test_the_failure_path_runs_the_defers_it_leaves(self):
        source = """\
edition 2;

fn inner(n: i64) -> Result(i64, str) {
    if n > 2 { return Result(i64, str)::Err("big") }
    return Result(i64, str)::Ok(n)
}

fn outer(n: i64) -> Result(i64, str) {
    defer { print(1000) }
    let a: [i64] = [1, 2]
    defer { print(len(a)) }
    let i = 0
    while i < n {
        let v = inner(i)?
        print(v)
        i = i + 1
    }
    return Result(i64, str)::Ok(42)
}

fn main() {
    match outer(5) {
        Result(i64, str)::Ok(v) => { print(v) }
        Result(i64, str)::Err(e) => { print(e) }
    }
    match outer(2) {
        Result(i64, str)::Ok(v) => { print(v) }
        Result(i64, str)::Err(e) => { print(e) }
    }
}
"""
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            path = write_source(directory, "defers", source)
            c_binary, native_binary = build_both(path, directory)
            c_out = run([str(c_binary)])
            native_out = run([str(native_binary)])
        self.assertEqual((c_out.stdout, c_out.stderr, c_out.returncode),
                         (native_out.stdout, native_out.stderr, native_out.returncode))
        # The failure leaves both defers behind it, in reverse order, and the error
        # travels out; the second call never fails and returns 42 after the same two.
        self.assertEqual(native_out.stdout.splitlines(),
                         ["0", "1", "2", "2", "1000", "big",
                          "0", "1", "2", "1000", "42"])

    def test_an_option_propagates_too(self):
        source = """\
edition 2;

fn found(n: i64) -> Option(i64) {
    if n > 0 { return Option(i64)::Some(n) }
    return Option(i64)::None
}

fn twice(n: i64) -> Option(i64) {
    let value = found(n)?
    return Option(i64)::Some(value * 2)
}

fn main() {
    match twice(5) {
        Option(i64)::Some(v) => { print(v) }
        Option(i64)::None => { print(0 - 1) }
    }
    match twice(0) {
        Option(i64)::Some(v) => { print(v) }
        Option(i64)::None => { print(0 - 1) }
    }
}
"""
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            path = write_source(directory, "option", source)
            c_binary, native_binary = build_both(path, directory)
            c_out = run([str(c_binary)])
            native_out = run([str(native_binary)])
        self.assertEqual(c_out.stdout, native_out.stdout)
        self.assertEqual(native_out.stdout.splitlines(), ["10", "-1"])

    def test_the_ir_states_the_early_return_and_the_payload_read(self):
        with tempfile.TemporaryDirectory() as temp:
            source = """\
edition 2;

fn small(n: i64) -> Result(i64, str) {
    if n > 2 { return Result(i64, str)::Err("big") }
    return Result(i64, str)::Ok(n)
}

fn total(n: i64) -> Result(i64, str) {
    let x = small(n)?
    return Result(i64, str)::Ok(x)
}

fn main() {
    match total(5) {
        Result(i64, str)::Ok(v) => { print(v) }
        Result(i64, str)::Err(e) => { print(e) }
    }
}
"""
            path = write_source(pathlib.Path(temp), "ir", source)
            result = run([*NAVA, "emit-ir", str(path)])
        self.assertEqual(result.returncode, 0, result.stderr)
        text = result.stdout
        self.assertIn("= tag %", text)
        self.assertIn("= payload %", text)
        # The failure variant is the last one the checker numbered, so the tag it is
        # compared with is a constant in the IR.
        self.assertIn("= const i64 1", text)
        self.assertIn("ret %", text)


def table_program(seed: int, rounds: int = 120) -> str:
    """A deterministic sequence of map and set operations, with what they answer.

    Two backends agreeing on a fixture is a weak claim when the thing under test is
    a hash table: the interesting cases are the ones a fixture does not reach — a
    table that has grown four times, a removal that left a tombstone in the probe
    chain of a key that collided with it, a rehash that has to place a key again.
    So the corpus is generated, the generator is seeded, and every answer the
    program prints is one the other backend has to produce too.
    """
    import random as _random

    generator = _random.Random(seed)
    keys = [generator.randrange(97) for _ in range(rounds)]
    doomed = [generator.randrange(97) for _ in range(rounds // 3)]
    lines = ["edition 2;", "", "fn main() -> i64 {"]
    lines.append("    let m: HashMap(i64, i64) = map_new()")
    lines.append("    let s: HashSet(i64) = set_new()")
    lines.append("    let i = 0")
    lines.append("    let keys: [i64] = [" + ", ".join(str(key) for key in keys) + "]")
    lines.append(f"    while i < {rounds} {{")
    lines.append("        let key = keys[i]")
    lines.append("        map_set(m, key, i)")
    lines.append("        set_add(s, key)")
    lines.append("        i = i + 1")
    lines.append("    }")
    lines.append("    print(map_len(m))")
    lines.append("    print(set_len(s))")
    lines.append(f"    let doomed: [i64] = ["
                 + ", ".join(str(key) for key in doomed) + "]")
    lines.append("    let removed = 0")
    lines.append("    i = 0")
    lines.append(f"    while i < {len(doomed)} {{")
    lines.append("        let key = doomed[i]")
    lines.append("        if map_remove(m, key) { removed = removed + 1 }")
    lines.append("        set_remove(s, key)")
    lines.append("        i = i + 1")
    lines.append("    }")
    lines.append("    print(map_len(m))")
    lines.append("    print(set_len(s))")
    lines.append("    print(removed)")
    lines.append("    i = 0")
    lines.append("    while i < 97 {")
    lines.append("        if map_contains(m, i) { print(i) }")
    lines.append("        i = i + 1")
    lines.append("    }")
    lines.append("    let from_map = 0")
    lines.append("    for key in m { from_map = from_map + key }")
    lines.append("    print(from_map)")
    lines.append("    let from_set = 0")
    lines.append("    for member in s { from_set = from_set + member }")
    lines.append("    print(from_set)")
    lines.append("    i = 0")
    lines.append("    while i < 97 {")
    lines.append("        match map_get(m, i) {")
    lines.append("            Option(i64)::Some(value) => { print(value) }")
    lines.append("            Option(i64)::None => { print(-1) }")
    lines.append("        }")
    lines.append("        i = i + 1")
    lines.append("    }")
    lines.append("    map_clear(m)")
    lines.append("    set_clear(s)")
    lines.append("    print(map_len(m))")
    lines.append("    print(set_len(s))")
    lines.append("    let after_clear = 0")
    lines.append("    for key in m { after_clear = after_clear + 1 }")
    lines.append("    for member in s { after_clear = after_clear + 1 }")
    lines.append("    print(after_clear)")
    lines.append("    return 0")
    lines.append("}")
    return "\n".join(lines) + "\n"


class NativeTableTests(unittest.TestCase):
    """`HashMap(K, V)` and `HashSet(T)` — one table, walked by position.

    A table is the shape where the two backends could most easily agree on every
    fixture and disagree on the thousandth key: the probe is a hash, the growth is
    a rehash, and a removal leaves a tombstone whose whole purpose is to keep the
    chain of a key that collided with it. So the tests here are not fixtures — they
    are generated sequences, seeded, long enough to grow the table five times and
    to leave tombstones behind before the next insert walks into them.

    The order a `for` visits is the table's own, not sorted: it is the order the
    slots are laid out in, which is why it is a *differential* rather than an
    expected value. Both backends print it and the two texts have to be equal.
    """

    def agrees(self, source: str, name: str = "tables") -> list[str]:
        """What the program prints, asserted identical on both backends."""
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            path = write_source(directory, name, source)
            c_binary, native_binary = build_both(path, directory)
            c_out = run_binary(c_binary)
            native_out = run_binary(native_binary)
        self.assertEqual(native_out, c_out)
        self.assertEqual(native_out[2], 0)
        return native_out[0].splitlines()

    def test_the_two_fixtures_agree_with_the_c_backend(self):
        """`map_basics` and `set_basics` leave the AST walk: both compile them."""
        for name in ("map_basics", "set_basics"):
            with tempfile.TemporaryDirectory() as temp:
                directory = pathlib.Path(temp)
                path = write_source(directory, name, fixture_text(name))
                c_binary, native_binary = build_both(path, directory)
                self.assertEqual(run_binary(native_binary), run_binary(c_binary))

    def test_a_generated_sequence_agrees_on_every_answer(self):
        """120 inserts, 40 removals, a walk, a lookup per key, and a clear."""
        for seed in (1, 7, 2024):
            lines = self.agrees(table_program(seed), f"tables{seed}")
            # The map and the set hold the same keys, and a clear empties both:
            # three answers the differential alone would not be able to fail on.
            self.assertEqual(lines[0], lines[1])
            self.assertEqual(lines[-3:], ["0", "0", "0"])
            self.assertTrue(len(lines) > 70)

    def test_the_iteration_order_is_the_table_s_own_and_the_two_agree(self):
        """The order is not sorted and not insertion order: it is the slots'.

        That is the claim a fixture cannot check, because a fixture is short enough
        to fit in one table. Here the table grows four times and every removal
        leaves a tombstone behind, so a backend that rehashed differently, or that
        buried a key whose chain crossed a tombstone, prints a different order.
        """
        source = """edition 2;
fn main() -> i64 {
    let s: HashSet(i64) = set_new()
    let i = 0
    while i < 300 { set_add(s, i * 13); i = i + 1 }
    i = 0
    while i < 300 {
        if i % 4 == 0 { set_remove(s, i * 13) }
        i = i + 1
    }
    i = 0
    while i < 300 {
        if i % 4 == 2 { set_add(s, i * 13 + 1) }
        i = i + 1
    }
    let text = ""
    for member in s { text = str_concat(text, str_concat(str_from_i64(member), ",")) }
    print(bytes_len(text))
    print(text)
    print(set_contains(s, 0))
    print(set_contains(s, 13))
    print(set_contains(s, 2 * 13 + 1))
    let total = 0
    for member in s { total = total + member }
    print(total)
    print(set_len(s))
    return 0
}
"""
        lines = self.agrees(source)
        # 0 was removed by the first pass; 13 was not; 27 was added by the second.
        self.assertEqual(lines[2], "false")
        self.assertEqual(lines[3], "true")
        self.assertEqual(lines[4], "true")
        self.assertEqual(lines[6], "300")

    def test_a_str_key_compares_by_value_and_not_by_its_address(self):
        """Two strings with the same bytes are one key, however they were built."""
        source = """edition 2;
fn main() -> i64 {
    let m: HashMap(str, i64) = map_new()
    let s: HashSet(str) = set_new()
    let i = 0
    while i < 40 {
        let word = str_from_i64(i)
        map_set(m, word, i)
        set_add(s, word)
        i = i + 1
    }
    print(map_len(m))
    print(set_len(s))
    print(map_contains(m, str_from_i64(7)))
    print(map_contains(m, str_concat(str_from_i64(1), str_from_i64(2))))
    print(set_contains(s, str_from_i64(39)))
    print(set_contains(s, "nope"))
    set_remove(s, str_from_i64(3))
    print(set_len(s))
    print(set_contains(s, str_from_i64(3)))
    let text = ""
    for word in s { text = str_concat(text, word) }
    print(bytes_len(text))
    print(text)
    return 0
}
"""
        lines = self.agrees(source)
        self.assertEqual(lines[0], "40")
        self.assertEqual(lines[1], "40")
        self.assertEqual(lines[2], "true")
        self.assertEqual(lines[3], "true")
        self.assertEqual(lines[6], "39")
        self.assertEqual(lines[7], "false")

    def test_a_removed_key_does_not_bury_one_that_collided_with_it(self):
        """The tombstone: take the key out, and its neighbour is still found.

        A table of capacity 8 holds keys 0..5; `find` for any of them walks past
        the ones that are gone. Removing one and asking for the rest is the whole
        reason a removal marks a slot instead of emptying it.
        """
        source = """edition 2;
fn main() -> i64 {
    let m: HashMap(i64, i64) = map_new()
    let i = 0
    while i < 6 { map_set(m, i, i * 10); i = i + 1 }
    print(map_remove(m, 2))
    print(map_remove(m, 2))
    print(map_len(m))
    i = 0
    while i < 6 {
        if map_contains(m, i) { print(i) }
        i = i + 1
    }
    map_set(m, 2, 999)
    print(map_len(m))
    print(map_contains(m, 2))
    match map_get(m, 2) {
        Option(i64)::Some(value) => { print(value) }
        Option(i64)::None => { print(-1) }
    }
    return 0
}
"""
        self.assertEqual(self.agrees(source),
                         ["true", "false", "5", "0", "1", "3", "4", "5", "6",
                          "true", "999"])

    def test_a_for_over_a_table_is_slots_live_and_key(self):
        """Three instructions, one each: the walk is the IR's, not a lowering trick."""
        module = lower_source("""edition 2;
fn main() -> i64 {
    let m: HashMap(i64, i64) = map_new()
    map_set(m, 1, 1)
    let total = 0
    for key in m { total = total + key }
    print(total)
    return 0
}
""")
        instructions = [instruction for block in module.functions[0].blocks
                        for instruction in block.instructions]
        self.assertEqual(len([item for item in instructions
                              if isinstance(item, ir.Slots)]), 1)
        self.assertEqual(len([item for item in instructions
                              if isinstance(item, ir.Live)]), 1)
        reads = [item for item in instructions if isinstance(item, ir.KeyAt)]
        self.assertEqual(len(reads), 1)
        # A map iterates its keys, so the walk binds the key type and not the value.
        self.assertEqual(reads[0].typ, "i64")
        self.assertEqual(module.tables, ["HashMap(i64, i64)"])

    def test_the_table_states_the_types_it_holds(self):
        """A module states its tables as it states its structs: both backends read it."""
        module = lower_source(fixture_text("map_basics"))
        self.assertEqual(module.tables, ["HashMap(str, i64)"])
        self.assertIn("Option(i64)", module.enums)
        # `Some` is 0 and `None` is 1 because the checker numbered them, and a
        # `map_get` that answers the other way round would match the wrong arm.
        self.assertEqual([name for name, _ in module.enums["Option(i64)"]],
                         ["Some", "None"])


class NativeVectorTests(unittest.TestCase):
    """`Vec(T)` — the one container whose size is not known when it is declared.

    A vector is the same value on both backends — a handle to a buffer with a
    length and a capacity — and it is the only one that *grows*: `push` doubles the
    capacity and copies the elements across. That is one loop in each backend and
    the two have to agree on the elements afterwards, on the capacity a run of
    pushes reaches, and on the message an index out of range stops with.

    The oracle is the other backend, and the sequences are longer than a fixture
    because a growth that loses an element loses it on the fourth doubling.
    """

    def agrees(self, source: str, name: str = "vectors") -> list[str]:
        """What the program prints, asserted identical on both backends."""
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            path = write_source(directory, name, source)
            c_binary, native_binary = build_both(path, directory)
            c_out = run_binary(c_binary)
            native_out = run_binary(native_binary)
        self.assertEqual(native_out, c_out)
        self.assertEqual(native_out[2], 0)
        return native_out[0].splitlines()

    def test_a_vector_agrees_with_the_c_backend(self):
        """Push, `len`, `capacity`, `get`, `set`, `pop` and a `for` over one."""
        source = """edition 2;
fn main() -> i64 {
    let v: Vec(i64) = vec_new()
    let i = 0
    while i < 40 {
        vec_push(v, i * i)
        i = i + 1
    }
    print(vec_len(v))
    print(vec_capacity(v))
    print(vec_get(v, 39))
    vec_set(v, 0, -7)
    print(vec_get(v, 0))
    match vec_pop(v) {
        Option(i64)::Some(last) => { print(last) }
        Option(i64)::None => { print(-1) }
    }
    print(vec_len(v))
    let total = 0
    for x in v { total = total + x }
    print(total)
    return 0
}
"""
        self.assertEqual(
            self.agrees(source),
            ["40", "64", "1521", "-7", "1521", "39", "19012"])

    def test_growth_keeps_every_element(self):
        """Five doublings later, every element is still where it was pushed."""
        count = 100
        lines = ["edition 2;", "", "fn main() -> i64 {", "    let v: Vec(i64) = vec_new()"]
        lines.append("    let i = 0")
        lines.append(f"    while i < {count} {{")
        lines.append("        vec_push(v, i * 3 - 1)")
        lines.append("        i = i + 1")
        lines.append("    }")
        lines.append("    print(vec_len(v))")
        lines.append("    print(vec_capacity(v))")
        lines.append("    let mismatched = 0")
        lines.append("    let at = 0")
        lines.append(f"    while at < {count} {{")
        lines.append("        if vec_get(v, at) != at * 3 - 1 { mismatched = mismatched + 1 }")
        lines.append("        at = at + 1")
        lines.append("    }")
        lines.append("    print(mismatched)")
        lines.append("    return 0")
        lines.append("}")
        self.assertEqual(self.agrees("\n".join(lines) + "\n"),
                         [str(count), "128", "0"])

    def test_the_bounds_trap_is_the_c_runtime_s(self):
        """`vec_get(v, 5)` on a vector of one: the C message, word for word."""
        source = """edition 2;
fn main() -> i64 {
    let v: Vec(i64) = vec_new()
    vec_push(v, 1)
    print(vec_get(v, 5))
    return 0
}
"""
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            path = write_source(directory, "bounds", source)
            c_binary, native_binary = build_both(path, directory)
            c_out = run_binary(c_binary)
            native_out = run_binary(native_binary)
        self.assertEqual(native_out[0], "")
        self.assertEqual(native_out[1],
                         "nava runtime error: array index 5 is out of bounds for length 1\n")
        self.assertEqual(c_out[1], native_out[1])
        self.assertEqual(c_out[2], 1)
        self.assertEqual(native_out[2], 1)

    def test_pop_on_an_empty_vector_answers_none(self):
        """The `Option` a `pop` answers is the checker's, tags and all."""
        source = """edition 2;
fn main() -> i64 {
    let v: Vec(i64) = vec_new()
    match vec_pop(v) {
        Option(i64)::Some(value) => { print(value) }
        Option(i64)::None => { print(-1) }
    }
    vec_push(v, 8)
    match vec_pop(v) {
        Option(i64)::Some(value) => { print(value) }
        Option(i64)::None => { print(-1) }
    }
    match vec_pop(v) {
        Option(i64)::Some(value) => { print(value) }
        Option(i64)::None => { print(-1) }
    }
    return 0
}
"""
        self.assertEqual(self.agrees(source), ["-1", "8", "-1"])

    def test_a_vector_of_strings_agrees(self):
        """An element that is a pointer: pushed, read back and walked."""
        source = """edition 2;
fn main() -> i64 {
    let v: Vec(str) = vec_new()
    vec_push(v, "one")
    vec_push(v, "two")
    vec_push(v, "three")
    print(vec_len(v))
    print(vec_get(v, 2))
    let joined = ""
    for item in v { joined = str_concat(joined, item) }
    print(joined)
    vec_set(v, 1, "TWO")
    print(vec_get(v, 1))
    return 0
}
"""
        self.assertEqual(self.agrees(source),
                         ["3", "three", "onetwothree", "TWO"])

    def test_a_for_over_a_vector_is_the_same_walk_as_over_an_array(self):
        """A `for` over either is one `len` and one `index`; the handle is the type.

        `v[0]` is not the language's syntax for a vector (`E0329`) and `len(v)` is
        not its length (`E0338`) — a vector answers `vec_get` and `vec_len`. What a
        `for` lowers to is therefore the one place the IR has to know that a vector
        keeps its length in the second word of its handle and not the first.
        """
        module = lower_source("""edition 2;
fn main() -> i64 {
    let a: [i64] = [1, 2, 3]
    let v: Vec(i64) = vec_new()
    vec_push(v, 4)
    let from_array = 0
    for x in a { from_array = from_array + x }
    let from_vector = 0
    for y in v { from_vector = from_vector + y }
    print(from_array + from_vector)
    return 0
}
""")
        instructions = [instruction for block in module.functions[0].blocks
                        for instruction in block.instructions]
        lengths = [item for item in instructions if isinstance(item, ir.Length)]
        reads = [item for item in instructions if isinstance(item, ir.Index)]
        self.assertEqual(len(lengths), 2)
        self.assertEqual(len(reads), 2)
        self.assertEqual(reads[0].typ, "i64")
        self.assertEqual(module.vectors, ["Vec(i64)"])


class NativeF64Tests(unittest.TestCase):
    """`f64` is printed as the shortest decimal that reads back as the same double.

    The C runtime says it with `%.*e`/`%.*f` at the first precision whose text `strtod`
    parses back to the value; the native backend cannot call this, and a seventeen-digit
    approximation would be a different output for the same program. So the digits are
    generated on exact integers — Burger and Dybvig's framing, where the value and the
    two half-gaps are integers once scaled and a digit is one subtraction loop — and the
    two rules that make it agree with `strtod` digit for digit are tested here where they
    are visible:

    * the loop stops at the first count whose **nearest** candidate lies inside the gap,
      which is why `0.03` is `0.03` and not the fifty-digit exact expansion of the double.
      It is not Dragon4's "shortest that reads back": `2**-1017` has no fifteen-digit text
      that reads back at all and its nearest sixteen-digit text is an exact midpoint, so
      the answer there is seventeen digits — the same answer `%.*e` gives;
    * a candidate that sits exactly halfway to a neighbour is inside only when the value
      is the **even** of the two, which is why one class of large integers keeps sixteen
      digits instead of seventeen.

    The oracle is the other backend, over every power of two and over a table of values
    where an off-by-one digit or an off-by-one exponent is what a mistake would look like.
    """

    #: Values whose shortest form is shorter than their exact decimal expansion, plus the
    #: five names the language gives to the values that are not finite, and the ends of
    #: the range. The expected text is what the C backend prints.
    TABLE = (
        ("0.1 + 0.2", ["0.30000000000000004"]),
        ("0.03", ["0.03"]),
        ("0.375", ["0.375"]),
        ("0.1", ["0.1"]),
        ("1.5", ["1.5"]),
        ("100.0", ["100.0"]),
        ("12.5", ["12.5"]),
        ("0.0001", ["0.0001"]),
        ("0.00001", ["1e-05"]),
        ("1.0e300", ["1e+300"]),
        ("1.0e-7", ["1e-07"]),
        ("5.0e-324", ["5e-324"]),
        ("2.2250738585072014e-308", ["2.2250738585072014e-308"]),
        ("1.7976931348623157e308", ["1.7976931348623157e+308"]),
        ("3.3201876582270392e+16", ["3.320187658227039e+16"]),
        ("9007199254740992.0", ["9007199254740992.0"]),
    )

    def prints(self, expressions: list[str]) -> list[str]:
        """Build one program with the native backend and return what it prints."""
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            source = "fn main() {\n" + "".join(
                f"    print({expression})\n" for expression in expressions) + "}\n"
            path = write_source(directory, "floats", source)
            binary = directory / "floats-native"
            result = run([*NAVA, "build", str(path), "-o", str(binary),
                          "--backend", "native"])
            if result.returncode != 0:
                raise AssertionError(f"the native backend refused: {result.stderr}")
            out = run_binary(binary)
        self.assertEqual(out[2], 0)
        return out[0].splitlines()

    def test_the_floats_fixture_agrees_with_the_c_backend(self):
        """The fixture covers arithmetic and printing; both backends say the same thing."""
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            path = write_source(directory, "floats", fixture_text("floats"))
            c_binary, native_binary = build_both(path, directory)
            c_out = run_binary(c_binary)
            native_out = run_binary(native_binary)
        self.assertEqual(c_out, native_out)
        self.assertEqual(native_out[0].splitlines(),
                         ["1.75", "1.25", "0.375", "6.0", "-1.5", "1.5",
                          "0.30000000000000004", "1.0", "-0.0", "1e+300", "1e-07",
                          "inf", "-inf", "nan", "false", "true", "false", "false",
                          "true", "true", "7.0", "2", "-2", "3.0"])

    def test_the_shortest_form_is_not_the_exact_expansion(self):
        """Seventeen digits are enough to read back; the digits beyond them are dropped."""
        expressions = [expression for expression, _ in self.TABLE]
        expected = [line for _, lines in self.TABLE for line in lines]
        self.assertEqual(self.prints(expressions), expected)

    def test_a_candidate_exactly_on_the_gap_reads_back(self):
        """Sixteen digits stand where the exact value is one half-gap away from them.

        These are the values that a loop stopping only strictly inside the gap prints with
        one digit too many, and the parity of the significand is what decides them: the
        midpoint belongs to an even value, and every one of these is even.
        """
        edges = ["3.3201876582270392e+16", "3.0925352787701443e+18",
                 "6.4295608915343344e+16", "2.1097935911224992e+16",
                 "7.1306516825968576e+16", "3.3720108055995768e+16"]
        self.assertEqual(self.prints(edges),
                         ["3.320187658227039e+16", "3.092535278770144e+18",
                          "6.429560891534334e+16", "2.109793591122499e+16",
                          "7.130651682596858e+16", "3.372010805599577e+16"])

    def test_every_power_of_two_and_its_neighbour_read_back(self):
        """A power of two has a lower gap of half a binade: 2098 values, both backends."""
        expressions = []
        for exponent in range(-1074, 1024):
            value = 2.0 ** exponent
            if not 0.0 < value < float("inf"):
                continue
            expressions.append(repr(value))
            if value * 2.0 < float("inf"):
                expressions.append(repr(value + value / 2.0))
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            source = "fn main() {\n" + "".join(
                f"    print({expression})\n" for expression in expressions) + "}\n"
            path = write_source(directory, "powers", source)
            c_binary, native_binary = build_both(path, directory)
            c_out = run_binary(c_binary)
            native_out = run_binary(native_binary)
        self.assertEqual((c_out[0], c_out[1], c_out[2]),
                         (native_out[0], native_out[1], native_out[2]))
        self.assertEqual(len(native_out[0].splitlines()), len(expressions))

    def test_the_exponent_is_padded_to_two_digits_and_signed(self):
        """`%e` writes `e+300`, `e-07` and never `e-7`: the text is compared, not parsed."""
        self.assertEqual(self.prints(["1.0e-7", "1.0e-8", "1.0e300", "9.9e-7", "1.0e-5"]),
                         ["1e-07", "1e-08", "1e+300", "9.9e-07", "1e-05"])

    def test_negative_zero_and_the_three_names_are_not_a_conversion(self):
        """`-0.0` keeps its sign, and `inf`, `-inf`, `nan` are spelled, not computed."""
        self.assertEqual(self.prints(["0.0", "-0.0", "1.0 / 0.0", "-1.0 / 0.0",
                                      "0.0 / 0.0"]),
                         ["0.0", "-0.0", "inf", "-inf", "nan"])

    def test_the_conversion_costs_no_c_library_call(self):
        """The digits are computed in the runtime unit: `strtod` is not linked in at all."""
        names = {symbol.name for unit in units_of("fn main() { print(1.5) }\n")
                 for symbol in unit.symbols}
        self.assertIn("nv_format_f64", names)
        self.assertIn("nv_bn_mul_small", names)
        self.assertNotIn("strtod", names)
        self.assertNotIn("snprintf", names)

    def test_the_corpus_tool_agrees_on_a_sample(self):
        """`tools/f64_corpus.py` is the differential's input, so the suite runs a slice.

        The full corpus is twelve thousand values and two compiler invocations of forty
        seconds each; a subset is enough here because the tool is deterministic and the
        fixtures above pin what the subset is drawn from. Running it at all is the point:
        a corpus generator that stopped emitting the edges would otherwise be found by
        nobody.
        """
        result = run([sys.executable, str(ROOT / "tools" / "f64_corpus.py"),
                      "--no-powers", "--random", "40", "--subnormal", "6", "--seed", "3"])
        self.assertEqual(result.returncode, 0, result.stderr)
        tools = ROOT / "tools"
        if str(tools) not in sys.path:
            sys.path.insert(0, str(tools))
        import f64_corpus                                    # noqa: PLC0415
        lines = result.stdout.count("print(")
        # three names and thirty-five curated values, then forty 64-bit patterns and six
        # 52-bit ones (each of which is printed negative too)
        self.assertEqual(lines, 3 + len(f64_corpus.curated()) + 40 + 2 * 6)
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            path = write_source(directory, "corpus", result.stdout)
            c_binary, native_binary = build_both(path, directory)
            c_out = run_binary(c_binary)
            native_out = run_binary(native_binary)
        self.assertEqual(len(native_out[0].splitlines()), lines)
        self.assertEqual((c_out[0], c_out[1], c_out[2]),
                         (native_out[0], native_out[1], native_out[2]))


class NativeFunctionValueTests(unittest.TestCase):
    """A function is a value: one word, the address of its code, and no closure.

    The claim has two halves and both are tested. The first is that the two backends
    agree about a program that passes a function around — as an argument, in a local,
    as a returned value, across a module. The second is structural: `funcref` and
    `call` name the *same* link-time symbol, so a function reached through a value and
    the same function called by name are one address, and the linker sees one
    definition of it.

    Negative controls: a generic function has no single address and is refused before
    either backend sees it; a function the program only ever names as a value is still
    compiled, which is the bug a `funcref` would otherwise be silent about.
    """

    EDITION = "edition 2;\n"

    def agrees(self, source: str, name: str = "fnvalue") -> list[str]:
        """What the program prints, asserted identical on both backends."""
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            path = write_source(directory, name, source)
            c_binary, native_binary = build_both(path, directory)
            c_out = run_binary(c_binary)
            native_out = run_binary(native_binary)
        self.assertEqual(native_out, c_out)
        self.assertEqual(native_out[2], 0)
        return native_out[0].splitlines()

    def test_a_function_passed_as_an_argument_runs(self):
        """`apply(twice, 21)`: the callee is a parameter, not a name."""
        self.assertEqual(self.agrees(self.EDITION + """fn twice(x: i64) -> i64 {
    return x * 2
}

fn apply(f: fn(i64) -> i64, x: i64) -> i64 {
    return f(x)
}

fn main() -> i64 {
    print(apply(twice, 21))
    return 0
}
"""), ["42"])

    def test_a_function_held_in_a_local_runs(self):
        """`let g: fn(i64) -> i64 = twice` — the value outlives the statement."""
        self.assertEqual(self.agrees(self.EDITION + """fn twice(x: i64) -> i64 {
    return x * 2
}

fn main() -> i64 {
    let g: fn(i64) -> i64 = twice
    print(g(5))
    return 0
}
"""), ["10"])

    def test_a_function_returned_from_a_function_runs(self):
        """`pick` answers a value; both of its answers are called."""
        self.assertEqual(self.agrees(self.EDITION + """fn twice(x: i64) -> i64 {
    return x * 2
}

fn triple(x: i64) -> i64 {
    return x * 3
}

fn pick(flag: bool) -> fn(i64) -> i64 {
    if flag { return twice }
    return triple
}

fn main() -> i64 {
    let f = pick(true)
    let g = pick(false)
    print(f(10))
    print(g(10))
    return 0
}
"""), ["20", "30"])

    def test_a_function_value_survives_a_loop_and_a_branch(self):
        """A mutable local of function type crosses two joins per iteration.

        The value is a block parameter like any other, which is the whole of the
        claim: SSA with joins has nothing special to say about a function.
        """
        self.assertEqual(self.agrees(self.EDITION + """fn twice(x: i64) -> i64 {
    return x * 2
}

fn triple(x: i64) -> i64 {
    return x * 3
}

fn main() -> i64 {
    let f: fn(i64) -> i64 = twice
    let i = 0
    let total = 0
    while i < 5 {
        if i % 2 == 0 { f = triple } else { f = twice }
        total = total + f(i)
        i = i + 1
    }
    print(total)
    return 0
}
"""), ["26"])

    def test_a_function_of_six_arguments_called_through_a_value(self):
        """Every argument register is full before the address is loaded.

        The callee goes into a register the arguments do not use; if it went into a
        seventh argument register by mistake, this is where it would show.
        """
        self.assertEqual(self.agrees(self.EDITION + """fn six(a: i64, b: i64, c: i64, d: i64, e: i64, g: i64) -> i64 {
    return a + b + c + d + e + g
}

fn apply6(f: fn(i64, i64, i64, i64, i64, i64) -> i64, n: i64) -> i64 {
    return f(n, n, n, n, n, n)
}

fn main() -> i64 {
    print(apply6(six, 7))
    return 0
}
"""), ["42"])

    def test_a_function_of_no_arguments_called_through_a_value(self):
        """An empty argument list is a call like any other."""
        self.assertEqual(self.agrees(self.EDITION + """fn answer() -> i64 {
    return 5
}

fn call0(f: fn() -> i64) -> i64 {
    return f()
}

fn main() -> i64 {
    print(call0(answer))
    return 0
}
"""), ["5"])

    def test_indirect_calls_nest(self):
        """A value called on the answer of a call through another value.

        The callee register is live while the argument is being computed, so a
        backend that reused it for the inner call would call the wrong function.
        """
        self.assertEqual(self.agrees(self.EDITION + """fn twice(x: i64) -> i64 {
    return x * 2
}

fn triple(x: i64) -> i64 {
    return x * 3
}

fn apply(f: fn(i64) -> i64, x: i64) -> i64 {
    return f(x)
}

fn main() -> i64 {
    let f: fn(i64) -> i64 = triple
    print(f(apply(twice, 4)))
    print(apply(twice, apply(triple, apply(twice, 1))))
    return 0
}
"""), ["24", "12"])

    def test_a_function_only_ever_named_as_a_value_is_reachable(self):
        """A `funcref` is a use of a function, or the linker finds no symbol.

        This is the whole of the reachability claim: the call that will run `body` is
        a call through a register and names nobody, so a walk that collected callees
        from `call` alone would leave `body` out of the program entirely.
        """
        module = lower_source(self.EDITION + """fn body() -> i64 {
    return 7
}

fn run(f: fn() -> i64) -> i64 {
    return f()
}

fn main() -> i64 {
    return run(body)
}
""")
        self.assertEqual([ir.symbol(function) for function in module.functions],
                         ["nava.entry.body", "nava.entry.run", "nava.entry.main"],
                         "a function the program never calls by name is compiled "
                         "all the same")

    def test_a_function_value_crosses_a_module(self):
        """`harness::run("checked", checked)`: a value from another file's code.

        The two functions are in two units, so the address is a relocation in one and
        a definition in the other — the linker's job, and the shape the standard
        library's own test runner has.
        """
        sources = {"harness.nv": self.EDITION + """pub fn run(name: str, body: fn() -> i64) -> i64 {
    let failures = body()
    if failures == 0 {
        print("ok")
        return 0
    }
    print("failed")
    return failures
}
"""}
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            for name, text in sources.items():
                (directory / name).write_text(text, encoding="utf-8")
            path = write_source(directory, "main", self.EDITION + """import harness;

fn body() -> i64 {
    print("ran")
    return 0
}

fn main() -> i64 {
    return harness::run("body", body)
}
""")
            c_binary, native_binary = build_both(path, directory)
            c_out = run_binary(c_binary)
            native_out = run_binary(native_binary)
        self.assertEqual(native_out, ("ran\nok\n", "", 0))
        self.assertEqual(native_out, c_out,
                         "the two backends disagree about a function value from "
                         "another module")

    def test_the_ir_names_the_function_a_value_points_at(self):
        """`funcref` says which function, in the text a reader and a diff both see."""
        text = ir.dump(lower_source(self.EDITION + """fn twice(x: i64) -> i64 {
    return x * 2
}

fn main() -> i64 {
    let g: fn(i64) -> i64 = twice
    return g(5)
}
"""))
        self.assertIn("%0 = funcref @nava.entry.twice : fn(i64) -> i64", text)
        self.assertIn("call %0(", text, "the call names no function, only a value")

    def test_a_value_and_a_direct_call_name_one_symbol(self):
        """The address in the register is the address in the `call`.

        A `funcref` and a direct call are two ways of writing one name, and the
        linker resolves both to the same definition. If they were two namespaces — a
        counter for labels, a symbol for calls — this is the test that would fail.
        """
        module = lower_source(self.EDITION + """fn twice(x: i64) -> i64 {
    return x * 2
}

fn main() -> i64 {
    let g: fn(i64) -> i64 = twice
    print(g(5))
    print(twice(6))
    return 0
}
""")
        units = x86_64.units(module)
        entry = units[0]
        named = [relocation.symbol for relocation in entry.relocations
                 if relocation.symbol == "nava.entry.twice"]
        self.assertEqual(len(named), 2,
                         "one name for the value and one for the direct call")
        defined = [symbol.name for unit in units for symbol in unit.symbols
                   if symbol.name == "nava.entry.twice"]
        self.assertEqual(len(defined), 1, "one definition of the function")

    def test_the_encoding_of_a_call_through_a_register(self):
        """`call *%r11` is FF /2; `lea rax, [rip+d]` is what fills it."""
        assembler = x86.Assembler()
        assembler.call_indirect(x86.R11)
        self.assertEqual(assembler.buffer.hex(" "), "41 ff d3")
        assembler = x86.Assembler()
        assembler.lea_rip(x86.RAX, "nava.entry.twice")
        self.assertEqual(assembler.buffer.hex(" "), "48 8d 05 00 00 00 00")

    def test_a_generic_function_has_no_value(self):
        """A template is instantiated by a call; it has no single address to take.

        A negative control, and the reason a function value can be one word: there is
        nothing to carry but an address, and a generic function has more than one.
        """
        source = self.EDITION + """fn id<T>(x: T) -> T {
    return x
}

fn main() -> i64 {
    let f: fn(i64) -> i64 = id
    return f(7)
}
"""
        with self.assertRaises(NavaError) as caught:
            checked(source)
        self.assertEqual(getattr(caught.exception, "code", None), "E0437")
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            path = write_source(directory, "generic", source)
            built = run([*NAVA, "build", str(path), "-o", str(directory / "native"),
                         "--backend", "native"])
        self.assertNotEqual(built.returncode, 0,
                            "a program the checker refused was compiled anyway")
        self.assertIn("E0437", built.stderr)


class NativeStringTests(unittest.TestCase):
    """`String` — text a piece at a time, and the copy that makes it a `str`.

    A `String` is the one owning value whose size is *text*: it grows a byte at a
    time behind a handle of three words, and its bytes are not a `str` until
    `string_text` copies them into the arena. That copy is the whole point — a
    builder's buffer moves when it grows, so a `str` must never point into it — and
    `test_the_text_is_a_copy_and_not_a_view` is the test that says so.

    The oracle is the other backend, and the sequences are longer than a fixture
    because a buffer that loses a byte loses it on the fourth doubling.
    """

    EDITION = "edition 2;\n"

    def agrees(self, source: str, name: str = "string") -> list[str]:
        """What the program prints, asserted identical on both backends."""
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            path = write_source(directory, name, source)
            c_binary, native_binary = build_both(path, directory)
            c_out = run_binary(c_binary)
            native_out = run_binary(native_binary)
        self.assertEqual(native_out, c_out)
        self.assertEqual(native_out[2], 0)
        return native_out[0].splitlines()

    def trap(self, source: str, name: str = "trapped") -> tuple[str, str, int]:
        """A byte the runtime refuses: the same stop, on both backends.

        An out-of-range byte is not a compile-time refusal but a trap, so what is
        compared is the run: empty stdout, the C runtime's line on standard error
        and status 1.
        """
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            path = write_source(directory, name, source)
            c_binary, native_binary = build_both(path, directory)
            c_out = run_binary(c_binary)
            native_out = run_binary(native_binary)
        self.assertEqual(native_out, c_out)
        self.assertEqual(native_out[0], "")
        self.assertEqual(native_out[2], 1)
        return native_out

    def test_a_builder_agrees_with_the_c_backend(self):
        """New, `push`, `len`, `capacity`, `text` and `clear`."""
        self.assertEqual(self.agrees(self.EDITION + """fn main() -> i64 {
    let s: String = string_new()
    string_push(s, "Hello, ")
    string_push(s, "world")
    print(string_text(s))
    print(string_len(s))
    print(string_capacity(s))
    string_clear(s)
    print(string_len(s))
    print(string_text(s))
    return 0
}
"""), ["Hello, world", "12", "32", "0", ""])

    def test_growth_keeps_every_byte(self):
        """Two hundred pushes: five doublings, and all four hundred bytes."""
        self.assertEqual(self.agrees(self.EDITION + """fn main() -> i64 {
    let s: String = string_new()
    let i = 0
    while i < 200 {
        string_push(s, "ab")
        i = i + 1
    }
    print(string_len(s))
    print(string_capacity(s))
    print(string_text(s))
    return 0
}
"""), ["400", "512", "ab" * 200])

    def test_the_text_is_a_copy_and_not_a_view(self):
        """`string_text` answers bytes the next push cannot move.

        A `str` taken from a builder and a builder pushed past its capacity: if the
        answer were a pointer into the buffer, the second print would show the new
        bytes through the old `str`.
        """
        self.assertEqual(self.agrees(self.EDITION + """fn main() -> i64 {
    let s: String = string_new()
    let i = 0
    while i < 200 {
        string_push(s, "ab")
        i = i + 1
    }
    let t: str = string_text(s)
    string_push(s, "ZZ")
    print(bytes_len(t))
    print(t == string_text(s))
    print(bytes_len(string_text(s)))
    return 0
}
"""), ["400", "false", "402"])

    def test_clear_keeps_the_capacity(self):
        """A builder that is emptied and filled again does not allocate twice."""
        self.assertEqual(self.agrees(self.EDITION + """fn main() -> i64 {
    let s: String = string_new()
    let i = 0
    while i < 200 {
        string_push(s, "ab")
        i = i + 1
    }
    string_clear(s)
    print(string_len(s))
    print(string_capacity(s))
    string_push(s, "again")
    print(string_text(s))
    print(string_capacity(s))
    return 0
}
"""), ["0", "512", "again", "512"])

    def test_an_empty_builder_answers_an_empty_text(self):
        """Nothing pushed: length 0, capacity 0, and a `str` of no bytes."""
        self.assertEqual(self.agrees(self.EDITION + """fn main() -> i64 {
    let s: String = string_new()
    print(string_len(s))
    print(string_capacity(s))
    print(bytes_len(string_text(s)))
    print(string_text(s))
    return 0
}
"""), ["0", "0", "0", ""])

    def test_a_byte_at_a_time(self):
        """`string_push_byte` takes the eight bits it is given and no more."""
        self.assertEqual(self.agrees(self.EDITION + """fn main() -> i64 {
    let s: String = string_new()
    let i = 0
    while i < 5 {
        string_push_byte(s, 65 + i)
        i = i + 1
    }
    print(string_text(s))
    print(string_len(s))
    string_push_byte(s, 255)
    print(string_len(s))
    return 0
}
"""), ["ABCDE", "5", "6"])

    def test_a_byte_outside_the_range_is_the_c_runtime_s_trap(self):
        """`a string byte must be in 0..255, got N` — the number, word for word."""
        for byte, shown in (("300", "300"), ("0 - 1", "-1"), ("256", "256"),
                            ("9223372036854775807", "9223372036854775807")):
            with self.subTest(byte=byte):
                _out, error, _status = self.trap(
                    self.EDITION + f"""fn main() -> i64 {{
    let s: String = string_new()
    string_push_byte(s, {byte})
    return 0
}}
""")
                self.assertEqual(
                    error,
                    f"nava runtime error: a string byte must be in 0..255, "
                    f"got {shown}\n")

    def test_a_nul_byte_is_refused_on_its_own(self):
        """A `String` is NUL-terminated, so a NUL inside it would lie about its length."""
        _out, error, _status = self.trap(self.EDITION + """fn main() -> i64 {
    let s: String = string_new()
    string_push_byte(s, 0)
    return 0
}
""")
        self.assertEqual(error,
                         "nava runtime error: a String cannot contain a NUL byte\n")

    def test_a_utf8_text_counts_bytes_and_not_characters(self):
        """`string_len` is the length of the buffer, which `strlen` is too."""
        self.assertEqual(self.agrees(self.EDITION + """fn main() -> i64 {
    let s: String = string_new()
    string_push(s, "Привет")
    print(string_len(s))
    print(bytes_len(string_text(s)))
    print(string_text(s))
    return 0
}
"""), ["12", "12", "Привет"])

    def test_a_builder_crosses_a_function_both_ways(self):
        """A `String` as a parameter, as a result, and reassigned in a loop.

        The reassignment is the interesting half: a mutable local of owning type
        lives in a cell, so this is a `String` in the module's slot types too.
        """
        self.assertEqual(self.agrees(self.EDITION + """fn build(prefix: str, n: i64) -> String {
    let s: String = string_new()
    string_push(s, prefix)
    let i = 0
    while i < n {
        string_push(s, ".")
        i = i + 1
    }
    return s
}

fn shown(s: String) -> i64 {
    print(string_text(s))
    return string_len(s)
}

fn main() -> i64 {
    let s: String = build("x", 3)
    print(shown(s))
    let i = 0
    while i < 4 {
        s = build("y", i)
        print(string_text(s))
        i = i + 1
    }
    print(string_len(s))
    return 0
}
"""), ["x...", "4", "y", "y.", "y..", "y...", "4"])

    def test_a_generated_sequence_agrees_on_every_answer(self):
        """Three hundred mixed operations, seeded, through both backends.

        Fixtures find a builder that loses a byte on the first doubling; a sequence
        this long grows past five of them, clears in the middle and interleaves
        byte-at-a-time pushes with string ones, which is where a capacity that was
        computed one byte short would show.
        """
        import random

        for seed in (11, 12, 13):
            with self.subTest(seed=seed):
                rng = random.Random(seed)
                lines = ["edition 2;", "", "fn main() -> i64 {",
                         "    let s: String = string_new()",
                         "    let held: Vec(str) = vec_new()",
                         "    let checks = 0"]
                expected: list[str] = []
                length, capacity = 0, 0

                def grown(needed: int) -> int:
                    """What `reserve` leaves the capacity at: doubling, from 32."""
                    if needed <= capacity:
                        return capacity
                    room = capacity if capacity > 0 else 32
                    while room < needed:
                        room *= 2
                    return room

                for step in range(300):
                    choice = rng.randrange(4)
                    if choice == 0:
                        text = rng.choice(["a", "bc", "def", "ghij", "k" * 40])
                        lines.append(f'    string_push(s, "{text}")')
                        length += len(text)
                        # `reserve` is asked for the text and the NUL it keeps.
                        capacity = grown(length + 1)
                    elif choice == 1:
                        byte = rng.randrange(32, 127)
                        lines.append(f"    string_push_byte(s, {byte})")
                        length += 1
                        capacity = grown(length + 1)
                    elif choice == 2:
                        lines.append("    string_clear(s)")
                        length = 0
                    else:
                        # A `str` taken now must survive everything that follows.
                        lines.append("    vec_push(held, string_text(s))")
                        lines.append("    print(string_len(s))")
                        lines.append("    print(string_capacity(s))")
                        expected.append(str(length))
                        expected.append(str(capacity))
                lines.append("    print(string_len(s))")
                expected.append(str(length))
                lines.append("    print(string_capacity(s))")
                expected.append(str(capacity))
                lines.append("    let i = 0")
                lines.append("    while i < vec_len(held) {")
                lines.append("        print(bytes_len(vec_get(held, i)))")
                lines.append("        i = i + 1")
                lines.append("    }")
                lines.append("    return 0")
                lines.append("}")
                source = "\n".join(lines) + "\n"
                # The lengths the snapshots had when they were taken, read back the
                # same way the program prints them.
                with tempfile.TemporaryDirectory() as temp:
                    directory = pathlib.Path(temp)
                    path = write_source(directory, f"sequence{seed}", source)
                    c_binary, native_binary = build_both(path, directory)
                    c_out = run_binary(c_binary)
                    native_out = run_binary(native_binary)
                self.assertEqual(native_out, c_out)
                self.assertEqual(native_out[2], 0)
                printed = native_out[0].splitlines()
                self.assertEqual(printed[:len(expected)], expected,
                                 "a length or a capacity disagrees with the model "
                                 "the sequence was generated from")

    def test_the_ir_states_the_builder_as_instructions(self):
        """A `String` is not a call: it is one instruction per operation."""
        text = ir.dump(lower_source(self.EDITION + """fn main() -> i64 {
    let s: String = string_new()
    string_push(s, "a")
    print(string_text(s))
    return 0
}
"""))
        self.assertIn("builtin String string_new()", text)
        self.assertIn("builtin void string_push(%0, %1)", text)
        self.assertIn("builtin str string_text(%0)", text)

    def test_the_family_is_emitted_for_what_the_program_uses(self):
        """`reserve` and the byte trap come only when a push needs them."""
        plain = self.EDITION + """fn main() -> i64 {
    let s: String = string_new()
    print(string_len(s))
    return 0
}
"""
        units = units_of(plain)
        runtime = next(unit for unit in units if unit.name == "runtime")
        names = {symbol.name for symbol in runtime.symbols}
        self.assertIn("nv_string_new", names)
        self.assertNotIn("nv_string_reserve", names,
                         "nothing in the program grows the buffer")
        self.assertNotIn("nv_string_push", names)
        self.assertNotIn("nv_fail_byte", names)
        with_bytes = self.EDITION + """fn main() -> i64 {
    let s: String = string_new()
    string_push_byte(s, 65)
    return 0
}
"""
        names = {symbol.name
                 for symbol in next(unit for unit in units_of(with_bytes)
                                    if unit.name == "runtime").symbols}
        self.assertIn("nv_string_reserve", names, "a push grows the buffer")
        self.assertIn("nv_fail_byte", names, "a byte may be out of range")
        self.assertNotIn("nv_string_push", names, "the program pushes no string")
        self.assertNotIn("nv_string_text", names)

    def compile_refusal(self, source: str, name: str = "refused") -> str:
        """A program this backend does not implement: the build fails, and says why."""
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            path = write_source(directory, name, source)
            built = run([*NAVA, "build", str(path), "-o", str(directory / "native"),
                         "--backend", "native"])
        self.assertNotEqual(built.returncode, 0,
                            "a program this backend does not implement was built")
        return built.stderr

    def test_a_string_in_a_struct_field_is_refused_by_name(self):
        """Owning storage in a record is a copy this backend does not make."""
        stderr = self.compile_refusal(
            self.EDITION + """struct Bag { text: String, n: i64 }

fn main() -> i64 {
    let s: String = string_new()
    string_push(s, "hi")
    let b: Bag = Bag(text: s, n: 1)
    print(string_len(b.text))
    return 0
}
""")
        self.assertIn("E0901", stderr)
        self.assertIn("String", stderr, "the refusal names the type it refused")

    def test_a_string_is_not_printable_because_it_is_not_a_str(self):
        """`print(s)` is a type error in the language, not a gap in a backend."""
        with self.assertRaises(NavaError) as caught:
            checked(self.EDITION + """fn main() -> i64 {
    let s: String = string_new()
    print(s)
    return 0
}
""")
        self.assertEqual(getattr(caught.exception, "code", None), "E0323")


class NativeSplitArgumentClockTests(unittest.TestCase):
    """`str_split`, the two argument functions and the three clock readings.

    What the three have in common is that each is a runtime the native backend
    had to write from nothing, and each has a C runtime whose words are already
    the language's: the separator trap, `argument index N is out of bounds for M
    argument(s)`, and a reading of the clock. A differential is only as good as
    the oracle, so every claim here is a comparison against the C backend
    rather than an expectation written down twice.

    The two that are *not* comparable by output are handled the only honest way
    a non-deterministic value can be: the clock is compared **in shape** — a
    millisecond reading is within a second of a second reading, and a monotonic
    reading does not go backwards — and the argument functions are compared by
    running both binaries with the same words after the program's own name.
    """

    EDITION = "edition 2;\n"

    def agrees(self, source: str, name: str = "split") -> list[str]:
        """What the program prints, asserted identical on both backends."""
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            path = write_source(directory, name, source)
            c_binary, native_binary = build_both(path, directory)
            c_out = run_binary(c_binary)
            native_out = run_binary(native_binary)
        self.assertEqual(native_out, c_out)
        self.assertEqual(native_out[2], 0)
        return native_out[0].splitlines()

    def trap(self, source: str, name: str = "trapped") -> tuple[str, str, int]:
        """A run-time refusal: the same stop, on both backends."""
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            path = write_source(directory, name, source)
            c_binary, native_binary = build_both(path, directory)
            c_out = run_binary(c_binary)
            native_out = run_binary(native_binary)
        self.assertEqual(native_out, c_out)
        self.assertEqual(native_out[0], "")
        self.assertEqual(native_out[2], 1)
        return native_out

    def words(self, source: str, arguments: list[str],
              name: str = "args") -> tuple[list[str], list[str]]:
        """Both backends' stdout, run with the same words after the name."""
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            path = write_source(directory, name, source)
            c_binary, native_binary = build_both(path, directory)
            c_out = subprocess.run([str(c_binary), *arguments], capture_output=True,
                                   text=True, timeout=120)
            native_out = subprocess.run([str(native_binary), *arguments],
                                        capture_output=True, text=True, timeout=120)
        self.assertEqual((native_out.stdout, native_out.stderr,
                          native_out.returncode),
                         (c_out.stdout, c_out.stderr, c_out.returncode))
        return c_out.stdout.splitlines(), native_out.stdout.splitlines()

    SPLIT_PROGRAM = """fn main() -> i64 {
    let parts: Vec(str) = vec_new()
    str_split(TEXT, SEP, parts)
    print(vec_len(parts))
    for part in parts { print(part) }
    return 0
}
"""

    def split_of(self, text: str, separator: str) -> list[str]:
        """The pieces of `text` by `separator`, on both backends."""
        source = (self.EDITION + self.SPLIT_PROGRAM
                  .replace("TEXT", '"' + text + '"').replace("SEP", '"' + separator + '"'))
        return self.agrees(source)

    def test_a_split_agrees_with_the_c_backend_on_every_shape(self):
        """The pieces, in order, for the separators a program actually writes."""
        cases = [("a,b,c", ","), ("a,bb,,ccc", ","), ("", ","), (",", ","),
                 (",,", ","), ("abc", ","), ("abc", "abc"), ("abc", "abcd"),
                 ("aXbXc", "X"), ("one two  three", "  "), ("//", "/"),
                 ("Привет мир", " "), ("aaa", "aa"), ("abab", "ab")]
        for text, separator in cases:
            with self.subTest(text=text, separator=separator):
                pieces = self.split_of(text, separator)
                expected = text.split(separator) if separator else []
                self.assertEqual(pieces[0], str(len(expected)))
                self.assertEqual(pieces[1:], expected)

    def test_a_piece_is_a_copy_and_not_a_window(self):
        """The pieces outlive the text they came from, which a view would not.

        `str_split` copies each piece into the arena, so a builder that grows
        after the split cannot move a piece the program is already holding: the
        split is taken, the builder is pushed past its capacity, and the pieces
        are read again.
        """
        source = self.EDITION + """fn main() -> i64 {
    let s: String = string_new()
    string_push(s, "a,bb,ccc")
    let parts: Vec(str) = vec_new()
    str_split(string_text(s), ",", parts)
    let i = 0
    while i < 512 { string_push(s, "x"); i = i + 1 }
    print(vec_len(parts))
    print(vec_get(parts, 0))
    print(vec_get(parts, 1))
    print(vec_get(parts, 2))
    print(string_len(s))
    return 0
}
"""
        # 8 bytes of text, then 512 more: the builder has moved, the pieces have not.
        self.assertEqual(self.agrees(source), ["3", "a", "bb", "ccc", "520"])

    def test_a_split_fills_a_vector_a_program_already_holds(self):
        """`str_split` appends: two splits of one vector are six pieces."""
        source = self.EDITION + """fn main() -> i64 {
    let parts: Vec(str) = vec_new()
    str_split("a-b", "-", parts)
    str_split("c-d-e", "-", parts)
    print(vec_len(parts))
    for part in parts { print(part) }
    return 0
}
"""
        self.assertEqual(self.agrees(source), ["5", "a", "b", "c", "d", "e"])

    def test_an_empty_separator_is_the_c_runtime_s_trap(self):
        """An empty separator would loop forever, so it is a stop with a message."""
        stderr = self.trap(self.EDITION + """fn main() -> i64 {
    let parts: Vec(str) = vec_new()
    str_split("abc", "", parts)
    return 0
}
""")[1]
        self.assertEqual(stderr, "nava runtime error: "
                                 "str_split needs a non-empty separator\n")

    def test_a_split_needs_no_push_the_program_wrote(self):
        """`str_split` pushes, so the vector family includes `push` and `reserve`.

        A program that never writes `vec_push` still has to grow the vector it
        hands to `str_split`. This is the link error the first version raised:
        `nv_vec_str_reserve` was emitted from what the program *said*, and the
        runtime's own push was not counted as saying it.
        """
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            path = write_source(directory, "onlysplit", self.EDITION + """fn main() -> i64 {
    let parts: Vec(str) = vec_new()
    str_split("a-b-c-d-e-f-g-h-i-j", "-", parts)
    print(vec_len(parts))
    return 0
}
""")
            built = run([*NAVA, "build", str(path), "-o", str(directory / "native"),
                         "--backend", "native"])
        self.assertEqual(built.returncode, 0, built.stderr)
        self.assertNotIn("undefined symbol", built.stderr, built.stderr)

    def test_the_two_argument_functions_agree_word_for_word(self):
        """`arg_count` is the words after the name; `arg_at` is one of them."""
        source = self.EDITION + """fn main() -> i64 {
    print(arg_count())
    let i = 0
    while i < arg_count() {
        print(arg_at(i))
        i = i + 1
    }
    return 0
}
"""
        for arguments in ([], ["one"], ["one", "two"], ["a", "b", "c", "d", "e"],
                          ["", "x"], ["with space", "tab\there"], ["Привет"]):
            with self.subTest(arguments=arguments):
                c_lines, native_lines = self.words(source, arguments)
                self.assertEqual(c_lines, [str(len(arguments)), *arguments])
                self.assertEqual(native_lines, c_lines)

    def test_a_word_is_a_str_and_not_a_borrow(self):
        """`arg_at` answers a `str`: it concatenates, splits and compares."""
        source = self.EDITION + """fn main() -> i64 {
    let first = arg_at(0)
    print(str_concat(first, "!"))
    print(first == "alpha")
    let parts: Vec(str) = vec_new()
    str_split(first, "-", parts)
    print(vec_len(parts))
    return 0
}
"""
        c_lines, native_lines = self.words(source, ["al-pha"])
        self.assertEqual(c_lines, ["al-pha!", "false", "2"])
        self.assertEqual(native_lines, c_lines)

    def test_an_index_out_of_bounds_is_the_c_runtime_s_trap(self):
        """`argument index N is out of bounds for M arguments` — plural and all."""
        source = self.EDITION + """fn main() -> i64 {
    print(arg_at(9))
    return 0
}
"""
        for arguments, expected in (([], "0 arguments"), (["only"], "1 argument"),
                                    (["a", "b"], "2 arguments")):
            with self.subTest(arguments=arguments):
                with tempfile.TemporaryDirectory() as temp:
                    directory = pathlib.Path(temp)
                    path = write_source(directory, "oob", source)
                    c_binary, native_binary = build_both(path, directory)
                    c_out = subprocess.run([str(c_binary), *arguments],
                                           capture_output=True, text=True, timeout=120)
                    native_out = subprocess.run([str(native_binary), *arguments],
                                                capture_output=True, text=True,
                                                timeout=120)
                self.assertEqual((native_out.stdout, native_out.stderr,
                                  native_out.returncode),
                                 (c_out.stdout, c_out.stderr, c_out.returncode))
                self.assertEqual(
                    native_out.stderr,
                    f"nava runtime error: argument index 9 is out of bounds "
                    f"for {expected}\n")

    def test_a_negative_index_is_refused_the_same_way(self):
        """`-1` is below the first word, and the message names it."""
        stderr = self.trap(self.EDITION + """fn main() -> i64 {
    print(arg_at(0 - 1))
    return 0
}
""")[1]
        self.assertEqual(stderr, "nava runtime error: argument index -1 is out of "
                                 "bounds for 0 arguments\n")

    def test_a_program_that_asks_for_no_word_pays_nothing(self):
        """`nv_argc`/`nv_argv` exist only for a program that reads them.

        The words are on the stack when `_start` runs and nowhere afterwards, so
        the copy into the data segment is the only place it can happen — and a
        program that never asks for one is not given it.
        """
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            plain = write_source(directory, "plain", self.EDITION + """fn main() -> i64 {
    return 0
}
""")
            asking = write_source(directory, "asking", self.EDITION + """fn main() -> i64 {
    print(arg_count())
    return 0
}
""")
            for path in (plain, asking):
                built = run([*NAVA, "build", str(path), "-o",
                             str(directory / f"{path.stem}-n"), "--backend", "native"])
                self.assertEqual(built.returncode, 0, built.stderr)
            plain_names = {symbol.name for symbol in reader.read(directory / "plain-n").symbols}
            asking_names = {symbol.name
                            for symbol in reader.read(directory / "asking-n").symbols}
            image = (directory / "asking-n").read_bytes()
            plain_image = (directory / "plain-n").read_bytes()
        # The two words live in the data segment, so their symbols carry its prefix.
        for word in ("nv_argc", "nv_argv"):
            self.assertNotIn(f"runtime.data.{word}", plain_names)
            self.assertIn(f"runtime.data.{word}", asking_names)
        self.assertNotIn("nv_arg_count", plain_names)
        self.assertIn("nv_arg_count", asking_names)
        self.assertLess(len(plain_image), len(image),
                        "a program that asks for no word emits less, not the same")

    def test_the_clock_reads_a_time_and_never_goes_backwards(self):
        """Three readings, compared in shape because a clock is not a constant.

        A reading cannot be asserted to a value, so what is compared is the
        relation the C runtime promises: milliseconds are the seconds scaled,
        and a monotonic reading never decreases.
        """
        source = self.EDITION + """fn main() -> i64 {
    let seconds = clock_seconds()
    let millis = clock_millis()
    let mono = clock_monotonic_millis()
    print(seconds > 1700000000)
    print(millis >= seconds * 1000)
    print(millis < (seconds + 2) * 1000)
    print(mono > 0)
    print(clock_monotonic_millis() >= mono)
    print(clock_millis() >= millis)
    print(clock_seconds() >= seconds)
    return 0
}
"""
        self.assertEqual(self.agrees(source),
                         ["true", "true", "true", "true", "true", "true", "true"])

    def test_the_two_backends_read_the_same_second(self):
        """A reading is a *time*, so the two binaries are run close together.

        The strongest comparison available for a non-deterministic value: build
        both, run both, and assert the seconds are within one of each other.
        """
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            path = write_source(directory, "clock", self.EDITION + """fn main() -> i64 {
    print(clock_seconds())
    print(clock_millis())
    return 0
}
""")
            c_binary, native_binary = build_both(path, directory)
            c_out = run_binary(c_binary)[0].splitlines()
            native_out = run_binary(native_binary)[0].splitlines()
        c_seconds, c_millis = int(c_out[0]), int(c_out[1])
        n_seconds, n_millis = int(native_out[0]), int(native_out[1])
        self.assertLessEqual(abs(n_seconds - c_seconds), 1,
                             "the two backends read the wall clock a second apart")
        self.assertLessEqual(abs(n_millis - c_millis), 1500)
        self.assertGreaterEqual(n_millis, n_seconds * 1000)
        self.assertLess(n_millis, (n_seconds + 2) * 1000)

    def test_a_failed_reading_would_stop_the_program(self):
        """The refusal is in the code, not in the test: the clock cannot fail.

        `clock_gettime` with a valid clock id does not fail, so the trap is
        unreachable — which is why this test asserts only that the helper is
        emitted with its stop in it, rather than pretending to reach it.
        """
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            path = write_source(directory, "clock", self.EDITION + """fn main() -> i64 {
    print(clock_seconds() >= 0)
    print(clock_millis() >= 0)
    print(clock_monotonic_millis() >= 0)
    return 0
}
""")
            built = run([*NAVA, "build", str(path), "-o", str(directory / "n"),
                         "--backend", "native"])
            self.assertEqual(built.returncode, 0, built.stderr)
            names = {symbol.name for symbol in reader.read(directory / "n").symbols}
            image = (directory / "n").read_bytes()
        for name in ("nv_clock_seconds", "nv_clock_millis",
                     "nv_clock_monotonic_millis"):
            self.assertIn(name, names, f"{name} was not emitted")
        self.assertIn(b"clock_gettime(CLOCK_REALTIME) failed", image)
        self.assertIn(b"clock_gettime(CLOCK_MONOTONIC) failed", image)

    def test_a_program_with_only_a_clock_links(self):
        """One reading pulls in one helper, and the syscall number is `228`."""
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            path = write_source(directory, "mono", self.EDITION + """fn main() -> i64 {
    print(clock_monotonic_millis() > 0)
    return 0
}
""")
            built = run([*NAVA, "build", str(path), "-o", str(directory / "n"),
                         "--backend", "native"])
            self.assertEqual(built.returncode, 0, built.stderr)
            names = {symbol.name for symbol in reader.read(directory / "n").symbols}
        self.assertIn("nv_clock_monotonic_millis", names)
        self.assertNotIn("nv_clock_seconds", names,
                         "a program that reads one clock emits one reader")
        self.assertNotIn("nv_clock_millis", names)

    def test_self_test_builds_and_runs_from_the_ir_on_both_backends(self):
        """`self_test` was the last fixture printed by the AST walk.

        It needs all six — `str_split`, the three clock readings and the two
        argument functions — so it is the one program that says the gap is
        closed, and it is compared on stdout, stderr and status.
        """
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            path = write_source(directory, "self_test", fixture_text("self_test"))
            c_binary, native_binary = build_both(path, directory)
            c_out = run_binary(c_binary)
            native_out = run_binary(native_binary)
            emitted = subprocess.run([*NAVA, "emit-c", str(path)], capture_output=True,
                                     text=True, timeout=300)
        self.assertEqual(emitted.returncode, 0, emitted.stderr)
        self.assertIn("A lowering of the shared IR", emitted.stdout,
                      "self_test is still printed by the AST walk")
        self.assertEqual(native_out, c_out)
        self.assertEqual(native_out[2], 0)
        self.assertTrue(native_out[0].strip(), "self_test printed nothing")
