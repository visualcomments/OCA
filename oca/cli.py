"""OCA command-line interface.

    oca scan   <repo> [--json] [--quiet]
    oca diff   <before.json> <after.json>
    oca report <repo> [-o oca-report.md]
    oca list-templates

The CLI is a thin wrapper: all real work happens in ``oca.scanner``.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from oca import __version__
from oca.scanner import oca_diff, oca_scan

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"


def _cmd_scan(args: argparse.Namespace) -> int:
    root = Path(args.path).expanduser().resolve()
    if not root.is_dir():
        print(f"oca: {root} is not a directory", file=sys.stderr)
        return 2

    result = oca_scan.scan(root)

    if args.json:
        out = json.dumps(result, ensure_ascii=False, indent=2, default=str)
        if args.output:
            Path(args.output).write_text(out + "\n", encoding="utf-8")
            print(f"oca: wrote {args.output}")
        else:
            print(out)
    elif args.quiet:
        s, fs = result["scores"], result["findings_summary"]
        print(f"overall={s['overall']} axes={s['axes']} "
              f"blockers={fs['BLOCKER']} major={fs['MAJOR']} minor={fs['MINOR']}")
    else:
        text = oca_scan.render_text(result)
        if args.output:
            Path(args.output).write_text(text + "\n", encoding="utf-8")
            print(f"oca: wrote {args.output}")
        else:
            print(text)
    return 0


def _cmd_diff(args: argparse.Namespace) -> int:
    sys.argv = ["oca_diff", args.before, args.after]
    return oca_diff.main()


def _cmd_report(args: argparse.Namespace) -> int:
    """Emit a report skeleton pre-filled with deterministic findings.

    The judgement sections (content quality, priorities, verdict) are left as
    explicit placeholders on purpose: a model or a human must fill them from
    the course files, never from guesswork.
    """
    root = Path(args.path).expanduser().resolve()
    if not root.is_dir():
        print(f"oca: {root} is not a directory", file=sys.stderr)
        return 2

    r = oca_scan.scan(root)
    s, a, fs = r["scores"], r["artifacts"], r["findings_summary"]

    L: list[str] = []
    L.append(f"# OCA-отчёт: {root.name}\n")
    L.append(f"**Путь:** `{root}`  ")
    if r["git"]["is_repo"]:
        L.append(f"**Коммитов:** {r['git']['commits']}  ")
    L.append(f"**Дата аудита:** <заполнить>  ")
    L.append(f"**Инструмент:** OCA v{__version__}\n")
    L.append("> Разделы ниже сгенерированы детерминированно. Разделы, помеченные")
    L.append("> `<!-- TODO -->`, требуют чтения файлов курса и не заполняются")
    L.append("> автоматически.\n")

    L.append("---\n\n## 1. Сводка\n")
    labels = {
        "structure": "Структура и полнота",
        "content": "Содержание и источники",
        "practice": "Практика и оценивание",
        "reproducibility": "Воспроизводимость",
        "licensing": "Лицензии и права",
        "agent_readiness": "Агент-готовность",
    }
    L.append("| Ось | Балл | Вес | Вклад |")
    L.append("|---|---|---|---|")
    for k, label in labels.items():
        v = s["axes"][k]
        L.append(f"| {label} | {v:.2f} | {s['weights'][k]:.0%} | {v * s['weights'][k]:.2f} |")
    L.append(f"| **ИТОГО** | **{s['overall']:.2f} / 5** | | |\n")
    L.append(f"Находки: BLOCKER={fs['BLOCKER']} · MAJOR={fs['MAJOR']} · MINOR={fs['MINOR']}\n")
    L.append("**Вердикт:** <!-- TODO: одна фраза — готов ли курс, что мешает -->\n")

    L.append("---\n\n## 2. Критические находки (BLOCKER)\n")
    blockers = [f for f in r["findings"] if f["level"] == "BLOCKER"]
    if blockers:
        for f in blockers:
            L.append(f"- **{f['code']}** — {f['message']}"
                     + (f"  \n  `{f['path']}`" if f["path"] else ""))
    else:
        L.append("Блокеров не найдено.")
    L.append("")

    L.append("---\n\n## 3. Существенные находки (MAJOR)\n")
    majors = [f for f in r["findings"] if f["level"] == "MAJOR"]
    if majors:
        for f in majors[:40]:
            L.append(f"- **{f['code']}** — {f['message']}"
                     + (f"  \n  `{f['path']}`" if f["path"] else ""))
        if len(majors) > 40:
            L.append(f"- …и ещё {len(majors) - 40}")
    else:
        L.append("Существенных находок нет.")
    L.append("")

    L.append("---\n\n## 4. Улучшения (по приоритету)\n")
    L.append("<!-- TODO: отсортировать по отношению польза/трудозатраты -->\n")
    L.append("| # | Улучшение | Ось | Польза | Труд | Файлы |")
    L.append("|---|---|---|---|---|---|")
    L.append("| 1 | | | | | |\n")

    L.append("---\n\n## 5. Артефакты\n")
    L.append("| Артефакт | Состояние |")
    L.append("|---|---|")
    def st(v: object) -> str:
        if isinstance(v, list):
            return ", ".join(f"`{x}`" for x in v) if v else "— нет"
        return f"`{v}`" if v else "— нет"
    L.append(f"| README | {st(a['readme'])} ({a['readme_chars']} симв.) |")
    L.append(f"| LICENSE | {st(a['license_files'])} |")
    L.append(f"| Лицензия контента | {st(a['content_license'])} |")
    L.append(f"| Syllabus | {st(a['syllabus'])} |")
    L.append(f"| Окружение | {st(a['environment'])} |")
    L.append(f"| Запуск | {st(a['build'])} |")
    L.append(f"| CI | {st(a['ci'])} |")
    L.append(f"| Корпус/источники | {st(a['corpus'])} |")
    L.append(f"| Верификация | {st(a['verification'])} |")
    L.append(f"| AGENTS.md | {st(a['agent_files'])} |")
    L.append(f"| syllabus.json | {st(a['syllabus_json'])} |")
    L.append("")

    L.append("---\n\n## 6. Метод\n")
    L.append(f"- Детерминированный скан: `oca scan` v{__version__} — "
             f"{r['counts']['files']} файлов, {r['counts']['lectures']} занятий, "
             f"{r['counts']['notebooks']} ноутбуков.")
    L.append("- Прочитанные вручную файлы: <!-- TODO: перечислить -->")
    L.append("- Ограничения: <!-- TODO: что НЕ проверялось -->")
    L.append("")

    L.append("---\n\n## 7. Суждения (JUDGEMENT)\n")
    L.append("<!-- TODO: оценочные утверждения с обоснованием; иначе «нет» -->")

    out = Path(args.output or (root / "oca-report.md"))
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"oca: wrote {out}")
    print(f"oca: overall={s['overall']} blockers={fs['BLOCKER']} "
          f"major={fs['MAJOR']} minor={fs['MINOR']}")
    return 0


def _cmd_templates(args: argparse.Namespace) -> int:
    if not TEMPLATE_DIR.is_dir():
        print("oca: no templates found", file=sys.stderr)
        return 1
    if args.dest:
        dest = Path(args.dest).expanduser()
        dest.mkdir(parents=True, exist_ok=True)
        for t in sorted(TEMPLATE_DIR.iterdir()):
            if t.is_file():
                shutil.copy2(t, dest / t.name)
                print(f"  → {dest / t.name}")
        return 0
    for t in sorted(TEMPLATE_DIR.iterdir()):
        if t.is_file():
            print(t.name)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="oca",
        description="OCA — Open Course Advisor: audit open course repositories.",
    )
    p.add_argument("--version", action="version", version=f"oca {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="scan a course repository")
    s.add_argument("path")
    s.add_argument("--json", action="store_true", help="machine-readable output")
    s.add_argument("--quiet", action="store_true", help="one-line summary")
    s.add_argument("-o", "--output", help="write output to a file")
    s.set_defaults(func=_cmd_scan)

    d = sub.add_parser("diff", help="compare two scan results")
    d.add_argument("before")
    d.add_argument("after")
    d.set_defaults(func=_cmd_diff)

    r = sub.add_parser("report", help="generate a report skeleton")
    r.add_argument("path")
    r.add_argument("-o", "--output", help="output path (default: <repo>/oca-report.md)")
    r.set_defaults(func=_cmd_report)

    t = sub.add_parser("templates", help="list or copy artefact templates")
    t.add_argument("dest", nargs="?", help="copy templates into this directory")
    t.set_defaults(func=_cmd_templates)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
