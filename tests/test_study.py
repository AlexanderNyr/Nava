"""Model-study harness gate (PLAN.md phase N3).

None of these tests calls a model. They check the machinery a real study needs:
that a measurement without a model is labelled as such, that a wrong but
compiling answer fails the correctness gate, that a missing usage counter stays
missing, that repairs are accounted separately from first attempts, and that a
provider error is recorded instead of being mistaken for an answer.
"""
from __future__ import annotations

import hashlib
import json
import os
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
                               "--attempts", "1", "--isolation", "auto", "--out", str(out))
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
                               "--languages", "nava,c", "--attempts", "2",
                               # `auto`, not `required`: a pipeline check has to run on
                               # any host, and the record names the backend that ran.
                               "--isolation", "auto", "--out", str(out))
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
        # Which backend ran is recorded, not assumed.
        self.assertIn(record["plan"]["isolation_backend"], {"namespaces", "rlimits"})
        for attempt in record["attempts"]:
            self.assertEqual(attempt["rounds"][0]["isolation_backend"],
                             record["plan"]["isolation_backend"])
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
                               "--task", "gcd", "--languages", "nava", "--attempts", "1",
                               "--isolation", "auto")
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
                               "--attempts", "1", "--store-raw", "--isolation", "auto",
                               "--out", str(out))
            self.assertEqual(result.returncode, 0, result.stderr)
            record = load(out)
        round_record = record["attempts"][0]["rounds"][0]
        self.assertIn("Task:", round_record["prompt"])
        self.assertIn("fn gcd", round_record["response"]["text"])


class HeldOutSetTests(unittest.TestCase):
    """A hidden set is only hidden if it lives outside the repository."""

    def _task_file(self, directory: Path) -> Path:
        path = directory / "held-out.json"
        path.write_text(json.dumps([{
            "id": "triple_sum",
            "description": "Read three decimal integers and print their sum.",
            "tests": [{"stdin": "1 2 3\n", "stdout": "6\n"},
                      {"stdin": "-5 0 5\n", "stdout": "0\n"}],
        }]), encoding="utf-8")
        return path

    def test_a_held_out_file_inside_the_repository_is_refused(self):
        result = run_study("--plan-only", "--provider", "mock",
                           "--held-out", str(ROOT / "eval" / "tasks.json"))
        self.assertEqual(result.returncode, 2)
        self.assertIn("inside the repository", result.stderr)

    def test_the_plan_records_the_digest_of_every_task_file(self):
        with tempfile.TemporaryDirectory() as temp:
            held_out = self._task_file(Path(temp))
            result = run_study("--plan-only", "--provider", "mock", "--held-out", str(held_out))
            self.assertEqual(result.returncode, 0, result.stderr)
            plan = json.loads(result.stdout)
            expected = hashlib.sha256(held_out.read_bytes()).hexdigest()
        files = {entry["name"]: entry for entry in plan["task_files"]}
        self.assertTrue(files["tasks.json"]["held_out"] is False)
        self.assertEqual(files["held-out set"]["held_out"], True)
        self.assertEqual(files["held-out set"]["tasks"], 1)
        self.assertEqual(files["held-out set"]["sha256"], expected)
        self.assertEqual(len(files["tasks.json"]["sha256"]), 64)

    def test_a_held_out_task_is_evaluated_against_its_own_file(self):
        # The evaluator only knew `eval/tasks.json`, so a held-out task id was
        # rejected before it could be run: the hidden set has to travel with
        # the attempt.
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            held_out = self._task_file(directory)
            source = directory / "answer.nv"
            source.write_text("fn main() {\n    let a = read_i64()\n    let b = read_i64()\n"
                              "    let c = read_i64()\n    print(a + b + c)\n}\n",
                              encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(ROOT / "eval" / "evaluate.py"), "--task", "triple_sum",
                 "--task-file", str(held_out), "--language", "nava", "--source", str(source)],
                capture_output=True, text=True, timeout=300)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            expected = hashlib.sha256(held_out.read_bytes()).hexdigest()
        self.assertTrue(report["summary"]["all_passed"])
        self.assertEqual(report["summary"]["total"], 2)
        self.assertEqual(report["task_file"]["sha256"], expected)

    def test_an_unknown_task_id_lists_what_the_file_holds(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            held_out = self._task_file(directory)
            source = directory / "answer.nv"
            source.write_text("fn main() { print(0) }\n", encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(ROOT / "eval" / "evaluate.py"), "--task", "gcd",
                 "--task-file", str(held_out), "--language", "nava", "--source", str(source)],
                capture_output=True, text=True, timeout=300)
        self.assertEqual(result.returncode, 2)
        self.assertIn("triple_sum", result.stderr)


