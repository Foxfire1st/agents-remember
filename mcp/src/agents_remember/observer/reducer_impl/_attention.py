"""Attention queue: rank what needs the human from the reduced surfaces.

Pure and deterministic: every source contributes one small builder and the
queue sorts by (severity, wait, id). Dismissals suppress lifecycle-bound items
until a newer triggering signal re-surfaces them.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from agents_remember.controlplane.attention_dismissals import AttentionDismissalRecord
from agents_remember.observer.projection import (
    AttentionItem,
    DriftSnapshotNode,
    LifecycleProjection,
    ProviderNode,
    SetupProgressNode,
)
from agents_remember.observer.reducer_impl._processes import _str_or_none
from agents_remember.observer.reducer_impl._types import AnalyticalInputs

# --- attention queue (slice 05) ----------------------------------------------

_SEVERITY_RANK: dict[str, int] = {"alarm": 0, "warn": 1, "info": 2}
_PROVIDER_DOWN: frozenset[str] = frozenset({"stopped", "failed", "error"})
_DISMISSABLE_REPO_KINDS: frozenset[str] = frozenset({"actionable-drift"})


def build_attention_queue(
    lifecycles: list[LifecycleProjection],
    providers: list[ProviderNode],
    given: AnalyticalInputs,
) -> list[AttentionItem]:
    """The home-screen attention queue: a ranked cross-section of what needs the human.

    Pure and deterministic -- a total sort by (severity, wait, id) -- so the served
    projection and sim replay stay byte-identical. Every ``waitSeconds`` is an age the
    reducer/readers already computed server-side (``staleSeconds`` /
    ``snapshotStaleSeconds`` / ``heartbeatAgeSeconds``), never a client's render time.
    The taxonomy is extensible (North-Star Constraint 7): each source contributes one
    small builder. Enclosure-derived items (pending review / worktree debt) are the
    hangar's job (5c).

    ``given.attention_dismissals`` (``{itemId: AttentionDismissalRecord}``, read from the
    compact :class:`AttentionDismissalStore`, leaf-28 S5.2) suppresses a lifecycle-bound item
    the operator cleared while keeping the derivation pure: an item is dropped when a
    matching lifecycle acknowledgement exists at or after its triggering ``signalTs``,
    and re-surfaces the moment a *newer* signal arrives.
    """
    items = [
        *_lifecycle_attention(lifecycles),
        *_provider_attention(providers),
        *_drift_attention(given.drift_snapshots),
        *_setup_attention(given.setup_progress),
        *_start_attention(given.engine_start_progress),
    ]
    dismissed = given.attention_dismissals
    items = [item for item in items if not _is_dismissed(item, dismissed)]
    items.sort(
        key=lambda item: (_SEVERITY_RANK.get(item.severity, 9), -(item.waitSeconds or 0.0), item.id)
    )
    return items


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/observer/reducer_impl/_attention.py:78).
def _is_dismissed(
    item: AttentionItem, dismissals: dict[str, AttentionDismissalRecord]
) -> bool:  # pragma: no cover
    """True when a lifecycle acknowledgement still suppresses this item (leaf-28 S5.2).

    A dismissal holds while it is at or after the item's triggering ``signalTs``;
    a *newer* signal (``signalTs`` > ``dismissedAt``) re-surfaces the item. An item
    with no ``signalTs`` anchor stays suppressed until its condition clears (it stops
    being emitted) -- the dismissal then has nothing to hide.
    """
    dismissal = dismissals.get(item.id)
    if dismissal is None:
        return False
    if dismissal.kind is not None and dismissal.kind != item.kind:
        return False
    if item.lifecycleId is not None or dismissal.lifecycleId is not None:
        if item.lifecycleId != dismissal.lifecycleId:
            return False
        return not _signal_after(item.signalTs, dismissal.dismissedAt)
    if item.kind not in _DISMISSABLE_REPO_KINDS:
        return False
    return not _signal_after(item.signalTs, dismissal.dismissedAt)


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/observer/reducer_impl/_attention.py:100).
def _signal_after(signal_ts: str | None, dismissed_at: str) -> bool:  # pragma: no cover
    """True when the triggering signal is strictly newer than the dismissal.

    Parses both ISO-8601 stamps (rather than comparing strings) so a stamp printed
    without microseconds still orders correctly.
    """
    if not signal_ts:
        return False
    signal = datetime.fromisoformat(signal_ts)
    dismissed = datetime.fromisoformat(dismissed_at)
    if signal.tzinfo is None:
        signal = signal.replace(tzinfo=UTC)
    if dismissed.tzinfo is None:
        dismissed = dismissed.replace(tzinfo=UTC)
    return signal > dismissed


def _lifecycle_attention(lifecycles: list[LifecycleProjection]) -> list[AttentionItem]:
    """An event-backed session that went quiet (one item per lifecycle).

    A synthesized dormant persistent worktree (``lastEventTs == ""``) is the hangar's job
    (note 06), not the queue.
    """
    return [
        AttentionItem(
            id=f"stale-session:{lifecycle.id}",
            kind="stale-session",
            severity="info",
            lane="lifecycle",
            title="Session gone quiet",
            waitSeconds=lifecycle.staleSeconds,
            lifecycleId=lifecycle.id,
            repoId=lifecycle.repoId,
            # The moment it last had activity (the quiet point): a revived session
            # advances this past a dismissal and re-surfaces if it later goes quiet again.
            signalTs=lifecycle.lastEventTs or None,
        )
        for lifecycle in lifecycles
        if lifecycle.inferred and lifecycle.state == "paused" and lifecycle.lastEventTs
    ]


def _provider_attention(providers: list[ProviderNode]) -> list[AttentionItem]:
    """A down/crashed provider -- the 2026-06-09 invisible fire, made an alarm."""
    return [
        AttentionItem(
            id=f"provider-down:{provider.id}",
            kind="provider-down",
            severity="alarm",
            lane="repo",
            title=f"Provider {provider.id} down",
            detail=provider.state,
            waitSeconds=provider.snapshotStaleSeconds,
            providerId=provider.id,
        )
        for provider in providers
        if provider.ok is False or provider.state in _PROVIDER_DOWN
    ]


def _drift_attention(drift_snapshots: list[DriftSnapshotNode]) -> list[AttentionItem]:
    """Onboarding that needs a refresh decision (actionable drift)."""
    return [
        AttentionItem(
            id=f"actionable-drift:{drift.repository}:{drift.branch}",
            kind="actionable-drift",
            severity="warn",
            lane="repo",
            title=f"{drift.actionableCount} actionable drift in {drift.repository}",
            detail=_drift_attention_detail(drift),
            waitSeconds=drift.snapshotStaleSeconds,
            repoId=drift.repository,
            signalTs=drift.checkedAt,
        )
        for drift in drift_snapshots
        if drift.actionableCount > 0
    ]


def _drift_attention_detail(drift: DriftSnapshotNode) -> str:
    """Concrete provenance for repo-level drift rows."""
    parts = [f"branch {drift.branch}"]
    if drift.memoryRoot:
        parts.append(f"memory {drift.memoryRoot}")
    else:
        parts.append("memory root unknown")
    if drift.reportPath:
        parts.append(f"report {drift.reportPath}")
    if drift.checkedAt:
        parts.append(f"checked {drift.checkedAt}")
    return " · ".join(parts)


def _setup_attention(setup_progress: list[SetupProgressNode]) -> list[AttentionItem]:
    """A provider-setup boot that failed or went stale mid-flight."""
    return [
        AttentionItem(
            id=f"failed-setup:{setup.group}",
            kind="failed-setup",
            severity="alarm" if setup.failedPhases else "warn",
            lane="worktree",
            title="Provider setup needs attention",
            detail=setup.currentPhase,
            waitSeconds=setup.heartbeatAgeSeconds,
            enclosure=setup.group,
        )
        for setup in setup_progress
        if setup.failedPhases or setup.state in {"failed", "stale"}
    ]


def _start_attention(start_progress: list[dict[str, Any]]) -> list[AttentionItem]:
    """A ``worktree_start`` gated before its contract was written -- the same master-caution the agent
    raises in chat (§9). Only blocked entries are alarms; a happy-path progress beat (no
    ``blockedReason``) is observability, not an alarm. Steady ``warn`` -- a human-choice gate, not a
    fault (faults flicker, §3.2)."""
    items: list[AttentionItem] = []
    for entry in start_progress:
        reason = _str_or_none(entry.get("blockedReason"))
        if reason is None:
            continue
        group = str(entry.get("worktreeGroup", ""))
        items.append(
            AttentionItem(
                id=f"blocked-start:{group.rsplit('/', 1)[-1]}",
                kind="blocked-start",
                severity="warn",
                lane="worktree",
                title="Worktree start blocked",
                detail=reason,
                enclosure=group or None,
                repoId=_str_or_none(entry.get("repoName")),
            )
        )
    return items
