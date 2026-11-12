"""Roaming: Capri floats by herself to a random spot (pure Python, unit tested).

She glides along a gentle curve (a quadratic Bézier with a random sideways
bend) with ease-in/ease-out, at a calm speed. The floating bob drawn by the
effects layer keeps running on top, so the trip looks like drifting.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass

from .docking import Rect

MIN_TRIP = 220        # px: shorter trips are not worth the show
EDGE_MARGIN = 40      # px kept free along the screen edges
SPEED = 70.0          # px per second
MIN_SECONDS, MAX_SECONDS = 4.0, 16.0


@dataclass(frozen=True)
class FloatTrip:
    """One trip from *start* to *end* (top-left of her square), bending via *control*."""

    start: tuple[float, float]
    control: tuple[float, float]
    end: tuple[float, float]
    duration: float

    @property
    def direction(self) -> float:
        """+1 when travelling right, -1 when travelling left."""
        return 1.0 if self.end[0] >= self.start[0] else -1.0

    def position(self, elapsed: float) -> tuple[float, float]:
        """Where she is after *elapsed* seconds (clamped to the trip)."""
        t = max(0.0, min(1.0, elapsed / self.duration))
        t = t * t * (3 - 2 * t)  # ease in and out
        (x0, y0), (cx, cy), (x1, y1) = self.start, self.control, self.end
        u = 1 - t
        return (u * u * x0 + 2 * u * t * cx + t * t * x1,
                u * u * y0 + 2 * u * t * cy + t * t * y1)

    def finished(self, elapsed: float) -> bool:
        return elapsed >= self.duration


def pick_destination(screen: Rect, size: int, current: tuple[int, int],
                     rng: random.Random | None = None) -> tuple[int, int] | None:
    """A random spot on *screen* at least ``MIN_TRIP`` px away, or ``None`` if the
    screen is too small for a worthwhile trip."""
    rng = rng or random
    low_x, high_x = screen.left + EDGE_MARGIN, screen.right - size - EDGE_MARGIN
    low_y, high_y = screen.top + EDGE_MARGIN, screen.bottom - size - EDGE_MARGIN
    if high_x <= low_x or high_y <= low_y:
        return None
    for _ in range(20):
        x, y = rng.randint(low_x, high_x), rng.randint(low_y, high_y)
        if math.hypot(x - current[0], y - current[1]) >= MIN_TRIP:
            return x, y
    return None


def plan_trip(start: tuple[int, int], end: tuple[int, int], screen: Rect, size: int,
              rng: random.Random | None = None) -> FloatTrip:
    """Build a curved trip; the bend never pushes her off the screen."""
    rng = rng or random
    (x0, y0), (x1, y1) = start, end
    length = math.hypot(x1 - x0, y1 - y0) or 1.0
    nx, ny = -(y1 - y0) / length, (x1 - x0) / length
    bend = rng.uniform(-0.25, 0.25) * length
    cx = (x0 + x1) / 2 + nx * bend
    cy = (y0 + y1) / 2 + ny * bend
    cx = max(screen.left, min(cx, screen.right - size))
    cy = max(screen.top, min(cy, screen.bottom - size))
    duration = max(MIN_SECONDS, min(MAX_SECONDS, length / SPEED))
    return FloatTrip((float(x0), float(y0)), (cx, cy), (float(x1), float(y1)), duration)
