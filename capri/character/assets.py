"""Character asset detection (pure Python, no Qt, unit tested).

Naming convention inside ``assets/character``:

* ``<state>.png``            single image for a state
* ``<state>_<n>.png``        animation frames, numbered from 1; frames take
                             priority over the single image
* ``animation.json``         optional per-state frame rates
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

STATES: tuple[str, ...] = (
    "idle", "blink", "happy", "talking", "thinking", "searching", "files",
    "coding", "alert", "error", "sleeping", "peek", "dragging", "pointing",
)

#: For each state, the states whose images are tried next (in order) when the
#: state has no image of its own. When the chain is exhausted the runtime
#: placeholder construct is drawn.
FALLBACKS: dict[str, tuple[str, ...]] = {
    "idle": (),
    "blink": ("idle",),
    "happy": ("idle",),
    "talking": ("idle",),
    "thinking": ("idle",),
    "searching": ("thinking", "idle"),
    "files": ("thinking", "idle"),
    "coding": ("thinking", "idle"),
    "alert": ("idle",),
    "error": ("alert", "idle"),
    "sleeping": ("idle",),
    "peek": ("idle",),
    "dragging": ("idle",),
    "pointing": ("idle",),
}

#: Where Capri's pointing hand is inside ``pointing`` images (fractions of the image,
#: character facing right). The beam starts there. Override in animation.json.
DEFAULT_HAND = (0.82, 0.40)

DEFAULT_FPS = 8.0
_NAME_RE = re.compile(r"^([a-z]+)(?:_(\d+))?\.png$")


@dataclass(frozen=True)
class StateAssets:
    """Images found for one state."""

    state: str
    frames: tuple[Path, ...]
    animated: bool


@dataclass
class AssetIndex:
    """Result of scanning the character folder."""

    directory: Path
    found: dict[str, StateAssets] = field(default_factory=dict)
    fps: dict[str, float] = field(default_factory=dict)
    default_fps: float = DEFAULT_FPS
    ignored: list[str] = field(default_factory=list)
    hand: tuple[float, float] = DEFAULT_HAND

    def has_own(self, state: str) -> bool:
        """True if *state* has its own image(s), ignoring fallbacks."""
        return state in self.found

    def resolve(self, state: str) -> StateAssets | None:
        """Images to use for *state*, following the fallback chain.

        Returns ``None`` when a placeholder must be drawn.
        """
        for candidate in (state, *FALLBACKS.get(state, ())):
            if candidate in self.found:
                return self.found[candidate]
        return None

    def fps_for(self, state: str) -> float:
        """Frame rate for the images of *state*."""
        return self.fps.get(state, self.default_fps)

    def placeholder_states(self) -> list[str]:
        """States that will be drawn with the runtime placeholder."""
        return [s for s in STATES if self.resolve(s) is None]

    def report(self) -> list[str]:
        """Human readable summary lines for the startup log."""
        lines = []
        for state in STATES:
            own = self.found.get(state)
            resolved = self.resolve(state)
            if own:
                kind = f"{len(own.frames)} frames" if own.animated else "single image"
                lines.append(f"{state}: {kind} ({own.frames[0].name})")
            elif resolved:
                lines.append(f"{state}: no image, using '{resolved.state}' images")
            else:
                lines.append(f"{state}: no image, using placeholder")
        for name in self.ignored:
            lines.append(f"ignored file: {name}")
        return lines


def load_animation_config(directory: Path) -> tuple[float, dict[str, float]]:
    """Read ``animation.json``; returns ``(default_fps, {state: fps})``."""
    path = Path(directory) / "animation.json"
    default_fps, per_state = DEFAULT_FPS, {}
    if not path.is_file():
        return default_fps, per_state
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        value = raw.get("default_fps", DEFAULT_FPS)
        if isinstance(value, (int, float)) and 0 < value <= 60:
            default_fps = float(value)
        for state, cfg in (raw.get("states") or {}).items():
            fps = cfg.get("fps") if isinstance(cfg, dict) else cfg
            if state in STATES and isinstance(fps, (int, float)) and 0 < fps <= 60:
                per_state[state] = float(fps)
    except (OSError, ValueError, AttributeError) as exc:
        log.warning("animation.json is invalid (%s); using %s fps.", exc, DEFAULT_FPS)
    return default_fps, per_state


def load_pointing_hand(directory: Path) -> tuple[float, float]:
    """Read ``"pointing": {"hand": [x, y]}`` from animation.json (fractions 0..1)."""
    path = Path(directory) / "animation.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        hand = ((raw.get("states") or {}).get("pointing") or {}).get("hand")
        if (isinstance(hand, (list, tuple)) and len(hand) == 2
                and all(isinstance(v, (int, float)) and 0 <= v <= 1 for v in hand)):
            return float(hand[0]), float(hand[1])
    except (OSError, ValueError, AttributeError):
        pass
    return DEFAULT_HAND


def scan_assets(directory: Path) -> AssetIndex:
    """Scan *directory* and build the index of available state images."""
    directory = Path(directory)
    index = AssetIndex(directory=directory)
    index.default_fps, index.fps = load_animation_config(directory)
    index.hand = load_pointing_hand(directory)
    if not directory.is_dir():
        return index

    singles: dict[str, Path] = {}
    frames: dict[str, dict[int, Path]] = {}
    for entry in sorted(directory.iterdir(), key=lambda p: p.name.lower()):
        if not entry.is_file() or entry.suffix.lower() != ".png":
            continue
        match = _NAME_RE.match(entry.name.lower())
        if not match or match.group(1) not in STATES:
            index.ignored.append(entry.name)
            continue
        state, number = match.group(1), match.group(2)
        if number is None:
            singles[state] = entry
        elif int(number) >= 1:
            frames.setdefault(state, {})[int(number)] = entry
        else:
            index.ignored.append(entry.name)

    for state in STATES:
        if state in frames:
            ordered = tuple(frames[state][n] for n in sorted(frames[state]))
            index.found[state] = StateAssets(state, ordered, animated=True)
        elif state in singles:
            index.found[state] = StateAssets(state, (singles[state],), animated=False)
    return index
