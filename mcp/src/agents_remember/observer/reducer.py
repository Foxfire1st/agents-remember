"""The reducer: events (+ file snapshots) -> resolved projections.

The single owner of interpretation (design §2.5). :func:`project_lifecycle`
folds one lifecycle's event log into a :class:`LifecycleProjection`; it is a pure
function of ``(events, now)`` so "same log => same projection" is testable and a
recorded log doubles as dev/test/demo/replay fixture. :func:`project_workspace`
assembles the whole tree from already-read inputs (logs + provider/enclosure
snapshots), keeping all file I/O at the call edge
(:mod:`agents_remember.observer.projection_store`).

The inferred layer reuses the write-side thresholds rather than re-deriving them:
a ``running`` lifecycle whose last event is older than ``STALE_AFTER_SECONDS``
projects ``paused`` (mirrors ``providers.setup_progress``'s ``_project_running``),
and a dormant never-promoted fleeting lifecycle projects ``abandoned`` (mirrors
``ambient._is_dormant_fleeting`` -- the reducer *projects* the state, the sweep
*prunes* the log). Both are marked ``inferred`` so a renderer never shows a
derived state as a written fact.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, get_args

from agents_remember.observer.events import Event
from agents_remember.observer.lifecycle_state import (
    INITIAL_PHASE,
    TERMINAL_STATES,
    State,
    coerce_phase,
)
from agents_remember.observer.projection import (
    ActionAvailability,
    EnclosureNode,
    LifecycleProjection,
    Metrics,
    ProviderNode,
    WorkspaceProjection,
)
from agents_remember.observer.timeutil import STALE_AFTER_SECONDS, TTL_SECONDS, age_seconds

_STATES: frozenset[str] = frozenset(get_args(State))


def project_lifecycle(events: list[Event], *, now: datetime) -> LifecycleProjection:
    """Fold one lifecycle's event log into its resolved projection.

    Pure: the only inputs are the log and ``now``. ``events`` must be non-empty
    and append-ordered (the store's invariant); ``events[0]`` is the
    self-contained ``lifecycle.started`` that seeds the projection.
    """
    if not events:
        raise ValueError("cannot project an empty event log")
    proj = _seed_from_started(events[0])
    corrections = _index_corrections(events)
    for event in events[1:]:
        if event.kind == "correction.recorded":
            continue  # its effect is applied at the corrected event's position
        proj = _apply_kind(proj, event)
        corrected = corrections.get(event.id)
        if corrected is not None:
            proj = proj.model_copy(update={"state": corrected})
    proj = proj.model_copy(update={"lastEventTs": events[-1].ts})
    proj = _project_inferred(proj, events, now)
    return proj.model_copy(update={"actions": _lifecycle_actions(proj)})


def project_workspace(
    logs: list[list[Event]],
    *,
    enclosures: list[EnclosureNode],
    providers: list[ProviderNode],
    now: datetime,
) -> WorkspaceProjection:
    """Assemble the whole tree from already-read logs + structural snapshots."""
    lifecycles = [project_lifecycle(log, now=now) for log in logs if log]
    enriched = [
        enclosure.model_copy(update={"actions": enclosure_actions(enclosure)})
        for enclosure in enclosures
    ]
    return WorkspaceProjection(
        generatedAt=now.isoformat(),
        lifecycles=lifecycles,
        enclosures=enriched,
        providers=providers,
        metrics=_metrics(lifecycles),
    )


# --- the fold ----------------------------------------------------------------


def _seed_from_started(event: Event) -> LifecycleProjection:
    data = event.data
    return LifecycleProjection(
        id=event.lifecycleId or "",
        state="running",
        phase=coerce_phase(str(data.get("phase", INITIAL_PHASE))),
        fleeting=bool(data.get("fleeting", True)),
        enclosure=event.enclosure,
        repoId=event.repoId,
        startedAt=event.ts,
        lastEventTs=event.ts,
    )


def _apply_kind(proj: LifecycleProjection, event: Event) -> LifecycleProjection:
    data = event.data
    updates: dict[str, Any] = {}
    if event.kind == "lifecycle.phase-changed":
        updates["phase"] = coerce_phase(str(data.get("phase", proj.phase)))
    elif event.kind == "lifecycle.blocked":
        updates["state"] = "blocked"
        updates["ask"] = data.get("ask")
    elif event.kind == "lifecycle.resumed":
        updates["state"] = "running"
        updates["ask"] = None
    elif event.kind == "lifecycle.paused":
        updates["state"] = "paused"
    elif event.kind == "lifecycle.promoted":
        updates["fleeting"] = False
        updates["scope"] = data.get("scope")
        if event.enclosure is not None:
            updates["enclosure"] = event.enclosure
        if event.repoId is not None:
            updates["repoId"] = event.repoId
    elif event.kind == "lifecycle.ended":
        updates["state"] = "completed" if data.get("outcome") == "completed" else "abandoned"
    elif event.kind == "tool.completed":
        tokens = data.get("tokens")
        if isinstance(tokens, int):
            updates["tokens"] = proj.tokens + tokens
    # lifecycle.heartbeat (and any unknown kind): liveness only -- staleness is
    # taken from events[-1].ts, so no projection field changes here.
    return proj.model_copy(update=updates)


def _index_corrections(events: list[Event]) -> dict[str, str]:
    """Map corrected-event-id -> replacement state (append-only corrections, §2.1).

    v1 corrects the ``state`` field (the audit case: a wrongly-recorded outcome).
    A correction names the event it ``corrects`` and the new ``state``; the value
    is validated against the state enum so a malformed correction is ignored.
    """
    index: dict[str, str] = {}
    for event in events:
        if event.kind != "correction.recorded":
            continue
        corrected = event.data.get("corrects")
        new_state = event.data.get("state")
        if isinstance(corrected, str) and isinstance(new_state, str) and new_state in _STATES:
            index[corrected] = new_state
    return index


def _project_inferred(
    proj: LifecycleProjection, events: list[Event], now: datetime
) -> LifecycleProjection:
    age = age_seconds(events[-1].ts, now)
    proj = proj.model_copy(update={"staleSeconds": age})
    if proj.state in TERMINAL_STATES or age is None:
        return proj
    # Mirrors ambient._is_dormant_fleeting: the reducer projects abandoned where
    # the opportunistic sweep would prune. Persistent lifecycles are never reaped.
    if proj.fleeting and age > TTL_SECONDS:
        return proj.model_copy(update={"state": "abandoned", "inferred": True})
    # Mirrors setup_progress._project_running: a live state gone quiet reads paused.
    if proj.state == "running" and age > STALE_AFTER_SECONDS:
        return proj.model_copy(update={"state": "paused", "inferred": True})
    return proj


# --- action availability (the reducer decides, never the UI) ------------------


def _lifecycle_actions(proj: LifecycleProjection) -> list[ActionAvailability]:
    blocked = proj.state == "blocked"
    return [
        ActionAvailability(
            action="resume",
            enabled=blocked,
            disabledReason=None if blocked else "lifecycle is not blocked",
            nextSafeAction=None if blocked else "resume becomes safe once the gate is resolved",
        )
    ]


def enclosure_actions(enclosure: EnclosureNode) -> list[ActionAvailability]:
    return [_integrate_action(enclosure), _cleanup_action(enclosure)]


def _integrate_action(enclosure: EnclosureNode) -> ActionAvailability:
    if enclosure.closeoutStatus != "completed":
        return ActionAvailability(
            action="integrate",
            enabled=False,
            disabledReason="closeout not complete",
            nextSafeAction="run worktree_closeout_preview then apply",
        )
    if enclosure.integrationStatus != "not-started":
        return ActionAvailability(
            action="integrate",
            enabled=False,
            disabledReason=f"integration already {enclosure.integrationStatus}",
        )
    return ActionAvailability(action="integrate", enabled=True)


def _cleanup_action(enclosure: EnclosureNode) -> ActionAvailability:
    if enclosure.integrationStatus != "completed":
        return ActionAvailability(
            action="cleanup",
            enabled=False,
            disabledReason="integration not complete",
            nextSafeAction="integrate first",
        )
    if enclosure.cleanup != "pending":
        return ActionAvailability(
            action="cleanup",
            enabled=False,
            disabledReason=f"already {enclosure.cleanup}",
        )
    return ActionAvailability(action="cleanup", enabled=True)


def _metrics(lifecycles: list[LifecycleProjection]) -> Metrics:
    return Metrics(
        lifecycleCount=len(lifecycles),
        runningCount=sum(1 for lc in lifecycles if lc.state == "running"),
        blockedCount=sum(1 for lc in lifecycles if lc.state == "blocked"),
        pausedCount=sum(1 for lc in lifecycles if lc.state == "paused"),
        totalTokens=sum(lc.tokens for lc in lifecycles),
    )
