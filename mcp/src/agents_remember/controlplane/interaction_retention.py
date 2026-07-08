"""Retention policy for short-lived gate and operator-inbox interactions."""

from __future__ import annotations

from datetime import datetime

from agents_remember.controlplane.operator_inbox_records import OperatorInboxEntry
from agents_remember.controlplane.records import GateKind, GateRecord
from agents_remember.observer.timeutil import age_seconds

GATE_RESPONSE_WAIT_TIMEOUT_SECONDS = 300.0
GATE_RESPONSE_WAIT_POLL_SECONDS = 5.0
INTERACTION_RECORD_TTL_SECONDS = 24 * 60 * 60.0
AGENT_PICKUP_TTL_SECONDS = 300.0

MUTATING_TOOL_GATE_KINDS: frozenset[GateKind] = frozenset(
    {
        "worktree-intent",
        "closeout-approval",
        "push-approval",
        "integration-approval",
        "cleanup-approval",
    }
)
PRUNE_IMMEDIATE_GATE_STATES = frozenset({"applied", "cancelled", "expired"})


def gate_keep_ids(
    records: list[GateRecord],
    *,
    now: datetime,
    ttl_seconds: float = INTERACTION_RECORD_TTL_SECONDS,
) -> set[str]:
    """Gate ids whose current snapshot still belongs in the compacted log."""
    latest: dict[str, GateRecord] = {}
    for record in records:
        latest[record.id] = record
    return {
        gate.id
        for gate in latest.values()
        if _keep_gate(gate, now=now, ttl_seconds=ttl_seconds)
    }


def inbox_keep_ids(
    entries: list[OperatorInboxEntry],
    *,
    now: datetime,
    ttl_seconds: float = INTERACTION_RECORD_TTL_SECONDS,
) -> set[str]:
    """Inbox ids whose current snapshot still belongs in the compacted log."""
    latest: dict[str, OperatorInboxEntry] = {}
    for entry in entries:
        latest[entry.id] = entry
    return {
        entry.id
        for entry in latest.values()
        if _keep_inbox_entry(entry, now=now, ttl_seconds=ttl_seconds)
    }


def delete_after_wait(gate: GateRecord) -> bool:
    """Whether a returned gate decision can be removed after the agent saw it."""
    return gate.kind not in MUTATING_TOOL_GATE_KINDS


def pickup_state(entry: OperatorInboxEntry, *, now: datetime) -> str:
    """Dashboard state for a pending response waiting for agent pickup."""
    age = age_seconds(entry.createdAt, now)
    if age is not None and age > AGENT_PICKUP_TTL_SECONDS:
        return "check-chat"
    return "waiting-for-agent"


def pickup_age_seconds(entry: OperatorInboxEntry, *, now: datetime) -> float | None:
    return age_seconds(entry.createdAt, now)


def _keep_gate(gate: GateRecord, *, now: datetime, ttl_seconds: float) -> bool:
    if gate.state in PRUNE_IMMEDIATE_GATE_STATES:
        return False
    age = age_seconds(gate.ts, now)
    return age is None or age <= ttl_seconds


def _keep_inbox_entry(
    entry: OperatorInboxEntry, *, now: datetime, ttl_seconds: float
) -> bool:
    """R1 (260707-HFX2-L1): compaction NEVER removes a pending/unacked row, regardless of age --
    an unacked row outlives any cleanup until it is acked (consumed) or ladder-resolved. Only a
    consumed row is subject to the age-bounded retention window (kept as an audit grace period;
    the ordinary consume path already deletes its row explicitly and never reaches compaction)."""
    if entry.state == "ladder-resolved":
        return False
    if entry.state == "pending":
        return True
    age = age_seconds(entry.createdAt, now)
    return age is None or age <= ttl_seconds
