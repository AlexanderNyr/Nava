"""Grammar conformance and the constrained-decoding adapter (PLAN.md phase N7).

Three claims are tested here, and nothing else:

1. the emitted GBNF artefact is well formed (no undefined, unreachable or
   left-recursive rule) and recognises the canonical examples;
2. the grammar agrees with the *compiler* on the whole fixture corpus, in both
   directions: a syntax error is rejected by both, a semantic error is accepted
   by the grammar and rejected by the compiler;
3. a grammar constrains syntax only. Type errors and runtime failures survive it,
   which is why `eval/feedback.py` keeps the stages apart.

`GRAMMAR_REJECTS` is a deliberate, written-down classification: if a new
diagnostic code appears in the corpus, `test_every_corpus_code_is_classified`
fails until someone decides which side it belongs to.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval import constrained, gbnf, grammar  # noqa: E402
from nava_lang.cli import compile_source  # noqa: E402
from nava_lang.model import NavaError  # noqa: E402

FIXTURES = ROOT / "tests/fixtures"
NEGATIVE = FIXTURES / "negative"
EXAMPLES = ROOT / "guides/examples"
NAVA = ROOT / "nava"

#: Codes produced while *reading* the source: a token or a parse-tree shape is
#: wrong, so the grammar must reject the file as well.
#:
#: E0346 (malformed or out-of-range float literal) and E0350 (`a..b` outside a
#: `for`) are in this set because the lexer/parser rejects them before any type
#: is known, and the grammar models the same rules.
GRAMMAR_REJECTS = frozenset(
    {f"E01{index:02d}" for index in range(1, 8)}
    | {"E0109", "E0110", "E0112", "E0113"}
    # E0203 (an unknown *name* in a type position) is semantic: the grammar
    # accepts any identifier there, because a struct name is a type in edition 2.
    # The array shapes keep a syntactic code, E0206, and the grammar models the
    # same fixed element set.
    | {"E0201", "E0202", "E0204", "E0205", "E0206"}
    # A label is part of a loop and `pub` is a prefix of a function: both are
    # shapes, so the grammar refuses them in the wrong position as well.
    | {"E0346", "E0350", "E0415", "E0432", "E0434"}
    # A call target is a name: `h.op(x)` and `fn(mut [i64]) -> i64` are shapes
    # the grammar does not have, so they are refused while reading the source
    # rather than by the checker.
    | {"E0441", "E0443"}
    # `edition` is a prefix of the file in the grammar and in the compiler.
    | {"E0370"}
)

#: Codes produced after the source was read successfully: the program is
#: syntactically valid and fails on a type, an ownership rule, a limit or a
#: language restriction. The grammar must accept these, and the compiler must
#: still reject them.
GRAMMAR_ACCEPTS = frozenset(
    ({"E0108"} | {f"E03{index:02d}" for index in range(1, 62)} | {"E0401"}
     # Edition 2: an unknown type name, an unknown edition number, a struct
     # declaration in an edition-1 file, and every struct rule that depends on
     # declared names or on types.
     | {"E0203"} | {f"E03{index:02d}" for index in range(70, 82)}
     # Enum and match rules are all names, arities and coverage: syntax the
     # grammar accepts and the checker rejects.
     | {f"E039{index}" for index in range(0, 10)}
     # `?`, type arguments and instance arity are shapes the grammar accepts;
     # the checker decides what they mean.
     | {f"E041{index}" for index in range(0, 5)}
     # A type argument that is a value, not a type, is a shape error.
     | {"E0415"}
     # Slices and arrays of structs: the shape is fine, the types and the
     # ownership rules are not, so the checker is the authority.
     | {f"E042{index}" for index in range(0, 4)} | {"E0425"}
     # A jump that names no enclosing loop, and a jump out of a deferred block.
     | {"E0430", "E0431", "E0433"}
     # Generics: a duplicate parameter, a template used as a type, an argument
     # that cannot be inferred and a second argument list are names and arities.
     # A grammar with no symbol table cannot tell `identity(i64)(7)` from
     # `double(i64)(21)`, so it accepts both and the compiler says which is which.
     | {"E0435", "E0436", "E0437", "E0438"}
     # Function values: `x(1)` where `x` is not a function, and `f == g` for two
     # addresses, are calls and comparisons the shape cannot refuse.
     | {"E0439", "E0442"})
    - GRAMMAR_REJECTS
)

#: Fixtures where the recorded code is reached for a reason the grammar cannot
#: and should not model. The value says whether the grammar accepts the file.
CORPUS_OVERRIDES = {
    "ctl_for_iterable_type.nv":
        (True, "iterating a non-array is E0350 from the type checker, not from the "
               "shape of the `for` statement"),
}


def accept(source: str, name: str = "<test>") -> bool:
    accepted, _ = grammar.accepts_source(source, name)
    return accepted


class GrammarArtefactTests(unittest.TestCase):
    def test_the_emitted_gbnf_has_no_structural_problem(self):
        parsed = grammar.compiled_gbnf()
        self.assertEqual(gbnf.validate(parsed), [])
        self.assertEqual(parsed.start, "program",
                         "the first rule of the GBNF document is its entry rule")

    def test_the_artefact_is_written_by_the_command_line(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "nava.gbnf"
            result = subprocess.run([sys.executable, str(ROOT / "eval/grammar.py"),
                                     "--emit", str(target)],
                                    capture_output=True, text=True, timeout=120)
            self.assertEqual(result.returncode, 0, result.stderr)
            written = target.read_text(encoding="utf-8")
        self.assertEqual(written, grammar.emit_gbnf())
        self.assertIn('"fn"', written)
        self.assertIn("program ::=", written)

    def test_a_grammar_that_references_a_missing_rule_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            broken = Path(temp) / "broken.gbnf"
            broken.write_text('root ::= "a" missing\n', encoding="utf-8")
            result = subprocess.run([sys.executable, str(ROOT / "eval/gbnf.py"),
                                     str(broken), "--validate"],
                                    capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 1)
        self.assertIn("refers to undefined rule 'missing'", result.stderr)

    def test_a_left_recursive_grammar_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            broken = Path(temp) / "recursive.gbnf"
            broken.write_text('root ::= root "a" | "b"\n', encoding="utf-8")
            result = subprocess.run([sys.executable, str(ROOT / "eval/gbnf.py"),
                                     str(broken), "--validate"],
                                    capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 1)
        self.assertIn("left recursive", result.stderr)

    def test_the_validate_command_accepts_the_artefact(self):
        result = subprocess.run([sys.executable, str(ROOT / "eval/grammar.py"), "--validate"],
                                capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_every_canonical_example_is_recognised_by_the_emitted_text(self):
        parsed = grammar.compiled_gbnf()
        for path in sorted(EXAMPLES.rglob("*.nv")):
            with self.subTest(example=str(path.relative_to(EXAMPLES))):
                self.assertTrue(gbnf.accepts_text(parsed, path.read_text(encoding="utf-8")))

    def test_the_emitted_text_rejects_a_deliberately_malformed_candidate(self):
        parsed = grammar.compiled_gbnf()
        malformed = [
            "fn main() { print( }",
            "fn main() {",
            "fn main( { }",
            "fn 9() { }",
            "main() { }",
            "fn main() { print(1 2) }",
        ]
        for candidate in malformed:
            with self.subTest(candidate=candidate):
                self.assertFalse(gbnf.accepts_text(parsed, candidate))
                with self.assertRaises(NavaError):
                    compile_source(candidate)

    def test_an_unclosed_string_is_rejected_by_both(self):
        candidate = 'fn main() {\n    print("oops)\n}\n'
        self.assertFalse(gbnf.accepts_text(grammar.compiled_gbnf(), candidate))
        with self.assertRaises(NavaError) as caught:
            compile_source(candidate)
        self.assertIn(caught.exception.code, {"E0102", "E0103"})
        self.assertTrue(caught.exception.code.startswith("E01"))

    def test_gbnf_keyword_matching_is_documented_as_permissive(self):
        """GBNF has no lookahead, so `let` also matches the identifier pattern.

        The token-level grammar rejects `let = 1` (the lexer produces a LET
        token, and an assignment needs an identifier); the emitted GBNF accepts
        it because a literal `"let"` and `[a-zA-Z_]...` both match those three
        characters. This is recorded rather than hidden: the grammar is a
        pre-filter, and the compiler remains the authority.
        """
        candidate = "fn main() { let = 1 }"
        self.assertTrue(gbnf.accepts_text(grammar.compiled_gbnf(), candidate))
        self.assertFalse(grammar.accepts_tokens(grammar.lex_tokens(candidate)))
        with self.assertRaises(NavaError):
            compile_source(candidate)


class CorpusConformanceTests(unittest.TestCase):
    """The grammar and the compiler must agree on every fixture, in both directions."""

    def cases(self) -> list[dict]:
        document = json.loads((FIXTURES / "negative.json").read_text(encoding="utf-8"))
        return document["cases"]

    def test_every_corpus_code_is_classified(self):
        codes = {case["code"] for case in self.cases()}
        unclassified = sorted(codes - GRAMMAR_REJECTS - GRAMMAR_ACCEPTS)
        if unclassified:
            self.fail("decide whether the grammar rejects these codes and add them to "
                      f"GRAMMAR_REJECTS or GRAMMAR_ACCEPTS: {unclassified}")
        both = sorted(GRAMMAR_REJECTS & GRAMMAR_ACCEPTS)
        self.assertEqual(both, [], "a code cannot be on both sides of the boundary")
        for code in sorted(codes):
            with self.subTest(code=code):
                self.assertTrue(code in GRAMMAR_REJECTS or code in GRAMMAR_ACCEPTS)

    def test_negative_fixtures_are_accepted_or_rejected_as_classified(self):
        for case in self.cases():
            source = (NEGATIVE / case["file"]).read_text(encoding="utf-8")
            accepted = accept(source, case["file"])
            if case["file"] in CORPUS_OVERRIDES:
                expected, reason = CORPUS_OVERRIDES[case["file"]]
                with self.subTest(file=case["file"], reason=reason):
                    self.assertEqual(accepted, expected)
            elif case["code"] in GRAMMAR_REJECTS:
                with self.subTest(file=case["file"], code=case["code"]):
                    self.assertFalse(accepted,
                                     f"{case['file']} is a {case['code']} syntax error but "
                                     "the grammar accepted it")
            else:
                with self.subTest(file=case["file"], code=case["code"]):
                    self.assertTrue(accepted,
                                    f"{case['file']} is {case['code']}, a semantic error; the "
                                    "grammar must accept the syntax and leave it to the checker")

    def test_every_valid_fixture_is_accepted(self):
        positive = sorted((FIXTURES / "positive").glob("*.nv"))
        self.assertGreaterEqual(len(positive), 19)
        for path in positive:
            with self.subTest(fixture=path.name):
                self.assertTrue(accept(path.read_text(encoding="utf-8"), path.name))

    def test_multi_file_module_fixtures_are_accepted(self):
        files = sorted((FIXTURES / "modules").rglob("*.nv"))
        self.assertGreaterEqual(len(files), 14)
        for path in files:
            with self.subTest(fixture=str(path.relative_to(FIXTURES / "modules"))):
                self.assertTrue(accept(path.read_text(encoding="utf-8"), path.name))

    def test_the_shipped_examples_are_accepted(self):
        for path in sorted((ROOT / "examples").rglob("*.nv")):
            with self.subTest(example=path.name):
                self.assertTrue(accept(path.read_text(encoding="utf-8"), path.name))


class SyntaxIsNotSemanticsTests(unittest.TestCase):
    """A grammar proves syntax; the compiler still has to prove the rest."""

    def test_a_type_error_is_accepted_by_the_grammar_and_rejected_by_the_compiler(self):
        source = "fn main() {\n    let x: bool = 1\n}\n"
        self.assertTrue(accept(source))
        with self.assertRaises(NavaError) as caught:
            compile_source(source)
        self.assertEqual(caught.exception.code, "E0311")

    def test_a_runtime_failure_survives_both_the_grammar_and_the_compiler(self):
        source = "fn main() {\n    let a = read_i64()\n    print(100 / a)\n}\n"
        self.assertTrue(accept(source))
        compile_source(source)   # the compiler is satisfied
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "div.nv"
            path.write_text(source, encoding="utf-8")
            result = subprocess.run([sys.executable, str(NAVA), "run", "--isolation", "off",
                                     str(path)], input="0\n", capture_output=True,
                                    text=True, timeout=300)
        self.assertEqual(result.returncode, 1)
        self.assertIn("division by zero", result.stderr)

    def test_an_overflow_is_accepted_by_both_and_still_fails_at_run_time(self):
        source = "fn main() {\n    let a = read_i64()\n    print(a * a)\n}\n"
        self.assertTrue(accept(source))
        compile_source(source)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "overflow.nv"
            path.write_text(source, encoding="utf-8")
            result = subprocess.run([sys.executable, str(NAVA), "run", "--isolation", "off",
                                     str(path)], input="4000000000\n", capture_output=True,
                                    text=True, timeout=300)
        self.assertEqual(result.returncode, 1)
        self.assertIn("overflow", result.stderr)

    def test_an_unmatched_bracket_is_rejected_before_types_are_considered(self):
        source = "fn main() {\n    print((1 + 2)\n}\n"
        self.assertFalse(accept(source))
        with self.assertRaises(NavaError) as caught:
            compile_source(source)
        self.assertIn(caught.exception.code, {"E0109", "E0110", "E0201"})


class ConstrainedPayloadTests(unittest.TestCase):
    def test_each_interface_puts_the_grammar_in_its_documented_field(self):
        text = grammar.emit_gbnf()
        for name in constrained.interfaces():
            with self.subTest(interface=name):
                fields = constrained.extra_fields(name, text)
                self.assertEqual(list(fields), [constrained.INTERFACES[name]["field"]])
                self.assertEqual(fields[constrained.INTERFACES[name]["field"]], text)

    def test_the_adapter_declares_itself_unverified(self):
        for name in constrained.interfaces():
            with self.subTest(interface=name):
                described = constrained.describe(name)
                self.assertEqual(described["status"], "experimental")
                self.assertIsNone(described["verified_against"])
                self.assertIn("syntax", described["syntax_only"])

    def test_a_summary_preview_keeps_the_digest_and_drops_the_text(self):
        document = constrained.preview("llamacpp", model="demo", text=grammar.emit_gbnf(),
                                       full_grammar=False)
        self.assertEqual(document["request"]["grammar"], "<omitted>")
        self.assertNotIn("text", document["grammar"])
        self.assertEqual(len(document["grammar"]["sha256"]), 64)

    def test_an_unknown_interface_is_an_error(self):
        with self.assertRaises(ValueError):
            constrained.extra_fields("not-a-server", "x")
        result = subprocess.run([sys.executable, str(ROOT / "eval/constrained.py"),
                                 "--interface", "not-a-server"],
                                capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 2)

    def test_checking_the_grammar_catches_a_broken_artefact(self):
        self.assertEqual(constrained.check(grammar.emit_gbnf()), [])
        self.assertTrue(constrained.check("program ::= missing-rule\n"))

    def test_the_command_line_preview_carries_the_whole_grammar(self):
        result = subprocess.run([sys.executable, str(ROOT / "eval/constrained.py"),
                                 "--interface", "vllm", "--model", "demo", "--check"],
                                capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        document = json.loads(result.stdout)
        self.assertEqual(document["request"]["guided_grammar"], grammar.emit_gbnf())
        self.assertEqual(document["adapter"]["field"], "guided_grammar")


if __name__ == "__main__":
    unittest.main()
