"""CI/CD analysis checker.

Goes beyond "does .github/workflows exist" to actually parse workflow files:
are dependencies pinned in CI? Are there dangerous patterns (untrusted
actions, script injection)? Is the CI actually testing anything meaningful?

Inspired by OSA's Scorecard integration but implemented as pure static
analysis — no external binary needed, no network, fully deterministic.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import Finding, FindingSpec, ScanContext

ORDER = 20

CHECKS = [
    FindingSpec("ci-no-tests", "MINOR", "medium", "M", "reproducibility",
                "CI есть, но не запускает тесты"),
    FindingSpec("ci-unpinned-action", "MAJOR", "high", "S", "reproducibility",
                "GitHub Action без фиксации версии (используется @main/@master)"),
    FindingSpec("ci-script-injection", "MAJOR", "high", "M", "reproducibility",
                "Потенциальная инъекция в run-блоке (${{ github.event.* }})"),
    FindingSpec("ci-no-python-version", "MINOR", "low", "S", "reproducibility",
                "CI не указывает версию Python"),
]

# Patterns for dangerous CI practices
_UNPINNED_ACTION_RE = re.compile(
    r"uses:\s*[^#\n]+@(main|master|v\d+)?\s*$", re.MULTILINE)
_PINNED_ACTION_RE = re.compile(
    r"uses:\s*[^#\n]+@[a-f0-9]{40}", re.MULTILINE)
_SCRIPT_INJECTION_RE = re.compile(
    r"\$\{\{\s*(github\.event\.(issue|pull_request|comment|review)\."
    r"(title|body|head\.ref)|steps\.\w+\.outputs?\.\w+)", re.IGNORECASE)
_TEST_COMMAND_RE = re.compile(
    r"(pytest|python3?\s+-m\s+pytest|tox|make\s+test|unittest|nosetests|"
    r"python3?\s+tests?/|python3?\s+-m\s+unittest|"
    r"bash\s+\S*(?:selftest|validate|check))", re.IGNORECASE)
_PYTHON_VERSION_RE = re.compile(
    r"python-version:\s*['\"]?[\d.]+['\"]?", re.IGNORECASE)


def _parse_workflow(text: str) -> dict:
    """Extract CI health signals from a workflow YAML file."""
    return {
        "has_tests": bool(_TEST_COMMAND_RE.search(text)),
        "has_python_version": bool(_PYTHON_VERSION_RE.search(text)),
        "unpinned_actions": len(_UNPINNED_ACTION_RE.findall(text)),
        "pinned_actions": len(_PINNED_ACTION_RE.findall(text)),
        "script_injections": len(_SCRIPT_INJECTION_RE.findall(text)),
    }


def check(ctx: ScanContext) -> list[Finding]:
    findings: list[Finding] = []

    # Find all workflow files
    workflow_files = ctx.files_matching(".github/workflows/*.yml") + \
                     ctx.files_matching(".github/workflows/*.yaml")

    if not workflow_files:
        return findings

    total_unpinned = 0
    total_injections = 0
    any_tests = False
    any_python_version = False

    for rel in workflow_files:
        text = ctx.read_text(rel)
        info = _parse_workflow(text)

        if info["has_tests"]:
            any_tests = True
        if info["has_python_version"]:
            any_python_version = True
        total_unpinned += info["unpinned_actions"]
        total_injections += info["script_injections"]

        # Per-file findings for dangerous patterns
        if info["script_injections"] > 0:
            findings.append(Finding(
                code="ci-script-injection", level="MAJOR",
                message=f"Потенциальная инъекция в {Path(rel).name}: "
                        f"${{{{ github.event.* }}}} в run-блоке",
                path=rel,
                details={"count": info["script_injections"]},
            ))

    if not any_tests and workflow_files:
        findings.append(Finding(
            code="ci-no-tests", level="MINOR",
            message="CI настроен, но не запускает тесты или проверки",
            path=workflow_files[0],
        ))

    if total_unpinned > 0:
        findings.append(Finding(
            code="ci-unpinned-action", level="MAJOR",
            message=f"{total_unpinned} GitHub Action(s) без SHA-фиксации "
                    f"(используют @main/@master вместо @sha256)",
            path=workflow_files[0],
            details={"count": total_unpinned},
        ))

    if not any_python_version and workflow_files:
        # Only flag this if the repo has Python files
        py_files = [p for p in ctx.flat if p.endswith(".py")]
        if py_files:
            findings.append(Finding(
                code="ci-no-python-version", level="MINOR",
                message="CI не фиксирует версию Python",
                path=workflow_files[0],
            ))

    return findings
