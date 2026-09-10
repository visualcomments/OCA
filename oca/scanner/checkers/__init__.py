"""Pluggable checkers for OCA scanner.

Each checker is a module that exposes:
    - CHECKS: list of FindingSpec(code, level, impact, effort, axis, message_template)
    - check(context: ScanContext) -> list[Finding]

The scanner discovers and runs all checkers automatically. Adding a new
check means creating a new file here — no changes to oca_scan.py needed.
"""

from __future__ import annotations

import importlib
import pkgutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class FindingSpec:
    """Static metadata about a kind of finding."""
    code: str
    level: str          # BLOCKER | MAJOR | MINOR
    impact: str         # blocking | high | medium | low
    effort: str         # S | M | L
    axis: str           # structure | content | practice | reproducibility | licensing | agent_readiness
    message_template: str = ""


@dataclass
class Finding:
    """A single diagnostic produced by a checker."""
    code: str
    level: str
    message: str
    path: str = ""
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class ScanContext:
    """Immutable snapshot of everything a checker might need.

    Passed to every checker so they never import oca_scan directly and
    never touch the filesystem on their own. This is what keeps the
    scanner deterministic and testable.
    """
    root: Path
    flat: dict[str, Path]              # relative path -> absolute Path
    top: list[str]                     # sorted top-level entries
    readme_path: str | None
    readme_chars: int
    license_files: list[str]
    license_derived: list[str]
    license_dirs: list[str]
    content_license: list[str]
    syllabus_path: str | None
    env_files: list[str]
    build_files: list[str]
    ci_files: list[str]
    corpus_files: list[str]
    verify_files: list[str]
    agent_files: list[str]
    tool_dirs: list[str]
    mirror_dirs: list[str]
    syllabus_json: bool
    lesson_paths: list[str]
    lesson_detail: list[dict]
    notebooks: dict[str, dict]
    links: dict
    git_repo: bool
    git_commits: int | None
    counts: dict[str, int]
    # Raw text access for checkers that need to read specific files.
    # Cached so repeated reads are free.
    _text_cache: dict[str, str] = field(default_factory=dict, repr=False)

    def read_text(self, rel_path: str, limit: int = 400_000) -> str:
        if rel_path in self._text_cache:
            return self._text_cache[rel_path]
        p = self.flat.get(rel_path)
        if p is None or not p.is_file():
            return ""
        try:
            text = p.read_text(encoding="utf-8", errors="replace")[:limit]
        except OSError:
            text = ""
        self._text_cache[rel_path] = text
        return text

    def has_file(self, rel_path: str) -> bool:
        return rel_path in self.flat

    def files_matching(self, pattern: str) -> list[str]:
        """Return relative paths matching a glob pattern."""
        import fnmatch
        return [p for p in self.flat if fnmatch.fnmatch(p, pattern)]


def discover_checkers() -> list[Any]:
    """Import every module in this package and return those with a `check` function."""
    checkers = []
    package_dir = Path(__file__).parent
    for info in pkgutil.iter_modules([str(package_dir)]):
        if info.name.startswith("_"):
            continue
        mod = importlib.import_module(f".{info.name}", package=__name__)
        if hasattr(mod, "check") and hasattr(mod, "CHECKS"):
            checkers.append(mod)
    return sorted(checkers, key=lambda m: getattr(m, "ORDER", 50))
