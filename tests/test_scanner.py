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

    print(f"\n{_passed} passed, {len(_failures)} failed")
    if _failures:
        for f in _failures:
            print(f"  - {f}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
