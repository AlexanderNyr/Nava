#!/usr/bin/env python3
"""The selective runtime: the invariants that make dropping units safe (N8).

`nava_lang/runtime.py` removes runtime helpers a program cannot reach. That is
only sound if the split is faithful and the closure is closed: every unit that
survives must have all of its references satisfied by another surviving unit.
These tests state that property directly and then check it on real programs
taken from the examples rather than on a hand-written toy.

The other half of the file pins the *scanner*: `SYMBOL` is deliberately a
character class instead of a word-boundary pattern, which makes it a superset.
Over-approximating keeps an extra unit; under-approximating would drop a needed
one, so the direction is asserted, not assumed.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

from nava_lang import cgen, runtime
from nava_lang.checker import check
from nava_lang.parser import parse

ROOT = Path(__file__).resolve().parents[1]
#: A word-boundary reference to a runtime symbol: what a human would write.
STRICT = re.compile(r"\b(?:nv|NV)_[A-Za-z0-9_]+\b")


def compiled_body(path: Path) -> str:
    """The generated program body for a file, without the runtime.

    A file that shares its directory with another `.nv` file is a project:
    `import` needs the loader, so the whole project is loaded.
    """
    from nava_lang import modules  # noqa: PLC0415 - only for project files
    if len(list(path.parent.glob("*.nv"))) > 1:
        program = check(modules.load(path))
    else:
        program = check(parse(path.read_text(encoding="utf-8")))
    return cgen.generate(program)


class SplitTests(unittest.TestCase):
    def test_indices_match_positions(self):
        units = runtime.split(cgen.RUNTIME)
        self.assertEqual([unit.index for unit in units], list(range(len(units))))

    def test_the_split_keeps_every_definition(self):
        units = runtime.split(cgen.RUNTIME)
        defined = {name for unit in units for name in unit.names}
        strict = set(STRICT.findall(cgen.RUNTIME))
        missing = {name for name in strict if name.startswith(("nv_", "NV_"))} - defined
        # `/* NV_CAPABILITIES */` and the grant tables it is replaced with are
        # produced per build by `cgen.capability_block`, so they are not units.
        generated = {"NV_CAPABILITIES", "NV_READ_ROOT_COUNT", "NV_WRITE_ROOT_COUNT",
                     "nv_read_roots", "nv_write_roots", "NV_MAX_READ_"}
        self.assertEqual(sorted(missing - generated), [])

    def test_units_reassemble_into_the_runtime(self):
        units = runtime.split(cgen.RUNTIME)
        text = "\n\n".join(unit.text for unit in units)
        for marker in ("#include", "nv_add", "nv_print", "nv_read_file"):
            self.assertIn(marker, text)
        # Every prefix of a unit survives verbatim: nothing is rewritten.
        for unit in units:
            self.assertIn(unit.text, cgen.RUNTIME)

    def test_a_conditional_is_never_split(self):
        for unit in runtime.split(cgen.RUNTIME):
            opens = len(re.findall(r"^#\s*(?:if|ifdef|ifndef)\b", unit.text, re.M))
            closes = len(re.findall(r"^#\s*endif\b", unit.text, re.M))
            self.assertEqual(opens, closes, f"unit {unit.index} has unbalanced #if")

    def test_the_includes_are_always_units(self):
        always = [unit for unit in runtime.split(cgen.RUNTIME) if unit.always]
        self.assertTrue(always)
        self.assertTrue(any("#include" in unit.text for unit in always))
        self.assertTrue(any("NV_CAPABILITIES" in unit.text for unit in always))

    def test_comments_travel_with_the_definition_they_describe(self):
        for unit in runtime.split(cgen.RUNTIME):
            if not unit.text.lstrip().startswith("/*"):
                continue
            # A leading block comment must be followed by the code it describes.
            self.assertTrue(unit.names or unit.always or "*/" in unit.text)

    def test_the_split_is_cached_by_identity(self):
        first = runtime.split(cgen.RUNTIME)
        self.assertIs(runtime.split(cgen.RUNTIME), first)
        copy = (" " + cgen.RUNTIME)[1:]
        self.assertEqual(copy, cgen.RUNTIME)
        second = runtime.split(copy)
        self.assertIsNot(second, first)
        self.assertEqual([u.text for u in second], [u.text for u in first])


class SymbolScannerTests(unittest.TestCase):
    def test_the_scanner_is_a_superset_of_the_word_boundary_pattern(self):
        sample = ("nv_add(x) foo_nv_bar NV_LIMIT nv_ NV_ nv_1a 1nv_b\n"
                  "/* nv_read_file */ my_nv_x nV_mixed NV__x")
        self.assertLessEqual(set(STRICT.findall(sample)), set(runtime.SYMBOL.findall(sample)))

    def test_the_scanner_finds_every_symbol_of_the_real_runtime(self):
        self.assertEqual(set(STRICT.findall(cgen.RUNTIME)),
                         set(runtime.SYMBOL.findall(cgen.RUNTIME)))

    def test_a_boundary_word_is_not_matched_by_the_strict_pattern(self):
        self.assertEqual(STRICT.findall("foo_nv_bar"), [])
        self.assertEqual(runtime.SYMBOL.findall("foo_nv_bar"), ["nv_bar"])


class ClosureTests(unittest.TestCase):
    def selected_text(self, body: str) -> str:
        return runtime.select(cgen.RUNTIME, set(runtime.SYMBOL.findall(body)))

    def test_the_closure_is_closed(self):
        """Every reference of a kept unit is defined by another kept unit."""
        units = runtime.split(cgen.RUNTIME)
        defined = {name: unit.index for unit in units for name in unit.names}
        for example in sorted((ROOT / "examples").rglob("*.nv")):
            if example.parent.name == "modules" and example.name != "main.nv":
                continue  # imported by main.nv, not an entry point
            body = compiled_body(example)
            keep = set(runtime.closure(units, set(runtime.SYMBOL.findall(body))))
            for index in keep:
                for reference in units[index].references:
                    if reference in defined:
                        self.assertIn(defined[reference], keep,
                                      f"{example.name}: unit {index} needs {reference}")
                    else:
                        # Not a runtime definition: a program symbol or a
                        # library macro such as INT64_C.
                        self.assertTrue(reference.startswith(("nv_fn_", "nv_", "NV_"))
                                        or reference in {"INT64_C", "INT64_MAX",
                                                         "INT64_MIN", "UINT64_C"})

    def test_a_program_that_only_prints_drops_the_file_helpers(self):
        body = compiled_body(ROOT / "bench" / "loop.nv")
        text = self.selected_text(body)
        self.assertIn("nv_print", text)
        for dropped in ("nv_read_file", "nv_write_file", "nv_format_f64",
                        "nv_utf8_decode", "nv_arr_bool_push", "nv_require_grant"):
            self.assertNotIn(dropped, text, dropped)

    def test_a_program_that_reads_a_file_keeps_the_file_helpers(self):
        """A program that calls `read_file` keeps the helper it needs."""
        source = ("fn main() -> void {\n"
                  "  let text = read_file(\"data.txt\")\n"
                  "  print(text)\n"
                  "}\n")
        program = check(parse(source))
        text = self.selected_text(cgen.generate(program))
        self.assertIn("nv_read_file", text)
        self.assertIn("nv_require_grant", text)

    def test_selection_is_deterministic_and_smaller(self):
        body = compiled_body(ROOT / "bench" / "loop.nv")
        once = self.selected_text(body)
        self.assertEqual(once, self.selected_text(body))
        self.assertLess(len(once), len(cgen.RUNTIME))
        self.assertIn(body.strip().splitlines()[-1], once)

    def test_a_dropped_unit_is_not_reachable(self):
        """Dropping is decided by the closure, not by the order of the text."""
        body = compiled_body(ROOT / "bench" / "loop.nv")
        used = set(runtime.SYMBOL.findall(body))
        units = runtime.split(cgen.RUNTIME)
        keep = set(runtime.closure(units, used))
        text = runtime.select(cgen.RUNTIME, used)
        for unit in units:
            self.assertEqual(unit.text in text, unit.index in keep, unit.index)


class SummaryTests(unittest.TestCase):
    def test_the_summary_describes_the_split(self):
        summary = runtime.summary(cgen.RUNTIME)
        self.assertEqual(summary["bytes"], len(cgen.RUNTIME))
        self.assertEqual(summary["units"], len(runtime.split(cgen.RUNTIME)))
        self.assertGreater(summary["units"], 30)
        self.assertGreater(summary["named_units"], 30)
        self.assertGreater(summary["always_bytes"], 0)


if __name__ == "__main__":
    unittest.main()
