"""Redelivery backoff + per-target rate limiting for pending unacked operator inbox entries.

R3 (260707-HFX2-L1): a pending/unacked row (``state == "pending"``; ``deliveryState`` never
reaches a terminal state on its own -- only ``consume`` does) carries a ``nextAttemptAt``
backoff schedule. L2 (the supervisor sweep, a sibling leaf) is the actual driver that walks
``OperatorInboxStore.list_redeliverable`` on a cadence and calls ``deliver_inbox_entry`` again;
this module is the pure math + the per-target rate-limit gate the sweep consults before
re-attempting, mirroring the ``OrchestrationNudgeStore.record`` rate-limit pattern
(``orchestration_nudges.py:81-99``: compare elapsed time since the last attempt against a
rate-limit floor) rather than a fixed interval -- a slow target backs off, a healthy one is not
throttled below the schedule.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from agents_remember.controlplane.operator_inbox_records import OperatorInboxEntry

# Exponential-ish backoff ladder, seconds; the last entry is the steady-state ceiling so a
# long-pending row does not retry every few seconds forever (26898-style thundering redelivery).
BACKOFF_SCHEDULE_SECONDS: tuple[float, ...] = (30.0, 60.0, 300.0, 900.0, 3600.0, 21600.0)

# The effective floor: a delivered/queued/unconfirmed/no-hosted-session row may still be in the
# harness queue or under model processing, so no retry path may run faster than this.
MIN_REDELIVERY_INTERVAL_SECONDS = 900.0
DEFAULT_RATE_LIMIT_SECONDS = MIN_REDELIVERY_INTERVAL_SECONDS

_REDELIVERABLE_DELIVERY_STATES = frozenset({"queued", "no-hosted-session", "delivered", "unconfirmed"})
"""Every deliveryState is redeliverable while the row is pending -- 'delivered' is never terminal
(R1: pasted != perceived), so a delivered-but-unacked row still schedules a nudge."""


def backoff_seconds_for_attempt(attempt_count: int) -> float:
    """The wait before attempt ``attempt_count + 1``, from the ladder (clamped to the ceiling)."""
    index = max(0, min(attempt_count, len(BACKOFF_SCHEDULE_SECONDS) - 1))
    return BACKOFF_SCHEDULE_SECONDS[index]


def require_redelivery_floor_seconds(
    rate_limit_seconds: float | None,
    *,
    owner: str = "redelivery interval",
) -> float:
    """Return the effective redelivery floor, refusing any sub-15-minute value."""
    if rate_limit_seconds is None:
        return DEFAULT_RATE_LIMIT_SECONDS
    if rate_limit_seconds < MIN_REDELIVERY_INTERVAL_SECONDS:
        raise ValueError(
            f"{owner} must be at least {MIN_REDELIVERY_INTERVAL_SECONDS:g} seconds"
        )
    return rate_limit_seconds


def next_attempt_at(
    *,
    now: datetime,
    attempt_count: int,
    redelivery_floor_seconds: float | None = None,
) -> str:
    """The durable ``nextAttemptAt`` timestamp stamped after one delivery attempt.

    A durable ROW, never an in-memory timer -- it is written atomically alongside the delivery
    snapshot, so it survives a daemon/MCP restart (the Restate durable-timer lesson R2 cites).
    """
    effective_seconds = max(
        backoff_seconds_for_attempt(attempt_count),
        require_redelivery_floor_seconds(
            redelivery_floor_seconds, owner="nextAttemptAt redelivery floor"
        ),
    )
    return (now + timedelta(seconds=effective_seconds)).isoformat()


def is_due(entry: OperatorInboxEntry, *, now: datetime) -> bool:
    """Whether ``entry`` is a pending row whose backoff window has elapsed."""
    if is_ladder_resolved(entry):
        return False
    if entry.state != "pending":
        return False
    if entry.deliveryState not in _REDELIVERABLE_DELIVERY_STATES:
        return False
    if entry.nextAttemptAt is None:
        return True
    try:
        due_at = datetime.fromisoformat(entry.nextAttemptAt)
    except ValueError:
        return True
    return now >= due_at


def is_rate_limited(
    entry: OperatorInboxEntry,
    *,
    now: datetime,
    rate_limit_seconds: float = DEFAULT_RATE_LIMIT_SECONDS,
) -> bool:
    """Whether the per-target floor still holds since the last attempt on this entry."""
    floor_seconds = require_redelivery_floor_seconds(
        rate_limit_seconds, owner="redelivery rate limit"
    )
    if entry.lastAttemptAt is None:
        return False
    try:
        last_attempt = datetime.fromisoformat(entry.lastAttemptAt)
    except ValueError:
        return False
    return (now - last_attempt).total_seconds() < floor_seconds


def redeliverable(
    entries: list[OperatorInboxEntry],
    *,
    now: datetime,
    rate_limit_seconds: float = DEFAULT_RATE_LIMIT_SECONDS,
) -> list[OperatorInboxEntry]:
    """Pending rows both past their backoff window and clear of the per-target rate limit."""
    return [
        entry
        for entry in entries
        if not is_ladder_resolved(entry)
        and is_due(entry, now=now)
        and not is_rate_limited(entry, now=now, rate_limit_seconds=rate_limit_seconds)
    ]


def is_ladder_resolved(entry: OperatorInboxEntry) -> bool:
    """Whether the escalation ladder has terminally resolved this row without an ack."""
    return entry.state == "ladder-resolved"
