"""Attention queue: rank what needs the human from the reduced surfaces.

Pure and deterministic: every source contributes one small builder and the
queue sorts by (severity, wait, id). Provider/setup/start dismissals hold until
their native reader observes recovery or removal. Drift occurrence is carried by
its persisted source signal. The five native kinds live in ``_attention_native``;
this module owns the one queue, its ranking and the one dismissal rule.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from agents_remember.controlplane.attention_dismissals import (
    AttentionDismissalRecord,
    AttentionObservation,
)
from agents_remember.observer.projection import (
    AttentionItem,
    DriftSnapshotNode,
    ProviderNode,
    SetupProgressNode,
    TaskEntry,
)
from agents_remember.observer.reducer_impl._attention_native import native_attention_items
from agents_remember.observer.reducer_impl._processes import _str_or_none
from agents_remember.observer.reducer_impl._types import AnalyticalInputs

# --- attention queue (slice 05) ----------------------------------------------

_SEVERITY_RANK: dict[str, int] = {"alarm": 0, "warn": 1, "info": 2}
_PROVIDER_DOWN: frozenset[str] = frozenset({"stopped", "failed", "error"})


def build_attention_queue(
    providers: list[ProviderNode],
    given: AnalyticalInputs,
    tasks: list[TaskEntry] | None = None,
    now: datetime | None = None,
) -> list[AttentionItem]:
    """The home-screen attention queue: a ranked cross-section of what needs the human.

    Pure and deterministic -- a total sort by (severity, wait, id) -- so the served
    projection and sim replay stay byte-identical. Every ``waitSeconds`` is an age the
    reducer/readers already computed server-side (``staleSeconds`` /
    ``snapshotStaleSeconds`` / ``heartbeatAgeSeconds``), never a client's render time.
    The taxonomy is extensible (North-Star Constraint 7): each source contributes one
    small builder. Enclosure-derived items (pending review / worktree debt) are the
    hangar's job (5c).

    ``given.attention_dismissals`` is the reconciled current acknowledgement map.
    Source cleanup owns observed recovery/removal; this pure fold only suppresses
    a matching current item. It never infers recovery from age or a missing node.

    ``tasks`` are the joined task entries, so a stall and a notice carry the canonical task
    they open; ``now`` is the pass clock the stall deadline is evaluated at. A live native
    cause (``itemClass == "action"``) is never suppressed by an acknowledgement: rule 7 says it
    clears only when its cause ends, and the backend refuses the dismissal too.
    """
    items = [
        *_provider_attention(providers),
        *_drift_attention(given.drift_snapshots),
        *_setup_attention(given.setup_progress),
        *_start_attention(given.engine_start_progress),
        *native_attention_items(
            given.native_attention,
            list(tasks or []),
            now or datetime.now().astimezone(),
        ),
    ]
    dismissed = given.attention_dismissals
    items = [item for item in items if not item.dismissible or not _is_dismissed(item, dismissed)]
    items.sort(
        key=lambda item: (_SEVERITY_RANK.get(item.severity, 9), -(item.waitSeconds or 0.0), item.id)
    )
    return items


def _is_dismissed(item: AttentionItem, dismissals: dict[str, AttentionDismissalRecord]) -> bool:
    """A drift acknowledgement ends only on different complete actionable findings.

    Missing fingerprints retain existing acknowledgements through the admitted
    first-read transition. Other kinds end through native recovery/removal proof.
    """
    dismissal = dismissals.get(item.id)
    if dismissal is None or dismissal.kind != item.kind:
        return False
    return (
        item.kind != "actionable-drift"
        or item.actionableFingerprint is None
        or dismissal.actionableFingerprint is None
        or item.actionableFingerprint == dismissal.actionableFingerprint
    )


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
        if provider_condition(provider) == "failed"
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
            actionableFingerprint=drift.actionableFingerprint,
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
        if setup_condition(setup) == "failed"
    ]


def _start_attention(start_progress: list[dict[str, Any]]) -> list[AttentionItem]:
    """A ``worktree_start`` gated before its contract was written -- the same master-caution the agent
    raises in chat (§9). Only blocked entries are alarms; a happy-path progress beat (no
    ``blockedReason``) is observability, not an alarm. Steady ``warn`` -- a human-choice gate, not a
    fault (faults flicker, §3.2)."""
    items: list[AttentionItem] = []
    for entry in start_progress:
        group, reason = start_facts(entry)
        if group is None or reason is None:
            continue
        items.append(
            AttentionItem(
                id=f"blocked-start:{group}",
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


def provider_condition(provider: ProviderNode) -> str:
    """A provider recurs only after an explicit healthy observation, then failure."""
    if provider.ok is False or provider.state in _PROVIDER_DOWN:
        return "failed"
    if provider.ok is True or provider.state in {"running", "ready", "healthy"}:
        return "healthy"
    return "unknown"


def setup_condition(setup: SetupProgressNode) -> str:
    """A setup recurs only after observed running/completed work, then failure."""
    if setup.failedPhases or setup.state in {"failed", "stale"}:
        return "failed"
    return "healthy" if setup.state in {"running", "completed"} else "unknown"


def observe_providers(nodes: list[ProviderNode], observation: AttentionObservation | None) -> None:
    if observation is not None:
        for node in nodes:
            observation.observe(
                f"provider-down:{node.id}", healthy=provider_condition(node) == "healthy"
            )


def observe_setup(node: SetupProgressNode, observation: AttentionObservation | None) -> None:
    if observation is not None:
        observation.observe(
            f"failed-setup:{node.group}", healthy=setup_condition(node) == "healthy"
        )


def observe_start(entry: dict[str, Any], observation: AttentionObservation | None) -> None:
    """A native happy-path beat or cleared progress file ends a blocked start."""
    if observation is not None:
        group, reason = start_facts(entry)
        if group is None:
            observation.complete = False
        else:
            observation.observe(f"blocked-start:{group}", healthy=reason is None)


def start_facts(entry: dict[str, Any]) -> tuple[str | None, str | None]:
    """The native full group and blocked reason; no reason is a happy-path beat."""
    return _str_or_none(entry.get("worktreeGroup")), _str_or_none(entry.get("blockedReason"))
