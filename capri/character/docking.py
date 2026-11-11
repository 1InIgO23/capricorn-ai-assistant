"""Docking geometry (pure Python, no Qt, unit tested).

Capri can dock in two ways:

* at a **screen edge**: half of her slides off-screen and she peeks back in;
* at the edge of **another application's window**: half of her slides behind the
  window (that half is not drawn) and she peeks out from its left, right or top side.

Coordinates are integers in Qt's logical pixels. ``Rect.right``/``bottom`` are
exclusive (``left + width``), unlike ``QRect.right()``.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

DOCK_MARGIN = 28   # how close (px) a drop must be to an edge to dock there
GAP = 4            # space between Capri and the edge when fully shown
MIN_WINDOW = 0.8   # a window must be at least this many character sizes tall/wide


@dataclass(frozen=True)
class Rect:
    """Axis-aligned rectangle with exclusive right/bottom."""

    left: int
    top: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height

    @property
    def center(self) -> tuple[float, float]:
        return self.left + self.width / 2, self.top + self.height / 2

    def contains(self, x: float, y: float) -> bool:
        return self.left <= x < self.right and self.top <= y < self.bottom

    def intersects(self, other: "Rect") -> bool:
        return (self.left < other.right and other.left < self.right
                and self.top < other.bottom and other.top < self.bottom)


@dataclass(frozen=True)
class AppWindow:
    """A visible top-level window of another application (topmost first in lists)."""

    handle: int
    title: str
    rect: Rect


@dataclass(frozen=True)
class DockTarget:
    """Where Capri is docked.

    ``along`` is her offset along the edge (y for left/right edges, x for
    top/bottom edges) relative to the rectangle, so she can follow a window
    that moves.
    """

    kind: str          # "screen" | "window"
    edge: str          # "left" | "right" | "top" | "bottom"
    rect: Rect         # the screen's available area, or the window's frame
    along: int = 0
    handle: int = 0    # window handle (kind == "window")
    title: str = ""

    def with_rect(self, rect: Rect) -> "DockTarget":
        return replace(self, rect=rect)


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(value, high)) if low <= high else low


def dock_position(target: DockTarget, size: int, hidden: bool) -> tuple[int, int]:
    """Top-left of Capri's square for *target*, peeking (*hidden*) or fully shown."""
    r, half = target.rect, size // 2
    if target.kind == "screen":
        if target.edge in ("left", "right"):
            y = _clamp(r.top + target.along, r.top, r.bottom - size)
            if target.edge == "left":
                return (r.left - half if hidden else r.left + GAP), y
            return (r.right - half if hidden else r.right - size - GAP), y
        x = _clamp(r.left + target.along, r.left, r.right - size)
        if target.edge == "top":
            return x, (r.top - half if hidden else r.top + GAP)
        return x, (r.bottom - half if hidden else r.bottom - size - GAP)

    # window: she stands outside the window and slides half of herself behind it
    if target.edge in ("left", "right"):
        y = _clamp(r.top + target.along, r.top - half, r.bottom - half)
        if target.edge == "left":
            return (r.left - half if hidden else r.left - size - GAP), y
        return (r.right - half if hidden else r.right + GAP), y
    x = _clamp(r.left + target.along, r.left - half, r.right - half)
    return x, (r.top - half if hidden else r.top - size - GAP)  # "top"


def _edge_visible(window_index: int, windows: list[AppWindow], x: float, y: float) -> bool:
    """True if the point on a window's edge is not covered by a window above it."""
    return not any(w.rect.contains(x, y) for w in windows[:window_index])


def _fits(target: DockTarget, size: int, screen: Rect) -> bool:
    """The fully shown position must be on the screen."""
    x, y = dock_position(target, size, hidden=False)
    return (screen.left <= x and x + size <= screen.right
            and screen.top <= y and y + size <= screen.bottom)


def window_edges(windows: list[AppWindow], screen: Rect, size: int) -> list[DockTarget]:
    """Every usable left/right/top window edge on *screen*, with ``along`` centered.

    An edge is usable when the window is big enough, the edge does not lie on
    the screen border (maximized windows), the middle of the edge is not covered
    by a higher window, and Capri fits on screen next to it.
    """
    out: list[DockTarget] = []
    for index, window in enumerate(windows):
        r = window.rect
        if r.width < size * MIN_WINDOW or r.height < size * MIN_WINDOW:
            continue
        if not r.intersects(screen):
            continue
        cx, cy = r.center
        candidates = []
        if r.left - screen.left > DOCK_MARGIN:
            candidates.append(("left", r.left, cy, r.height // 2 - size // 2))
        if screen.right - r.right > DOCK_MARGIN:
            candidates.append(("right", r.right - 1, cy, r.height // 2 - size // 2))
        if r.top - screen.top > DOCK_MARGIN:
            candidates.append(("top", cx, r.top, r.width // 2 - size // 2))
        for edge, px, py, along in candidates:
            target = DockTarget("window", edge, r, along, window.handle, window.title)
            if _edge_visible(index, windows, px, py) and _fits(target, size, screen):
                out.append(target)
    return out


def find_dock(char: Rect, screen: Rect, windows: list[AppWindow], *,
              use_windows: bool = True, margin: int = DOCK_MARGIN) -> DockTarget | None:
    """Decide where Capri docks after being dropped at *char*.

    Screen edges win over window edges. A window edge is chosen when she was
    dropped touching it from outside (or overlapping it by up to half her width).
    """
    size = char.width
    cx, cy = char.center
    if char.left - screen.left < margin:
        return DockTarget("screen", "left", screen, char.top - screen.top)
    if screen.right - char.right < margin:
        return DockTarget("screen", "right", screen, char.top - screen.top)
    if char.top - screen.top < margin:
        return DockTarget("screen", "top", screen, char.left - screen.left)
    if screen.bottom - char.bottom < margin:
        return DockTarget("screen", "bottom", screen, char.left - screen.left)
    if not use_windows:
        return None

    half = size / 2
    for index, window in enumerate(windows):
        r = window.rect
        if r.width < size * MIN_WINDOW or r.height < size * MIN_WINDOW:
            continue
        hits = []
        if r.top - half <= cy <= r.bottom - half + size:
            hits.append(("left", r.left - half - margin <= cx <= r.left + margin, r.left, cy,
                         char.top - r.top))
            hits.append(("right", r.right - margin <= cx <= r.right + half + margin,
                         r.right - 1, cy, char.top - r.top))
        if r.left - half <= cx <= r.right + half:
            hits.append(("top", r.top - half - margin <= cy <= r.top + margin, cx, r.top,
                         char.left - r.left))
        for edge, near, px, py, along in hits:
            if not near:
                continue
            target = DockTarget("window", edge, r, along, window.handle, window.title)
            if _edge_visible(index, windows, px, py) and _fits(target, size, screen):
                return target
    return None


def dock_obstructed(target: DockTarget, char: Rect, windows: list[AppWindow]) -> bool:
    """True when a window *above* the docked one now overlaps Capri's square.

    Then she would look cut in half over the wrong window, so she must step out.
    *windows* is topmost first. A docked window missing from the list (hidden,
    cloaked) also counts as obstructed.
    """
    for window in windows:
        if window.handle == target.handle:
            return False
        if window.rect.intersects(char):
            return True
    return True
