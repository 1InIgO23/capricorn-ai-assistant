"""Loads the detected character images as pixmaps scaled to the character size."""
from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap

from .assets import AssetIndex, scan_assets

log = logging.getLogger(__name__)


class SpriteLibrary:
    """Pixmap cache on top of :class:`AssetIndex`."""

    def __init__(self, directory: Path, size: int) -> None:
        self.directory = Path(directory)
        self.size = size
        self.index: AssetIndex = scan_assets(self.directory)
        self._frames: dict[str, list[QPixmap]] = {}
        self.reload(size)

    def reload(self, size: int | None = None) -> None:
        """Rescan the folder and reload every image (also used by "Reload assets")."""
        if size:
            self.size = size
        self.index = scan_assets(self.directory)
        self._frames = {}
        for state, assets in self.index.found.items():
            frames = []
            for path in assets.frames:
                pixmap = QPixmap(str(path))
                if pixmap.isNull():
                    log.warning("Could not load image %s", path.name)
                    continue
                frames.append(pixmap.scaled(self.size, self.size, Qt.KeepAspectRatio,
                                            Qt.SmoothTransformation))
            if frames:
                self._frames[state] = frames
        # states whose files all failed to load must fall back like missing ones
        for state in list(self.index.found):
            if state not in self._frames:
                del self.index.found[state]
        log.info("Character assets in %s:", self.directory)
        for line in self.index.report():
            log.info("  %s", line)
        placeholders = self.index.placeholder_states()
        if placeholders:
            log.info("States drawn with the runtime placeholder: %s", ", ".join(placeholders))

    def has_own(self, state: str) -> bool:
        """True when *state* has its own image(s)."""
        return state in self._frames

    def frame(self, state: str, elapsed: float) -> QPixmap | None:
        """Pixmap for *state* after *elapsed* seconds in it, or ``None`` for a placeholder."""
        resolved = self.index.resolve(state)
        if resolved is None:
            return None
        frames = self._frames.get(resolved.state)
        if not frames:
            return None
        if len(frames) == 1:
            return frames[0]
        fps = self.index.fps_for(resolved.state)
        return frames[int(max(0.0, elapsed) * fps) % len(frames)]

    def duration(self, state: str) -> float:
        """Length in seconds of one loop of the state's own animation (0 if none)."""
        frames = self._frames.get(state)
        if not frames:
            return 0.0
        return len(frames) / self.index.fps_for(state)
