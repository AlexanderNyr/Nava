#!/usr/bin/env python3
"""`bench/compare.py`: turning two profile records into one classified claim.

The whole point of this module is that it can say "no". A tool that reports
every difference as an improvement would make the N8 receipts worthless, so the
tests below feed it known shapes — a clear win, a clear loss, a difference
inside the noise, a difference too small to matter, a phase that one side never
measured — and require the matching verdict. Sizes are exact counts and are
compared exactly; a missing phase is `missing`, never zero.
"""
from __future__ import annotations

import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from bench import compare

SCHEMA = "nava-profile/1"


def stage(median, stdev=0.1, samples=5):
    if median is None:
        return {"samples": [], "median_ms": None, "min_ms": None, "stdev_ms": None}
    return {"samples": [median] * samples, "median_ms": median,
            "min_ms": median, "stdev_ms": stdev}


def workload(name="loop", **stages):
    entries = {
        "frontend_total": stage(1.0), "lex": stage(0.2), "parse": stage(0.5),
        "check": stage(0.1), "emit": stage(0.1),
    }
    entries.update(stages)
    return {
        "workload": name,
        "source": "bench/loop.nv",
        "note": "",
        "source_bytes": 172,
        "stages_ms": entries,
        "emitted_c_bytes": 23442,
        "runtime_units": {"variant": "full", "selective": True, "units": 50,
                          "runtime_bytes": 22675, "emitted_bytes": 23442},
        "processes_ms": {"cli_emit_c": stage(80.0), "cc": stage(70.0),
                         "binary_startup": stage(1.3), "run": stage(27.0)},
        "binary_bytes": 16352,
        "output": "ok\n",
        "failure": None,
    }


def record(**overrides):
    base = {
        "schema": SCHEMA,
        "label": "synthetic",
        "runtime": "selected",
        "environment": {"recorded_at_utc": "2026-10-27T00:00:00+00:00",
                        "platform": "Linux-test", "cc_version": "cc 14.2.0",
                        "cc_flags": ["-std=c11", "-O2"]},
        "configuration": {"opt": "O2", "stage_samples": 15, "process_samples": 5},
        "processes": {"python_startup": stage(14.0), "import_and_cli": stage(50.0)},
        "workloads": [workload()],
        "failures": [],
    }
    base.update(overrides)
    return base


class ClassifyTests(unittest.TestCase):
    def test_a_clear_improvement(self):
        entry = compare.classify(10.0, 5.0, 0.2, 0.2, sigma=2.0, floor=0.05)
        self.assertEqual(entry["verdict"], "improved")
        self.assertEqual(entry["delta_ms"], -5.0)
        self.assertEqual(entry["relative"], -0.5)

    def test_a_clear_regression(self):
        entry = compare.classify(10.0, 12.0, 0.2, 0.3, sigma=2.0, floor=0.05)
        self.assertEqual(entry["verdict"], "regressed")
        self.assertEqual(entry["delta_over_spread"], round(2.0 / 0.3, 3))

    def test_a_difference_inside_the_noise_is_inconclusive(self):
        entry = compare.classify(10.0, 10.4, 0.5, 0.4, sigma=2.0, floor=0.05)
        self.assertEqual(entry["verdict"], "inconclusive")

    def test_a_meaningless_difference_is_inconclusive_even_when_exact(self):
        # 0.04 ms is far outside the (tiny) spread but only 0.4% of the median.
        entry = compare.classify(10.0, 10.04, 0.001, 0.001, sigma=2.0, floor=0.05)
        self.assertEqual(entry["verdict"], "inconclusive")

    def test_a_missing_side_is_missing_not_zero(self):
        entry = compare.classify(10.0, None, 0.1, 0.1, sigma=2.0, floor=0.05)
        self.assertEqual(entry["verdict"], "missing")
        self.assertIsNone(entry["delta_ms"])
        self.assertIsNone(entry["relative"])
        self.assertIsNone(compare.classify(None, 10.0, 0.1, 0.1,
                                           sigma=2.0, floor=0.05)["delta_ms"])

    def test_the_sigma_and_floor_are_configurable(self):
        strict = compare.classify(10.0, 9.0, 0.1, 0.1, sigma=20.0, floor=0.05)
        self.assertEqual(strict["verdict"], "inconclusive")
        loose = compare.classify(10.0, 9.0, 0.1, 0.1, sigma=2.0, floor=0.001)
        self.assertEqual(loose["verdict"], "improved")


