#!/usr/bin/env python3
"""Release-readiness gate (PLAN.md phase N9).

`tools/verify_release.py` is only useful if it can fail, so every check it makes
is exercised here twice: once against the real tree (which must pass) and once
against a deliberately broken copy built in a temporary directory (which must
not). The broken copies are synthetic artifacts — a wheel without the standard
library, an sdist without its license, an archive holding a private key — so the
gate runs in a second and never needs the network.
"""
from __future__ import annotations

import hashlib
import io
import json
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import verify_release as release  # noqa: E402

WHEEL = "nava_lang-0.1.0-py3-none-any.whl"
SDIST = "nava_lang-0.1.0.tar.gz"

METADATA = """Metadata-Version: 2.4
Name: nava-lang
Version: 0.1.0
License-Expression: Apache-2.0
Requires-Python: >=3.10
Provides-Extra: tokens
Requires-Dist: tiktoken; extra == "tokens"
"""

ENTRY_POINTS = "[console_scripts]\nnava = nava_lang.cli:main\n"


def build_wheel(directory: Path, *, stdlib=True, with_tests=False, secret=False) -> Path:
    path = directory / WHEEL
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("nava_lang/__init__.py", '__version__ = "0.1.0"\n')
        archive.writestr("nava_lang/cli.py", "def main():\n    return 0\n")
        if stdlib:
            for name in release.REQUIRED_STDLIB:
                archive.writestr(f"nava_lang/stdlib/{name}.nv", "fn main() {}\n")
        if with_tests:
            archive.writestr("tests/test_thing.py", "\n")
        if secret:
            archive.writestr("nava_lang/leak.py", 'KEY = "sk-' + "a" * 24 + '"\n')
        archive.writestr("nava_lang-0.1.0.dist-info/METADATA", METADATA)
        archive.writestr("nava_lang-0.1.0.dist-info/entry_points.txt", ENTRY_POINTS)
        archive.writestr("nava_lang-0.1.0.dist-info/licenses/LICENSE", "Apache License\n")
    return path


def build_sdist(directory: Path, *, license_file=True, results=False) -> Path:
    path = directory / SDIST
    prefix = "nava_lang-0.1.0"
    members = {
        "README.md": "# Nava\n",
        "pyproject.toml": "[project]\n",
        "NOTICE": "Nava\n",
        "tools/verify_release.py": "\n",
        "tests/registry.py": "\n",
        "nava_lang/__init__.py": "\n",
    }
    if license_file:
        members["LICENSE"] = "Apache License\n"
    for name in release.REQUIRED_STDLIB:
        members[f"nava_lang/stdlib/{name}.nv"] = "\n"
    if results:
        members["bench/results/2026-01-01-run.json"] = "{}\n"
    with tarfile.open(path, "w:gz") as archive:
        for name, text in members.items():
            data = text.encode()
            info = tarfile.TarInfo(f"{prefix}/{name}")
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return path


class MetadataTests(unittest.TestCase):
    def test_the_repository_declares_one_version_everywhere(self):
        self.assertEqual(release.problems_metadata(), [])

    def test_a_version_mismatch_is_reported(self):
        original = release._package_version
        release._package_version = lambda: "0.2.0"
        try:
            problems = release.problems_metadata()
        finally:
            release._package_version = original
        self.assertTrue(any("0.2.0" in problem and "0.1.0" in problem
                            for problem in problems), problems)

    def test_a_released_changelog_section_must_match_the_version(self):
        changelog = ROOT / "CHANGELOG.md"
        original = changelog.read_text(encoding="utf-8")
        try:
            changelog.write_text("# Changelog\n\n## 0.0.9\n\n- old\n\n## Unreleased\n",
                                 encoding="utf-8")
            problems = release.problems_metadata()
        finally:
            changelog.write_text(original, encoding="utf-8")
        self.assertTrue(any("0.0.9" in problem for problem in problems), problems)
        self.assertEqual(changelog.read_text(encoding="utf-8"), original)

    def test_the_ci_matrix_and_the_support_matrix_agree(self):
        matrix = release.ci_matrix()
        self.assertEqual(matrix["python"], ["3.10", "3.11", "3.12", "3.13"])
        self.assertEqual(matrix["compiler"], ["gcc", "clang"])
        rows = release.supported_matrix()
        exercised = [row for row in rows if row["status"] == "exercised"]
        self.assertEqual(len(exercised), 1, rows)
        self.assertEqual(exercised[0]["python"], matrix["python"])
        self.assertEqual(exercised[0]["compiler"], matrix["compiler"])

    def test_a_platform_cannot_be_promoted_without_evidence(self):
        original = release.ci_matrix
        release.ci_matrix = lambda: {"python": ["3.10", "3.11", "3.12", "3.13", "3.9"],
                                     "compiler": ["gcc", "clang"]}
        try:
            problems = release.problems_metadata()
        finally:
            release.ci_matrix = original
        self.assertTrue(any("3.9" in problem for problem in problems), problems)

    def test_an_unknown_status_is_rejected(self):
        original = release.supported_matrix
        release.supported_matrix = lambda: [{"platform": "Mars", "runner": "-",
                                             "python": ["3.13"], "compiler": ["gcc"],
                                             "status": "probably fine"}]
        try:
            problems = release.problems_metadata()
        finally:
            release.supported_matrix = original
        self.assertTrue(any("probably fine" in problem for problem in problems), problems)

    def test_the_license_is_the_one_the_metadata_claims(self):
        license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
        self.assertIn("Apache License", license_text)
        self.assertIn("Version 2.0, January 2004", license_text)
        self.assertIn("limitations under the License.", license_text)
        notice = (ROOT / "NOTICE").read_text(encoding="utf-8")
        self.assertIn("Apache License", notice)
        self.assertIn("Version 2.0", notice)
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('license = "Apache-2.0"', pyproject)
        self.assertIn('license-files = ["LICENSE", "NOTICE"]', pyproject)