class RateLimitTests(unittest.TestCase):
    """A gateway limit must not be recorded as a model failure (N3, Groq)."""

    def make_provider(self, responses, **kwargs):
        """A provider whose transport is a scripted list of outcomes."""
        calls = {"count": 0, "requests": []}
        waits: list[float] = []

        class FakeHandle:
            def __init__(self, body):
                self._body = body

            def read(self):
                return self._body

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def fake_urlopen(request, timeout=None):
            calls["count"] += 1
            calls["requests"].append(request)
            outcome = responses[min(calls["count"] - 1, len(responses) - 1)]
            if isinstance(outcome, Exception):
                raise outcome
            return FakeHandle(json.dumps(outcome).encode("utf-8"))

        provider = providers.OpenAICompatibleProvider(
            "https://api.example.invalid/v1", "m", api_key_env="NAVA_TEST_KEY",
            sleep=waits.append, **kwargs)
        original = providers.urllib.request.urlopen
        providers.urllib.request.urlopen = fake_urlopen
        self.addCleanup(setattr, providers.urllib.request, "urlopen", original)
        return provider, calls, waits

    def http_error(self, status, headers=None, message_text="rate limited"):
        import email.message
        import io
        message = email.message.Message()
        for name, value in (headers or {}).items():
            message[name] = value
        body = json.dumps({"error": {"message": message_text}}).encode("utf-8")
        return providers.urllib.error.HTTPError(
            "https://api.example.invalid/v1/chat/completions", status, "error",
            message, io.BytesIO(body))

    def test_seconds_parses_the_shapes_groq_returns(self):
        for text, expected in (("1.5", 1.5), ("690ms", 0.69), ("12.015s", 12.015),
                               ("1m26.4s", 86.4), ("2m", 120.0), ("7", 7.0)):
            with self.subTest(text=text):
                self.assertAlmostEqual(providers._seconds(text), expected, places=6)

    def test_seconds_refuses_to_guess(self):
        for text in ("", "soon", "later today"):
            with self.subTest(text=text):
                self.assertIsNone(providers._seconds(text))

    def test_the_delay_prefers_what_the_server_asked_for(self):
        headers = {"retry-after": "3.5"}
        self.assertEqual(providers._retry_delay(headers, fallback=1.0, cap=300), 3.5)
        self.assertEqual(providers._retry_delay({"retry-after": "999"}, fallback=1.0, cap=30), 30)
        self.assertEqual(providers._retry_delay({}, fallback=2.0, cap=300), 2.0)

    def test_a_rate_limited_call_is_retried_and_succeeds(self):
        ok = {"choices": [{"message": {"content": "fine"}, "finish_reason": "stop"}],
              "usage": {"prompt_tokens": 10, "completion_tokens": 3}, "model": "m"}
        os.environ["NAVA_TEST_KEY"] = "local"
        self.addCleanup(os.environ.pop, "NAVA_TEST_KEY", None)
        provider, calls, waits = self.make_provider(
            [self.http_error(429, {"retry-after": "2"}), self.http_error(429), ok])
        response = provider.complete(providers.Request(prompt="p", model="m"))
        self.assertTrue(response.ok, response.error)
        self.assertEqual(response.text, "fine")
        self.assertEqual(response.attempts, 3)
        self.assertEqual(calls["count"], 3)
        self.assertEqual(waits[0], 2.0, "the server's retry-after is honoured")
        self.assertGreaterEqual(waits[1], 1.0, "then exponential backoff")

    def test_retries_are_bounded_and_then_reported_as_an_error(self):
        os.environ["NAVA_TEST_KEY"] = "local"
        self.addCleanup(os.environ.pop, "NAVA_TEST_KEY", None)
        provider, calls, waits = self.make_provider(
            [self.http_error(429, {"retry-after": "0"})], max_retries=2, max_retry_wait=0)
        response = provider.complete(providers.Request(prompt="p", model="m"))
        self.assertFalse(response.ok)
        self.assertEqual(response.error_class, "HTTPError")
        self.assertEqual(response.attempts, 3, "one call plus two retries")
        self.assertEqual(calls["count"], 3)

    def test_a_permanent_error_is_not_retried(self):
        os.environ["NAVA_TEST_KEY"] = "local"
        self.addCleanup(os.environ.pop, "NAVA_TEST_KEY", None)
        provider, calls, waits = self.make_provider(
            [self.http_error(401), {"choices": []}])
        response = provider.complete(providers.Request(prompt="p", model="m"))
        self.assertEqual(response.error_class, "HTTPError")
        self.assertEqual(calls["count"], 1)
        self.assertEqual(waits, [])

    def test_the_request_carries_a_real_user_agent(self):
        # Groq answers the default `Python-urllib/3.13` with HTTP 403 and
        # Cloudflare's `error code: 1010`; a study must not depend on that.
        ok = {"choices": [{"message": {"content": "x"}, "finish_reason": "stop"}],
              "usage": {"prompt_tokens": 1, "completion_tokens": 1}, "model": "m"}
        os.environ["NAVA_TEST_KEY"] = "local"
        self.addCleanup(os.environ.pop, "NAVA_TEST_KEY", None)
        provider, calls, waits = self.make_provider([ok])
        provider.complete(providers.Request(prompt="p", model="m"))
        self.assertEqual(calls["requests"][0].get_header("User-agent"),
                         providers.DEFAULT_USER_AGENT)
        self.assertNotIn("Python-urllib", providers.DEFAULT_USER_AGENT)

    def test_extra_headers_are_sent_but_only_their_names_are_recorded(self):
        ok = {"choices": [{"message": {"content": "x"}, "finish_reason": "stop"}],
              "usage": {"prompt_tokens": 1, "completion_tokens": 1}, "model": "m"}
        os.environ["NAVA_TEST_KEY"] = "local"
        self.addCleanup(os.environ.pop, "NAVA_TEST_KEY", None)
        provider, calls, waits = self.make_provider(
            [ok], extra_headers={"X-Tenant": "secret-tenant-id"})
        provider.complete(providers.Request(prompt="p", model="m"))
        self.assertEqual(calls["requests"][0].get_header("X-tenant"), "secret-tenant-id")
        described = json.dumps(provider.describe())
        self.assertIn("X-Tenant", described)
        self.assertNotIn("secret-tenant-id", described)

    def test_a_daily_quota_is_not_retried(self):
        # Groq: "Rate limit reached ... on tokens per day (TPD): Limit 200000",
        # with `x-should-retry: false`. The reset is hours away; retrying only
        # converts every attempt into a provider error after a long stall.
        os.environ["NAVA_TEST_KEY"] = "local"
        self.addCleanup(os.environ.pop, "NAVA_TEST_KEY", None)
        daily = ("Rate limit reached for model x on tokens per day (TPD): "
                 "Limit 200000, Used 198689. Please try again in 3m58s.")
        provider, calls, waits = self.make_provider(
            [self.http_error(429, {"retry-after": "239", "x-should-retry": "false"}, daily)],
            max_retries=8)
        response = provider.complete(providers.Request(prompt="p", model="m"))
        self.assertEqual(calls["count"], 1, "a daily cap must be reported at once")
        self.assertEqual(waits, [])
        self.assertEqual(response.error_class, "DailyQuotaExceeded")
        self.assertIn("tokens per day", response.error)

    def test_a_daily_limit_is_recognised_from_the_message_alone(self):
        import email.message
        message = email.message.Message()
        message["retry-after"] = "239"
        self.assertEqual(providers._blocking_class(
            "Rate limit reached for model x on tokens per day (TPD): Limit 200000", message),
            "DailyQuotaExceeded")
        self.assertIsNone(providers._blocking_class("anything", {"x-should-retry": "false"}),
                          "x-should-retry alone is not evidence of a daily cap")
        self.assertIsNone(providers._blocking_class("rate limited", {"retry-after": "3"}))

    def test_a_requests_own_size_is_not_a_daily_quota(self):
        # Groq: "Request too large ... on output tokens per minute (OTPM):
        # Limit 1000, Requested 1200" -- retrying cannot help, but calling it a
        # daily quota would be a lie about the cause.
        import email.message
        message = email.message.Message()
        message["x-should-retry"] = "false"
        detail = ("Rate limit reached for model `qwen/qwen3.8-27b` ... Request too large "
                  "on output tokens per minute (OTPM): Limit 1000, Requested 1200. The "
                  "request's expected output tokens exceed the enforced limit; reduce "
                  "max_tokens (or the request's expected output) and try again.")
        self.assertEqual(providers._blocking_class(detail, message), "RequestTooLarge")

    def test_a_transient_output_limit_is_retried_not_blocked(self):
        # Groq answers "Limit 1000, Used 285, Requested 900. Please try again in
        # 11.1s" with `x-should-retry: false`, and it *is* worth retrying.
        import email.message
        message = email.message.Message()
        message["x-should-retry"] = "false"
        detail = ('{"error":{"message":"Rate limit reached for model `qwen/qwen3.8-27b` ... '
                  'on output tokens per minute (OTPM): Limit 1000, Used 285, Requested 900. '
                  'Please try again in 11.1s."}}')
        self.assertIsNone(providers._blocking_class(detail, message))


