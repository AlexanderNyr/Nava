"""Model-study harness gate (PLAN.md phase N3).

None of these tests calls a model. They check the machinery a real study needs:
that a measurement without a model is labelled as such, that a wrong but
compiling answer fails the correctness gate, that a missing usage counter stays
missing, that repairs are accounted separately from first attempts, and that a
provider error is recorded instead of being mistaken for an answer.
"""
from __future__ import annotations

import json
import statistics
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval import providers, study  # noqa: E402
from eval.extraction import extract_code  # noqa: E402
from eval.prompt import GUIDES, build_parts  # noqa: E402

STUDY = ROOT / "eval/study.py"


def run_study(*arguments, timeout=600):
    return subprocess.run([sys.executable, str(STUDY), *arguments],
                          capture_output=True, text=True, timeout=timeout)


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class ClassificationTests(unittest.TestCase):
    def test_thresholds_match_the_plan(self):
        small = study.classify_scale(3, 5)
        self.assertEqual(small["label"], "exploratory")
        self.assertEqual(len(small["reasons"]), 2)
        self.assertEqual(small["thresholds"], {"tasks": 10, "attempts_per_task_language": 30})

    def test_full_study_is_empirical(self):
        self.assertEqual(study.classify_scale(10, 30)["label"], "empirical")
        self.assertEqual(study.classify_scale(12, 31)["label"], "empirical")

    def test_one_missing_threshold_keeps_it_exploratory(self):
        self.assertEqual(study.classify_scale(10, 29)["label"], "exploratory")
        self.assertEqual(study.classify_scale(9, 30)["label"], "exploratory")


class AggregateTests(unittest.TestCase):
    def make(self, passed_first, passed_final=None, wall=None, tokens=None):
        passed_final = passed_first if passed_final is None else passed_final
        return {
            "first_attempt_passed": passed_first,
            "final_passed": passed_final,
            "repaired": passed_final and not passed_first,
            "total_wall_ms": wall,
            "prompt_tokens": tokens,
            "completion_tokens": tokens,
            "time_to_correct_ms": wall if passed_final else None,
            "rounds_used": 1,
            "provider_error": None,
            "rounds": [{"compile_ok": True, "ttft_ms": None}],
        }

    def test_missing_measurements_are_null_not_zero(self):
        entry = study.aggregate([self.make(True), self.make(False)])
        self.assertEqual(entry["pass_at_1"]["point"], 0.5)
        self.assertIsNone(entry["median_wall_ms"])
        self.assertIsNone(entry["median_completion_tokens"])
        self.assertEqual(entry["tokens_reported"],
                         {"prompt": 0, "completion": 0, "of_attempts": 2})

    def test_reported_values_are_summarized(self):
        entry = study.aggregate([self.make(True, True, 100.0, 50), self.make(True, True, 300.0, 70)])
        self.assertEqual(entry["median_wall_ms"], 200.0)
        self.assertEqual(entry["median_completion_tokens"], 60.0)
        self.assertEqual(entry["tokens_reported"]["of_attempts"], 2)

    def test_bootstrap_is_deterministic_and_contains_the_point(self):
        values = [True, False, True, True, False]
        first = study.bootstrap_ci(values)
        second = study.bootstrap_ci(values)
        self.assertEqual(first, second)
        self.assertLessEqual(first["low"], first["point"])
        self.assertLessEqual(first["point"], first["high"])
        self.assertEqual(first["resamples"], study.BOOTSTRAP_RESAMPLES)

    def test_empty_input_yields_nulls(self):
        entry = study.aggregate([])
        self.assertEqual(entry, {"attempts": 0})


