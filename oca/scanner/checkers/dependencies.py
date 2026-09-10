"""Dependency analysis checker.

Goes beyond "requirements.txt exists" to actually parse what is inside:
are versions pinned? Are there known-dangerous patterns (wildcard deps,
editable installs from URLs)? Is the environment reproducible?

This is the kind of check OSA does with its DependencyExtractor but
adapted for courses where reproducibility matters more than freshness.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import Finding, FindingSpec, ScanContext

ORDER = 10

CHECKS = [
    FindingSpec("dep-unpinned", "MINOR", "medium", "M", "reproducibility",
                "{count} зависимость(ей) без фиксации версии"),
    FindingSpec("dep-wildcard", "MAJOR", "high", "S", "reproducibility",
                "Зависимость с wildcard (*) — непредсказуемая установка"),
    FindingSpec("dep-editable-url", "MINOR", "medium", "M", "reproducibility",
                "Editable-установка из URL — не воспроизводится без сети"),
    FindingSpec("dep-no-hash", "MINOR", "low", "M", "reproducibility",
                "requirements.txt без хешей — нет защиты от подмены пакетов"),
]

# Patterns that indicate a dependency line (not a comment, option, or blank).
_DEP_LINE_RE = re.compile(r"^[a-zA-Z0-9_][a-zA-Z0-9_.\-]*(\[.*?\])?\s*(.*)$")
_VERSION_PIN_RE = re.compile(r"[=<>!~]=")
_WILDCARD_RE = re.compile(r"\*")
_EDITABLE_URL_RE = re.compile(r"-e\s+(https?://|git\+|svn\+)")


def _parse_requirements(text: str) -> dict:
    """Parse a requirements file into structured info."""
    lines = [l.strip() for l in text.splitlines()
             if l.strip() and not l.strip().startswith("#")]
    total = 0
    pinned = 0
    hard_pinned = 0
    wildcards = []
    editable_urls = []

    for line in lines:
        if line.startswith("-"):
            # Options like -r, -e, --index-url
            if _EDITABLE_URL_RE.match(line):
                editable_urls.append(line)
            continue

        m = _DEP_LINE_RE.match(line)
        if not m:
            continue
        total += 1
        version_part = m.group(2) or ""
        if "==" in version_part:
            hard_pinned += 1
            pinned += 1
        elif _VERSION_PIN_RE.search(version_part):
            pinned += 1
        if _WILDCARD_RE.search(version_part):
            wildcards.append(line)

    return {
        "total": total,
        "pinned": pinned,
        "hard_pinned": hard_pinned,
        "wildcards": wildcards,
        "editable_urls": editable_urls,
        "pin_ratio": pinned / total if total else 0.0,
    }


def check(ctx: ScanContext) -> list[Finding]:
    findings: list[Finding] = []

    req_files = [p for p in ctx.flat
                 if Path(p).name.lower().startswith("requirements")
                 and Path(p).suffix.lower() in (".txt", ".in")]

    if not req_files:
        return findings

    all_wildcards: list[str] = []
    all_editable: list[str] = []
    total_deps = 0
    total_unpinned = 0

    for rel in req_files:
        text = ctx.read_text(rel)
        info = _parse_requirements(text)
        total_deps += info["total"]
        total_unpinned += info["total"] - info["pinned"]
        all_wildcards.extend(info["wildcards"])
        all_editable.extend(info["editable_urls"])

        # Per-file finding when pinning is poor
        if info["total"] >= 3 and info["pin_ratio"] < 0.5:
            findings.append(Finding(
                code="dep-unpinned",
                level="MINOR",
                message=f"{info['total'] - info['pinned']} из {info['total']} "
                        f"зависимостей без фиксации версии в {Path(rel).name}",
                path=rel,
                details={"pin_ratio": round(info["pin_ratio"], 2),
                         "total": info["total"], "pinned": info["pinned"]},
            ))

    for wc in all_wildcards[:5]:
        findings.append(Finding(
            code="dep-wildcard", level="MAJOR",
            message=f"Wildcard-зависимость: {wc[:80]}",
            path=req_files[0],
        ))

    for ed in all_editable[:5]:
        findings.append(Finding(
            code="dep-editable-url", level="MINOR",
            message=f"Editable из URL: {ed[:80]}",
            path=req_files[0],
        ))

    return findings
