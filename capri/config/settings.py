"""Persistent user settings stored as JSON in the app data folder."""
from __future__ import annotations

import copy
import json
import logging
import os
from pathlib import Path
from typing import Any

from .paths import default_workspace, settings_file

log = logging.getLogger(__name__)

EFFECT_LEVELS = ("off", "subtle", "full")

DEFAULTS: dict[str, Any] = {
    "backend": "ollama",
    # Per-backend configuration: {"<key>": {"base_url": ..., "model": ..., "api_key": ...}}
    "backends": {},
    "workspace": "",
    "script_timeout": 60,
    "sleep_minutes": 10,
    "always_on_top": True,
    "character_size": 220,
    "effects_intensity": "full",
    "remarks_enabled": True,
    "remarks_interval_minutes": 20,
    "max_tool_rounds": 12,
    "window": {"x": None, "y": None, "dock": None},
    "last_conversation": "",
}


def _deep_merge(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge *extra* into a copy of *base*."""
    out = copy.deepcopy(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


class Settings:
    """Small dotted-key wrapper around the settings JSON file."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or settings_file()
        self.data: dict[str, Any] = copy.deepcopy(DEFAULTS)
        self.load()

    # ------------------------------------------------------------------ io
    def load(self) -> None:
        """Load the file, keeping defaults for anything missing or broken."""
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self.data = _deep_merge(DEFAULTS, raw)
        except (OSError, ValueError) as exc:
            log.warning("Could not read settings (%s); using defaults.", exc)

    def save(self) -> None:
        """Write the settings atomically."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError as exc:
            log.error("Could not save settings: %s", exc)

    # -------------------------------------------------------------- access
    def get(self, key: str, default: Any = None) -> Any:
        """Read a value using a dotted key such as ``"window.x"``."""
        node: Any = self.data
        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, key: str, value: Any) -> None:
        """Set a value using a dotted key (intermediate dicts are created)."""
        node = self.data
        parts = key.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value

    def backend_config(self, key: str) -> dict[str, str]:
        """Return the stored configuration dict of one backend (may be empty)."""
        cfg = self.get(f"backends.{key}", {})
        return dict(cfg) if isinstance(cfg, dict) else {}

    def secrets(self) -> list[str]:
        """All stored API keys, used to redact them from the logs."""
        out = []
        for cfg in (self.get("backends", {}) or {}).values():
            if isinstance(cfg, dict) and cfg.get("api_key"):
                out.append(str(cfg["api_key"]))
        return out

    @property
    def workspace(self) -> Path:
        """The script workspace folder (created on demand)."""
        raw = str(self.get("workspace") or "").strip()
        path = Path(os.path.expandvars(os.path.expanduser(raw))) if raw else default_workspace()
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            log.error("Cannot create workspace %s: %s", path, exc)
        return path

    def effects_level(self) -> str:
        """The validated effects intensity level."""
        level = str(self.get("effects_intensity", "full"))
        return level if level in EFFECT_LEVELS else "full"
