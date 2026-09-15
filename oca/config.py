"""OCA configuration.

Loads settings from .oca.yml in the course root (if present), falling back
to sensible defaults. This replaces hardcoded thresholds and lets course
authors tune the scanner to their context without forking OCA.

Example .oca.yml:
    # Override lesson target for a short workshop
    lesson_target: 4

    # Ignore specific paths from scanning
    ignore_paths:
      - vendor/
      - third-party/

    # Disable specific checkers
    disabled_checkers:
      - testing

    # Custom axis weights (must sum to 1.0)
    weights:
      structure: 0.30
      content: 0.20
      practice: 0.20
      reproducibility: 0.15
      licensing: 0.10
      agent_readiness: 0.05
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DEFAULTS = {
    "lesson_target": 8,
    "ignore_paths": [],
    "disabled_checkers": [],
    "weights": {
        "structure": 0.25,
        "content": 0.20,
        "practice": 0.20,
        "reproducibility": 0.15,
        "licensing": 0.10,
        "agent_readiness": 0.10,
    },
    "linkcheck_timeout": 10,
    "linkcheck_workers": 8,
}

CONFIG_NAMES = (".oca.yml", ".oca.yaml", ".oca.json", "oca.yml", "oca.yaml")


def load_config(root: Path) -> dict[str, Any]:
    """Load config from the course root, merged with defaults."""
    config = dict(DEFAULTS)

    for name in CONFIG_NAMES:
        path = root / name
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
            if name.endswith(".json"):
                user = json.loads(text)
            else:
                # Try YAML first, fall back to simple key: value parsing
                try:
                    import yaml
                    user = yaml.safe_load(text) or {}
                except ImportError:
                    user = _parse_simple_yaml(text)
            if isinstance(user, dict):
                _merge(config, user)
        except (OSError, ValueError, json.JSONDecodeError):
            pass  # Malformed config is silently ignored
        break

    # Validate weights sum to ~1.0. `weights` may be absent, null (`weights:`
    # with nothing under it), or a non-mapping from a hand-written file; all
    # three used to reach `sum(w.values())` and crash the whole scan with an
    # AttributeError, so the type is checked before the values are read.
    w = config.get("weights")
    if not isinstance(w, dict) or not w:
        config["weights"] = DEFAULTS["weights"]
    else:
        try:
            total = sum(float(v) for v in w.values())
        except (TypeError, ValueError):
            total = 0.0
        if abs(total - 1.0) > 0.01:
            config["weights"] = DEFAULTS["weights"]

    return config


def _merge(base: dict, override: dict) -> None:
    """Deep merge override into base, in place."""
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _merge(base[key], value)
        else:
            base[key] = value


def _parse_simple_yaml(text: str) -> dict:
    """Minimal YAML parser for when PyYAML is not installed.

    Handles flat key: value pairs, one level of nesting, and simple lists
    (- item). Not a full YAML parser — just enough for typical .oca.yml files.

    Nesting matters: the fallback is what runs when PyYAML is absent, and the
    documented config format nests `weights:` and `disabled_checkers:` under
    their keys. The first version of this function only understood flat pairs,
    so `weights:` became `None` and the five axis weights leaked out as
    top-level keys — which then crashed the weights check downstream.
    """
    result: dict[str, Any] = {}
    current_key: str | None = None
    current_list: list[str] | None = None
    # Keys that actually received a nested block. A key left as an empty
    # mapping because nothing followed it is a null in real YAML, not `{}`.
    filled: set[str] = set()

    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip())
        stripped = raw.strip()

        # A comment runs from a `#` that follows whitespace (YAML's rule),
        # so URLs and `#` inside values survive.
        if " #" in stripped:
            stripped = stripped.split(" #", 1)[0].rstrip()
            if not stripped:
                continue

        if indent == 0:
            # A new top-level key ends whatever nested block was open.
            current_list = None
            current_key = None

        if stripped.startswith("- ") and current_key:
            if current_list is None:
                current_list = []
                parent = result.get(current_key)
                if not isinstance(parent, list):
                    # The parent was created as a placeholder mapping (or is
                    # absent); a list wins because the file used list syntax.
                    parent = []
                    result[current_key] = parent
                current_list = parent
            current_list.append(stripped[2:].strip())
            filled.add(current_key)
            continue

        if ":" not in stripped:
            continue

        key, _, value = stripped.partition(":")
        key = key.strip()
        value = value.strip()

        if indent == 0:
            current_key = key
            current_list = None
            if value:
                result[key] = _scalar(value)
            else:
                # Unknown whether a nested block or a null follows; start a
                # mapping and let a later "- item" line replace it with a list.
                result[key] = {}
        else:
            parent = result.get(current_key)
            if not isinstance(parent, dict):
                parent = {}
                if current_key:
                    result[current_key] = parent
            parent[key] = _scalar(value) if value else {}
            if current_key:
                filled.add(current_key)

    for key, value in list(result.items()):
        if isinstance(value, dict) and not value and key not in filled:
            result[key] = None

    return result


def _scalar(value: str) -> Any:
    """Parse a YAML scalar the simple way: number if it looks like one."""
    try:
        return float(value) if "." in value else int(value)
    except ValueError:
        if value.lower() in ("true", "false"):
            return value.lower() == "true"
        return value
