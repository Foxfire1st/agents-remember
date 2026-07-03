"""Admission policy for worktree-scoped provider/runtime projection."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from agents_remember.observer.events import Event
from agents_remember.observer.lifecycle_state import TERMINAL_STATES
from agents_remember.observer.projection import EnclosureNode, LifecycleProjection
from agents_remember.observer.reducer import project_lifecycle

PROVIDER_RELEVANT_PHASES = frozenset(
    {"request", "trust-checkpoint", "reframe-research", "decide", "build"}
)
# An archived leaf enclosure (the durable "this worktree is finished" signal). A master series is
# retired only when EVERY leaf is archived.
ARCHIVED_CLEANUP_STATES = frozenset({"completed", "abandoned"})
# After a master series is fully archived, keep its whole event history for one week before the
# inactivity TTL is allowed to retire it (the developer-chosen grace).
MASTER_ARCHIVE_GRACE_SECONDS = 7 * 24 * 60 * 60


def admitted_worktree_groups(
    enclosures: list[EnclosureNode],
    lifecycle_logs: list[list[Event]],
    *,
    now: datetime,
) -> set[str]:
    """Worktree groups whose isolated provider state is still operational."""
    lifecycles = _project_lifecycle_map(lifecycle_logs, now=now)
    groups: set[str] = set()
    for enclosure in enclosures:
        if not _enclosure_is_provider_relevant(enclosure):
            continue
        lifecycle = lifecycles.get(enclosure.lifecycleId)
        # A MISSING lifecycle log (pruned for inactivity) must not retire a live enclosure: the durable
        # enclosure state above is the source of truth. Only a PRESENT log that is genuinely terminal or
        # past the provider-relevant phases demotes the worktree's provider stack.
        if lifecycle is not None and (
            lifecycle.state in TERMINAL_STATES or lifecycle.phase not in PROVIDER_RELEVANT_PHASES
        ):
            continue
        groups.add(Path(enclosure.worktreeGroup).name)
    return groups


def active_enclosure_worktree_groups(
    enclosures: list[EnclosureNode],
    lifecycle_logs: list[list[Event]],
    *,
    now: datetime,
) -> set[str]:
    """Worktree groups still tied to a live enclosure (durable file state, not a prunable log).

    An "active" worktree is decided by the enclosure CONTRACT — a leaf whose cleanup is still pending
    is live regardless of whether its lifecycle event log has been retired for inactivity. The event
    log is a best-effort demotion signal only: a present, genuinely terminal log drops the group, but a
    MISSING log never does (that was the bug — a running worktree vanished from the Engine Room an hour
    after its last lifecycle event because the log it keyed on had been pruned).
    """
    lifecycles = _project_lifecycle_map(lifecycle_logs, now=now)
    groups: set[str] = set()
    for enclosure in enclosures:
        if not enclosure.lifecycleId or not enclosure.worktreeGroup:
            continue
        if enclosure.cleanup in ARCHIVED_CLEANUP_STATES:
            continue
        lifecycle = lifecycles.get(enclosure.lifecycleId)
        if lifecycle is not None and lifecycle.state in TERMINAL_STATES:
            continue
        groups.add(Path(enclosure.worktreeGroup).name)
    return groups


def series_retained_lifecycle_ids(
    enclosures: list[EnclosureNode],
    *,
    now: datetime,
) -> set[str]:
    """Lifecycle ids whose event logs must be RETAINED because their master series is not retired.

    Durable-state retention that SUPERSEDES the per-log inactivity TTL. Leaf enclosures are grouped
    into their master series (repo + task name). A series is retired only when EVERY leaf is archived
    (cleanup ``completed``/``abandoned``), and even then its whole history is kept for a one-week grace
    measured from the most recently finalized leaf contract. So while any leaf is live, ALL of the
    series' leaves' logs are protected — a running durable master never loses its own or a sibling
    leaf's events. Fleeting/standalone logs (no enclosure, or an enclosure with no task name) are not
    returned here and keep the ordinary inactivity TTL.
    """
    by_master: dict[tuple[str, str], list[EnclosureNode]] = {}
    for enclosure in enclosures:
        if not enclosure.lifecycleId or not enclosure.taskName:
            continue
        by_master.setdefault((enclosure.repoName, enclosure.taskName), []).append(enclosure)
    retained: set[str] = set()
    for group in by_master.values():
        if _series_is_retired(group, now=now):
            continue
        retained.update(enclosure.lifecycleId for enclosure in group)
    return retained


def _series_is_retired(group: list[EnclosureNode], *, now: datetime) -> bool:
    """True once every leaf of a master series is archived AND its grace window has elapsed."""
    if any(enclosure.cleanup not in ARCHIVED_CLEANUP_STATES for enclosure in group):
        return False  # at least one leaf is still live -> the series is not retired
    finalized_at = max(
        (
            ts
            for ts in (_contract_finalized_at(enclosure.enclosure) for enclosure in group)
            if ts is not None
        ),
        default=None,
    )
    if finalized_at is None:
        return True  # archived with no readable contract timestamp -> no grace to extend
    return (now - finalized_at) > timedelta(seconds=MASTER_ARCHIVE_GRACE_SECONDS)


def _contract_finalized_at(contract_path: str) -> datetime | None:
    """The leaf contract's last-write time (its finalize/cleanup stamp), or ``None`` if unreadable."""
    try:
        mtime = Path(contract_path).stat().st_mtime
    except OSError:
        return None
    return datetime.fromtimestamp(mtime, tz=UTC)


def _project_lifecycle_map(
    lifecycle_logs: list[list[Event]],
    *,
    now: datetime,
) -> dict[str, LifecycleProjection]:
    lifecycles = {}
    for events in lifecycle_logs:
        if not events:
            continue
        try:
            lifecycle = project_lifecycle(events, now=now)
        except ValueError:
            continue
        lifecycles[lifecycle.id] = lifecycle
    return lifecycles


def _enclosure_is_provider_relevant(enclosure: EnclosureNode) -> bool:
    if not enclosure.lifecycleId:
        return False
    if not enclosure.worktreeGroup:
        return False
    if enclosure.cleanup in {"completed", "abandoned"}:
        return False
    return enclosure.closeoutStatus != "completed" and enclosure.integrationStatus != "completed"
