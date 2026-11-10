"""A simple persistent mood model: bored / content / excited / tired."""
from __future__ import annotations

import math
import time
from typing import Any

MOODS = ("bored", "content", "excited", "tired")


class MoodEngine:
    """Tracks two slow variables and derives a mood from them.

    * ``engagement`` rises with interaction and decays with a 30 minute half-life.
    * ``energy`` drifts towards a target that depends on the time of day and
      on how long the current session has been running.
    """

    def __init__(self, state: dict[str, Any] | None = None, now: float | None = None) -> None:
        state = state or {}
        now = time.time() if now is None else now
        self.energy = float(state.get("energy", 0.8))
        self.engagement = float(state.get("engagement", 0.35))
        saved = float(state.get("saved_at", now))
        self._decay_engagement(max(0.0, now - saved))
        self._clamp()

    def _clamp(self) -> None:
        self.energy = min(1.0, max(0.0, self.energy))
        self.engagement = min(1.0, max(0.0, self.engagement))

    def _decay_engagement(self, seconds: float) -> None:
        self.engagement *= 0.5 ** (seconds / 1800.0)

    def note_interaction(self, kind: str) -> None:
        """Register an interaction (``message``, ``click``, ``hover``, ``task``)."""
        boost = {"message": 0.16, "task": 0.1, "click": 0.05, "hover": 0.01}.get(kind, 0.03)
        self.engagement += boost
        self._clamp()

    @staticmethod
    def energy_target(hour: int, session_minutes: float) -> float:
        """Energy she drifts towards for a given hour and session length."""
        if 0 <= hour < 6:
            base = 0.22
        elif hour >= 23:
            base = 0.38
        elif hour >= 21:
            base = 0.6
        elif hour < 9:
            base = 0.7
        else:
            base = 0.9
        fatigue = min(0.4, max(0.0, (session_minutes - 120.0) / 60.0 * 0.1))
        return max(0.05, base - fatigue)

    def tick(self, seconds: float, hour: int, session_minutes: float) -> None:
        """Advance the model by *seconds*."""
        self._decay_engagement(seconds)
        target = self.energy_target(hour, session_minutes)
        blend = 1.0 - math.exp(-seconds / 1200.0)
        self.energy += (target - self.energy) * blend
        self._clamp()

    @property
    def mood(self) -> str:
        """The current mood name."""
        if self.energy < 0.35:
            return "tired"
        if self.engagement > 0.6:
            return "excited"
        if self.engagement < 0.12:
            return "bored"
        return "content"

    def to_dict(self) -> dict[str, Any]:
        """Serializable state."""
        return {"energy": round(self.energy, 4), "engagement": round(self.engagement, 4),
                "saved_at": time.time()}
