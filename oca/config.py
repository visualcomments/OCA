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

    # Validate weights sum to ~1.0
    w = config.get("weights", {})
    total = sum(w.values())
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

    Handles flat key: value pairs and simple lists (- item).
    Not a full YAML parser — just enough for typical .oca.yml files.
    """
    result: dict[str, Any] = {}
    current_key = None
    current_list: list[str] | None = None

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("- ") and current_key:
            if current_list is None:
                current_list = []
                result[current_key] = current_list
            current_list.append(stripped[2:].strip())
        elif ":" in stripped:
            key, _, value = stripped.partition(":")
            key = key.strip()
            value = value.strip()
            current_key = key
            current_list = None
            if value:
                # Try to parse as number
                try:
                    result[key] = float(value) if "." in value else int(value)
                except ValueError:
                    result[key] = value
            else:
                result[key] = None

    return result