class CompareTests(unittest.TestCase):
    def test_workload_phases_are_compared_with_the_same_rule(self):
        after = record()
        after["workloads"][0]["stages_ms"]["frontend_total"] = stage(0.5)
        before = record()
        result = compare.compare(before, after)
        entry = result["workloads"][0]["phases"]["frontend_total"]
        self.assertEqual(entry["verdict"], "improved")
        self.assertEqual(result["workloads"][0]["verdict"], "improved")
        self.assertEqual(result["summary"]["improved"], 1)
        self.assertIn("loop.frontend_total", result["summary"]["improvements"])

    def test_process_rows_and_workload_rows_are_compared(self):
        after = record()
        after["processes"]["python_startup"] = stage(14.5)
        after["processes"]["import_and_cli"] = stage(30.0)
        result = compare.compare(record(), after)
        self.assertEqual(result["processes"]["python_startup"]["verdict"],
                         "inconclusive")
        self.assertEqual(result["processes"]["import_and_cli"]["verdict"], "improved")
        self.assertEqual(result["processes"]["import_and_cli"]["before_ms"], 50.0)

    def test_sizes_are_exact_counts(self):
        after = record()
        after["workloads"][0]["emitted_c_bytes"] = 4909
        after["workloads"][0]["binary_bytes"] = 16352
        after["workloads"][0]["source_bytes"] = 200
        sizes = compare.compare(record(), after)["workloads"][0]["sizes"]
        self.assertEqual(sizes["emitted_c_bytes"]["verdict"], "reduced")
        self.assertEqual(sizes["emitted_c_bytes"]["delta"], -18533)
        self.assertEqual(sizes["binary_bytes"]["verdict"], "unchanged")
        self.assertEqual(sizes["source_bytes"]["verdict"], "increased")
        self.assertEqual(sizes["source_bytes"]["relative"], round(28 / 172, 4))

    def test_a_workload_that_only_one_side_measured_is_missing(self):
        after = record()
        after["workloads"][0]["binary_bytes"] = None
        entry = compare.compare(record(), after)["workloads"][0]
        self.assertEqual(entry["sizes"]["binary_bytes"]["verdict"], "missing")
        self.assertIsNone(entry["sizes"]["binary_bytes"]["delta"])

    def test_a_workload_present_on_one_side_only_is_missing(self):
        before = record()
        before["workloads"].append(workload("float"))
        result = compare.compare(before, record())
        self.assertEqual(result["workloads"][1]["verdict"], "missing")
        self.assertEqual(result["summary"]["missing"], 1)

    def test_an_absent_phase_is_never_zero(self):
        before = record()
        before["workloads"][0]["stages_ms"]["lex"] = stage(None)
        result = compare.compare(before, record())
        entry = result["workloads"][0]["phases"]["lex"]
        self.assertEqual(entry["verdict"], "missing")
        self.assertIsNone(entry["before_ms"])

    def test_environment_mismatches_are_reported(self):
        after = record(environment={"recorded_at_utc": "2026-10-27T01:00:00+00:00",
                                    "platform": "Darwin-test", "cc_version": "cc 13.0.0",
                                    "cc_flags": ["-std=c11", "-O3"]})
        after["configuration"]["opt"] = "O3"
        result = compare.compare(record(), after)
        joined = " | ".join(result["environment_mismatches"])
        self.assertIn("platform", joined)
        self.assertIn("cc_version", joined)
        self.assertIn("cc_flags", joined)
        self.assertIn("optimisation level", joined)

    def test_matching_environments_produce_no_warning(self):
        self.assertEqual(compare.compare(record(), record())["environment_mismatches"],
                         [])

    def test_the_records_are_not_modified(self):
        before, after = record(), record()
        before_copy, after_copy = copy.deepcopy(before), copy.deepcopy(after)
        compare.compare(before, after)
        self.assertEqual(before, before_copy)
        self.assertEqual(after, after_copy)

    def test_the_rule_is_written_into_the_result(self):
        result = compare.compare(record(), record(), sigma=3.0, floor=0.1)
        self.assertEqual(result["schema"], "nava-comparison/1")
        self.assertEqual(result["rule"]["sigma"], 3.0)
        self.assertEqual(result["rule"]["floor"], 0.1)
        self.assertIn("inconclusive", result["rule"]["note"])


class CliTests(unittest.TestCase):
    def write(self, directory: Path, name: str, payload: dict) -> Path:
        path = directory / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def run_cli(self, *arguments):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = compare.main([str(argument) for argument in arguments])
        return code, stdout.getvalue(), stderr.getvalue()

    def test_a_regression_exits_non_zero_only_when_asked(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            after = record()
            after["workloads"][0]["stages_ms"]["frontend_total"] = stage(2.0)
            before_path = self.write(directory, "before.json", record())
            after_path = self.write(directory, "after.json", after)
            code, output, _ = self.run_cli(before_path, after_path)
            self.assertEqual(code, 0)
            self.assertIn("regressed", output)
            self.assertIn("loop", output)
            code, _, _ = self.run_cli(before_path, after_path, "--fail-on-regression")
            self.assertEqual(code, 1)

    def test_inconclusive_does_not_fail_the_gate(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            after = record()
            after["workloads"][0]["stages_ms"]["frontend_total"] = stage(1.02)
            code, _, _ = self.run_cli(self.write(directory, "a.json", record()),
                                      self.write(directory, "b.json", after),
                                      "--fail-on-regression")
            self.assertEqual(code, 0)

    def test_json_output_round_trips(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            target = directory / "out" / "comparison.json"
            code, _, _ = self.run_cli(self.write(directory, "a.json", record()),
                                      self.write(directory, "b.json", record()),
                                      "--json", target, "--quiet")
            self.assertEqual(code, 0)
            payload = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema"], "nava-comparison/1")
            self.assertEqual(payload["summary"]["improved"], 0)

    def test_quiet_prints_nothing(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            code, output, error = self.run_cli(
                self.write(directory, "a.json", record()),
                self.write(directory, "b.json", record()), "--quiet")
            self.assertEqual((code, output, error), (0, "", ""))

    def test_environment_mismatch_is_a_warning_not_a_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            before = record()
            after = record(environment={"recorded_at_utc": "x", "platform": "Other",
                                        "cc_version": "cc 1.0", "cc_flags": ["-O2"]})
            code, output, error = self.run_cli(self.write(directory, "a.json", before),
                                               self.write(directory, "b.json", after))
            self.assertEqual(code, 0)
            self.assertIn("warning", error)
            self.assertIn("platform", error)

    def test_a_foreign_record_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            broken = record(schema="something-else")
            with self.assertRaises(SystemExit) as caught:
                self.run_cli(self.write(directory, "a.json", broken),
                             self.write(directory, "b.json", record()))
            self.assertEqual(caught.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
