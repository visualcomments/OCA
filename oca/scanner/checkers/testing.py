"""Test infrastructure checker.

Checks whether the course has tests, what framework they use, and whether
the test structure is meaningful. Courses that teach programming should
have tests — both as examples for students and as quality assurance.

This complements the existing practice axis (which checks assignments
and grading rubrics inside lessons) with a structural check at the
repository level.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import Finding, FindingSpec, ScanContext

ORDER = 30

CHECKS = [
    FindingSpec("no-tests", "MINOR", "medium", "M", "practice",
                "Нет тестов — код курса не проверяется автоматически"),
    FindingSpec("tests-no-framework", "MINOR", "low", "S", "practice",
                "Тесты есть, но без известного фреймворка"),
]

_TEST_DIR_RE = re.compile(r"^(tests?|specs?|__tests__)$", re.IGNORECASE)
_TEST_FILE_RE = re.compile(r"(^test_|_test\.py$|^spec_|_spec\.(rb|js|ts)$)", re.IGNORECASE)
_FRAMEWORK_RE = {
    "pytest": re.compile(r"\b(import pytest|from pytest|pytest\.|@pytest\.)"),
    "unittest": re.compile(r"\b(import unittest|from unittest|unittest\.)"),
    "hypothesis": re.compile(r"\b(from hypothesis|import hypothesis)"),
    "jest": re.compile(r"\b(describe\(|it\(|expect\(|jest\.)"),
    "mocha": re.compile(r"\b(describe\(|it\(|beforeEach)"),
}


def check(ctx: ScanContext) -> list[Finding]:
    findings: list[Finding] = []

    # Check for test directories
    test_dirs = [p for p in ctx.top if _TEST_DIR_RE.match(p)]

    # Check for test files anywhere
    test_files = [p for p in ctx.flat if _TEST_FILE_RE.search(Path(p).name)]

    if not test_dirs and not test_files:
        # Only flag if the course has code (Python, JS, etc.)
        code_files = [p for p in ctx.flat
                      if Path(p).suffix.lower() in (".py", ".js", ".ts", ".rb", ".java")]
        if code_files:
            findings.append(Finding(
                code="no-tests", level="MINOR",
                message=f"Курс содержит {len(code_files)} файлов кода, "
                        f"но нет тестов",
                details={"code_files": len(code_files)},
            ))
        return findings

    # Detect frameworks
    detected_frameworks: set[str] = set()
    for rel in test_files[:50]:  # Sample first 50 test files
        text = ctx.read_text(rel, limit=50_000)
        for name, pattern in _FRAMEWORK_RE.items():
            if pattern.search(text):
                detected_frameworks.add(name)

    # Also check requirements.txt / pyproject.toml for test deps
    for req_file in ["requirements.txt", "requirements-dev.txt",
                     "pyproject.toml", "setup.cfg", "tox.ini"]:
        if ctx.has_file(req_file):
            text = ctx.read_text(req_file)
            for name in _FRAMEWORK_RE:
                if name in text.lower():
                    detected_frameworks.add(name)

    if test_files and not detected_frameworks:
        findings.append(Finding(
            code="tests-no-framework", level="MINOR",
            message=f"Найдено {len(test_files)} тестовых файлов, "
                    f"но фреймворк не определён",
            path=test_files[0],
            details={"test_files": len(test_files)},
        ))

    return findings
