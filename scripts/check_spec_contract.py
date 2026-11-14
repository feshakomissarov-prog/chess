"""Fast stdlib-only CI gate for CC-VERIFY-01 in new/changed task specs.

This is an authoring-contract check, not proof of substantive test quality.
Usage:
  python scripts/check_spec_contract.py --base <PR_BASE_SHA>
  python scripts/check_spec_contract.py --files docs/specs/NEW_TASK.md
"""
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SPECS = ROOT / "docs" / "specs"
MARKER = "<!-- verification-contract: docs/process/VERIFICATION_PROTOCOL.md -->"
REQUIRED_HEADINGS = (
    "## План проверок и стоимость CI",
    "### Уровень 1",
    "### Уровень 2",
    "### Уровень 3",
    "## Критерии приёмки и доказательства",
)
TEMPLATE = "_TASK_TEMPLATE.md"


def repo_git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(
            f"git {' '.join(args)} failed ({result.returncode}): {result.stderr.strip()}"
        )
    return result.stdout


def changed_spec_paths(base: str) -> list[Path]:
    if not base or not all(char in "0123456789abcdefABCDEF" for char in base):
        raise ValueError("--base must be a commit SHA")
    diff = repo_git(
        "diff", "--name-only", "--diff-filter=ACMRT",
        f"{base}...HEAD", "--", "docs/specs",
    )
    return [ROOT / name for name in diff.splitlines() if name.endswith(".md")]


def validate_spec(path: Path) -> list[str]:
    issues: list[str] = []
    if path.name == TEMPLATE:
        return issues
    try:
        relative = path.resolve().relative_to(SPECS.resolve())
    except ValueError:
        return ["Not inside docs/specs: " + str(path)]
    if len(relative.parts) < 1:
        return ["Invalid path: " + str(path)]
    if not path.is_file():
        return ["Missing spec file: " + str(path)]
    text = path.read_text(encoding="utf-8-sig")
    if text.count(MARKER) != 1:
        issues.append("Require exactly one verification-contract marker")
    lines = {line.strip() for line in text.splitlines()}
    for heading in REQUIRED_HEADINGS:
        if heading not in lines:
            issues.append("Missing heading: " + heading)
    if "exact-head" not in text.lower() and "exact head" not in text.lower():
        issues.append("Describe exact-head final CI evidence")
    if "docs-only" not in text.lower():
        issues.append("Explain docs-only behavior / when L3 need not repeat")
    return issues


def validate_anchors() -> list[str]:
    issues: list[str] = []
    for path in (
        ROOT / "AGENTS.md",
        ROOT / "docs/process/VERIFICATION_PROTOCOL.md",
        SPECS / TEMPLATE,
    ):
        if not path.is_file():
            issues.append(f"Missing mandatory policy file: {path.relative_to(ROOT)}")
    agents = ROOT / "AGENTS.md"
    if agents.is_file():
        text = agents.read_text(encoding="utf-8")
        if "CC-VERIFY-01" not in text or "docs/process/VERIFICATION_PROTOCOL.md" not in text:
            issues.append("AGENTS.md must link to CC-VERIFY-01 permanent protocol")
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description="Check mandatory tiered testing plan in specs")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--base", help="PR base commit SHA for changed Markdown specs")
    group.add_argument("--files", nargs="+", help="Explicit spec paths (local lint)")
    args = parser.parse_args()

    try:
        paths = changed_spec_paths(args.base) if args.base else [ROOT / p for p in args.files]
    except (RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    errors = validate_anchors()
    relevant = [p for p in paths if p.suffix.lower() == ".md" and p.name != TEMPLATE]
    for path in relevant:
        for problem in validate_spec(path):
            errors.append(f"{path.relative_to(ROOT)}: {problem}")

    if errors:
        print("CC-VERIFY-01 CONTRACT CHECK FAILED:")
        for problem in errors:
            print(f"  - {problem}")
        print("Use docs/specs/_TASK_TEMPLATE.md and docs/process/VERIFICATION_PROTOCOL.md")
        return 1
    print(f"CC-VERIFY-01 PASS: {len(relevant)} edited task spec(s) checked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
