"""Versioned model-facing guide and its canonical examples (PLAN.md phase N7).

The guide is part of the measurement, not decoration: a study record cites its
digest, so these tests fail when the text, the manifest, the examples or the
prompt builder stop agreeing. Nothing here calls a model, and nothing here
invents a token count.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval import constrained  # noqa: E402
from eval import grammar as grammar_module  # noqa: E402
from eval import guide as guide_module  # noqa: E402
from eval import overhead as overhead_module  # noqa: E402
from eval import prompt as prompt_module  # noqa: E402
from eval import tokens as tokens_module  # noqa: E402
from nava_lang.checker import BUILTINS  # noqa: E402
from nava_lang.cli import compile_source  # noqa: E402
from nava_lang.lexer import KEYWORDS  # noqa: E402
from nava_lang.model import NavaError  # noqa: E402
from nava_lang.parser import parse  # noqa: E402

GUIDES = ROOT / "guides"
MEASUREMENTS = ROOT / "eval" / "measurements"
EXAMPLES = GUIDES / "examples"
NAVA = ROOT / "nava"


def run_example(entry: Path, stdin: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(NAVA), "run", "--isolation", "off", str(entry)],
        input=stdin, capture_output=True, text=True, timeout=300,
        cwd=str(EXAMPLES))


class GuideManifestTests(unittest.TestCase):
    def test_recorded_digests_and_sizes_match_the_files(self):
        self.assertEqual(guide_module.verify(), [])

    def test_the_manifest_names_a_current_guide_that_exists(self):
        manifest = guide_module.load_manifest()
        self.assertEqual(manifest["schema"], "nava-guides/1")
        current = manifest["current"]
        self.assertIn(current, manifest["guides"])
        entry = manifest["guides"][current]
        self.assertEqual(entry["version"], 1)
        self.assertEqual(entry["language"], "nava")

    def test_the_guide_tracks_the_documented_specification_version(self):
        specification = (ROOT / "LANGUAGE.md").read_text(encoding="utf-8")
        match = re.search(r"\*\*Specification version (\d+)\*\*", specification)
        self.assertIsNotNone(match, "LANGUAGE.md must declare its specification version")
        guide = guide_module.load()
        self.assertEqual(guide.spec_version, int(match.group(1)))
        self.assertIn(f"specification version {guide.spec_version}", guide.text)

    def test_an_edited_guide_without_a_manifest_update_is_reported(self):
        """The digest is the contract; a silent edit must be visible."""
        guide = guide_module.load()
        manifest = guide_module.load_manifest()
        original = manifest["guides"][guide.name]["sha256"]
        manifest["guides"][guide.name]["sha256"] = "0" * 64
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "manifest.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            saved = guide_module.MANIFEST
            guide_module.MANIFEST = path
            try:
                problems = guide_module.verify()
            finally:
                guide_module.MANIFEST = saved
        self.assertEqual(original, guide.digest)
        self.assertTrue(any("sha256 does not match" in problem for problem in problems))


class GuideExampleTests(unittest.TestCase):
    def setUp(self):
        self.document = guide_module.load_examples()

    def test_the_manifest_lists_the_current_guide(self):
        self.assertEqual(self.document["schema"], "nava-guide-examples/1")
        self.assertEqual(self.document["guide"], guide_module.load().name)

    def test_every_example_runs_and_prints_its_recorded_output(self):
        self.assertGreaterEqual(len(self.document["examples"]), 6)
        for case in self.document["examples"]:
            with self.subTest(example=case["id"]):
                entry = EXAMPLES / case["entry"]
                self.assertTrue(entry.is_file(), f"{entry} is recorded but missing")
                result = run_example(entry, case["stdin"])
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, case["stdout"])

    def test_every_example_file_is_reachable_from_a_recorded_entry(self):
        entries = {case["entry"] for case in self.document["examples"]}
        imported = set()
        for entry in entries:
            program = parse((EXAMPLES / entry).read_text(encoding="utf-8"))
            for item in program.imports:
                imported.add(f"{Path(entry).parent}/{item.name}.nv"
                             if Path(entry).parent != Path(".") else f"{item.name}.nv")
        for path in sorted(EXAMPLES.rglob("*.nv")):
            relative = str(path.relative_to(EXAMPLES))
            with self.subTest(file=relative):
                self.assertTrue(relative in entries or relative in imported,
                                f"{relative} is neither an example entry nor imported by one")

    def test_every_example_is_covered_by_the_grammar_and_the_compiler(self):
        from eval import grammar
        for case in self.document["examples"]:
            entry = EXAMPLES / case["entry"]
            source = entry.read_text(encoding="utf-8")
            with self.subTest(example=case["id"]):
                self.assertTrue(grammar.accepts_source(source, str(entry))[0])
                compile_source(source, path=entry)


class GuideCoverageTests(unittest.TestCase):
    """The guide must describe what the compiler implements, not a past version."""

    def setUp(self):
        self.guide = guide_module.load()
        self.text = self.guide.text

    def test_every_keyword_is_named(self):
        for keyword in sorted(KEYWORDS):
            with self.subTest(keyword=keyword):
                self.assertIn(keyword, self.text, f"the guide never mentions {keyword!r}")

    def test_every_builtin_is_named(self):
        for builtin in sorted(BUILTINS):
            with self.subTest(builtin=builtin):
                self.assertIn(f"{builtin}(", self.text,
                              f"the guide never documents {builtin}()")

    def test_the_guide_keeps_the_implemented_features_and_drops_the_stale_denials(self):
        """N7 replaced a guide that still said arrays and `for` did not exist."""
        for feature in ("for ", "while ", "array_zeros", "f64", "import ", "::"):
            with self.subTest(feature=feature):
                self.assertIn(feature, self.text)
        for denial in ("no arrays", "no for loops", "Only these features are available"):
            with self.subTest(denial=denial):
                self.assertNotIn(denial, self.text)

    def test_the_guide_lists_every_standard_library_function(self):
        """A guide that omits a stdlib function sends a model to the wrong call."""
        for module in sorted((ROOT / "nava_lang/stdlib").glob("*.nv")):
            source = module.read_text(encoding="utf-8")
            for name in re.findall(r"^fn (\w+)", source, re.MULTILINE):
                if name == "module_version":
                    continue
                with self.subTest(module=module.stem, function=name):
                    self.assertIn(f"{module.stem}::{name}", self.text)

    def test_the_whole_program_examples_in_the_guide_compile(self):
        blocks = re.findall(r"```nava\n(.*?)```", self.text, re.DOTALL)
        programs = [block for block in blocks
                    if block.lstrip().startswith(("fn ", "import "))]
        self.assertGreaterEqual(len(programs), 2, "the guide should show whole programs")
        for index, program in enumerate(programs, 1):
            with self.subTest(block=index):
                with tempfile.TemporaryDirectory() as temp:
                    entry = Path(temp) / "main.nv"
                    entry.write_text(program, encoding="utf-8")
                    try:
                        compile_source(program, path=entry)
                    except NavaError as error:
                        self.fail(f"guide program {index} does not compile: "
                                  f"{error.code} {error.message}")


class GuidePromptTests(unittest.TestCase):
    def test_the_prompt_carries_the_versioned_guide_verbatim(self):
        prompt = prompt_module.build_prompt("gcd", "nava")
        self.assertIn(prompt_module.NAVA_GUIDE, prompt)
        self.assertIn(prompt_module.GUIDE_INFO["sha256"], json.dumps(
            {"sha256": prompt_module.GUIDE_DIGESTS["nava"]}))

    def test_the_c_and_cpp_guides_have_digests_too(self):
        for language in ("c", "cpp"):
            with self.subTest(language=language):
                self.assertEqual(len(prompt_module.GUIDE_DIGESTS[language]), 64)
        self.assertEqual(prompt_module.GUIDE_DIGESTS["nava"], guide_module.load().digest)

    def test_the_prompt_builder_reports_the_guide_metadata(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "eval/prompt.py"), "--task", "gcd",
             "--language", "nava", "--json"],
            capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        document = json.loads(result.stdout)
        self.assertEqual(document["guide_metadata"]["name"], prompt_module.GUIDE_INFO["name"])
        self.assertEqual(document["guide_metadata"]["sha256"],
                         prompt_module.GUIDE_DIGESTS["nava"])
        self.assertIn(prompt_module.NAVA_GUIDE, document["prompt"])


class GuideTokenTests(unittest.TestCase):
    """Token counts are measured or missing; there is no estimate."""

    def test_sizes_are_exact_and_tokens_are_null_without_a_tokenizer(self):
        guide = guide_module.load()
        tokenizer = tokens_module.make_tokenizer()
        record = tokens_module.measure({"guide": guide.text}, tokenizer)
        part = record["parts"][0]
        self.assertEqual(part["bytes"], len(guide.text.encode("utf-8")))
        self.assertEqual(part["chars"], len(guide.text))
        self.assertEqual(part["lines"], guide.text.count("\n"))
        self.assertIn(record["status"], ("measured", "not-measured"))
        if record["status"] == "not-measured":
            self.assertIsNone(record["tokens"])
            self.assertIsNone(part["tokens"])
            self.assertTrue(record["reason"])
        else:
            self.assertGreater(record["tokens"], 0)

    def test_a_missing_tokenizer_library_is_reported_not_estimated(self):
        tokenizer = tokens_module.make_tokenizer("this-encoding-does-not-exist")
        self.assertFalse(tokenizer.available)
        self.assertIsNone(tokenizer.count("fn main() {}"))
        self.assertIn("tiktoken", tokenizer.reason or "")

    def test_require_measured_fails_when_nothing_was_measured(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "eval/tokens.py"), "--guide",
             "--require", "measured"],
            capture_output=True, text=True, timeout=120)
        record = json.loads(result.stdout)
        if record["status"] == "measured":
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertEqual(result.returncode, 1)
            self.assertIn("--require measured was requested", result.stderr)


class GuideOverheadTests(unittest.TestCase):
    """The whole guidance overhead of a prompt, and the records that carry it."""

    def test_the_overhead_covers_every_task_and_language(self):
        record = overhead_module.measure()
        self.assertEqual(record["schema"], "nava-overhead/1")
        self.assertEqual(record["tasks"], sorted(prompt_module.load_tasks()))
        self.assertTrue(record["pairs"])
        for case in record["pairs"]:
            self.assertEqual(set(case["languages"]), set(prompt_module.LANGUAGES))
            for language in prompt_module.LANGUAGES:
                part = case["languages"][language]
                prompt = prompt_module.build_prompt(case["task"], language)
                self.assertEqual(part["prompt"]["chars"], len(prompt))
                self.assertEqual(part["prompt"]["bytes"], len(prompt.encode("utf-8")))
                self.assertEqual(part["prompt"]["lines"], prompt.count("\n"))
                # The complete prompt is its parts plus the separators.
                self.assertGreaterEqual(part["prompt"]["chars"],
                                        part["guide"]["chars"] + part["task"]["chars"])
            # The prompt carries the guide stripped of its trailing newline, so the
            # part digest identifies what the model saw, not the file on disk.
            self.assertEqual(case["languages"]["nava"]["guide"]["sha256"],
                             guide_module.sha256(prompt_module.NAVA_GUIDE.strip()))
            self.assertEqual(record["guide"]["sha256"], prompt_module.GUIDE_DIGESTS["nava"])
            for other in ("c", "cpp"):
                self.assertGreater(case["languages"]["nava"]["guide"]["chars"],
                                   case["languages"][other]["guide"]["chars"],
                                   "the Nava guide is the expensive one, so it must be charged")

    def test_the_overhead_is_the_difference_between_the_prompts(self):
        # A deterministic test double, not a measurement: this checks arithmetic.
        double = tokens_module.Tokenizer("test-double", "chars//3",
                                         counter=lambda text: len(text) // 3)
        record = overhead_module.measure(["gcd"], double)
        case = record["pairs"][0]
        nava = case["languages"]["nava"]["prompt"]
        c_prompt = case["languages"]["c"]["prompt"]
        self.assertEqual(case["guidance_overhead"]["c"]["prompt"]["chars"],
                         nava["chars"] - c_prompt["chars"])
        self.assertEqual(case["guidance_overhead"]["c"]["prompt"]["tokens"],
                         nava["tokens"] - c_prompt["tokens"])

    def test_without_a_tokenizer_the_overhead_has_no_token_numbers(self):
        record = overhead_module.measure(["gcd"], tokens_module.make_tokenizer())
        if record["status"] == "measured":
            self.assertIsNotNone(record["languages"]["nava"]["prompt"]["tokens"])
            return
        self.assertIsNone(record["languages"]["nava"]["prompt"]["tokens"])
        self.assertIsNone(record["pairs"][0]["guidance_overhead"]["c"]["prompt"]["tokens"])
        self.assertTrue(record["reason"])

    def test_a_record_can_never_contain_an_invented_token_count(self):
        record = overhead_module.measure(["gcd"], tokens_module.make_tokenizer())
        status = record["status"]
        self.assertIn(status, ("measured", "not-measured"))
        for case in record["pairs"]:
            for language in prompt_module.LANGUAGES:
                tokens = case["languages"][language]["prompt"]["tokens"]
                if status == "not-measured":
                    self.assertIsNone(tokens)
                else:
                    self.assertIsInstance(tokens, int)
                    self.assertGreater(tokens, 0)

    def test_the_recorded_measurements_still_describe_the_current_guide(self):
        paths = sorted(MEASUREMENTS.glob("overhead-*.json"))
        self.assertTrue(paths, "no recorded guidance-overhead measurement")
        grammar_digest = constrained.grammar_metadata(grammar_module.emit_gbnf())["sha256"]
        for path in paths:
            record = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(record["schema"], "nava-overhead/1", path.name)
            self.assertEqual(record["guide"]["sha256"], prompt_module.GUIDE_DIGESTS["nava"],
                             f"{path.name} cites a guide digest that is no longer current")
            for case in record["pairs"]:
                self.assertEqual(case["languages"]["nava"]["guide"]["sha256"],
                                 guide_module.sha256(prompt_module.NAVA_GUIDE.strip()), path.name)
            self.assertEqual(record["grammar"]["sha256"], grammar_digest,
                             f"{path.name} cites a grammar digest that is no longer current")
            self.assertIn(path.name.split("-", 1)[1].removesuffix(".json"),
                          (record["tokenizer"]["name"] or ""))
            for case in record["pairs"]:
                for language, part in case["languages"].items():
                    prompt = prompt_module.build_prompt(case["task"], language)
                    self.assertEqual(part["prompt"]["chars"], len(prompt), path.name)
                    self.assertEqual(part["prompt"]["bytes"], len(prompt.encode("utf-8")),
                                     path.name)
                    self.assertEqual(part["prompt"]["lines"], prompt.count("\n"), path.name)
                    tokens = part["prompt"]["tokens"]
                    self.assertTrue(tokens is None or tokens > 0, path.name)
            self.assertTrue(record["pairs"], path.name)

    def test_the_command_line_can_demand_a_measured_overhead(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "eval/overhead.py"), "--task", "gcd",
             "--require", "measured"],
            capture_output=True, text=True, timeout=300)
        self.assertIn(result.returncode, (0, 1))
        self.assertIn("status:", result.stderr)
        if result.returncode == 1:
            self.assertIn("--require measured was requested", result.stderr)
        else:
            self.assertIn("tokens", result.stderr)


if __name__ == "__main__":
    unittest.main()
