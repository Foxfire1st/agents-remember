"""The projection cadence: the one pacing decision every dashboard process shares.

Kept in its own stdlib-only module so the import-light daemon agent-notifier can name the cadence it
hands a spawned child without importing the projector (and, through it, the serving stack).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProjectionCadence:
    """How fast the projector may tick, and how long a quiet world may go unprojected.

    Both bounds describe one pacing decision: ``interval`` is the floor between ticks and
    ``heartbeat`` is the ceiling on staleness when nothing changes. Setting one without the other
    is how a "fast" projector ends up serving hour-old state.
    """

    interval: float = 1.0
    heartbeat: float | None = None


DEFAULT_PROJECTION_CADENCE = ProjectionCadence()


__all__ = ["DEFAULT_PROJECTION_CADENCE", "ProjectionCadence"]
