#!/usr/bin/env python3
"""Regression tests for the OCA scanner.

Run:  python tests/test_scanner.py      (no pytest required)
      python -m pytest tests/ -q        (if pytest is installed)

The suite encodes the calibration lessons learned on real courses. Each test
corresponds to a defect that actually shipped once — that is the point.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from oca.scanner import oca_scan  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"

_failures: list[str] = []
_passed = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global _passed
    if cond:
        _passed += 1
        print(f"  ok   {name}")
    else:
        _failures.append(f"{name}: {detail}")
        print(f"  FAIL {name}  {detail}")


def scan_fixture(name: str) -> dict:
    return oca_scan.scan(FIXTURES / name)


def main() -> int:
    print("OCA scanner regression suite\n")

    # ── 1. numbered markdown lectures (ip-law-course layout) ────────────────
    print("[numbered markdown lectures]")
    r = scan_fixture("numbered-md")
    check("detects 3 lessons from NN_topic.md",
          len(r["lectures"]) == 3, f"got {len(r['lectures'])}: {[d['path'] for d in r['lectures']]}")
    check("does NOT collapse lectures/ into one container",
          not any(d["path"] == "lectures" for d in r["lectures"]),
          "lectures/ was collapsed")
    check("finds no blockers", r["findings_summary"]["BLOCKER"] == 0,
          str([f["code"] for f in r["findings"] if f["level"] == "BLOCKER"]))

    # ── 2. Cyrillic dirs + PDF only (optimization-methods layout) ───────────
    print("\n[Cyrillic dirs, PDF-only]")
    r = scan_fixture("cyrillic-pdf")
    check("detects lessons under Лекции/",
          len(r["lectures"]) >= 2, f"got {len(r['lectures'])}")
    check("reports pdf-only-materials",
          any(f["code"] == "pdf-only-materials" for f in r["findings"]),
          str([f["code"] for f in r["findings"]]))
    check("does NOT claim PDF lessons lack objectives",
          not any(f["code"] == "lecture-gaps" for f in r["findings"]),
          "false 'lecture-gaps' on PDF lessons")

    # ── 3. semester modules must not be double-counted ──────────────────────
    print("\n[semester modules, no double counting]")
    r = scan_fixture("semester-modules")
    check("counts modules, not every file inside",
          len(r["lectures"]) <= 4, f"got {len(r['lectures'])}: {[d['path'] for d in r['lectures']]}")

    # ── 4. lab* dirs ────────────────────────────────────────────────────────
    print("\n[lab directories]")
    r = scan_fixture("labs")
    check("detects lab1/lab2 as lessons",
          len(r["lectures"]) >= 2, f"got {len(r['lectures'])}")

    # ── 5. licence detection ────────────────────────────────────────────────
    print("\n[licensing]")
    r = scan_fixture("numbered-md")
    check("LICENSE present -> no no-license blocker",
          not any(f["code"] == "no-license" for f in r["findings"]))
    check("LICENSE-CONTENT recognised",
          not any(f["code"] == "no-content-license" for f in r["findings"]),
          "content licence not recognised")

    # ── 6. empty repo is genuinely bad ──────────────────────────────────────
    print("\n[empty repository]")
    with tempfile.TemporaryDirectory() as td:
        r = oca_scan.scan(Path(td))
    check("empty dir scores low", r["scores"]["overall"] < 2.0,
          f"got {r['scores']['overall']}")
    check("empty dir has blockers", r["findings_summary"]["BLOCKER"] > 0)

    # ── 7. determinism ──────────────────────────────────────────────────────
    print("\n[determinism]")
    a = scan_fixture("numbered-md")
    b = scan_fixture("numbered-md")
    check("same input -> same output",
          json.dumps(a, sort_keys=True, default=str) == json.dumps(b, sort_keys=True, default=str))

    # ── 8. invalid path ─────────────────────────────────────────────────────
    print("\n[error handling]")
    try:
        oca_scan.scan(Path("/nonexistent/oca-test"))
        check("missing path raises", False, "no exception")
    except (FileNotFoundError, NotADirectoryError, SystemExit):
        check("missing path raises", True)

    # ── 9. finding prioritisation and grouping ─────────────────────────────
    print("\n[prioritisation and grouping]")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "README.md").write_text("# Курс\n\n" + "описание. " * 40, encoding="utf-8")
        # no LICENSE -> BLOCKER; no syllabus -> MAJOR; several noisy notebooks
        (root / "lectures").mkdir()
        for i in range(1, 4):
            (root / "lectures" / f"0{i}_t.md").write_text(
                f"# Тема {i}\n\n## Задания\n\nРешить.\n", encoding="utf-8")
        nb = {"cells": [{"cell_type": "code", "execution_count": None, "outputs": [],
                         "metadata": {}, "source": ["x = 1"]}],
              "metadata": {}, "nbformat": 4, "nbformat_minor": 5}
        for i in range(5):
            (root / f"nb{i}.ipynb").write_text(json.dumps(nb), encoding="utf-8")
        r = oca_scan.scan(root)

    by_code = {f["code"]: f for f in r["findings"]}
    check("every finding carries an impact",
          all("impact" in f for f in r["findings"]), "some finding lacks impact")
    check("BLOCKER sorts before cosmetic findings",
          r["findings"][0].get("impact") == "blocking",
          f"first impact={r['findings'][0].get('impact')}")
    check("no-license is blocking",
          by_code.get("no-license", {}).get("impact") == "blocking")
    check("notebook-hygiene is low impact",
          by_code.get("notebook-hygiene", {}).get("impact") == "low")
    hygiene = by_code.get("notebook-hygiene")
    check("repeated notebook findings collapse into one group",
          hygiene is not None and hygiene.get("count", 0) >= 5,
          f"count={hygiene.get('count') if hygiene else None}")
    check("grouped finding keeps every affected path",
          hygiene is not None and len(hygiene.get("paths") or []) >= 5,
          f"paths={len(hygiene.get('paths') or []) if hygiene else 0}")
    check("summary reports impact bands",
          r["findings_summary"].get("blocking", 0) >= 1,
          str(r["findings_summary"]))

    # Grouping must not merge findings that say different things.
    print("\n[grouping preserves distinct messages]")
    msgs = [f["message"] for f in r["findings"] if f["code"] == "lecture-gaps"]
    check("distinct lecture-gaps messages stay separate",
          len(msgs) == len(set(msgs)), f"{msgs}")

    # ── 10. scoring is unaffected by presentation ──────────────────────────
    print("\n[graded scoring]")
    a = scan_fixture("numbered-md")
    # The fixture's requirements.txt holds one bare line ("pytest"), so the
    # environment is deliberately not reproducible, and a graded score must
    # sit below the old binary 1.0 rather than pretend otherwise.
    check("bare requirements.txt is not scored as reproducible",
          a["scores"]["evidence"]["env_score"] < 0.5,
          f"env_score={a['scores']['evidence']['env_score']}")
    check("graded overall is stable",
          abs(a["scores"]["overall"] - 2.93) < 0.02,
          f"got {a['scores']['overall']}")

    # ── 11. licence detection beyond the canonical name ────────────────────
    print("\n[licence naming variants]")

    def licence_case(name: str, files: dict[str, str]) -> dict:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "README.md").write_text("# Курс\n\n" + "описание. " * 40, encoding="utf-8")
            (root / "lectures").mkdir()
            (root / "lectures" / "01_t.md").write_text("# Тема\n\n## Задания\n\nРешить.\n",
                                                       encoding="utf-8")
            for rel, body in files.items():
                p = root / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(body, encoding="utf-8")
            return oca_scan.scan(root)

    def codes(r: dict) -> list[str]:
        return [f["code"] for f in r["findings"] if "licen" in f["code"]]

    # flutter-mipt: split code/text licences, no plain LICENSE
    r = licence_case("split", {
        "LICENSE-code.md": "# Лицензия на код\n\nMIT\n",
        "LICENSE-text.md": "# Лицензия на текст\n\nCreative Commons CC BY-SA 4.0\n",
    })
    check("split licence files are not a BLOCKER",
          "no-license" not in codes(r), str(codes(r)))
    check("unconventional name is reported as informational",
          "nonstandard-license-name" in codes(r), str(codes(r)))
    check("text licence recognised as content licence",
          "no-content-license" not in codes(r), str(codes(r)))

    # scireason: LICENSE + LICENSE_SCOPE.md + LICENSES/*.txt
    r = licence_case("scope", {
        "LICENSE": "GNU GENERAL PUBLIC LICENSE Version 3\n",
        "LICENSE_SCOPE.md": "# License scope\n\nApplies to course plans, assignments, "
                            "rubrics, teaching notes and other educational materials.\n",
        "LICENSES/GPL-3.0-or-later.txt": "GNU GENERAL PUBLIC LICENSE Version 3\n",
    })
    check("licence directory is recognised",
          "no-license" not in codes(r), str(codes(r)))
    check("scope file counts as content licence",
          "no-content-license" not in codes(r), str(codes(r)))
    check("canonical LICENSE earns the full licence-and-content weight",
          # 0.50 (canonical LICENSE) + 0.30 (content licence) = 0.80 of 5 = 4.0.
          # The remaining 0.20 is the corpus/provenance artefact, absent here.
          abs(r["scores"]["axes"]["licensing"] - 4.0) < 0.01,
          f"got {r['scores']['axes']['licensing']}")
    check("split names score below a canonical file but well above zero",
          3.0 < licence_case("split2", {
              "LICENSE-code.md": "MIT\n",
              "LICENSE-text.md": "Creative Commons CC BY-SA 4.0\n",
          })["scores"]["axes"]["licensing"] < 4.0,
          "unexpected split-licence score")

    # A course with genuinely no licence must still be blocked.
    r = licence_case("none", {"notes.txt": "hello\n"})
    check("no licence at all is still a BLOCKER",
          "no-license" in codes(r), str(codes(r)))

    # A file that merely contains the word must not count.
    r = licence_case("false-positive", {"LICENSE_PLACEHOLDER.txt": "TODO\n"})
    check("bare placeholder is not mistaken for a granted licence",
          r["scores"]["axes"]["licensing"] < 5.0,
          f"got {r['scores']['axes']['licensing']}")

    # ── 12. the grading ladder itself ──────────────────────────────────────
    print("\n[grading ladder]")

    def env_case(body: str, filename: str = "requirements.txt") -> dict:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "README.md").write_text("# Курс\n\n" + "описание. " * 40, encoding="utf-8")
            (root / filename).write_text(body, encoding="utf-8")
            return oca_scan.scan(root)["scores"]["evidence"]["env_score"]

    bare = env_case("numpy\npandas\nscipy\n")
    pinned = env_case("numpy==1.26.4\npandas==2.2.0\nscipy==1.12.0\n")
    soft = env_case("numpy>=1.26\npandas>=2.2\nscipy>=1.12\n")
    lock = env_case("numpy==1.26.4\n", "poetry.lock")

    check("bare names score low", bare < 0.4, f"got {bare}")
    check("hard pins score high", pinned > 0.8, f"got {pinned}")
    check("pinned beats bare", pinned > bare, f"{pinned} vs {bare}")
    check("range bounds score between bare and pinned",
          bare < soft < pinned, f"bare={bare} soft={soft} pinned={pinned}")
    check("lock file scores full", lock == 1.0, f"got {lock}")

    # Notebook grading must not let cosmetic warnings zero the axis.
    print("\n[notebook grading is severity-weighted]")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "README.md").write_text("# Курс\n\n" + "описание. " * 40, encoding="utf-8")
        messy = {"cells": [{"cell_type": "code", "execution_count": None, "outputs": [],
                            "metadata": {}, "source": ["x = 1"]} for _ in range(20)],
                 "metadata": {}, "nbformat": 4, "nbformat_minor": 5}
        for i in range(4):
            (root / f"nb{i}.ipynb").write_text(json.dumps(messy), encoding="utf-8")
        r = oca_scan.scan(root)
    check("cosmetic notebook warnings do not zero reproducibility",
          r["scores"]["axes"]["reproducibility"] > 0.0,
          f"got {r['scores']['axes']['reproducibility']}")
    check("notebook score stays above the floor",
          r["scores"]["evidence"]["notebook_score"] >= 0.0,
          f"got {r['scores']['evidence']['notebook_score']}")

    print(f"\n{_passed} passed, {len(_failures)} failed")
    if _failures:
        for f in _failures:
            print(f"  - {f}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
