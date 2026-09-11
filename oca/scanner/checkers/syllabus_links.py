"""Syllabus-lecture link checker.

Verifies that the course syllabus actually links to lecture files and
that every lecture file is reachable from the syllabus. A syllabus that
lists topics without links forces students to navigate the directory
manually — this was the case in all four courses before improvement.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import Finding, FindingSpec, ScanContext

ORDER = 25

CHECKS = [
    FindingSpec("syllabus-no-lecture-links", "MAJOR", "high", "S", "structure",
                "syllabus.md не содержит ссылок на файлы занятий"),
    FindingSpec("lecture-not-in-syllabus", "MINOR", "medium", "S", "structure",
                "Файл занятия не упомянут в syllabus.md"),
]

# Markdown links: [text](path)
_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")


def check(ctx: ScanContext) -> list[Finding]:
    findings: list[Finding] = []

    # We need the Markdown syllabus, not the JSON one — only .md files
    # contain navigable links to lecture files.
    syllabus_md = None
    if ctx.syllabus_path and ctx.syllabus_path.endswith(".md"):
        syllabus_md = ctx.syllabus_path
    else:
        # Look for a .md syllabus in the flat file list
        for rel in ctx.flat:
            name = Path(rel).name.lower()
            if name in ("syllabus.md", "программа.md", "program.md",
                        "course-info.md"):
                syllabus_md = rel
                break

    if not syllabus_md:
        return findings  # no markdown syllabus; other checkers handle this

    syllabus_text = ctx.read_text(syllabus_md)

    # Extract all relative link targets from the syllabus
    linked_paths: set[str] = set()
    for m in _LINK_RE.finditer(syllabus_text):
        target = m.group(2).split("#")[0].strip()
        if target and not target.startswith(("http://", "https://", "mailto:")):
            # Resolve relative to the syllabus location
            resolved = str((Path(syllabus_md).parent / target).resolve())
            linked_paths.add(resolved)

    # Check: does the syllabus link to ANY lecture file?
    lecture_links = sum(
        1 for rel in ctx.lesson_paths
        if str((Path(rel).parent.parent / rel).resolve()) in linked_paths
        or any(Path(lp).name in syllabus_text for lp in [rel])
    )

    # Simpler check: are lecture filenames mentioned as link targets?
    lecture_names = {Path(rel).name for rel in ctx.lesson_paths}
    linked_lecture_names = set()
    for m in _LINK_RE.finditer(syllabus_text):
        target = m.group(2).split("#")[0].strip()
        name = Path(target).name
        if name in lecture_names:
            linked_lecture_names.add(name)

    if ctx.lesson_paths and not linked_lecture_names:
        findings.append(Finding(
            code="syllabus-no-lecture-links", level="MAJOR",
            message=f"syllabus.md не содержит ссылок ни на один из "
                    f"{len(ctx.lesson_paths)} файлов занятий",
            path=syllabus_md,
        ))

    # Check: is every lecture mentioned in the syllabus?
    for rel in sorted(ctx.lesson_paths):
        name = Path(rel).name
        if name not in syllabus_text and name not in linked_lecture_names:
            findings.append(Finding(
                code="lecture-not-in-syllabus", level="MINOR",
                message=f"Занятие {name} не упомянуто в syllabus.md",
                path=rel,
            ))

    return findings