class ProviderTests(unittest.TestCase):
    def test_mock_does_not_invent_timings_or_tokens(self):
        provider = providers.MockProvider(["answer"])
        response = provider.complete(providers.Request(prompt="p"))
        self.assertEqual(response.text, "answer")
        self.assertIsNone(response.wall_ms)
        self.assertIsNone(response.prompt_tokens)
        report = response.report()
        self.assertFalse(report["prompt_tokens_reported"])
        self.assertTrue(report["ok"])

    def test_openai_provider_without_a_key_does_not_call_out(self):
        provider = providers.OpenAICompatibleProvider("http://127.0.0.1:9/v1", "test-model",
                                                      api_key_env="NAVA_TEST_MISSING_KEY")
        response = provider.complete(providers.Request(prompt="p", model="test-model"))
        self.assertFalse(response.ok)
        self.assertEqual(response.error_class, "ProviderError")
        self.assertIn("NAVA_TEST_MISSING_KEY", response.error)
        self.assertNotIn("Authorization", json.dumps(provider.describe()))

    def test_credentials_are_redacted_from_errors(self):
        headers = {"Authorization": "Bearer super-secret-token"}
        self.assertEqual(providers._redact("failed: super-secret-token rejected", headers),
                         "failed: [redacted] rejected")

    def test_replay_provider_matches_by_prompt_hash(self):
        request = providers.Request(prompt="hello", system="sys")
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "answers.jsonl"
            path.write_text(json.dumps({"prompt_sha256": request.prompt_sha256,
                                        "response": "```nava\nfn main() {}\n```",
                                        "prompt_tokens": 11, "completion_tokens": 5}) + "\n",
                            encoding="utf-8")
            provider = providers.ReplayProvider(path)
            hit = provider.complete(request)
            miss = provider.complete(providers.Request(prompt="other"))
        self.assertEqual(hit.text, "```nava\nfn main() {}\n```")
        self.assertEqual((hit.prompt_tokens, hit.completion_tokens), (11, 5))
        self.assertFalse(miss.ok)
        self.assertEqual(miss.error_class, "ReplayMiss")


class ExtractionTests(unittest.TestCase):
    def test_tagged_fence_wins(self):
        code, provenance = extract_code("```nava\nfn main() {}\n```\n```c\nint main(void){}\n```", "nava")
        self.assertIn("fn main", code)
        self.assertEqual(provenance["method"], "tagged-fence")

    def test_untagged_fence_is_used(self):
        code, provenance = extract_code("Here:\n```\nfn main() {}\n```", "nava")
        self.assertEqual(code.strip(), "fn main() {}")
        self.assertEqual(provenance["method"], "untagged-fence")

    def test_no_fence_uses_the_whole_text(self):
        code, provenance = extract_code("fn main() { print(1) }", "nava")
        self.assertEqual(provenance["method"], "whole-text")

    def test_foreign_fence_falls_back_without_editing(self):
        code, provenance = extract_code("```python\nprint(1)\n```", "nava")
        self.assertEqual(provenance["method"], "whole-text-fallback")
        self.assertIn("```python", code)


class RecordValidationTests(unittest.TestCase):
    """`eval/check_study_record.py` must reject a record that overstates itself."""

    def run_validator(self, record: dict, *arguments):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "record.json"
            path.write_text(json.dumps(record), encoding="utf-8")
            return subprocess.run(
                [sys.executable, str(ROOT / "eval/check_study_record.py"), str(path), *arguments],
                capture_output=True, text=True, timeout=120)

    def build_record(self) -> dict:
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "study.json"
            result = run_study("--provider", "mock", "--task", "gcd", "--languages", "nava",
                               "--attempts", "1", "--out", str(out))
            self.assertEqual(result.returncode, 0, result.stderr)
            return load(out)

    def test_a_genuine_record_passes(self):
        result = self.run_validator(self.build_record(), "--require", "not-measured")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_mock_labelled_measured_is_rejected(self):
        record = self.build_record()
        record["model_measurements"]["status"] = "measured"
        result = self.run_validator(record)
        self.assertEqual(result.returncode, 1)
        self.assertIn("mock run must not be labelled measured", result.stderr)

    def test_empirical_label_below_the_thresholds_is_rejected(self):
        record = self.build_record()
        record["scale"] = {"label": "empirical", "reasons": [],
                           "thresholds": {"tasks": 10, "attempts_per_task_language": 30}}
        result = self.run_validator(record)
        self.assertEqual(result.returncode, 1)
        self.assertIn("labelled empirical with only 1 tasks", result.stderr)

    def test_measured_without_evaluated_attempts_is_rejected(self):
        record = self.build_record()
        record["provider"]["provider"] = "openai-compatible"
        record["model_measurements"]["status"] = "measured"
        record["model_measurements"]["evaluated_attempts"] = 0
        result = self.run_validator(record)
        self.assertEqual(result.returncode, 1)
        self.assertIn("zero evaluated attempts", result.stderr)

    def test_require_status_is_enforced(self):
        result = self.run_validator(self.build_record(), "--require", "measured")
        self.assertEqual(result.returncode, 1)
        self.assertIn("--require measured was requested", result.stderr)


