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

    def key(f: dict) -> tuple:
        return (f["code"], f.get("path") or "", f["message"])

    bset = {key(f) for f in before["findings"]}
    aset = {key(f) for f in after["findings"]}

    closed = bset - aset
    opened = aset - bset

    print()
    if closed:
        print(f"Закрыто находок: {len(closed)}")
        for code, path, msg in sorted(closed):
            print(f"  ✓ [{code}] {msg}" + (f"  ({path})" if path else ""))
    if opened:
        print(f"\nНовых находок: {len(opened)}")
        for code, path, msg in sorted(opened):
            print(f"  ✗ [{code}] {msg}" + (f"  ({path})" if path else ""))
    if not closed and not opened:
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
