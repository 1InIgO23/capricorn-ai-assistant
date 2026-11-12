"""Tests for the floating trips."""
import math
import random

from capri.character.docking import Rect
from capri.character.roaming import (EDGE_MARGIN, MAX_SECONDS, MIN_SECONDS, MIN_TRIP,
                                     pick_destination, plan_trip)

SCREEN = Rect(0, 0, 1920, 1040)
S = 200


def test_destination_is_on_screen_and_far_enough() -> None:
    rng = random.Random(1)
    for _ in range(200):
        x, y = pick_destination(SCREEN, S, (800, 400), rng)
        assert EDGE_MARGIN <= x <= SCREEN.right - S - EDGE_MARGIN
        assert EDGE_MARGIN <= y <= SCREEN.bottom - S - EDGE_MARGIN
        assert math.hypot(x - 800, y - 400) >= MIN_TRIP


def test_tiny_screen_has_no_destination() -> None:
    assert pick_destination(Rect(0, 0, 250, 250), S, (10, 10)) is None


def test_trip_is_smooth_and_ends_at_destination() -> None:
    trip = plan_trip((100, 500), (1500, 300), SCREEN, S, random.Random(3))
    assert MIN_SECONDS <= trip.duration <= MAX_SECONDS
    assert trip.position(0) == (100.0, 500.0)
    assert trip.position(trip.duration) == (1500.0, 300.0)
    assert trip.direction == 1.0
    steps = [trip.position(trip.duration * i / 100) for i in range(101)]
    jumps = [math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(steps, steps[1:])]
    assert max(jumps) < 40          # no teleporting: small steps all the way
    assert jumps[0] < jumps[50]     # eases in ...
    assert jumps[-1] < jumps[50]    # ... and out
    for x, y in steps:
        assert -1 <= x <= SCREEN.right - S + 1 and -1 <= y <= SCREEN.bottom - S + 1


def test_leftward_trip_faces_left() -> None:
    assert plan_trip((1500, 300), (200, 300), SCREEN, S).direction == -1.0