class StudyRunTests(unittest.TestCase):
    def test_end_to_end_mock_study_is_labeled_as_not_measured(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "study.json"
            result = run_study("--provider", "mock", "--task", "gcd", "--task", "sum_to_n",
                               "--languages", "nava,c", "--attempts", "2", "--out", str(out))
            self.assertEqual(result.returncode, 0, result.stderr)
            record = load(out)
        self.assertEqual(record["schema"], "nava-study/1")
        self.assertEqual(record["scale"]["label"], "exploratory")
        self.assertEqual(record["model_measurements"]["status"], "not-measured")
        self.assertIn("does not call a model", " ".join(record["notes"]))
        # gcd is answered correctly, sum_to_n with a compiling but wrong program.
        self.assertAlmostEqual(record["aggregates"]["gcd/nava"]["pass_at_1"]["point"], 1.0)
        self.assertAlmostEqual(record["aggregates"]["sum_to_n/nava"]["pass_at_1"]["point"], 0.0)
        self.assertEqual(record["aggregates"]["sum_to_n/nava"]["compile_rate"]["point"], 1.0)
        self.assertEqual(len(record["attempts"]), 8, "2 tasks x 2 languages x 2 attempts")
        for attempt in record["attempts"]:
            self.assertIsNotNone(attempt["rounds"][0]["isolation_backend"])
            self.assertEqual(attempt["rounds"][0]["extraction"]["method"], "tagged-fence")
        # Order is rotated per attempt so no language is always first:
        # each attempt block holds one entry per task and language.
        block = 2 * 2
        first_attempt_languages = [a["language"] for a in record["attempts"][:block]]
        second_attempt_languages = [a["language"] for a in record["attempts"][block:2 * block]]
        self.assertNotEqual(first_attempt_languages[0], second_attempt_languages[0])
        self.assertEqual(set(first_attempt_languages), {"nava", "c"})

    def test_plan_only_does_not_call_anything(self):
        result = run_study("--plan-only", "--provider", "mock", "--task", "gcd",
                           "--languages", "nava", "--attempts", "3")
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(result.stdout)
        self.assertEqual(plan["attempts_per_task_language"], 3)
        self.assertEqual(plan["expected_scale"]["label"], "exploratory")
        self.assertEqual(plan["request"]["guide_included"], True)

    def test_repairs_are_counted_separately_from_first_attempts(self):
        wrong = "```nava\nfn main() { print(0) }\n```\n"
        right = "```nava\nfn gcd(a: i64, b: i64) -> i64 {\n    while b != 0 {\n        let r = a % b\n        a = b\n        b = r\n    }\n    return a\n}\nfn main() { print(gcd(read_i64(), read_i64())) }\n```\n"
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "study.json"
            result = run_study("--provider", "mock", "--task", "gcd", "--languages", "nava",
                               "--attempts", "1", "--repair-rounds", "1", "--out", str(out),
                               "--mock-script-unused")
            self.assertNotEqual(result.returncode, 0, "an unknown flag must be rejected")
        # The harness itself has no --mock-script flag; drive the accounting directly.
        provider = providers.MockProvider([wrong, right])
        parts = build_parts("gcd", "nava")
        with tempfile.TemporaryDirectory() as temp:
            attempt = study.attempt_once(
                task_id="gcd", language="nava", attempt_index=0, provider=provider,
                request_template={"prompt": study.prompt_module.build_prompt("gcd", "nava")},
                parts=parts, system=None, repair_rounds=1, isolation="auto",
                timeout=2.0, memory=256, store_raw=False, workdir=Path(temp),
            )
        self.assertFalse(attempt["first_attempt_passed"])
        self.assertTrue(attempt["final_passed"])
        self.assertTrue(attempt["repaired"])
        self.assertEqual(attempt["rounds_used"], 2)
        self.assertEqual(len(attempt["rounds"]), 2)
        # `print(0)` is wrong but not maximally wrong: gcd(0,0) is 0, so it passes
        # exactly one case. The gate is that it does not pass the suite.
        self.assertLess(attempt["rounds"][0]["tests_passed"], attempt["rounds"][0]["tests_total"])
        self.assertFalse(attempt["rounds"][0]["passed"])
        self.assertEqual(attempt["rounds"][1]["tests_passed"], attempt["rounds"][1]["tests_total"])
        # The repair prompt carries the guide and the task again, plus diagnostics.
        self.assertIn("Your previous answer failed", provider.calls[1].prompt)
        self.assertIn(GUIDES["nava"].strip(), provider.calls[1].prompt)

    def test_provider_error_is_recorded_and_not_mistaken_for_an_answer(self):
        failing = providers.MockProvider([providers.Response(error="quota exceeded",
                                                             error_class="HTTPError")])
        parts = build_parts("gcd", "nava")
        with tempfile.TemporaryDirectory() as temp:
            attempt = study.attempt_once(
                task_id="gcd", language="nava", attempt_index=0, provider=failing,
                request_template={"prompt": study.prompt_module.build_prompt("gcd", "nava")},
                parts=parts, system=None, repair_rounds=1, isolation="auto",
                timeout=2.0, memory=256, store_raw=False, workdir=Path(temp),
            )
        self.assertEqual(attempt["provider_error"]["class"], "HTTPError")
        self.assertFalse(attempt["final_passed"])
        self.assertEqual(len(attempt["rounds"]), 1, "a provider error must not trigger repairs")
        self.assertIsNone(attempt["time_to_correct_ms"])

    def test_study_exit_code_reports_provider_errors(self):
        with tempfile.TemporaryDirectory() as temp:
            answers = Path(temp) / "answers.jsonl"
            answers.write_text("", encoding="utf-8")   # every lookup misses
            result = run_study("--provider", "replay", "--replay-file", str(answers),
                               "--task", "gcd", "--languages", "nava", "--attempts", "1")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        record = json.loads(result.stdout) if result.stdout.startswith("{") else None
        self.assertIsNone(record, "the summary is human-readable by default")

    def test_model_status_is_not_measured_when_every_call_failed(self):
        """A run whose provider calls all failed must not present itself as measured."""
        environment = {"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8"}
        result = subprocess.run(
            [sys.executable, str(STUDY), "--provider", "openai", "--base-url", "http://127.0.0.1:9/v1",
             "--model", "demo", "--api-key-env", "NAVA_TEST_MISSING_KEY", "--task", "gcd",
             "--languages", "nava", "--attempts", "1", "--out", "/dev/stdout", "--quiet"],
            capture_output=True, text=True, timeout=300, env=environment,
        )
        self.assertEqual(result.returncode, 1)
        record = json.loads(result.stdout)
        self.assertEqual(record["model_measurements"]["status"], "not-measured")
        self.assertEqual(record["model_measurements"]["evaluated_attempts"], 0)
        self.assertIn("incomplete", record["model_measurements"]["reason"])
        self.assertEqual(record["attempts"][0]["provider_error"]["class"], "ProviderError")

    def test_store_raw_keeps_prompts_and_answers(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "study.json"
            result = run_study("--provider", "mock", "--task", "gcd", "--languages", "nava",
                               "--attempts", "1", "--store-raw", "--out", str(out))
            self.assertEqual(result.returncode, 0, result.stderr)
            record = load(out)
        round_record = record["attempts"][0]["rounds"][0]
        self.assertIn("Task:", round_record["prompt"])
        self.assertIn("fn gcd", round_record["response"]["text"])


if __name__ == "__main__":
    unittest.main()