class SyntaxFloorTests(unittest.TestCase):
    def test_every_file_parses_with_the_declared_python_floor(self):
        self.assertEqual(release.problems_syntax((3, 10)), [])

    def test_a_newer_syntax_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            sample = Path(temp) / "sample.py"
            sample.write_text("match value:\n    case 1:\n        pass\n",
                              encoding="utf-8")
            original = release.ROOT
            release.ROOT = Path(temp)
            try:
                problems = release.problems_syntax((3, 9))
            finally:
                release.ROOT = original
            self.assertTrue(any("sample.py" in problem for problem in problems), problems)


class SecretTests(unittest.TestCase):
    def test_the_repository_carries_no_credentials(self):
        self.assertEqual(release.problems_secrets(), [])

    def test_credential_shapes_are_caught(self):
        samples = {
            "openai.txt": 'key = "sk-' + "a" * 24 + '"',
            "github.txt": "token: ghp_" + "b" * 24,
            "aws.txt": "AKIA" + "C" * 16,
            "key.pem": "-----BEGIN RSA" + " PRIVATE KEY-----\n",
            "slack.txt": "xoxb-" + "1" * 12,
        }
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            for name, text in samples.items():
                (directory / name).write_text(text + "\n", encoding="utf-8")
            problems = release.problems_secrets([directory])
        reported = "\n".join(problems)
        for name in samples:
            self.assertIn(name, reported)
        self.assertGreaterEqual(len(problems), len(samples))

    def test_prose_about_secrets_is_not_a_secret(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            (directory / "SECURITY.md").write_text(
                "Never commit an API key or a password; use --api-key-env.\n",
                encoding="utf-8")
            self.assertEqual(release.problems_secrets([directory]), [])


class ArtifactTests(unittest.TestCase):
    def test_a_complete_pair_of_artifacts_passes(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            build_wheel(directory)
            build_sdist(directory)
            self.assertEqual(release.problems_artifacts(directory), [])

    def test_a_wheel_without_the_standard_library_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            build_wheel(directory, stdlib=False)
            build_sdist(directory)
            problems = release.problems_artifacts(directory)
        self.assertTrue(any("stdlib/math.nv" in problem for problem in problems), problems)

    def test_tests_may_ship_in_an_sdist_but_not_in_a_wheel(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            build_wheel(directory, with_tests=True)
            build_sdist(directory)
            problems = release.problems_artifacts(directory)
        self.assertTrue(any("test-suite file" in problem for problem in problems), problems)

    def test_a_credential_inside_an_artifact_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            build_wheel(directory, secret=True)
            build_sdist(directory)
            problems = release.problems_artifacts(directory)
        self.assertTrue(any("OpenAI-style key" in problem for problem in problems), problems)

    def test_an_sdist_without_its_license_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            build_wheel(directory)
            build_sdist(directory, license_file=False)
            problems = release.problems_artifacts(directory)
        self.assertTrue(any("missing LICENSE" in problem for problem in problems), problems)

    def test_benchmark_records_never_ship(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            build_wheel(directory)
            build_sdist(directory, results=True)
            problems = release.problems_artifacts(directory)
        self.assertTrue(any("benchmark result record" in problem for problem in problems),
                        problems)

    def test_the_digests_are_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            wheel = build_wheel(directory)
            build_sdist(directory)
            expected = hashlib.sha256(wheel.read_bytes()).hexdigest()
            digests = release.artifact_digests(directory)
        self.assertEqual(set(digests), {SDIST, WHEEL})
        self.assertEqual(digests[WHEEL], expected)


class ReportTests(unittest.TestCase):
    def test_the_report_is_machine_readable(self):
        report = release.run(["metadata", "syntax"])
        self.assertEqual(report["schema"], "nava-release-check/1")
        self.assertTrue(report["ok"])
        self.assertEqual(report["version"], release._package_version())
        self.assertEqual(set(report["checks"]), {"metadata", "syntax"})
        json.dumps(report)

    def test_a_failed_check_makes_the_report_unhappy(self):
        original = release.problems_syntax
        release.problems_syntax = lambda *args, **kwargs: ["boom"]
        try:
            report = release.run(["syntax"])
        finally:
            release.problems_syntax = original
        self.assertFalse(report["ok"])
        self.assertEqual(report["problems"], ["boom"])

    def test_the_cli_exits_non_zero_on_a_problem(self):
        self.assertEqual(release.main(["--quiet"]), 0)
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(release.main(["--quiet", "--artifacts", temp]), 1)


class DocumentationTests(unittest.TestCase):
    def test_the_release_checklist_names_the_real_commands(self):
        text = (ROOT / "RELEASE.md").read_text(encoding="utf-8")
        for command in ("python3 tools/verify_release.py",
                        "python3 -m build",
                        "python3 tests/registry.py",
                        "python3 -m unittest discover -s tests -q"):
            self.assertIn(command, text)
        self.assertIn("What a release does not claim", text)

    def test_the_readme_points_at_the_release_document(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("RELEASE.md", readme)
        self.assertIn("Apache-2.0", readme)

    def test_the_workflow_runs_the_release_gate(self):
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("python3 tools/verify_release.py", workflow)
        self.assertIn("release:", workflow)
        self.assertIn("fail-fast: false", workflow)
        # The packaging lane must use the wheel from a directory that is not the
        # checkout, or a missing package-data entry would go unnoticed.
        self.assertIn('cd "$RUNNER_TEMP', workflow)


if __name__ == "__main__":
    unittest.main()
