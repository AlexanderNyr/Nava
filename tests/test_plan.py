"""The plan is a document the repository is held to (PLAN.md, phases N0–N18).

Nothing here checks that the plan is *wise*. It checks the two things a plan can
silently lose: that every phase still carries the parts a gate needs, and that a
phase cannot be called delivered without a delivery record behind it.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# N0–N9 use `#### Tasks` subheadings; N10–N18 use bold labels. Both are accepted,
# because the guard is about the content being there, not about the typography.
REQUIRED_SECTIONS = (
    ("Objective", ("**Objective:**", "#### Objective")),
    ("Tasks", ("#### Tasks", "**Tasks**")),
    ("Test gate", ("#### Test gate", "**Test gate**")),
    ("Deliverable", ("#### Deliverable", "**Deliverable", "**Deliverable:**")),
)


def plan_text() -> str:
    return (ROOT / "PLAN.md").read_text(encoding="utf-8")


def phase_blocks(text: str) -> dict[str, str]:
    """Split the phase details into one block per `### Phase N<k> — …` heading."""
    parts = re.split(r"^### Phase (N\d+)\b.*$", text, flags=re.MULTILINE)
    blocks: dict[str, str] = {}
    for index in range(1, len(parts), 2):
        blocks[parts[index]] = parts[index + 1]
    return blocks


class PhaseRecordTests(unittest.TestCase):
    def test_every_phase_has_an_objective_tasks_gate_and_deliverable(self):
        blocks = phase_blocks(plan_text())
        self.assertGreaterEqual(len(blocks), 19, "phases N0–N18 must all be described")
        for name, block in blocks.items():
            for section, spellings in REQUIRED_SECTIONS:
                self.assertTrue(any(spelling in block for spelling in spellings),
                                f"{name} has no {section}")

    def test_the_independence_phases_also_carry_a_negative_control(self):
        blocks = phase_blocks(plan_text())
        for number in range(10, 19):
            name = f"N{number}"
            self.assertIn(name, blocks, f"{name} is specified in §4A but not described")
            self.assertIn("**Negative controls:**", blocks[name],
                          f"{name} has a gate with no negative control")

    def test_a_phase_is_not_called_delivered_without_a_record(self):
        text = plan_text()
        blocks = phase_blocks(text)
        for name, block in blocks.items():
            claimed = re.search(r"\*\*Delivered[^*]*\*\*|State \| \*\*Delivered", block)
            if claimed is None:
                continue
            recorded = re.search(r"#### Delivery record", block) or re.search(
                rf"#### Delivery record[^\n]*\n", text)
            self.assertIsNotNone(recorded, f"{name} is claimed delivered with no delivery record")

    def test_the_status_line_names_the_last_specified_phase(self):
        header = plan_text().splitlines()[2]
        self.assertIn("N0–N9 DELIVERED", header)
        self.assertIn("N10–N18", header)
        self.assertIn("NOT STARTED", header)


class SpecificationCriteriaTests(unittest.TestCase):
    def test_the_six_criteria_exist_and_are_numbered(self):
        text = plan_text()
        for number in range(1, 7):
            self.assertIn(f"| C{number} |", text, f"criterion C{number} is missing")

    def test_every_criterion_names_the_phase_that_proves_it(self):
        text = plan_text()
        table = text.split("### 4A.1", 1)[1].split("### 4A.2", 1)[0]
        for line in table.splitlines():
            if re.match(r"\| C\d ", line):
                cells = [cell.strip() for cell in line.strip("|").split("|")]
                self.assertRegex(cells[-1], r"N\d", f"criterion with no phase: {line}")

    def test_the_feature_audit_names_a_phase_for_each_row(self):
        text = plan_text()
        audit = text.split("### 4A.2", 1)[1].split("### 4A.3", 1)[0]
        rows = [line for line in audit.splitlines() if line.startswith("| ") and "| N" in line]
        self.assertGreaterEqual(len(rows), 10, "the audit table lost its rows")
        for row in rows:
            self.assertRegex(row, r"\| N\d\d?[,\s]", f"audit row with no phase: {row}")

    def test_the_audit_numbers_match_the_current_compiler(self):
        """The audit is evidence, so it is regenerated, not remembered."""
        result = tree_audit()
        # PLAN.md groups thousands ("4 394"); the audit counts digits.
        text = re.sub(r"(?<=\d)[ \u00a0](?=\d{3}\b)", "", plan_text())
        self.assertIn(f"{result['lines']} lines", text,
                      "PLAN.md quotes a compiler size the tree no longer has")
        self.assertIn(f"{result['functions']} functions", text,
                      "PLAN.md quotes a function count the tree no longer has")

    def test_decisions_are_numbered_without_gaps(self):
        text = plan_text()
        numbers = [int(n) for n in re.findall(r"^(?:\*\*|### )D(\d+)\.", text, flags=re.MULTILINE)]
        self.assertEqual(numbers, list(range(1, len(numbers) + 1)),
                         "decision numbers must be sequential with no duplicates")

    def test_the_non_goals_point_at_the_reversal(self):
        text = plan_text()
        section = text.split("## 5. Definition of done and non-goals", 1)[1]
        self.assertIn("self-hosting the compiler (N15)", section)
        self.assertIn("package manager (N13", section)
        self.assertIn("independent specification with a dependent toolchain", section)


def tree_audit() -> dict[str, int]:
    import ast

    functions = 0
    lines = 0
    for path in sorted((ROOT / "nava_lang").glob("*.py")):
        source = path.read_text(encoding="utf-8")
        lines += len(source.splitlines())
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions += 1
    return {"functions": functions, "lines": lines}


if __name__ == "__main__":
    unittest.main()
