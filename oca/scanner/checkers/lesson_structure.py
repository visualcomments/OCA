"""Lesson structure checker.

Verifies that each lecture has learning objectives and navigation links.
These are standard elements of well-structured course materials:

- Learning objectives tell students what they should be able to do after
  the lesson, not just what topic is covered.
- Navigation links (previous/next/syllabus) let students move through
  the course without returning to the directory listing.

Both were missing in all four courses before improvement.
"""

from __future__ import annotations

import re

from . import Finding, FindingSpec, ScanContext

ORDER = 35

CHECKS = [
    FindingSpec("no-learning-goals", "MINOR", "medium", "S", "structure",
                "В занятии нет раздела «Цели занятия» / «Learning objectives»"),
    FindingSpec("no-navigation", "MINOR", "low", "S", "structure",
                "В занятии нет навигации между занятиями"),
]

_GOALS_RE = re.compile(
    r"(?m)^##\s+(?:Цели\s+занятия|Learning\s+[Oo]bjectives|"
    r"Цели\s+урока|Lesson\s+[Gg]oals|Цели)\s*$")

_NAV_RE = re.compile(
    r"(?i)(?:Навигация|Navigation|\←.*→|предыдущее|следующее|"
    r"previous|next)", re.IGNORECASE)


def check(ctx: ScanContext) -> list[Finding]:
    findings: list[Finding] = []

    for rel in ctx.lesson_paths:
        text = ctx.read_text(rel)

        if not _GOALS_RE.search(text):
            findings.append(Finding(
                code="no-learning-goals", level="MINOR",
                message="Нет раздела «Цели занятия» / «Learning objectives»",
                path=rel,
            ))

        if not _NAV_RE.search(text):
            findings.append(Finding(
                code="no-navigation", level="MINOR",
                message="Нет навигации между занятиями",
                path=rel,
            ))

    return findings
