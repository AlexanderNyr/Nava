"""A/B harness and structured repair feedback (PLAN.md phase N7).

No test here calls a model. What is tested is the accounting: that the two arms
differ only in the request payload, that a record without a model says
`not-measured` and cannot carry a claim, that a tampered record is refused, and
that repair rounds are fed one structured document whose stage (`parse`,
`compile`, `tests`) is recorded separately from the first attempt.
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

from eval import feedback, providers, study  # noqa: E402
from eval.prompt import build_parts  # noqa: E402

AB = ROOT / "eval/ab.py"
CHECK = ROOT / "eval/check_ab_record.py"


def run_ab(*arguments, timeout=1200):
    return subprocess.run([sys.executable, str(AB), *arguments],
                          capture_output=True, text=True, timeout=timeout)


def run_check(path: Path, *arguments):
    return subprocess.run([sys.executable, str(CHECK), str(path), *arguments],
                          capture_output=True, text=True, timeout=120)


class FeedbackTests(unittest.TestCase):
    def test_a_syntax_error_is_reported_as_the_parse_stage(self):
        document, summary, text = feedback.build("fn main( {\n")
        self.assertEqual(document["stage"], "parse")
        self.assertIn("did not parse", summary)
        self.assertTrue(document["diagnostics"][0]["code"].startswith(("E01", "E02")))
        self.assertIn("nava-feedback/1", text)

    def test_a_type_error_carries_the_code_and_the_hint(self):
        document, _, text = feedback.build("fn main() {\n    let x: bool = 1\n}\n")
        self.assertEqual(document["stage"], "compile")
        diagnostic = document["diagnostics"][0]
        self.assertEqual(diagnostic["code"], "E0311")
        self.assertEqual((diagnostic["line"], diagnostic["column"]), (2, 9))
        self.assertTrue(diagnostic["hint"])
        self.assertEqual(json.loads(text)["diagnostics"][0]["code"], "E0311")

    def test_a_wrong_answer_is_reported_as_the_tests_stage(self):
        report = {"compile": {"ok": True}, "tests": [
            {"case": 1, "passed": True, "stdin": "48 18\n",
             "expected_stdout": "6\n", "actual_stdout": "6\n"},
            {"case": 2, "passed": False, "stdin": "0 31\n",
             "expected_stdout": "31\n", "actual_stdout": "0\n",
             "exit_code": 0, "reason": "wrong output"},
        ]}
        document, summary, text = feedback.build("fn main() { print(0) }\n", report)
        self.assertEqual(document["stage"], "tests")
        self.assertIn("failed 1 of 2 tests", summary)
        case = document["failures"][0]
        self.assertEqual((case["case"], case["expected"], case["actual"]),
                         (2, "31\n", "0\n"))
        self.assertEqual(json.loads(text)["failures"][0]["case"], 2)

    def test_feedback_is_capped_and_dropped_items_are_counted(self):
        report = {"compile": {"ok": True}, "tests": [
            {"case": index, "passed": False, "stdin": f"{index}\n",
             "expected_stdout": "x" * 400, "actual_stdout": "y" * 400,
             "exit_code": 0, "reason": "wrong output"} for index in range(1, 6)]}
        document, _, text = feedback.build("fn main() { print(0) }\n", report, limit=600)
        self.assertLessEqual(len(text.encode("utf-8")), 600)
        self.assertGreater(document["elided"], 0)

    def test_the_metadata_identifies_the_feedback_without_repeating_it(self):
        document, _, text = feedback.build("fn main() {\n    let x: bool = 1\n}\n")
        described = feedback.describe(document, text)
        self.assertEqual(described["schema"], "nava-feedback/1")
        self.assertEqual(described["codes"], ["E0311"])
        self.assertEqual(described["bytes"], len(text.encode("utf-8")))
        self.assertEqual(len(described["sha256"]), 64)


class RepairAccountingTests(unittest.TestCase):
    """A repaired answer is not a first-attempt success, in either feedback mode."""

    WRONG = "```nava\nfn main() { print(0) }\n```\n"
    RIGHT = (
        "```nava\nfn gcd(a: i64, b: i64) -> i64 {\n    while b != 0 {\n"
        "        let r = a % b\n        a = b\n        b = r\n    }\n    return a\n}\n\n"
        "fn main() {\n    print(gcd(read_i64(), read_i64()))\n}\n```\n")

    def attempt(self, mode: str) -> tuple[dict, providers.MockProvider]:
        provider = providers.MockProvider([self.WRONG, self.RIGHT])
        parts = build_parts("gcd", "nava")
        with tempfile.TemporaryDirectory() as temp:
            attempt = study.attempt_once(
                task_id="gcd", language="nava", attempt_index=0, provider=provider,
                request_template={"prompt": study.prompt_module.build_prompt("gcd", "nava")},
                parts=parts, system=None, repair_rounds=1, isolation="auto",
                timeout=2.0, memory=256, store_raw=False, workdir=Path(temp),
                feedback_mode=mode,
            )
        return attempt, provider

    def test_structured_feedback_keeps_first_attempt_and_repair_apart(self):
        attempt, provider = self.attempt("structured")
        self.assertFalse(attempt["first_attempt_passed"])
        self.assertTrue(attempt["final_passed"])
        self.assertTrue(attempt["repaired"])
        self.assertEqual(attempt["rounds_used"], 2)
        self.assertEqual(attempt["rounds"][0]["feedback"]["stage"], "tests")
        self.assertEqual(attempt["rounds"][0]["feedback"]["schema"], "nava-feedback/1")
        self.assertIsNone(attempt["rounds"][1]["feedback"])
        # The repair prompt carries the structured document, not raw stderr.
        self.assertIn("nava-feedback/1", provider.calls[1].prompt)
        self.assertIn("Your previous answer failed", provider.calls[1].prompt)

    def test_text_feedback_still_works_and_is_marked_as_such(self):
        attempt, provider = self.attempt("text")
        self.assertTrue(attempt["repaired"])
        self.assertEqual(attempt["rounds"][0]["feedback"]["schema"], "text")
        self.assertIn("Diagnostics:", provider.calls[1].prompt)

    def test_a_missing_measurement_is_never_zero(self):
        attempt, _ = self.attempt("structured")
        self.assertIsNone(attempt["prompt_tokens"])
        self.assertIsNone(attempt["completion_tokens"])
        self.assertIsNone(attempt["time_to_correct_ms"])


class ABRecordTests(unittest.TestCase):
    """One real (mock) run is shared; the validator cases mutate its record."""

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.path = Path(cls.temp.name) / "ab.json"
        result = run_ab("--provider", "mock", "--task", "gcd", "--attempts", "1",
                        "--isolation", "auto", "--out", str(cls.path))
        assert result.returncode == 0, result.stderr
        cls.record = json.loads(cls.path.read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_the_record_passes_its_own_validator(self):
        result = run_check(self.path, "--require", "not-measured")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_arms_differ_only_in_the_request_payload(self):
        arms = self.record["arms"]
        self.assertEqual(arms["free"]["request_fields"], [])
        self.assertEqual(len(arms["constrained"]["request_fields"]), 1)
        prompts = lambda arm: [round_record["prompt_sha256"]
                               for attempt in arms[arm]["attempts"]
                               for round_record in attempt["rounds"]]
        self.assertEqual(prompts("free"), prompts("constrained"))
        self.assertNotEqual(arms["free"]["attempts"][0]["rounds"][0]["prompt_sha256"], "")

    def test_no_claim_is_made_by_the_harness(self):
        comparison = self.record["comparison"]
        self.assertIsNone(comparison["claim"])
        self.assertEqual(comparison["claim_status"], "open")
        self.assertEqual(self.record["status"], "not-measured")
        self.assertTrue(self.record["claim_open"])
        self.assertIn("does not call a model", self.record["status_reason"])
        self.assertTrue(any("does not call a model" in note for note in self.record["notes"]))

    def test_a_missing_token_count_is_null_with_a_reason(self):
        guide = self.record["guidance"]["guide"]
        if guide["tokens"] is None:
            self.assertEqual(self.record["guidance"]["tokens_status"], "not-measured")
            self.assertTrue(self.record["guidance"]["tokens_reason"])
        else:
            self.assertGreater(guide["tokens"], 0)

    def test_the_grammar_and_guide_digests_are_recorded(self):
        grammar_meta = self.record["guidance"]["grammar"]
        self.assertEqual(len(grammar_meta["sha256"]), 64)
        self.assertGreater(grammar_meta["bytes"], 0)
        self.assertEqual(grammar_meta["interface"], "llamacpp")
        self.assertEqual(len(self.record["guidance"]["guide"]["sha256"]), 64)

    def mutated(self, mutate) -> Path:
        record = json.loads(json.dumps(self.record))
        mutate(record)
        path = Path(self.temp.name) / "mutated.json"
        path.write_text(json.dumps(record), encoding="utf-8")
        return path

    def test_tampering_is_refused(self):
        def measured(record):
            record["status"] = "measured"

        def claimed(record):
            record["comparison"]["claim"] = "constrained decoding is better"

        def different_prompt(record):
            record["arms"]["constrained"]["attempts"][0]["rounds"][0]["prompt_sha256"] = "x"

        def zero_tokens(record):
            record["guidance"]["guide"]["tokens"] = 0

        def free_arm_sent_a_grammar(record):
            record["arms"]["free"]["request_fields"] = ["grammar"]

        def wrong_aggregate(record):
            record["arms"]["free"]["aggregates"]["gcd/nava"]["pass_at_1"]["point"] = 0.5

        def fewer_attempts(record):
            record["arms"]["constrained"]["attempts"] = []

        cases = {
            "status flipped to measured": measured,
            "claim without a measured run": claimed,
            "prompts differ between arms": different_prompt,
            "token count zero": zero_tokens,
            "free arm sent a grammar": free_arm_sent_a_grammar,
            "aggregate does not match": wrong_aggregate,
            "arms cover different work": fewer_attempts,
        }
        for label, mutate in cases.items():
            with self.subTest(case=label):
                result = run_check(self.mutated(mutate))
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn("ab record:", result.stderr)

    def test_require_measured_is_enforced(self):
        result = run_check(self.path, "--require", "measured")
        self.assertEqual(result.returncode, 1)
        self.assertIn("--require measured was requested", result.stderr)

    def test_plan_only_announces_both_arms_and_calls_nothing(self):
        result = run_ab("--plan-only", "--provider", "mock", "--task", "gcd", "--attempts", "3")
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(result.stdout)
        self.assertEqual(plan["arms"], ["free", "constrained"])
        self.assertEqual(plan["attempts_per_task"], 3)
        self.assertIn("prompts are identical", plan["difference_between_arms"])

    def test_a_run_keeps_repairs_out_of_pass_at_1(self):
        """The mock answers `sum_to_n` with a wrong program, in both arms.

        The repair round is recorded with its structured feedback, and neither
        `pass_at_1` nor `final_pass` picks up the repaired attempt: a repaired
        answer is never counted as a first-attempt success.
        """
        path = Path(self.temp.name) / "repair.json"
        result = run_ab("--provider", "mock", "--task", "sum_to_n", "--attempts", "1",
                        "--repair-rounds", "1", "--isolation", "auto", "--out", str(path))
        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads(path.read_text(encoding="utf-8"))
        for arm in ("free", "constrained"):
            attempt = record["arms"][arm]["attempts"][0]
            self.assertFalse(attempt["first_attempt_passed"])
            self.assertFalse(attempt["final_passed"])
            self.assertFalse(attempt["repaired"])
            self.assertEqual(len(attempt["rounds"]), 2)
            self.assertEqual(attempt["rounds"][0]["feedback"]["stage"], "tests")
            self.assertEqual(attempt["rounds"][0]["feedback"]["schema"], "nava-feedback/1")
            bucket = record["arms"][arm]["aggregates"]["sum_to_n/nava"]
            self.assertEqual(bucket["pass_at_1"]["point"], 0.0)
            self.assertEqual(bucket["final_pass"]["point"], 0.0)


if __name__ == "__main__":
    unittest.main()
