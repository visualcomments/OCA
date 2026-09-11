"""Coordinate debt checker for courses with verified corpora.

Courses that cite from a verified corpus require coordinates in the form
"file · fragment #N" for every direct quote. When coordinates are missing
(marked as "фрагмент —" without a number), this represents an unresolved
debt: the citation cannot be independently verified.

This checker counts such debts and flags them when they exceed a threshold.
It does NOT flag courses without a verification report, since the contract
only applies to courses with verified corpora.

Found in history-and-philosophy-of-science-graduate: 33 of 57 coordinates
had no fragment number after a broken insertion script.
"""

from __future__ import annotations

import re

from . import Finding, FindingSpec, ScanContext

ORDER = 40

CHECKS = [
    FindingSpec("coordinates-missing-number", "MINOR", "medium", "M", "content",
                "Координаты цитат без номера фрагмента"),
]

# Matches source lines with empty coordinates: "фрагмент —" or "фрагмент -"
# at end of line, without a following #N
_EMPTY_COORD_RE = re.compile(
    r"(?m)^\*?\*?Источник:\*?\*?\s*`[^`]+`\s*·\s*фрагмент\s*[—\-]\s*$")

# Also catch explicit "requires verification" markers
_UNRESOLVED_RE = re.compile(r"номер требует сверки|TODO.*координат", re.IGNORECASE)


def check(ctx: ScanContext) -> list[Finding]:
    findings: list[Finding] = []

    # Only apply to courses that have a verification report — the contract
    # is meaningless without one.
    has_report = any(
        "verification" in p.lower() and "report" in p.lower()
        for p in ctx.flat
    )
    if not has_report:
        return findings

    total_empty = 0
    total_unresolved = 0
    affected_files: list[str] = []

    for rel in ctx.lesson_paths:
        text = ctx.read_text(rel)
        empty = len(_EMPTY_COORD_RE.findall(text))
        unresolved = len(_UNRESOLVED_RE.findall(text))
        count = empty + unresolved
        if count > 0:
            total_empty += empty
            total_unresolved += unresolved
            affected_files.append(rel)

    total = total_empty + total_unresolved
    if total > 0:
        n_files = len(affected_files)
        findings.append(Finding(
            code="coordinates-missing-number", level="MINOR",
            message=f"{total} координат цитат без номера фрагмента "
                    f"в {n_files} занятиях "
                    f"({total_empty} пустых, {total_unresolved} требуют сверки)",
            path=affected_files[0] if affected_files else None,
            details={
                "total": total,
                "empty": total_empty,
                "unresolved": total_unresolved,
                "files": n_files,
            },
        ))

    return findings
