"""The observer substrate's timing vocabulary: its clock type and its thresholds.

The write side (``ambient``) and the read side (``reducer``) share these, so the
projection's ``paused (quiet)`` threshold and the heartbeat ticker can never drift
apart on two copies. ``providers.setup_progress`` keeps its own copy on purpose: it
predates the observer package and is a provider-layer concern, not part of this
substrate.

The stamp-aging primitive these thresholds are compared against is NOT here. It is
shared with the control plane's retention rules, so it is defined by the lower of the
two packages -- ``controlplane.stamps.age_seconds`` (see ``layers.toml``).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

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
