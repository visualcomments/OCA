#!/usr/bin/env python3
"""Compare two OCA scan JSON results and print the delta.

Usage:
    python3 oca_diff.py before.json after.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

AXIS_LABELS = {
    "structure": "Структура и полнота",
    "content": "Содержание и источники",
    "practice": "Практика и оценивание",
    "reproducibility": "Воспроизводимость",
    "licensing": "Лицензии и права",
    "agent_readiness": "Агент-готовность",
}


def load(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def instances(findings: list[dict]) -> dict:
    """Expand findings into one record per affected file.

    A grouped finding holds many file paths in `paths` while a single one
    holds just `path`. To compare two scans honestly we expand BOTH sides into
    one record per affected file: otherwise collapsing 431 notes into a single
    group reads as "431 findings closed", which is false.

    Exposed at module level (not nested inside `main`) so the property can be
    tested directly.
    """
    out: dict[tuple, int] = {}
    for f in findings:
        paths = f.get("paths") or ([f["path"]] if f.get("path") else [""])
        n = f.get("count", 1)
        # A group's count may exceed its stored paths (paths are capped);
        # keep the undistributed remainder under an empty-path key so the
        # totals still match.
        for p in paths:
            key = (f["code"], p, f["message"])
            out[key] = out.get(key, 0) + 1
        if n > len(paths):
            key = (f["code"], "", f["message"])
            out[key] = out.get(key, 0) + (n - len(paths))
    return out


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2

    before, after = load(sys.argv[1]), load(sys.argv[2])

    print("OCA delta")
    print("=" * 64)

    b, a = before["scores"]["axes"], after["scores"]["axes"]
    for key, label in AXIS_LABELS.items():
        bv, av = b.get(key, 0.0), a.get(key, 0.0)
        d = av - bv
        arrow = "→" if abs(d) < 0.005 else ("↑" if d > 0 else "↓")
        print(f"  {label:<24} {bv:5.2f} {arrow} {av:5.2f}   ({d:+.2f})")

    bo = before["scores"]["overall"]
    ao = after["scores"]["overall"]
    print(f"  {'ИТОГО':<24} {bo:5.2f} → {ao:5.2f}   ({ao - bo:+.2f})")

    print()
    bf = before["findings_summary"]
    af = after["findings_summary"]
    for lvl in ("BLOCKER", "MAJOR", "MINOR"):
        print(f"  {lvl:<8} {bf.get(lvl, 0):3d} → {af.get(lvl, 0):3d}")

    bc_all, ac_all = instances(before["findings"]), instances(after["findings"])

    closed = bc_all.keys() - ac_all.keys()
    opened = ac_all.keys() - bc_all.keys()
    resized = [(k, bc_all[k], ac_all[k]) for k in bc_all.keys() & ac_all.keys()
               if bc_all[k] != ac_all[k]]

    print()
    if resized:
        print(f"Изменилось количество экземпляров: {len(resized)}")
        for (code, path, msg), b_n, a_n in sorted(resized)[:12]:
            print(f"  ± [{code}] {msg[:60]}  {b_n}→{a_n}" + (f"  ({path})" if path else ""))
        if len(resized) > 12:
            print(f"  …и ещё {len(resized) - 12}")

    if closed:
        print(f"\nЗакрыто находок: {len(closed)}")
        for code, path, msg in sorted(closed)[:24]:
            print(f"  ✓ [{code}] {msg}" + (f"  ({path})" if path else ""))
        if len(closed) > 24:
            print(f"  …и ещё {len(closed) - 24}")
    if opened:
        print(f"\nНовых находок: {len(opened)}")
        for code, path, msg in sorted(opened)[:24]:
            print(f"  ✗ [{code}] {msg}" + (f"  ({path})" if path else ""))
        if len(opened) > 24:
            print(f"  …и ещё {len(opened) - 24}")
    if not closed and not opened and not resized:
        print("Состав находок не изменился.")

    print()
    bc, ac = before["counts"], after["counts"]
    print("Объём: "
          f"файлов {bc['files']}→{ac['files']}, "
          f"занятий {bc['lectures']}→{ac['lectures']}, "
          f"ноутбуков {bc['notebooks']}→{ac['notebooks']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
