"""Theme loading. ``assets/theme.json`` is the single editable source of
colors, fonts and effect parameters; the defaults below are used for any key
that is missing (or if the file is absent or broken)."""
from __future__ import annotations

import copy
import json
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_THEME: dict[str, Any] = {
    "name": "Capri Construct",
    "colors": {
        "background": "#08080D",
        "surface": "#0F1020",
        "surface_alt": "#1E2036",
        "border": "#A23BF2",
        "accent": "#FF1450",
        "accent_soft": "#FF8DB4",
        "secondary": "#12F56C",
        "tertiary": "#2A6BFF",
        "text": "#F5F2F8",
        "text_dim": "#8E98A8",
        "success": "#12F56C",
        "warning": "#FFC61A",
        "alert": "#FF8A1F",
        "error": "#FF3030",
        "glass_alpha": 0.88,
    },
    "state_colors": {
        "idle": "#FF1450",
        "blink": "#FF1450",
        "happy": "#FF8DB4",
        "talking": "#12F56C",
        "thinking": "#A23BF2",
        "searching": "#2A6BFF",
        "files": "#12F56C",
        "coding": "#FFC61A",
        "alert": "#FF8A1F",
        "error": "#FF3030",
        "sleeping": "#6A3FA8",
        "peek": "#FF1450",
        "dragging": "#FF8DB4",
        "pointing": "#12F56C",
        "floating": "#FF8DB4",
    },
    "pointer": {
        "duration": 6.0,
        "beam_jitter": 9.0,
        "beam_width": 2.5,
        "glow_width": 9.0,
        "packets": 4,
    },
    "mood_tints": {
        "bored": "#6A3FA8",
        "content": "#FF1450",
        "excited": "#FF8DB4",
        "tired": "#2A3F7A",
    },
    "fonts": {
        # Windows fonts first, then their macOS counterparts (missing ones are skipped).
        "ui": ["Segoe UI", "Helvetica Neue", "Arial"],
        "display": ["Bahnschrift", "DIN Alternate", "Avenir Next Condensed", "Segoe UI",
                    "Helvetica Neue", "Arial"],
        "mono": ["Cascadia Mono", "Consolas", "SF Mono", "Menlo", "Courier New"],
        "ui_size": 10,
        "mono_size": 10,
        "hud_size": 9,
    },
    "effects": {
        "intensity": {"off": 0.0, "subtle": 0.45, "full": 1.0},
        "bob_amplitude": 6.0,
        "bob_period": 3.4,
        "breath_scale": 0.02,
        "breath_period": 4.6,
        "glitch_interval": [4.0, 11.0],
        "glitch_duration": 0.18,
        "glitch_max_offset": 18,
        "glitch_slices": [2, 4],
        "rgb_split": 2.0,
        "scanline_spacing": 3,
        "scanline_alpha": 0.16,
        "glow_radius": 12,
        "glow_alpha": 0.8,
        "rerender_ms": 380,
        "tilt_degrees": 6.0,
        "awareness_radius": 280,
        "pulse_period": 1.1,
        "night_dim": 0.8,
        "panel_scanline_alpha": 0.05,
        "panel_radius": 10,
        "decrypt_chars_per_second": 60,
    },
    "syntax": {
        "text": "#F5F2F8",
        "keyword": "#FF1450",
        "string": "#12F56C",
        "number": "#FFC61A",
        "comment": "#6F7A8C",
        "function": "#5C93FF",
        "class": "#FF8DB4",
        "operator": "#C77DFF",
        "builtin": "#C77DFF",
    },
}


def _merge(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


class Theme:
    """Read-only view over the theme dictionary (Qt-free on purpose)."""

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        self.data = _merge(DEFAULT_THEME, data or {})

    @classmethod
    def load(cls, path: Path) -> "Theme":
        """Load a theme file; fall back to defaults on any problem."""
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("theme root must be an object")
            return cls(raw)
        except FileNotFoundError:
            log.info("No theme file at %s; using built-in theme.", path)
        except (OSError, ValueError) as exc:
            log.warning("Theme file %s is invalid (%s); using built-in theme.", path, exc)
        return cls()

    def color(self, name: str) -> str:
        """Hex string for a named UI color."""
        value = self.data["colors"].get(name)
        return value if isinstance(value, str) else "#FF00FF"

    def state_color(self, state: str) -> str:
        """Hex glow color for a character state."""
        return self.data["state_colors"].get(state) or self.color("accent")

    def mood_tint(self, mood: str) -> str:
        """Hex tint for a mood."""
        return self.data["mood_tints"].get(mood) or self.color("accent")

    def syntax(self, name: str) -> str:
        """Hex color for a syntax highlighting role."""
        return self.data["syntax"].get(name) or self.color("text")

    def pointer(self, name: str, default: Any = None) -> Any:
        """A parameter of the window-pointing beam."""
        return (self.data.get("pointer") or {}).get(name, default)

    def fx(self, name: str, default: Any = None) -> Any:
        """An effect parameter."""
        return self.data["effects"].get(name, default)

    def intensity(self, level: str) -> float:
        """Multiplier (0..1) for an effects intensity level."""
        try:
            return float(self.data["effects"]["intensity"].get(level, 1.0))
        except (TypeError, ValueError, AttributeError):
            return 1.0

    def glass_alpha(self) -> float:
        """Opacity of the translucent panels."""
        try:
            return max(0.2, min(1.0, float(self.data["colors"].get("glass_alpha", 0.88))))
        except (TypeError, ValueError):
            return 0.88

    def font_families(self, kind: str) -> list[str]:
        """Font family list (first available wins) for ui/display/mono."""
        value = self.data["fonts"].get(kind) or ["Segoe UI"]
        return [value] if isinstance(value, str) else list(value)

    def font_size(self, kind: str) -> int:
        """Point size for ``ui_size`` / ``mono_size`` / ``hud_size``."""
        try:
            return int(self.data["fonts"].get(kind, 10))
        except (TypeError, ValueError):
            return 10
