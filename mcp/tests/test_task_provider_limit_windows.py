"""MIK-R190 rule 13: a limited task's hover names the window that actually holds the seat.

The producer stores a report's ``until`` as the latest reset among the exhausted windows, so the
seat's own window is that one. The served detail used to name whichever window the runtime listed
first, which told a reader "Session resets in two hours" while the same account's weekly window
held the seat for four days.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from agents_remember.observer.provider_limits import (
    ProviderLimitReport,
    ProviderLimits,
    ProviderLimitWindow,
    task_provider_limit,
)

NOW = datetime(2026, 10, 21, 10, 0, tzinfo=UTC)
PAST = ProviderLimitWindow(id="session-past", label="Session", resetsAt=NOW - timedelta(hours=1))
SESSION = ProviderLimitWindow(id="session", label="Session", resetsAt=NOW + timedelta(hours=2))
WEEKLY = ProviderLimitWindow(id="weekly", label="Weekly", resetsAt=NOW + timedelta(days=4))


@dataclass(frozen=True)
class Seat:
    """The identity one task's agent carries: its id and its role."""

    agent_id: str
    role: str | None


def report(*windows: ProviderLimitWindow, until: datetime | None = None) -> ProviderLimitReport:
    """One report of the product's file, exhausted until the latest of its windows."""
    resets = [window.resetsAt for window in windows]
    return ProviderLimitReport(
        until=until if until is not None else max(resets),
        sourceLabel="Claude",
        accountLabel="Max 20x",
        windows=windows,
        observedAt=NOW,
    )


def limits(
    reports: dict[str, ProviderLimitReport], agents: dict[str, tuple[str, ...]]
) -> ProviderLimits:
    return ProviderLimits(schema="ar-provider-limits/v1", reports=reports, agents=agents)


@pytest.mark.parametrize("windows", [(SESSION, WEEKLY), (WEEKLY, SESSION)])
def test_the_seat_names_the_window_whose_reset_holds_it(
    windows: tuple[ProviderLimitWindow, ...],
) -> None:
    """Both orders: the runtime's listing order never decides which window is served."""
    fact = task_provider_limit(
        limits({"r1": report(*windows)}, {"agent-a": ("r1",)}),
        [Seat("agent-a", "worker")],
        NOW,
    )

    assert fact is not None
    assert [(seat.window, seat.resetsAt) for seat in fact.seats] == [("Weekly", WEEKLY.resetsAt)]
    # The task's own end is still the latest reset of the seat's report.
    assert fact.until == WEEKLY.resetsAt


def test_a_window_that_already_reset_does_not_hold_the_seat() -> None:
    """An exhausted window whose reset has passed explains nothing about the wait that remains."""
    fact = task_provider_limit(
        limits({"r1": report(PAST, WEEKLY)}, {"agent-a": ("r1",)}),
        [Seat("agent-a", "reviewer")],
        NOW,
    )

    assert fact is not None
    assert [(seat.window, seat.resetsAt) for seat in fact.seats] == [("Weekly", WEEKLY.resetsAt)]


def test_two_windows_resetting_together_name_one_window_for_the_same_file() -> None:
    """Ties keep the report's own order, so one file never serves two different windows."""
    twin = ProviderLimitWindow(id="weekly-twin", label="Weekly (twin)", resetsAt=WEEKLY.resetsAt)
    first = task_provider_limit(
        limits({"r1": report(WEEKLY, twin)}, {"agent-a": ("r1",)}),
        [Seat("agent-a", "worker")],
        NOW,
    )
    again = task_provider_limit(
        limits({"r1": report(WEEKLY, twin)}, {"agent-a": ("r1",)}),
        [Seat("agent-a", "worker")],
        NOW,
    )

    assert first is not None and again is not None
    assert first.seats[0].window == "Weekly"
    assert first.seats[0].window == again.seats[0].window


def test_each_seat_keeps_its_own_report_while_the_task_names_the_earliest_end() -> None:
    """Two seats, two reports: per-seat detail is the seat's latest reset, the task's is the first."""
    fact = task_provider_limit(
        limits(
            {
                "r-session": report(SESSION),
                "r-weekly": report(WEEKLY),
                "r-older": report(PAST),
            },
            {
                "agent-a": ("r-weekly", "r-older"),
                "agent-b": ("r-session",),
            },
        ),
        [Seat("agent-a", "worker"), Seat("agent-b", "reviewer")],
        NOW,
    )

    assert fact is not None
    assert [(seat.role, seat.window, seat.resetsAt) for seat in fact.seats] == [
        ("worker", "Weekly", WEEKLY.resetsAt),
        ("reviewer", "Session", SESSION.resetsAt),
    ]
    # Max per seat, min between seats: the task moves when the first seat can.
    assert fact.until == SESSION.resetsAt


def test_a_seat_bound_to_several_reports_names_the_one_that_ends_last() -> None:
    """One account's several reports hold the seat until the last of them clears."""
    fact = task_provider_limit(
        limits(
            {"r-session": report(SESSION), "r-weekly": report(WEEKLY)},
            {"agent-a": ("r-session", "r-weekly")},
        ),
        [Seat("agent-a", "worker")],
        NOW,
    )

    assert fact is not None
    assert [(seat.window, seat.resetsAt) for seat in fact.seats] == [("Weekly", WEEKLY.resetsAt)]
    assert fact.until == WEEKLY.resetsAt


def test_the_served_reset_keeps_the_exact_instant_the_producer_stored() -> None:
    """L184-R1-F28's serving half: the wire carries the producer's instant, fractions and all.

    Two legal resets a second apart inside the same minute, and two inside the same second, are
    different instants. Nothing in the product's own read, projection or serialisation may fold
    them: the exactness a reader is owed is decided downstream, by the surface that formats this
    value, and the value it is given here is exact.
    """

    early = ProviderLimitWindow(
        id="session-early", label="Session", resetsAt=NOW.replace(microsecond=1_000)
    )
    late = ProviderLimitWindow(
        id="session-late", label="Session", resetsAt=NOW.replace(microsecond=999_000)
    )

    fact = task_provider_limit(
        limits(
            {"r-early": report(early), "r-late": report(late)},
            {"agent-a": ("r-early",), "agent-b": ("r-late",)},
        ),
        [Seat("agent-a", "worker"), Seat("agent-b", "reviewer")],
        NOW,
    )

    assert fact is not None
    served = fact.model_dump(mode="json")
    # The wire writes UTC with its Z; the instant, and its microseconds, are the producer's.
    stored = [
        early.resetsAt.isoformat().replace("+00:00", "Z"),
        late.resetsAt.isoformat().replace("+00:00", "Z"),
    ]
    assert [seat["resetsAt"] for seat in served["seats"]] == stored
    assert served["until"] == stored[0]
    assert stored[0] != stored[1]
