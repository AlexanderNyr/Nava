"""Registry guard tests: a missing, skipped or unregistered test must fail.

These tests are intentionally thin: all logic lives in `tests/registry.py` so it
can also run as a standalone command (`python3 tests/registry.py`) in CI.
"""
from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

import registry  # noqa: E402


class RegistryTests(unittest.TestCase):
    def test_registered_tests_are_present_and_not_skipped(self):
        self.assertEqual(registry.check_registered_tests(), [])
        if registry.missing_tools():
            self.assertTrue(registry.allow_missing_tools())
            return
        self.assertEqual(registry.check_skip_state(), [])

    def test_required_tools_are_present(self):
        missing = registry.missing_tools()
        if missing:
            self.assertTrue(
                registry.allow_missing_tools(),
                f"required tools are missing: {missing}; do not treat skipped tests as a pass",
            )
        for tool in registry.REQUIRED_TOOLS:
            if tool not in missing:
                self.assertIsNotNone(registry.shutil.which(tool))

    def test_task_fixtures_match_registry_and_reference(self):
        problems = registry.check_tasks()
        self.assertEqual(problems, [], "\n".join(problems))
        cases = sum(len(task["tests"]) for task in registry.load_tasks())
        self.assertEqual(cases, sum(registry.REGISTERED_TASKS.values()))
        self.assertEqual([task["id"] for task in registry.load_tasks()], list(registry.REGISTERED_TASKS))

    def test_diagnostics_registry_is_complete(self):
        """Every diagnostic code has a fixture, a documented test and a doc entry."""
        problems = registry.check_diagnostics()
        self.assertEqual(problems, [], "\n".join(problems))
        self.assertGreaterEqual(len(registry.CODES), 51)
        for code, (severity, title) in registry.CODES.items():
            self.assertIn(severity, ("error", "warning"), code)
            self.assertTrue(title, f"{code} has no title")

    def test_spec_fixtures_are_registered(self):
        problems = registry.check_fixtures()
        self.assertEqual(problems, [], "\n".join(problems))
        for directory, expected in registry.REGISTERED_FIXTURE_DIRS.items():
            self.assertEqual(len(list((registry.ROOT / directory).glob("*.nv"))), expected, directory)

    def test_registry_module_guard_passes(self):
        environment = dict(os.environ)
        environment.pop("NAVA_ALLOW_MISSING_TOOLS", None)
        result = subprocess.run(
            [sys.executable, str(ROOT / "tests/registry.py")],
            capture_output=True, text=True, timeout=60, env=environment,
        )
        if registry.missing_tools():
            self.assertEqual(result.returncode, 1)
            self.assertIn("required tools are missing", result.stderr)
        else:
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(result.stdout.startswith("OK:"), result.stdout)


if __name__ == "__main__":
    unittest.main()
