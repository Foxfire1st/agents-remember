"""Shared time helpers for the observer substrate.

The write side (``ambient``) and the read side (``reducer``) both age ISO-8601
stamps against a clock to decide staleness, so this is the one definition they
share -- the projection's ``paused (quiet)`` threshold and the heartbeat
ticker can never drift apart on two copies. ``providers.setup_progress`` keeps
its own copy on purpose: it predates the observer package and is a
provider-layer concern, not part of this substrate.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

# A clock returns timezone-aware "now". Injected at the edges so a reduction is
# a pure function of (events, snapshots, now) and tests are deterministic.
Clock = Callable[[], datetime]

# Observer timing thresholds (design §1.5-1.6; §8 assigns the defaults to the
# lifecycle-tools slice). Co-located here because they are shared across the
# write side (the heartbeat ticker + TTL sweep in ``ambient``) and the read side
# (the projection's paused-by-dormancy and abandoned inference in ``reducer``) --
# one definition so the two can never drift. The 15s beat mirrors the proven
# setup-progress cadence.
HEARTBEAT_SECONDS = 15.0
STALE_AFTER_SECONDS = 180.0
TTL_SECONDS = 3600.0


def age_seconds(stamp: str, now: datetime) -> float | None:
    """Seconds between an ISO-8601 ``stamp`` and ``now``; ``None`` when unparseable.

    A naive stamp is assumed UTC: the substrate always writes offset-aware
    stamps, but a hand-edited or legacy log must degrade to ``None``/UTC rather
    than crash the projection.
    """
    try:
        then = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    if then.tzinfo is None:
        then = then.replace(tzinfo=UTC)
    return (now - then).total_seconds()
