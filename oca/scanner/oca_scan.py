#!/usr/bin/env python3
"""OCA deterministic scanner for open course repositories.

Performs the reproducible, LLM-free half of an OCA audit: it collects facts
about a course repository so that scoring never depends on model recall.

Usage:
    python3 oca_scan.py <repo-path> [--json] [--quiet]

Exit codes:
    0 - scan completed (findings do not affect the exit code)
    2 - the path is not a directory / cannot be scanned
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# ── patterns ────────────────────────────────────────────────────────────────

README_RE = re.compile(r"^README(\.\w+)?$", re.IGNORECASE)
LICENSE_RE = re.compile(r"^(LICENSE|LICENCE|COPYING|NOTICE)(\.\w+)?$", re.IGNORECASE)
# Content licenses: LICENSE-code / LICENSE-text (flutter-mipt), CC-BY, CONTENT-LICENSE
CONTENT_LICENSE_RE = re.compile(r"(CONTENT[-_]?LICENSE|LICENSE[-_]?(CONTENT|CC|TEXT|DOCS?)|CC[-_]?BY)", re.IGNORECASE)
SYLLABUS_RE = re.compile(r"^(syllabus|программа|program|course[-_]?info)\.(md|json|ya?ml|toml|pdf)$", re.IGNORECASE)
# Lesson files may be .md, .ipynb, .pdf or .tex, numbered or Cyrillic-named.
# Two shapes are common: "01_topic.md" (bare number) and "lecture01.md".
LECTURE_RE = re.compile(r"^(?:\d{1,2}[\s\-_.]|(?:lecture|lesson|week|занятие|лекция|topic|seminar|"
                        r"семинар|homework|hw|домашн)[\s\-_]?\d+)", re.IGNORECASE)
LECTURE_DIR_RE = re.compile(r"^(lectures?|lessons?|seminars?|занятия|лекции|семинары|"
                            r"домашние задания|homeworks?|tasks?|задания|labs?|"
                            r"лабораторные|workshops?|practice)$", re.IGNORECASE)
NOTEBOOK_SUFFIX = ".ipynb"
# Artifacts that count as course material for structure scoring.
MATERIAL_SUFFIXES = (".md", ".ipynb", ".pdf", ".tex", ".html", ".pptx")
SLIDE_RE = re.compile(r"(слайд|slide|презентац|presentation|лекц|lecture)", re.IGNORECASE)
ENV_FILES = ("requirements.txt", "environment.yml", "environment.yaml", "pyproject.toml",
             "Pipfile", "poetry.lock", "uv.lock", "renv.lock")
BUILD_FILES = ("Makefile", "makefile", "justfile", "Justfile", "Taskfile.yml", "build.sh", "run.sh")
AGENT_FILES = ("AGENTS.md", "CLAUDE.md", ".cursorrules")
CI_DIRS = (".github/workflows", ".gitlab-ci.yml", ".circleci")
ASSIGNMENT_RE = re.compile(r"(задани|assignment|exercise|упражнени|практик|homework|домашн)", re.IGNORECASE)
GRADING_RE = re.compile(r"(критери|оцениван|rubric|grading|балл|score|assessment)", re.IGNORECASE)
SELFCHECK_RE = re.compile(r"(самопроверк|вопросы для|self[- ]?check|quiz|контрольные вопросы)", re.IGNORECASE)
OBJECTIVES_RE = re.compile(r"(цел[иь]|задачи занятия|learning outcomes?|результаты обучения|you will learn)", re.IGNORECASE)
CORPUS_RE = re.compile(r"(CORPUS|PROVENANCE|BIBLIOGRAPHY|REFERENCES|ИСТОЧНИКИ)", re.IGNORECASE)
VERIFY_RE = re.compile(r"(verif|проверк|validate)", re.IGNORECASE)
SOURCES_RE = re.compile(r"(источник|литератур|references?|bibliograph|список литератур)", re.IGNORECASE)

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache",
             ".pytest_cache", ".ipynb_checkpoints", "dist", "build", ".tox"}


# ── helpers ─────────────────────────────────────────────────────────────────

def walk_files(root: Path) -> list[Path]:
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".git")]
        for name in filenames:
            out.append(Path(dirpath) / name)
    return out


def read_text(path: Path, limit: int = 400_000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""


def rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def git_commit_count(root: Path) -> int | None:
    try:
        r = subprocess.run(["git", "-C", str(root), "rev-list", "--count", "HEAD"],
                           capture_output=True, text=True, timeout=10)
        return int(r.stdout.strip()) if r.returncode == 0 else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def git_is_repo(root: Path) -> bool:
    try:
        r = subprocess.run(["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"],
                           capture_output=True, text=True, timeout=10)
        return r.returncode == 0 and r.stdout.strip() == "true"
    except (OSError, subprocess.SubprocessError):
        return False


# ── notebook checks ─────────────────────────────────────────────────────────

def scan_notebook(path: Path) -> dict:
    """Static notebook hygiene — no execution, no nbformat dependency."""
    try:
        nb = json.loads(read_text(path, limit=20_000_000))
    except (json.JSONDecodeError, OSError) as exc:
        return {"parse_error": str(exc)}

    cells = nb.get("cells", [])
    issues: list[str] = []
    total_code = total_md = empty = 0
    exec_counts: list = []

    for cell in cells:
        ctype = cell.get("cell_type")
        src = "".join(cell.get("source", []))
        if ctype == "code":
            total_code += 1
            if not src.strip():
                empty += 1
            ec = cell.get("execution_count")
            exec_counts.append(ec)
            if cell.get("outputs"):
                for o in cell["outputs"]:
                    if o.get("output_type") == "error":
                        issues.append(f"stored error output in cell (ename={o.get('ename')})")
        elif ctype == "markdown":
            total_md += 1

    numbered = [e for e in exec_counts if isinstance(e, int)]
    if numbered and numbered != sorted(numbered):
        issues.append("execution_count out of order (notebook run top-to-bottom)")
    if len(numbered) != len(exec_counts):
        issues.append(f"{len(exec_counts) - len(numbered)} code cell(s) never executed (null execution_count)")
    if numbered and numbered != list(range(numbered[0], numbered[0] + len(numbered))):
        issues.append("gaps or duplicates in execution_count")
    if empty:
        issues.append(f"{empty} empty code cell(s)")
    if total_code and total_md == 0:
        issues.append("no markdown cells — notebook is code without narrative")
    if total_code > 50:
        issues.append(f"large notebook ({total_code} code cells) — consider splitting")

    src_all = json.dumps(nb)
    if re.search(r'["\'](/home/|/Users/|[A-Z]:\\\\|C:/)', src_all):
        issues.append("absolute local path hardcoded")
    if "google.colab" in src_all:
        issues.append("Colab-specific code (not reproducible off Colab)")

    return {
        "cells": len(cells),
        "code_cells": total_code,
        "markdown_cells": total_md,
        "executed": len(numbered),
        "has_outputs": any(c.get("outputs") for c in cells if c.get("cell_type") == "code"),
        "issues": issues,
    }


# ── link checks ─────────────────────────────────────────────────────────────

MD_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")


def check_links(files: list[Path], root: Path) -> dict:
    """Collect relative links (checked locally) and external links (not checked).

    External links are gathered but never fetched here: `scan()` must stay
    deterministic and offline. Across the surveyed courses, markdown holds
    3082 external links — but notebooks hold 8858, three times as many.
    A course's links live in its teaching material, not only in its README,
    so notebooks are scanned too (as JSON source text, which is what a
    notebook is).
    """
    broken: list[dict] = []
    total = 0
    external: list[dict] = []
    seen_ext: set[tuple[str, str]] = set()
    RAW_URL_RE = re.compile(r"https?://[^\s\)\]\"'<>,;]+")

    def clean_url(u: str) -> str:
        """Strip trailing punctuation that belongs to the prose, not the URL.

        Notebook cells are JSON strings, so a URL frequently ends with a
        literal escape (`.../optimize.html\\n`) or a closing bracket that the
        regex cannot see inside an escaped context. Leaving those in produces
        a 404 against a URL that is actually fine.
        """
        u = u.replace("\\n", "").replace("\\r", "").replace("\\t", "")
        u = u.rstrip(".,;:!?")
        while u.endswith(")") and u.count("(") < u.count(")"):
            u = u[:-1]
        return u.strip()

    for f in files:
        suffix = f.suffix.lower()
        if suffix not in (".md", ".markdown", ".rst", ".ipynb"):
            continue
        text = read_text(f)
        relpath = rel(f, root)

        if suffix == ".ipynb":
            # Notebooks carry links in markdown cells and in outputs; take
            # every absolute URL rather than trying to parse the JSON here.
            for raw in RAW_URL_RE.findall(text):
                url = clean_url(raw)
                key = (relpath, url)
                if key not in seen_ext and url:
                    seen_ext.add(key)
                    external.append({"file": relpath, "url": url})
            continue

        for raw in MD_LINK_RE.findall(text):
            if raw.startswith(("http://", "https://")):
                url = clean_url(raw)
                key = (relpath, url)
                if key not in seen_ext and url:
                    seen_ext.add(key)
                    external.append({"file": relpath, "url": url})
                continue
            if raw.startswith(("mailto:", "#", "tel:")):
                continue
            target = raw.split("#", 1)[0]
            if not target:
                continue
            total += 1
            resolved = (f.parent / target).resolve()
            if not resolved.exists():
                broken.append({"file": relpath, "target": raw})

        # Bare URLs in markdown text are common too and were previously
        # invisible: `[x](y)` misses "see https://example.com/paper".
        for raw in RAW_URL_RE.findall(text):
            url = clean_url(raw)
            key = (relpath, url)
            if key not in seen_ext and url:
                seen_ext.add(key)
                external.append({"file": relpath, "url": url})

    return {"relative_links": total, "broken": broken, "external": external}


# ── main scan ───────────────────────────────────────────────────────────────

def scan(root: Path) -> dict:
    # Fail loudly on a bad path. Silently scoring a typo as a real course
    # (an empty dir still scores ~1.0) would hide the mistake behind a
    # plausible-looking number, which is worse than an error.
    root = Path(root)
    if not root.exists():
        raise FileNotFoundError(f"{root} не существует")
    if not root.is_dir():
        raise NotADirectoryError(f"{root} не является директорией")

    files = walk_files(root)
    flat = {rel(f, root): f for f in files}
    names_lower = {p.lower(): p for p in flat}
    top = {p for p in flat if "/" not in p}

    # README
    readme_path = next((flat[p] for p in top if README_RE.match(p)), None)
    readme_chars = len(read_text(readme_path)) if readme_path else 0

    # ── licenses ────────────────────────────────────────────────────────────
    # Real courses name licence files in several ways, and the strict
    # `^LICENSE$` match produced a false BLOCKER on courses that license
    # things BETTER than average:
    #   flutter-mipt   LICENSE-code.md + LICENSE-text.md   (split by asset type)
    #   scireason      LICENSE + LICENSE_SCOPE.md + LICENSES/GPL-3.0-or-later.txt
    #   deep-vision    LICENSE + vendored per-model licences
    # So detection is: any top-level name containing a licence keyword, plus
    # licence directories, plus a scan one level deep for licence text.
    LICENSE_ANY_RE = re.compile(
        r"(^|[-_.])(licen[cs]e|copying|copyright|notice)([-_.]|$)", re.IGNORECASE)
    LICENSE_DIR_RE = re.compile(r"^(licen[cs]es?|legal)$", re.IGNORECASE)

    license_files = [p for p in top if LICENSE_RE.match(p)]
    # derived names at the root: LICENSE-code.md, LICENSE_SCOPE.md, ...
    derived = [p for p in top if p not in license_files and LICENSE_ANY_RE.search(p)]
    # licence directories and their contents (LICENSES/GPL-3.0-or-later.txt)
    license_dirs = [p for p in top if (root / p).is_dir() and LICENSE_DIR_RE.match(p)]
    in_license_dir: list[str] = []
    for d in license_dirs:
        in_license_dir += [q for q in flat if q.startswith(d + "/") and "/" not in q[len(d) + 1:]]

    # Split out the subset that speaks about CONTENT (text, slides, docs)
    # versus code, so the two axes can be scored separately.
    content_license = [p for p in top if CONTENT_LICENSE_RE.search(p)]
    # A single scope file can cover content too; detect it by reading, not by
    # guessing from the name.
    for p in derived + in_license_dir:
        if p in content_license:
            continue
        body = read_text(flat[p], limit=6000).lower()
        if any(k in body for k in ("creative commons", "cc by", "cc-by",
                                   "educational materials", "учебн", "text and",
                                   "slides", "контент", "материал")):
            content_license.append(p)

    # Anything that actually grants a licence counts for the licence axis,
    # whatever it is called.
    all_license_artifacts = license_files + derived + in_license_dir

    # syllabus
    syllabus_rel = next((p for p in flat if SYLLABUS_RE.match(Path(p).name)), None)
    syllabus_path = syllabus_rel

    # ── lesson discovery (three strategies, union) ──────────────────────────
    # Real course repos in the wild rarely follow `lectures/NN_topic.md`.
    # Observed layouts: numbered module dirs (1-intro-ai-studio/), Cyrillic
    # dirs (Лекции/, Домашние задания/), week*/seminar*/homework* dirs,
    # PDF-only lectures, slide decks. Count the container when files inside
    # are fragments of one lesson, not one lesson per file.
    lessons: set[str] = set()          # lesson container paths (dir or file)
    lesson_kind: dict[str, str] = {}

    def _add_lesson(p: str, kind: str) -> None:
        lessons.add(p)
        lesson_kind.setdefault(p, kind)

    # (a) numbered / Cyrillic-named lesson containers anywhere in the tree.
    # A directory only collapses into ONE lesson when its children are not
    # themselves lessons; `lectures/01_x.md ... lectures/16_z.md` must yield
    # 16 lessons, while `optimization/Лекции/Лекция 6/{slide,notes}.pdf`
    # yields one per nested lesson dir.
    NESTED_RE = re.compile(
        r"^(week|seminar|семинар|модуль|module|тема|topic|занятие|лекция|lecture|lesson|"
        r"homework|hw|домашн)[\s\-_]?\d+$", re.IGNORECASE)

    def _files_under(container: str) -> list[str]:
        return [q for q in flat if q.startswith(container + "/")]

    def _container_children_are_lessons(container: str) -> bool:
        """True when the direct children are themselves lesson dirs/files."""
        prefix = container + "/"
        child_dirs, child_files = set(), []
        for q in flat:
            if not q.startswith(prefix):
                continue
            rest = q[len(prefix):]
            if "/" in rest:
                child_dirs.add(rest.split("/")[0])
            else:
                child_files.append(rest)
        if child_dirs and any(NESTED_RE.match(d) or LECTURE_DIR_RE.match(d) for d in child_dirs):
            return True
        numbered = [f for f in child_files if LECTURE_RE.match(Path(f).stem)]
        return bool(numbered)

    for p in flat:
        parts = p.split("/")
        for seg in parts[:-1]:
            if LECTURE_RE.match(seg) or LECTURE_DIR_RE.match(seg) or NESTED_RE.match(seg):
                idx = parts.index(seg)
                container = "/".join(parts[:idx + 1])
                # Walk down while children are still lesson containers.
                if _container_children_are_lessons(container):
                    # descend one level: register each lesson child instead
                    prefix = container + "/"
                    for q in flat:
                        if not q.startswith(prefix):
                            continue
                        rest = q[len(prefix):]
                        head = rest.split("/")[0]
                        child = prefix + head
                        if "/" in rest and (NESTED_RE.match(head) or LECTURE_RE.match(head)):
                            _add_lesson(child, "dir")
                        elif "/" not in rest and LECTURE_RE.match(Path(head).stem):
                            _add_lesson(child, "file")
                else:
                    _add_lesson(container, "dir")
                break
        stem = Path(p).stem
        if p.endswith(MATERIAL_SUFFIXES) and LECTURE_RE.match(stem):
            _add_lesson(p, "file")

    # (b) numbered top-level module dirs (1-intro-ai-studio, 2016-fall, ...)
    top_dirs = {p.split("/")[0] for p in flat if "/" in p}
    for d in top_dirs:
        if re.match(r"^\d+[-_]", d) or re.match(r"^(week|module|тема)[\s\-_]?\d", d, re.IGNORECASE):
            _add_lesson(d, "module")
        # year-term layout (ml-course-hse: 2016-fall, 2017-spring)
        if re.match(r"^\d{4}[-_](fall|spring|summer|winter|осень|весна)", d, re.IGNORECASE):
            _add_lesson(d, "module")

    # (b2) lab*/homework*/task* dirs even without a digit (unn-itmm-ycloud/lab1)
    for p in flat:
        parts = p.split("/")
        for seg in parts[:-1]:
            if re.match(r"^(labs?|hw|homeworks?|tasks?|задания|семинары)[\s\-_]?\d*$", seg, re.IGNORECASE) \
                    and re.search(r"\d", seg):
                idx = parts.index(seg)
                _add_lesson("/".join(parts[:idx + 1]), "dir")
                break

    # (c) slide decks and lecture PDFs without numeric prefix
    slide_files = [p for p in flat
                   if p.endswith((".pdf", ".pptx"))
                   and SLIDE_RE.search(Path(p).stem)]
    for p in slide_files:
        _add_lesson(p, "slide")

    # (d) de-duplicate: when a module container is already counted, drop the
    # lessons nested inside it (ml-course-hse counts 2016-fall once, not its
    # 30 lecture notes plus 20 seminar dirs as separate lessons).
    modules = sorted(p for p, k in lesson_kind.items() if k == "module")
    if modules:
        for m in modules:
            for p in [q for q in lessons if q != m and q.startswith(m + "/")]:
                lessons.discard(p)
                lesson_kind.pop(p, None)
        # if modules cover the course, drop the bare lesson-container dirs too
        if len(lessons) > 2 * len(modules):
            for p in [q for q in lessons if lesson_kind.get(q) == "dir"]:
                lessons.discard(p)
                lesson_kind.pop(p, None)

    lesson_paths = sorted(lessons)
    lecture_files = lesson_paths  # kept for backward-compatible naming

    # per-lesson signals — read dir contents when the lesson is a container
    def lesson_text(container: str) -> str:
        full = root / container
        if full.is_file():
            if full.suffix.lower() == ".pdf":
                return ""  # binary; counts by presence, not text
            return read_text(full)
        chunks = []
        for q in flat:
            if q.startswith(container + "/"):
                if q.endswith((".md", ".txt", ".ipynb", ".py")):
                    chunks.append(read_text(flat[q], limit=120_000))
        return "\n".join(chunks)

    lecture_detail = []
    for p in lesson_paths:
        text = lesson_text(p)
        if (root / p).is_dir():
            n_files = sum(1 for q in flat if q.startswith(p + "/"))
        else:
            n_files = 1
        lecture_detail.append({
            "path": p,
            "kind": lesson_kind.get(p, "file"),
            "chars": len(text),
            "files": n_files,
            "objectives": bool(OBJECTIVES_RE.search(text)),
            "assignments": bool(ASSIGNMENT_RE.search(text)),
            "grading": bool(GRADING_RE.search(text)),
            "self_check": bool(SELFCHECK_RE.search(text)),
            "sources": bool(SOURCES_RE.search(text)),
            "citations": len(re.findall(r"\[\d+\]|\(\d{4}\)|https?://", text)),
        })

    # notebooks
    nb_paths = [p for p in flat if p.endswith(NOTEBOOK_SUFFIX)]
    notebooks = {p: scan_notebook(flat[p]) for p in nb_paths}

    # environment / build
    env_files = [p for p in flat if Path(p).name in ENV_FILES]
    build_files = [p for p in flat if Path(p).name in BUILD_FILES]
    makefiles = [p for p in flat if Path(p).name.lower() == "makefile"]
    make_targets: list[str] = []
    if makefiles:
        mk = read_text(flat[makefiles[0]])
        make_targets = re.findall(r"^([a-zA-Z0-9_.-]+):(?!=)", mk, re.MULTILINE)

    # agent-readiness
    agent_files = [p for p in flat if Path(p).name in AGENT_FILES]
    syllabus_json = [p for p in flat if Path(p).name.lower() == "syllabus.json"]
    mirror_dirs = sorted({d for d in {".agents", ".claude", ".cursor"} if (root / d).is_dir()})
    tool_dirs = sorted({p.split("/")[0] for p in flat
                        if "/" in p and re.match(r"(tools?|scripts?|bin)$", p.split("/")[0], re.I)})

    # verification / tests / CI
    verify_files = [p for p in flat if VERIFY_RE.search(Path(p).name) and p.endswith((".py", ".md", ".sh"))]
    test_files = [p for p in flat if re.search(r"(^|/)(tests?|spec)/", p) or Path(p).name.startswith("test_")]
    ci_files = [p for p in flat if p.startswith(".github/workflows/")
                or p in (".gitlab-ci.yml", ".circleci/config.yml")]

    # corpus
    corpus_files = [p for p in flat if CORPUS_RE.search(Path(p).name) and p.endswith((".md", ".bib", ".json", ".yaml", ".yml"))]

    links = check_links(files, root)
    git_repo = git_is_repo(root)

    # ── findings ────────────────────────────────────────────────────────────
    findings: list[dict] = []

    def add(level: str, code: str, message: str, path: str | None = None) -> None:
        findings.append({"level": level, "code": code, "message": message, "path": path})

    if not readme_path:
        add("BLOCKER", "no-readme", "README отсутствует в корне")
    elif readme_chars < 200:
        add("MAJOR", "thin-readme", f"README слишком короткий ({readme_chars} символов)", readme_path)

    if not all_license_artifacts:
        add("BLOCKER", "no-license", "Нет файла LICENSE в корне")
    elif not license_files and not in_license_dir:
        # Recorded as informational rather than a blocker: the course does
        # grant a licence, it just names it unconventionally. `flutter-mipt`
        # split code/text licences deliberately — that is better practice
        # than one blanket file, not a defect.
        add("MINOR", "nonstandard-license-name",
            "Лицензия есть, но под нестандартным именем: "
            + ", ".join(all_license_artifacts[:4])
            + " — стоит добавить канонический LICENSE для распознавания инструментами")
    if not content_license:
        add("MINOR", "no-content-license",
            "Не найдена отдельная лицензия для учебного контента (текст/слайды)")

    if not syllabus_path:
        add("MAJOR", "no-syllabus", "Нет syllabus.md/json — программа курса не зафиксирована")
    if not lecture_files:
        add("MAJOR", "no-lectures", "Не найдено файлов занятий (lectures/NN_*.md и т.п.)")
    elif syllabus_rel:
        syl = read_text(flat[syllabus_rel])
        declared = len(re.findall(r"^\s*(?:[-*]|\d+\.|\|)\s*\S", syl, re.MULTILINE))
        if declared and len(lecture_files) < declared * 0.5:
            add("MAJOR", "syllabus-mismatch",
                f"syllabus упоминает ~{declared} позиций, найдено {len(lecture_files)} файлов занятий",
                syllabus_path)

    # Only judge text-level signals on lessons we could actually read as text.
    # PDF-only lectures carry binary content; flagging them for "no objectives"
    # would be a false positive, so they are reported separately.
    text_lessons = [d for d in lecture_detail if d["chars"] > 0]
    pdf_only = [d for d in lecture_detail if d["chars"] == 0 and d["path"].endswith(".pdf")]

    for d in text_lessons:
        missing = [k for k in ("objectives", "assignments", "self_check", "sources") if not d[k]]
        if missing:
            add("MINOR", "lecture-gaps",
                f"в занятии нет: {', '.join(missing)}", d["path"])
        if d["chars"] < 500:
            add("MAJOR", "stub-lecture", f"занятие почти пустое ({d['chars']} символов)", d["path"])

    if pdf_only:
        add("MINOR", "pdf-only-materials",
            f"{len(pdf_only)} занятие(й) только в PDF — текст не индексируется, "
            f"поиск и проверка цитат невозможны", pdf_only[0]["path"])

    if text_lessons and not any(d["assignments"] for d in text_lessons):
        add("MAJOR", "no-assignments", "Ни в одном читаемом занятии не найдено блока заданий")
    if text_lessons and not any(d["grading"] for d in text_lessons):
        add("MAJOR", "no-grading", "Ни в одном читаемом занятии не найдено критериев оценивания")

    if links["broken"]:
        for b in links["broken"][:20]:
            add("MAJOR", "broken-link", f"битая относительная ссылка → {b['target']}", b["file"])

    for p, info in notebooks.items():
        if "parse_error" in info:
            add("MAJOR", "bad-notebook", f"ноутбук не парсится: {info['parse_error']}", p)
            continue
        for issue in info["issues"]:
            add("MINOR", "notebook-hygiene", issue, p)

    if notebooks and not env_files:
        add("MAJOR", "no-environment", "Есть ноутбуки/код, но нет файла окружения")
    if not env_files and not build_files:
        add("MINOR", "no-build", "Нет ни окружения, ни команды запуска (Makefile и т.п.)")
    if not ci_files:
        add("MINOR", "no-ci", "Нет CI — проверки не воспроизводятся автоматически")

    if not corpus_files:
        add("MINOR", "no-corpus", "Нет CORPUS/PROVENANCE/BIBLIOGRAPHY — происхождение материалов не зафиксировано")
    if not verify_files:
        add("MINOR", "no-verification", "Нет скриптов/отчётов верификации")

    if not agent_files:
        add("MINOR", "no-agents-md", "Нет AGENTS.md — курс не готов к работе агента")
    if not syllabus_json:
        add("MINOR", "no-machine-syllabus", "Нет syllabus.json — программа не машиночитаема")

    # ── axis scores (0-5), deterministic part only ──────────────────────────
    def ratio(num: int, den: int) -> float:
        return (num / den) if den else 0.0

    # A course is not penalised for having fewer than 16 lessons: a 6-module
    # workshop is a legitimate course. Scale on a soft target of 8.
    LESSON_TARGET = 8
    n_lessons = len(lecture_files)
    # Signals are only measurable on lessons readable as text; PDF-only
    # lessons cannot be judged for objectives/sources, so they neither earn
    # nor lose points on those sub-criteria.
    judged = text_lessons if text_lessons else lecture_detail
    n_judged = max(len(judged), 1)
    text_coverage = ratio(len(text_lessons), max(n_lessons, 1))

    # ── graded evidence for the binary-looking checks ───────────────────────
    env_score, env_info = grade_env(env_files, flat, read_text)
    build_score = grade_build(build_files)
    nb_score = grade_notebooks(notebooks)

    # Structure: a README that documents the course beats one that is merely
    # long enough to pass a threshold; syllabus with a week-by-week plan beats
    # a title.
    readme_score = graded_ratio(readme_chars, 3000, floor=0.4) if readme_path else 0.0
    if readme_path and readme_chars >= 200:
        readme_score = max(readme_score, 0.5)
    syl_score = 0.0
    if syllabus_path:
        syl_score = 0.6
        if syllabus_rel:
            syl_body = read_text(flat[syllabus_rel])
            weeks = len(re.findall(r"^\s*(?:[-*]|\d+\.|\|)", syl_body, re.MULTILINE))
            syl_score = 1.0 if weeks >= 6 else (0.8 if weeks >= 3 else 0.6)

    structure = 5 * (
        0.30 * syl_score
        + 0.30 * ratio(min(n_lessons, LESSON_TARGET), LESSON_TARGET)
        + 0.20 * readme_score
        + 0.20 * (ratio(sum(1 for d in judged if d["objectives"]), n_judged) if text_lessons else 0.5)
    )
    # Content: a real corpus with provenance and a verification script is
    # stronger than a bare bibliography, so both are graded.
    content = 5 * (
        0.35 * (ratio(sum(1 for d in judged if d["sources"]), n_judged) if text_lessons else 0.5)
        + 0.30 * (1.0 if (corpus_files and verify_files) else (0.6 if corpus_files else 0.0))
        + 0.20 * (1.0 if verify_files else 0.0)
        + 0.15 * (ratio(sum(1 for d in judged if d["citations"] > 3), n_judged) if text_lessons else 0.5)
    )
    # Practice: assignments plus a rubric plus self-check questions, each
    # graded by how many lessons actually carry them (already a ratio).
    practice = 5 * (
        0.35 * (ratio(sum(1 for d in judged if d["assignments"]), n_judged) if text_lessons else 0.5)
        + 0.30 * (ratio(sum(1 for d in judged if d["grading"]), n_judged) if text_lessons else 0.5)
        + 0.35 * (ratio(sum(1 for d in judged if d["self_check"]), n_judged) if text_lessons else 0.5)
    )
    # Reproducibility: now graded, so a bare requirements.txt (0.25) no
    # longer scores the same as a fully pinned one (1.0).
    repro = 5 * (
        0.30 * env_score
        + 0.25 * build_score
        + 0.25 * (1.0 if ci_files else 0.0)
        + 0.20 * nb_score
    )
    # Licensing rewards the substance, not the filename: any recognisable
    # licence artefact earns the base score, the canonical name earns full.
    licensing = 5 * (
        0.50 * (1 if license_files else (0.8 if all_license_artifacts else 0))
        + 0.30 * (1 if content_license else 0)
        + 0.20 * (1 if corpus_files else 0)
    )
    agent_ready = 5 * (
        0.30 * (1 if agent_files else 0)
        + 0.25 * (1 if syllabus_json else 0)
        + 0.20 * (1 if tool_dirs else 0)
        + 0.15 * (1 if mirror_dirs else 0)
        + 0.10 * (1 if verify_files else 0)
    )

    # Attach impact + collapse duplicates BEFORE storing, so the JSON
    # consumers (oca diff, report generator) see the same prioritised view
    # as the text renderer.
    findings = annotate_findings(findings)

    axes = {
        "structure": round(structure, 2),
        "content": round(content, 2),
        "practice": round(practice, 2),
        "reproducibility": round(repro, 2),
        "licensing": round(licensing, 2),
        "agent_readiness": round(agent_ready, 2),
    }
    weights = {"structure": 0.25, "content": 0.20, "practice": 0.20,
               "reproducibility": 0.15, "licensing": 0.10, "agent_readiness": 0.10}
    overall = round(sum(axes[k] * weights[k] for k in axes), 2)

    return {
        "root": str(root),
        "git": {"is_repo": git_repo, "commits": git_commit_count(root) if git_repo else None},
        "counts": {
            "files": len(files),
            "lectures": len(lecture_files),
            "notebooks": len(nb_paths),
            "markdown": sum(1 for p in flat if p.endswith(".md")),
        },
        "artifacts": {
            "readme": readme_path,
            "readme_chars": readme_chars,
            "license_files": license_files,
            "license_derived": derived,
            "license_dirs": license_dirs,
            "content_license": content_license,
            "syllabus": syllabus_path,
            "syllabus_json": syllabus_json,
            "environment": env_files,
            "build": build_files,
            "make_targets": make_targets,
            "ci": ci_files,
            "corpus": corpus_files,
            "verification": verify_files,
            "tests": test_files[:20],
            "agent_files": agent_files,
            "skill_mirrors": mirror_dirs,
            "tool_dirs": tool_dirs,
        },
        "lectures": lecture_detail,
        "notebooks": notebooks,
        "links": {"relative_links": links["relative_links"], "broken_count": len(links["broken"]),
                  "broken": links["broken"][:50],
                  # Collected, never fetched — see oca.linkcheck for the
                  # opt-in network pass and why it lives outside scan().
                  "external_count": len(links["external"]),
                  "external": links["external"][:200]},
        "scores": {"axes": axes, "weights": weights, "overall": overall,
                   "evidence": {
                       "environment": env_info,
                       "env_score": round(env_score, 3),
                       "build_score": round(build_score, 3),
                       "notebook_score": round(nb_score, 3),
                       "readme_score": round(readme_score, 3),
                       "syllabus_score": round(syl_score, 3),
                       "license_score": round(
                           1.0 if license_files else (0.8 if all_license_artifacts else 0.0), 3),
                   }},
        "findings": findings,
        "findings_summary": {
            **{lvl: sum(1 for f in findings if f["level"] == lvl)
               for lvl in ("BLOCKER", "MAJOR", "MINOR")},
            **summarise_findings(findings),
        },
    }


# ── graded scoring helpers ──────────────────────────────────────────────────
# Binary checks ("requirements.txt exists → 1.0") cannot tell a reproducible
# course from one that merely has the file. Measured across 14 real courses,
# `requirements.txt` came in two clearly different kinds:
#
#   10/10 lines pinned   deep-vision-and-graphics-shad/week04-.../requirements.txt
#    0/18 lines pinned   ai-studio-course/requirements.txt
#
# The first pins every dependency; the second lists bare names, so a student
# installing it next year gets different library versions than the author
# used. Both scored identically. These helpers award partial credit so the
# score reflects the difference.

def graded_ratio(num: float, den: float, floor: float = 0.0) -> float:
    """Ratio, never below `floor` when the denominator is non-zero."""
    if not den:
        return 0.0
    return max(floor, min(1.0, num / den))


def grade_env(env_files: list[str], flat: dict[str, Path], read_text_fn) -> tuple[float, dict]:
    """Score the environment by how reproducible it actually is (0..1).

    Steps: a lock file is best; a fully pinned requirements file is next;
    partly pinned is partial; bare names are barely better than nothing.
    """
    if not env_files:
        return 0.0, {"kind": "none"}

    names = {Path(p).name.lower() for p in env_files}
    # 1.0 — a lock file pins the whole transitive closure
    if names & {"poetry.lock", "uv.lock", "pipfile.lock", "renv.lock", "conda-lock.yml"}:
        return 1.0, {"kind": "lock"}

    # environment.yml / pyproject with pinned deps
    for p in env_files:
        if Path(p).name in ("environment.yml", "environment.yaml"):
            body = read_text_fn(flat[p])
            deps = [l for l in body.splitlines() if l.strip().startswith("-")]
            pinned = sum(1 for l in deps if re.search(r"[=<>!~]=?", l))
            return graded_ratio(pinned, len(deps), floor=0.4), {
                "kind": "environment.yml", "pinned": pinned, "total": len(deps)}
        if Path(p).name == "pyproject.toml":
            return 0.85, {"kind": "pyproject"}

    # requirements file: score by the share of pinned dependencies
    reqs = [p for p in env_files if Path(p).name.lower().startswith("requirements")]
    if reqs:
        best, info = 0.0, {"kind": "requirements"}
        for p in reqs:
            lines = [l.strip() for l in read_text_fn(flat[p]).splitlines()
                     if l.strip() and not l.strip().startswith(("#", "-"))]
            if not lines:
                continue
            # `==` is a hard pin; `>=`/`~=` bound the range but drift
            hard = sum(1 for l in lines if "==" in l)
            soft = sum(1 for l in lines if re.search(r"[<>!~]=", l))
            share = (hard + 0.5 * soft) / len(lines)
            if share > best:
                best, info = share, {"kind": "requirements", "pinned": hard + soft,
                                     "hard": hard, "total": len(lines), "file": p}
        # Bare names still count for something (the file is not worthless),
        # but must not be confused with a reproducible environment.
        return max(0.25, best), info

    return 0.4, {"kind": "other"}


def grade_build(build_files: list[str]) -> float:
    """A Makefile with real targets beats a lone run.sh."""
    if not build_files:
        return 0.0
    names = {Path(p).name.lower() for p in build_files}
    if names & {"justfile", "taskfile.yml"}:
        return 1.0
    if "makefile" in names:
        return 1.0
    return 0.6


def grade_notebooks(notebooks: dict) -> float:
    """Notebook cleanliness, 0..1.

    Previously this subtracted the raw count of notebooks with any issue,
    which let a course lose the entire weight for cosmetic cell-ordering
    warnings. Weighted by severity instead: a notebook that does not parse
    is a real defect, a stray empty cell is not.
    """
    if not notebooks:
        return 1.0
    penalty = 0.0
    for info in notebooks.values():
        if "parse_error" in info:
            penalty += 1.0
            continue
        issues = info.get("issues", [])
        # Cap per-notebook penalty so one huge messy notebook cannot zero the axis.
        penalty += min(0.3, 0.08 * len(issues))
    return max(0.0, 1.0 - penalty / max(len(notebooks), 1))


# ── findings noise control ──────────────────────────────────────────────────
# Not every finding deserves equal room in a report. Measured on 14 real
# courses, `notebook-hygiene` alone produced 835 findings — 69% of all
# output — while 8 genuine BLOCKERs drowned in it. So each finding code
# carries two extra attributes:
#
#   impact   — does this change whether the course is usable, or is it polish?
#   weight   — sort key, severity x impact. Higher = more urgent.
#
# `group` findings are collapsed into one line with a count instead of one
# line each: 431 cosmetic notes about execution_count is not 431 problems.
IMPACT = {
    # structural / legal — a reader cannot use or reuse the course
    "no-readme": "blocking",
    "no-license": "blocking",
    "no-lectures": "blocking",
    # materially reduce value
    "no-syllabus": "high",
    "no-assignments": "high",
    "no-grading": "high",
    "no-environment": "high",
    "broken-link": "high",
    "bad-notebook": "high",
    "stub-lecture": "high",
    "syllabus-mismatch": "high",
    "thin-readme": "high",
    "pdf-only-materials": "high",
    # worth fixing, not urgent
    "no-content-license": "medium",
    "no-corpus": "medium",
    "no-verification": "medium",
    "lecture-gaps": "medium",
    "no-ci": "medium",
    "no-build": "medium",
    # cosmetic / informational
    "notebook-hygiene": "low",
    "no-agents-md": "low",
    "no-machine-syllabus": "low",
}
IMPACT_ORDER = {"blocking": 0, "high": 1, "medium": 2, "low": 3}
IMPACT_LABELS = {
    "blocking": "БЛОКИРУЕТ",
    "high": "ВАЖНО",
    "medium": "СТОИТ ПОЧИНИТЬ",
    "low": "КОСМЕТИКА",
}
SEVERITY_ORDER = {"BLOCKER": 0, "MAJOR": 1, "MINOR": 2}
# Codes whose findings are collapsed into a counted group in text output.
# Only codes that repeat verbatim across files belong here: a group reports
# one message plus a count, so merging findings that differ in message would
# lose information.
GROUP_CODES = {"notebook-hygiene", "lecture-gaps", "broken-link", "bad-notebook"}


def finding_weight(f: dict) -> tuple:
    """Sort key: most urgent first.

    Ordered by impact, then severity, then number of affected files. Impact
    leads because severity alone is too coarse: `no-agents-md` and
    `no-content-license` are both MINOR, but one is polish and the other is
    a legal question.
    """
    return (
        IMPACT_ORDER.get(f.get("impact", "medium"), 2),
        SEVERITY_ORDER.get(f["level"], 2),
        -len(f.get("paths") or ([f["path"]] if f.get("path") else [])),
        f["code"],
    )


def annotate_findings(findings: list[dict]) -> list[dict]:
    """Attach impact and group identical findings."""
    for f in findings:
        f["impact"] = IMPACT.get(f["code"], "medium")

    groups: dict[tuple, dict] = {}
    out: list[dict] = []
    for f in findings:
        if f["code"] in GROUP_CODES:
            # Group on the message too. Two findings of the same code can say
            # different things ("no self_check" vs "no objectives, self_check");
            # merging them would silently drop the distinction and make the
            # group's message describe only its first member.
            key = (f["code"], f["level"], f["impact"], f["message"])
            g = groups.get(key)
            if g is None:
                g = {
                    "level": f["level"], "code": f["code"], "impact": f["impact"],
                    "message": f["message"], "path": f["path"],
                    "paths": [f["path"]] if f["path"] else [],
                    "count": 1,
                }
                groups[key] = g
                out.append(g)
            else:
                g["count"] += 1
                if f["path"]:
                    g["paths"].append(f["path"])
        else:
            out.append(f)

    out.sort(key=finding_weight)
    return out


def summarise_findings(findings: list[dict]) -> dict:
    """Counts per level plus per impact band, for the report header."""
    by_impact: dict[str, int] = {}
    for f in findings:
        by_impact[f.get("impact", "medium")] = by_impact.get(f.get("impact", "medium"), 0) + 1
    return {
        "total": len(findings),
        "by_impact": by_impact,
        "blocking": by_impact.get("blocking", 0),
    }


def _render_finding(f: dict) -> str:
    """One finding as a single readable line.

    A grouped finding reports its count instead of repeating itself 431
    times; a single finding shows its file path.
    """
    n = f.get("count", 1)
    head = f"[{f['level']}] {f['message']}"
    if n > 1:
        head += f"   ×{n}"
    paths = f.get("paths") or ([f["path"]] if f.get("path") else [])
    if paths:
        head += f"   ({paths[0]}" + (f" +{len(paths) - 1}" if len(paths) > 1 else "") + ")"
    return head


# ── rendering ───────────────────────────────────────────────────────────────

def render_text(r: dict) -> str:
    L: list[str] = []
    a = r["artifacts"]
    s = r["scores"]
    L.append(f"OCA scan: {r['root']}")
    L.append("=" * 72)
    L.append(f"Файлов: {r['counts']['files']} | занятий: {r['counts']['lectures']} | "
             f"ноутбуков: {r['counts']['notebooks']} | markdown: {r['counts']['markdown']}")
    if r["git"]["is_repo"]:
        L.append(f"Git: да, коммитов: {r['git']['commits']}")
    L.append("")
    L.append("Артефакты:")
    L.append(f"  README            : {a['readme'] or '— ОТСУТСТВУЕТ'} ({a['readme_chars']} симв.)")
    L.append(f"  LICENSE           : {', '.join(a['license_files']) or '— ОТСУТСТВУЕТ'}")
    if a.get("license_derived") or a.get("license_dirs"):
        extra = (a.get("license_derived") or []) + [
            d + "/" for d in (a.get("license_dirs") or [])]
        L.append(f"  Прочие лицензии   : {', '.join(extra)}")
    L.append(f"  Лицензия контента : {', '.join(a['content_license']) or '— не найдена'}")
    L.append(f"  Syllabus          : {a['syllabus'] or '— ОТСУТСТВУЕТ'}")
    L.append(f"  Окружение         : {', '.join(a['environment']) or '— нет'}")
    L.append(f"  Запуск            : {', '.join(a['build']) or '— нет'}"
             + (f" (цели: {', '.join(a['make_targets'][:8])})" if a['make_targets'] else ""))
    L.append(f"  CI                : {', '.join(a['ci']) or '— нет'}")
    L.append(f"  Корпус/источники  : {', '.join(a['corpus']) or '— нет'}")
    L.append(f"  Верификация       : {', '.join(a['verification']) or '— нет'}")
    L.append(f"  AGENTS.md         : {', '.join(a['agent_files']) or '— нет'}")
    L.append(f"  syllabus.json     : {', '.join(a['syllabus_json']) or '— нет'}")
    L.append(f"  Зеркала навыков   : {', '.join(a['skill_mirrors']) or '— нет'}")
    L.append(f"  Инструменты       : {', '.join(a['tool_dirs']) or '— нет'}")
    L.append("")
    L.append("Оценки по осям (0–5):")
    labels = {
        "structure": "Структура и полнота",
        "content": "Содержание и источники",
        "practice": "Практика и оценивание",
        "reproducibility": "Воспроизводимость",
        "licensing": "Лицензии и права",
        "agent_readiness": "Агент-готовность",
    }
    for k, v in s["axes"].items():
        bar = "█" * int(round(v)) + "·" * (5 - int(round(v)))
        L.append(f"  {labels[k]:<24} {bar} {v:.2f}  (вес {s['weights'][k]:.0%})")
    L.append(f"  {'ИТОГО':<24} {'':<5} {s['overall']:.2f} / 5")
    L.append("")
    fs = r["findings_summary"]
    tot = fs.get("total", len(r["findings"]))
    bands = fs.get("by_impact", {})
    L.append(f"Находки: BLOCKER={fs['BLOCKER']} MAJOR={fs['MAJOR']} MINOR={fs['MINOR']}"
             f"  (всего {tot})")
    if bands:
        L.append("  по важности: "
                 + " · ".join(f"{IMPACT_LABELS[k]}={bands[k]}"
                              for k in ("blocking", "high", "medium", "low") if k in bands))

    # Three blocks by impact, most urgent first. Within a block the sorted
    # order from annotate_findings is preserved.
    for band in ("blocking", "high", "medium", "low"):
        items = [f for f in r["findings"] if f.get("impact") == band]
        if not items:
            continue
        L.append(f"\n[{IMPACT_LABELS[band]}]  {len(items)}")
        for f in items:
            L.append("  • " + _render_finding(f))
            for extra in (f.get("paths") or [])[3:6]:
                L.append(f"      … {extra}")
            if len(f.get("paths") or []) > 6:
                L.append(f"      … и ещё {len(f['paths']) - 6}")

    if r["lectures"]:
        L.append("\nЗанятия:")
        for d in r["lectures"][:40]:
            flags = "".join(k[0].upper() if d[k] else "·"
                            for k in ("objectives", "assignments", "grading", "self_check", "sources"))
            L.append(f"  [{flags}] {d['path']}  ({d['chars']} симв., ссылок: {d['citations']})")
        L.append("  Флаги: O=цели A=задания G=критерии S=самопроверка R=источники")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description="OCA deterministic course-repository scanner")
    ap.add_argument("path", help="путь к репозиторию курса")
    ap.add_argument("--json", action="store_true", help="вывести JSON вместо текста")
    ap.add_argument("--quiet", action="store_true", help="только сводка")
    args = ap.parse_args()

    root = Path(args.path).expanduser().resolve()
    if not root.is_dir():
        print(f"error: {root} не является директорией", file=sys.stderr)
        return 2

    result = scan(root)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    elif args.quiet:
        s = result["scores"]
        fs = result["findings_summary"]
        print(f"overall={s['overall']} axes={s['axes']} "
              f"blockers={fs['BLOCKER']} major={fs['MAJOR']} minor={fs['MINOR']}")
    else:
        print(render_text(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
