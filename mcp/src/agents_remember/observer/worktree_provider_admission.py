"""Admission policy for worktree-scoped provider/runtime projection."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from agents_remember.observer.events import Event
from agents_remember.observer.lifecycle_state import TERMINAL_STATES
from agents_remember.observer.projection import EnclosureNode, LifecycleProjection
from agents_remember.observer.reducer import project_lifecycle

PROVIDER_RELEVANT_PHASES = frozenset(
    {"request", "trust-checkpoint", "reframe-research", "decide", "build"}
)


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
        if lifecycle is None:
            continue
        if lifecycle.state in TERMINAL_STATES or lifecycle.phase not in PROVIDER_RELEVANT_PHASES:
            continue
        groups.add(Path(enclosure.worktreeGroup).name)
    return groups


def active_enclosure_worktree_groups(
    enclosures: list[EnclosureNode],
    lifecycle_logs: list[list[Event]],
    *,
    now: datetime,
) -> set[str]:
    """Worktree groups still tied to a non-terminal enclosure lifecycle."""
    lifecycles = _project_lifecycle_map(lifecycle_logs, now=now)
    groups: set[str] = set()
    for enclosure in enclosures:
        if not enclosure.lifecycleId or not enclosure.worktreeGroup:
            continue
        if enclosure.cleanup in {"completed", "abandoned"}:
            continue
        lifecycle = lifecycles.get(enclosure.lifecycleId)
        if lifecycle is None or lifecycle.state in TERMINAL_STATES:
            continue
        groups.add(Path(enclosure.worktreeGroup).name)
    return groups


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
