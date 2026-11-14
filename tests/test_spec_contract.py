"""Unit tests for the fast spec authoring gate (no Qt/PySide6 required)."""

from pathlib import Path
import tempfile
import unittest

from scripts.check_spec_contract import (
    MARKER,
    REQUIRED_HEADINGS,
    SPECS,
    validate_spec,
)


def make_valid_spec() -> str:
    return "\n".join(
        [
            "# SPEC-TEST — demonstration",
            MARKER,
            "## Goal",
            *REQUIRED_HEADINGS,
            "- L1: target pytest tests/test_feature.py",
            "- L2: affected Windows visual checks",
            "- L3: full exact-head Windows pytest and native Qt",
            "- docs-only: do not repeat expensive L3",
        ]
    )


class TestSpecContract(unittest.TestCase):
    def check_content(self, content: str) -> list[str]:
        with tempfile.TemporaryDirectory(dir=SPECS, prefix="contract-test-") as temp:
            target = Path(temp) / "example.md"
            target.write_text(content, encoding="utf-8")
            return validate_spec(target)

    def test_valid_spec_passes(self) -> None:
        self.assertEqual(self.check_content(make_valid_spec()), [])

    def test_missing_marker_fails(self) -> None:
        problems = self.check_content(make_valid_spec().replace(MARKER, ""))
        self.assertTrue(any("marker" in problem for problem in problems), problems)

    def test_duplicate_marker_fails(self) -> None:
        problems = self.check_content(make_valid_spec() + "\n" + MARKER)
        self.assertTrue(any("marker" in problem for problem in problems), problems)

    def test_missing_targeted_plan_fails(self) -> None:
        problems = self.check_content(
            make_valid_spec().replace("### Уровень 2", "### Some other section")
        )
        self.assertTrue(any("Уровень 2" in problem for problem in problems), problems)

    def test_missing_docs_only_explanation_fails(self) -> None:
        problems = self.check_content(make_valid_spec().replace("docs-only", "documentation"))
        self.assertTrue(any("docs-only" in problem for problem in problems), problems)


if __name__ == "__main__":
    unittest.main()
