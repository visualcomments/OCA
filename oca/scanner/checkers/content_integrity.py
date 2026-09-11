"""Content integrity checker for course lectures.

Catches damage patterns that break the teaching process but are invisible
to structural checks: duplicated sections from failed quote insertions,
orphaned report fragments left by broken tooling, and OCR contamination
leaking into self-check questions.

All three patterns were found in production courses:
- history-and-philosophy-of-science-graduate had 11/26 lectures with
  duplicated tails and orphaned `verification/REPORT.md`) lines
- lecture 09 in the same course had its entire question block replaced
  by raw OCR text from Hume's Enquiry
"""

from __future__ import annotations

import re
from collections import Counter

from . import Finding, FindingSpec, ScanContext

ORDER = 30

CHECKS = [
    FindingSpec("duplicate-sections", "MAJOR", "high", "M", "content",
                "Раздел занятия дублируется — признак оборванной вставки"),
    FindingSpec("orphaned-report-line", "MAJOR", "high", "S", "content",
                "Висячая строка отчёта верификации — след неудачной вставки"),
    FindingSpec("ocr-in-questions", "MAJOR", "high", "M", "content",
                "Блок вопросов содержит текст корпуса (OCR) вместо вопросов"),
]

# Heading pattern: ## followed by a non-empty title
_HEADING_RE = re.compile(r"(?m)^## (.+)$")

# Orphaned line left behind when a verification report insertion fails
_ORPHAN_RE = re.compile(r"(?m)^>?\s*`verification/REPORT\.md`\)\s*$")

# Self-check questions section
_QUESTIONS_SECTION_RE = re.compile(
    r"## Вопросы для самопроверки(.*?)(?=\n## |\Z)", re.S)

# OCR markers: long latin runs, archaic ligatures from scanned XVIII–XIX c.
# texts, characteristic misspellings from long-s rendering
_OCR_MARKERS = [
    re.compile(r"[a-zA-Z]{25,}"),                          # long latin run
    re.compile(r"\b\w*(?:fubject|paffion|adverfity|"        # long-s forms
               r"fenfible|meafure|conclufion)\w*\b"),
    re.compile(r"[A-Za-z]{3,}\s+[A-Za-z]{3,}\s+"           # five+ english words
               r"[A-Za-z]{3,}\s+[A-Za-z]{3,}\s+[A-Za-z]{3,}"),
]


def check(ctx: ScanContext) -> list[Finding]:
    findings: list[Finding] = []

    for rel in ctx.lesson_paths:
        text = ctx.read_text(rel)

        # ── Duplicate sections ────────────────────────────────────────────
        headings = _HEADING_RE.findall(text)
        dupes = sorted({h for h in headings if headings.count(h) > 1})
        if dupes:
            findings.append(Finding(
                code="duplicate-sections", level="MAJOR",
                message=f"Дублируются разделы: {', '.join(dupes[:3])}"
                        + (f" (+{len(dupes)-3} ещё)" if len(dupes) > 3 else ""),
                path=rel,
                details={"duplicates": dupes},
            ))

        # ── Orphaned report lines ─────────────────────────────────────────
        orphans = _ORPHAN_RE.findall(text)
        if orphans:
            findings.append(Finding(
                code="orphaned-report-line", level="MAJOR",
                message=f"Найдено {len(orphans)} висячих строк отчёта "
                        f"(`verification/REPORT.md)`)",
                path=rel,
                details={"count": len(orphans)},
            ))

        # ── OCR contamination in questions ────────────────────────────────
        m = _QUESTIONS_SECTION_RE.search(text)
        if m:
            body = m.group(1)
            for marker in _OCR_MARKERS:
                if marker.search(body):
                    findings.append(Finding(
                        code="ocr-in-questions", level="MAJOR",
                        message="Блок «Вопросы для самопроверки» содержит "
                                "текст корпуса (OCR), а не вопросы",
                        path=rel,
                    ))
                    break  # one finding per file is enough

    return findings
