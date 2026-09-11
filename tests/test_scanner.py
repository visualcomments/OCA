#!/usr/bin/env python3
"""Regression tests for the OCA scanner.

Run:  python tests/test_scanner.py      (no pytest required)
      python -m pytest tests/ -q        (if pytest is installed)

The suite encodes the calibration lessons learned on real courses. Each test
corresponds to a defect that actually shipped once — that is the point.
"""

from __future__ import annotations

import json
import os
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

    # ── 13. external links: collected offline, classified without network ──
    print("\n[external links]")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "README.md").write_text(
            "# Курс\n\n" + "описание. " * 40
            + "\n[ссылка](https://example.com/a) и голая https://example.com/b\n"
            + "[битая локальная](missing/file.md)\n",
            encoding="utf-8")
        nb = {"cells": [{"cell_type": "markdown", "metadata": {},
                         "source": ["См. https://arxiv.org/abs/1706.03762\\n"]}],
              "metadata": {}, "nbformat": 4, "nbformat_minor": 5}
        (root / "nb.ipynb").write_text(json.dumps(nb), encoding="utf-8")
        r = oca_scan.scan(root)

    urls = {e["url"] for e in r["links"]["external"]}
    check("markdown links are collected",
          "https://example.com/a" in urls, str(urls))
    check("bare markdown URLs are collected",
          "https://example.com/b" in urls, str(urls))
    check("notebook URLs are collected",
          any("arxiv.org" in u for u in urls), str(urls))
    check("trailing escape is stripped from notebook URLs",
          all("\\n" not in u for u in urls), str(urls))
    check("relative broken link still reported",
          r["links"]["broken_count"] == 1, str(r["links"]["broken"]))
    # The scan result holds Path objects, so it cannot be dumped directly.
    # What matters is that no status/verdict field exists at all: scan()
    # collects links but never judges them, which is what keeps it offline.
    check("scan records link counts but no verdicts",
          "external_count" in r["links"] and "by_status" not in r["links"],
          str(sorted(r["links"].keys())))
    check("scan output carries no cache or fetch metadata",
          not any(k in r["links"] for k in ("checked_at", "cache", "results")),
          str(sorted(r["links"].keys())))

    # Classification logic, exercised without any real request.
    print("\n[link classification without network]")
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from oca import linkcheck as lc

    check("DOI and publisher redirects count as ok",
          302 in lc.OK_STATUSES and 200 in lc.OK_STATUSES)
    check("403 is blocked, not broken",
          403 in lc.BLOCKED_STATUSES and 403 not in lc.OK_STATUSES)
    check("429 is blocked, not broken", 429 in lc.BLOCKED_STATUSES)
    check("404 is not treated as blocked", 404 not in lc.BLOCKED_STATUSES)
    check("messenger hosts are recognised as sandbox-limited",
          lc._is_sandbox_limited("https://t.me/someone", "Network is unreachable"))
    check("unreachable network error is sandbox-limited",
          lc._is_sandbox_limited("https://ordinary.edu/x", "Network is unreachable"))
    check("a plain 404 is NOT sandbox-limited",
          not lc._is_sandbox_limited("https://ordinary.edu/x", "HTTP 404"))
    check("host extraction handles subdomains",
          lc._host_of("https://a.b.example.com/x") == "a.b.example.com")

    # Cache round-trip: offline mode must serve without network.
    print("\n[link cache]")
    with tempfile.TemporaryDirectory() as td:
        cache = Path(td)
        lc.LinkCache(cache).put("https://example.com/a",
                                {"status": "ok", "code": 200, "reason": ""})
        report = lc.check_links([{"file": "README.md", "url": "https://example.com/a"}],
                                cache, offline=True)
        check("cached URL is served in offline mode",
              report["by_status"].get("ok") == 1, str(report["by_status"]))
        report2 = lc.check_links([{"file": "README.md", "url": "https://example.com/uncached"}],
                                 cache, offline=True)
        check("uncached URL is 'unknown' offline, never fetched",
              report2["by_status"].get("unknown") == 1, str(report2["by_status"]))

    # ── 14. the improvement plan ───────────────────────────────────────────
    print("\n[improvement plan]")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "README.md").write_text(
            "# Курс\n\n" + "описание. " * 40
            + "\n[битая1](missing/a.md)\n[битая2](missing/b.md)\n[битая3](missing/c.md)\n",
            encoding="utf-8")
        (root / "lectures").mkdir()
        for i in range(1, 4):
            (root / "lectures" / f"0{i}_t.md").write_text(
                f"# Тема {i}\n\n## Задания\n\nРешить.\n", encoding="utf-8")
        r = oca_scan.scan(root)

    plan = r["plan"]
    check("plan is produced", bool(plan), "empty plan")
    check("every plan row carries effort and benefit",
          all(p.get("effort") in ("S", "M", "L") and p["gain"] > 0 for p in plan),
          str([(p["code"], p.get("effort"), p["gain"]) for p in plan]))
    check("plan is ordered by benefit per hour, descending",
          all(plan[i]["priority"] >= plan[i + 1]["priority"] for i in range(len(plan) - 1)),
          str([p["priority"] for p in plan]))

    # Three broken links are three different messages but ONE action.
    broken = [p for p in plan if p["code"] == "broken-link"]
    check("repeated defects merge into a single plan row",
          len(broken) == 1, f"{len(broken)} broken-link rows")
    check("merged row reports its true scope",
          broken and broken[0]["count"] >= 3,
          f"count={broken[0]['count'] if broken else None}")

    # A BLOCKER must not be buried under a mass of cosmetic repeats.
    first_blocker = next((i for i, p in enumerate(plan)
                          if p["impact"] == "blocking"), None)
    check("a blocker appears near the top of the plan",
          first_blocker is not None and first_blocker <= 3,
          f"index={first_blocker}")

    # Effort is a property of the kind of fix, not of the instance count.
    lic = [p for p in plan if p["code"] == "no-license"]
    check("adding a LICENSE is a quick win",
          lic and lic[0]["effort"] == "S", str(lic))
    gaps = [p for p in plan if p["code"] == "lecture-gaps"]
    check("writing lesson content is heavy work",
          not gaps or gaps[0]["effort"] == "L", str(gaps))

    # ── 15. lesson layouts that broke the scanner on real repositories ─────
    # Every case below was found by scanning 16 real courses; the previous
    # behaviour is named in each check so a regression is recognisable.
    print("\n[lesson layout regressions]")

    def layout(files: dict[str, str], dirs: list[str] | None = None) -> dict:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "README.md").write_text("# Курс\n\n" + "описание. " * 40, encoding="utf-8")
            for d in (dirs or []):
                (root / d).mkdir(parents=True, exist_ok=True)
            for rel, body in files.items():
                p = root / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(body, encoding="utf-8")
            return oca_scan.scan(root)

    body = "# Занятие\n\n## Проверьте себя\n\nВопросы.\n"

    # week_01_DSP: underscore separator. Previously the module was swallowed
    # by de-duplication and a 12-week course scored as ONE lesson.
    r = layout({f"week_{i:02d}_topic/README.md": body for i in range(1, 13)})
    check("week_NN_name modules are counted separately",
          r["counts"]["lectures"] >= 10,
          f"got {r['counts']['lectures']}, expected 12")

    # homework01: no separator between word and number.
    r = layout({f"homework{i:02d}/task.md": body for i in range(1, 6)})
    check("homeworkNN modules are counted separately",
          r["counts"]["lectures"] >= 4,
          f"got {r['counts']['lectures']}, expected 5")

    # Textbook layout: one chapter per directory, each with README.md.
    r = layout({f"book/part-1/{i:02d}-topic/README.md": body for i in range(1, 7)})
    check("textbook chapters (folder/README.md) are lessons",
          r["counts"]["lectures"] >= 5,
          f"got {r['counts']['lectures']}, expected 6")

    # One lesson must not be counted three times (dir + notes.pdf + lecture.pdf).
    r = layout({
        "Лекции/Лекция 1/Лекция 1.pdf": "",
        "Лекции/Лекция 1/Заметки Лекция 1.pdf": "",
        "Лекции/Лекция 2/Лекция 2.pdf": "",
        "Лекции/Лекция 2/Заметки Лекция 2.pdf": "",
    })
    check("a lecture folder with PDFs counts once, not three times",
          r["counts"]["lectures"] == 2,
          f"got {r['counts']['lectures']}, expected 2")

    # …but a SECTION holding many lessons must not collapse.
    r = layout({f"Семинары/Семинар {i}/notes.md": body for i in range(1, 9)})
    check("a section of many lessons does not collapse into one",
          r["counts"]["lectures"] >= 7,
          f"got {r['counts']['lectures']}, expected 8")

    # PDF-only lessons must still be reported even when wrapped in a folder.
    r = layout({"Лекции/Лекция 1/Лекция 1.pdf": ""})
    check("PDF-only lessons are still reported from inside a folder",
          any(f["code"] == "pdf-only-materials" for f in r["findings"]),
          str([f["code"] for f in r["findings"]]))

    # ── 16. determinism across processes ──────────────────────────────────
    # The suite's other determinism checks run in ONE process, where set
    # iteration order is stable. The scanner is invoked as a separate
    # process in real use, where Python randomises string hashing — and a
    # bare `set` of top-level files made the licence list come out in a
    # different order each run, changing the findings text. Only a subprocess
    # with a forced hash seed catches that.
    print("\n[determinism across processes]")
    import subprocess

    fixture = Path(__file__).resolve().parent / "fixtures" / "numbered-md"
    pkg_root = Path(__file__).resolve().parent.parent
    outputs = []
    for seed in ("0", "1", "2"):
        env = dict(os.environ, PYTHONHASHSEED=seed, PYTHONPATH=str(pkg_root))
        proc = subprocess.run(
            [sys.executable, "-m", "oca.cli", "scan", str(fixture), "--json"],
            capture_output=True, text=True, env=env, timeout=120)
        if proc.returncode != 0:
            check(f"scan runs with PYTHONHASHSEED={seed}", False, proc.stderr[:200])
            continue
        outputs.append(proc.stdout)
    check("scan output is identical across hash seeds",
          len(outputs) == 3 and len(set(outputs)) == 1,
          f"{len(set(outputs))} distinct outputs from {len(outputs)} runs")

    # A directory whose contents differ only by name order must scan the same.
    print("\n[file order does not affect the result]")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "README.md").write_text("# Курс\n\n" + "описание. " * 40, encoding="utf-8")
        for name in ("LICENSE-code.md", "LICENSE-text.md", "NOTICE.md", "COPYING.md"):
            (root / name).write_text("MIT\n", encoding="utf-8")
        first = oca_scan.scan(root)["findings"]
        second = oca_scan.scan(root)["findings"]
        check("repeated scans of the same directory agree",
              json.dumps(first, sort_keys=True, default=str)
              == json.dumps(second, sort_keys=True, default=str),
              "findings differ between two scans")

    # ── 17. pluggable checkers ─────────────────────────────────────────────
    print("\n[pluggable checkers]")

    def checker_case(files: dict[str, str]) -> list[dict]:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "README.md").write_text("# Курс\n\n" + "описание. " * 40, encoding="utf-8")
            for rel, body in files.items():
                p = root / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(body, encoding="utf-8")
            r = oca_scan.scan(root)
            return r["findings"]

    # Dependencies checker
    findings = checker_case({"requirements.txt": "numpy\npandas\nscipy\n"})
    dep_codes = [f["code"] for f in findings if f["code"].startswith("dep-")]
    check("unpinned deps are flagged", "dep-unpinned" in dep_codes, str(dep_codes))

    findings = checker_case({"requirements.txt": "numpy==1.26.4\npandas==2.2.0\n"})
    dep_codes = [f["code"] for f in findings if f["code"].startswith("dep-")]
    check("fully pinned deps produce no dep findings", not dep_codes, str(dep_codes))

    # CI checker
    workflow = ("name: CI\non: push\njobs:\n  test:\n    runs-on: ubuntu-latest\n"
                "    steps:\n      - uses: actions/checkout@main\n      - run: echo hello\n")
    findings = checker_case({".github/workflows/ci.yml": workflow})
    ci_codes = [f["code"] for f in findings if f["code"].startswith("ci-")]
    check("unpinned action is flagged", "ci-unpinned-action" in ci_codes, str(ci_codes))
    check("CI without tests is flagged", "ci-no-tests" in ci_codes, str(ci_codes))

    # Testing checker
    findings = checker_case({"src/main.py": "print('hello')\n"})
    test_codes = [f["code"] for f in findings if f["code"].startswith("no-tests")]
    check("code without tests is flagged", bool(test_codes), str(test_codes))

    findings = checker_case({
        "src/main.py": "print('hello')\n",
        "tests/test_main.py": "import pytest\ndef test_x(): pass\n",
    })
    test_codes = [f["code"] for f in findings if f["code"] == "no-tests"]
    check("code with tests is not flagged", not test_codes, str(test_codes))

    # ── 18. configuration ──────────────────────────────────────────────────
    print("\n[configuration]")
    from oca.config import load_config

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cfg = load_config(root)
        check("defaults are used when no config exists",
              cfg["lesson_target"] == 8, str(cfg["lesson_target"]))

        (root / ".oca.yml").write_text(
            "lesson_target: 4\ndisabled_checkers:\n  - testing\n",
            encoding="utf-8")
        cfg = load_config(root)
        check("custom lesson_target is loaded",
              cfg["lesson_target"] == 4, str(cfg["lesson_target"]))
        check("disabled_checkers is loaded",
              "testing" in cfg["disabled_checkers"], str(cfg["disabled_checkers"]))


    # ── 19. руководства шагов не считаются занятиями ─────────────────────
    print("\n[guide steps are not lessons]")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        # A course with 3 real lessons plus a guide directory whose files are
        # numbered as process steps. An earlier version counted the guide
        # steps as lessons and reported phantom "lecture-gaps" findings --
        # measuring guide documents against a lesson checklist.
        (root / "lectures").mkdir()
        for i in range(1, 4):
            (root / "lectures" / f"{i:02d}_tema.md").write_text(
                f"# Занятие {i:02d}\n\n## Цели занятия\n\n- цель\n\n"
                "## Вопросы для самопроверки\n\n1. вопрос?\n\n"
                "## Задания\n\n- задание\n", encoding="utf-8")
        guide = root / "capstone-aviation-radar"
        guide.mkdir()
        for name in ("10-legal-public-domain.md", "20-assignments.md",
                     "30-dataset-spec-template.md", "45-community-competition.md"):
            (guide / name).write_text(f"# {name}\n\nтекст\n", encoding="utf-8")

        res = oca_scan.scan(str(root))
        check("guide steps are not counted as lessons",
              len(res["lectures"]) == 3, str(len(res["lectures"])))
        gaps = [f.get("path", "") for f in res["findings"]
                if f["code"] == "lecture-gaps"]
        phantom = [g for g in gaps if "capstone" in g]
        check("no phantom lecture-gaps from guide steps",
              not phantom, str(phantom))

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        # A round-numbered file in a real lecture directory IS a lesson.
        (root / "lectures").mkdir()
        for name in ("01_vvedenie.md", "10_arhitektura.md", "20_itog.md"):
            (root / "lectures" / name).write_text(
                "# Занятие\n\n## Цели занятия\n\n- цель\n", encoding="utf-8")
        res = oca_scan.scan(str(root))
        check("round numbers in a lecture dir stay lessons",
              len(res["lectures"]) == 3, str(len(res["lectures"])))

    # ── 20. content integrity: duplicate sections ────────────────────────
    print("\n[content integrity: duplicate sections]")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "lectures").mkdir()
        (root / "lectures" / "10_kant.md").write_text(
            "# Занятие 10\n\n## Тезис\n\nТекст.\n\n"
            "## Авторский синтез\n\nСинтез.\n\n"
            "## Вопросы для самопроверки\n\n1. Вопрос?\n\n"
            "## Задания\n\n- Задание.\n\n"
            "## Авторский синтез\n\nСинтез.\n\n"
            "## Вопросы для самопроверки\n\n1. Вопрос?\n\n"
            "## Задания\n\n- Задание.\n",
            encoding="utf-8")
        (root / "syllabus.md").write_text("# Программа\n", encoding="utf-8")
        res = oca_scan.scan(str(root))
        codes = [f["code"] for f in res["findings"]]
        check("duplicate-sections detected",
              "duplicate-sections" in codes, str(codes))

    # ── 21. content integrity: orphaned report line ──────────────────────
    print("\n[content integrity: orphaned report line]")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "lectures").mkdir()
        (root / "lectures" / "16_popper.md").write_text(
            "# Занятие 16\n\n## Тезис\n\nТекст.\n\n"
            "> `verification/REPORT.md`)\n\n"
            "## Вопросы для самопроверки\n\n1. Вопрос?\n",
            encoding="utf-8")
        (root / "syllabus.md").write_text("# Программа\n", encoding="utf-8")
        res = oca_scan.scan(str(root))
        codes = [f["code"] for f in res["findings"]]
        check("orphaned-report-line detected",
              "orphaned-report-line" in codes, str(codes))

    # ── 22. content integrity: OCR in questions ──────────────────────────
    print("\n[content integrity: OCR in questions]")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "lectures").mkdir()
        (root / "lectures" / "09_berkli.md").write_text(
            "# Занятие 9\n\n## Тезис\n\nТекст.\n\n"
            "## Вопросы для самопроверки\n\n"
            "1. Что утверждает имматuOME People are fubject to a certain "
            "delicacy of paffion and adverfity\n\n"
            "## Задания\n\n- Задание.\n",
            encoding="utf-8")
        (root / "syllabus.md").write_text("# Программа\n", encoding="utf-8")
        res = oca_scan.scan(str(root))
        codes = [f["code"] for f in res["findings"]]
        check("ocr-in-questions detected",
              "ocr-in-questions" in codes, str(codes))

    # Clean lecture does NOT trigger content integrity findings
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "lectures").mkdir()
        (root / "lectures" / "01_intro.md").write_text(
            "# Занятие 1\n\n## Тезис\n\nТекст.\n\n"
            "## Цели занятия\n\n- Цель.\n\n"
            "## Вопросы для самопроверки\n\n1. Вопрос?\n2. Ещё?\n\n"
            "## Задания\n\n- Задание.\n\n"
            "**Навигация:** [← 00](00.md) · [Программа](../syllabus.md)\n",
            encoding="utf-8")
        (root / "syllabus.md").write_text(
            "# Программа\n\n| 01 | [Введение](lectures/01_intro.md) |\n",
            encoding="utf-8")
        res = oca_scan.scan(str(root))
        ci_codes = [f["code"] for f in res["findings"]
                    if f["code"] in ("duplicate-sections",
                                     "orphaned-report-line",
                                     "ocr-in-questions")]
        check("clean lecture has no content-integrity findings",
              not ci_codes, str(ci_codes))

    # ── 23. syllabus-lecture links ───────────────────────────────────────
    print("\n[syllabus-lecture links]")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "lectures").mkdir()
        (root / "lectures" / "01_intro.md").write_text(
            "# Занятие\n", encoding="utf-8")
        (root / "lectures" / "02_topic.md").write_text(
            "# Занятие\n", encoding="utf-8")
        (root / "syllabus.md").write_text(
            "# Программа\n\n| № | Тема |\n|---|---|\n| 01 | Введение |\n",
            encoding="utf-8")
        res = oca_scan.scan(str(root))
        codes = [f["code"] for f in res["findings"]]
        check("syllabus-no-lecture-links detected",
              "syllabus-no-lecture-links" in codes, str(codes))

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "lectures").mkdir()
        (root / "lectures" / "01_intro.md").write_text(
            "# Занятие\n", encoding="utf-8")
        (root / "syllabus.md").write_text(
            "# Программа\n\n| 01 | [Введение](lectures/01_intro.md) |\n",
            encoding="utf-8")
        res = oca_scan.scan(str(root))
        codes = [f["code"] for f in res["findings"]]
        check("no false positive when syllabus links lectures",
              "syllabus-no-lecture-links" not in codes, str(codes))

    # ── 24. lesson structure: goals and navigation ───────────────────────
    print("\n[lesson structure: goals and navigation]")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "lectures").mkdir()
        (root / "lectures" / "01_bare.md").write_text(
            "# Занятие 1\n\n## Тезис\n\nТекст.\n\n"
            "## Вопросы для самопроверки\n\n1. Вопрос?\n",
            encoding="utf-8")
        (root / "syllabus.md").write_text("# Программа\n", encoding="utf-8")
        res = oca_scan.scan(str(root))
        codes = [f["code"] for f in res["findings"]]
        check("no-learning-goals detected",
              "no-learning-goals" in codes, str(codes))
        check("no-navigation detected",
              "no-navigation" in codes, str(codes))

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "lectures").mkdir()
        (root / "lectures" / "01_full.md").write_text(
            "# Занятие 1\n\n## Тезис\n\nТекст.\n\n"
            "## Цели занятия\n\n- Цель.\n\n"
            "## Вопросы для самопроверки\n\n1. Вопрос?\n\n"
            "**Навигация:** [← 00](00.md) · [Программа](../syllabus.md)\n",
            encoding="utf-8")
        (root / "syllabus.md").write_text("# Программа\n", encoding="utf-8")
        res = oca_scan.scan(str(root))
        struct_codes = [f["code"] for f in res["findings"]
                        if f["code"] in ("no-learning-goals", "no-navigation")]
        check("full lecture has no structure findings",
              not struct_codes, str(struct_codes))

    # ── 25. coordinate debt ──────────────────────────────────────────────
    print("\n[coordinate debt]")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "lectures").mkdir()
        (root / "verification").mkdir()
        (root / "verification" / "REPORT.md").write_text(
            "# Отчёт\n", encoding="utf-8")
        (root / "lectures" / "01_intro.md").write_text(
            "# Занятие\n\n> **Цитата:** «текст»\n"
            "**Источник:** `file.txt` · фрагмент —\n",
            encoding="utf-8")
        (root / "syllabus.md").write_text("# Программа\n", encoding="utf-8")
        res = oca_scan.scan(str(root))
        codes = [f["code"] for f in res["findings"]]
        check("coordinates-missing-number detected",
              "coordinates-missing-number" in codes, str(codes))

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "lectures").mkdir()
        (root / "lectures" / "01_intro.md").write_text(
            "# Занятие\n\n**Источник:** `file.txt` · фрагмент —\n",
            encoding="utf-8")
        (root / "syllabus.md").write_text("# Программа\n", encoding="utf-8")
        res = oca_scan.scan(str(root))
        codes = [f["code"] for f in res["findings"]]
        check("no coordinate-debt finding without verification report",
              "coordinates-missing-number" not in codes, str(codes))

    # ── 26. CI: python3 and bash selftest recognised ─────────────────────
    print("\n[CI: python3 and bash selftest]")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / ".github" / "workflows").mkdir(parents=True)
        (root / ".github" / "workflows" / "ci.yml").write_text(
            "name: CI\non: push\njobs:\n  test:\n    runs-on: ubuntu-latest\n"
            "    steps:\n      - run: python3 tests/test_tools.py\n"
            "      - run: bash .github/scripts/selftest.sh\n",
            encoding="utf-8")
        (root / "tests").mkdir()
        (root / "tests" / "test_tools.py").write_text(
            "def test_x(): pass\n", encoding="utf-8")
        res = oca_scan.scan(str(root))
        codes = [f["code"] for f in res["findings"]]
        check("python3 + bash selftest counts as tests",
              "ci-no-tests" not in codes, str(codes))

    print(f"\n{_passed} passed, {len(_failures)} failed")
    if _failures:
        for f in _failures:
            print(f"  - {f}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