class HeaderOptionTests(unittest.TestCase):
    def test_headers_are_parsed_in_both_shapes(self):
        headers, problem = study._parse_headers(["X-One: 1", "X-Two=2"])
        self.assertIsNone(problem)
        self.assertEqual(headers, {"X-One": "1", "X-Two": "2"})

    def test_a_malformed_header_is_refused_before_anything_is_called(self):
        for item in ("X-One", ": 1", "X-One: "):
            with self.subTest(item=item):
                headers, problem = study._parse_headers([item])
                self.assertEqual(headers, {})
                self.assertIn("Name: value", problem)

    def test_the_key_cannot_be_smuggled_through_a_header(self):
        headers, problem = study._parse_headers(["Authorization: Bearer other-key"])
        self.assertEqual(headers, {})
        self.assertIn("--api-key-env", problem)


class IsolationRefusalTests(unittest.TestCase):
    """A setup that cannot run must fail loudly, not produce a table of zeros."""

    def test_required_isolation_is_refused_before_anything_is_measured(self):
        with tempfile.TemporaryDirectory() as temp:
            # `unshare` exists here and refuses, which is what a restricted host
            # (AppArmor, seccomp, a container) actually looks like.
            empty = Path(temp) / "empty-bin"
            empty.mkdir()
            script = empty / "unshare"
            script.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            script.chmod(0o755)
            out = Path(temp) / "study.json"
            environment = {"PATH": f"{empty}:{os.environ.get('PATH', '/usr/bin:/bin')}",
                           "LANG": "C.UTF-8", "HOME": temp}
            result = subprocess.run(
                [sys.executable, str(STUDY), "--provider", "mock", "--task", "gcd",
                 "--languages", "nava", "--attempts", "1", "--isolation", "required",
                 "--out", str(out)],
                capture_output=True, text=True, env=environment, timeout=300)
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertIn("needs Linux namespaces", result.stderr)
            # The message must say what to do instead.
            self.assertIn("--isolation auto", result.stderr)
            self.assertFalse(out.exists(), "a refused run must not leave a record behind")

    def test_an_evaluator_refusal_is_an_error_not_a_wrong_answer(self):
        # `evaluate.py` exits 2 with an `error` field when it refuses to run.
        # Treating that as "0 of N tests passed" is how a broken setup turns into
        # a plausible-looking model result.
        refusal = {"error": "isolation mode 'required' needs Linux namespaces",
                   "isolation": {"mode": "required", "backend": None, "available": False},
                   "summary": {"passed": 0, "total": 7, "all_passed": False}}
        original = study.run_evaluator
        study.run_evaluator = lambda *a, **k: dict(refusal)
        self.addCleanup(setattr, study, "run_evaluator", original)
        with tempfile.TemporaryDirectory() as temp:
            provider = providers.MockProvider(["fn main() { print(0) }\n"])
            parts = build_parts("gcd", "nava")
            record = study.attempt_once(
                task_id="gcd", language="nava", attempt_index=0, provider=provider,
                request_template={"prompt": study.prompt_module.build_prompt("gcd", "nava"),
                                  "model": None, "temperature": 0.0, "max_tokens": 64,
                                  "stream": False},
                parts=parts, system=None, repair_rounds=0, isolation="required",
                timeout=5.0, memory=256, store_raw=False, workdir=Path(temp))
        self.assertFalse(record["final_passed"])
        self.assertIsNotNone(record["provider_error"])
        self.assertEqual(record["provider_error"]["class"], "EvaluatorError")
        self.assertIn("namespaces", record["provider_error"]["message"])

if __name__ == "__main__":
    unittest.main()
