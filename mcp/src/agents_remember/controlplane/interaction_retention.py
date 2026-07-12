"""Retention policy for short-lived gate and operator-inbox interactions."""

from __future__ import annotations

from datetime import datetime

from agents_remember.controlplane.operator_inbox_records import (
    OperatorInboxEntry,
    fold_operator_inbox_entries,
)
from agents_remember.controlplane.records import GateKind, GateRecord
from agents_remember.observer.timeutil import age_seconds

GATE_RESPONSE_WAIT_TIMEOUT_SECONDS = 300.0
GATE_RESPONSE_WAIT_POLL_SECONDS = 5.0
INTERACTION_RECORD_TTL_SECONDS = 24 * 60 * 60.0
AGENT_PICKUP_TTL_SECONDS = 300.0

INBOX_PENDING_TTL_SECONDS = 48 * 60 * 60.0
"""Ruled invariant (developer, 2026-07-09): no row outranks system health -- pending/unacked rows
age out too. The inbox is a notification surface, not the record; the durable artifact (turn
report, task doc, gate) lives on disk and survives the purge. A nudge nobody consumed within this
window is stale noise; if its condition still holds, the supervisor recreates one fresh row.
Supersedes the HFX2-L1 R1 immortal-pending rule, which let the 2026-07-09 escalation storm grow a
227 MB / 20k-pending-row inbox that took the host down."""

INBOX_MAX_CURRENT_ROWS = 500
"""Hard health cap on folded inbox rows: past this, compaction keeps the newest rows and evicts
the oldest regardless of state. A correctly coalescing system sits orders of magnitude below this
(one row per distinct root cause); the cap is the backstop that bounds the store even when a
producer misbehaves."""

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
    max_rows: int = INBOX_MAX_CURRENT_ROWS,
    current: dict[str, OperatorInboxEntry] | None = None,
) -> set[str]:
    """Inbox ids whose current snapshot still belongs in the compacted log."""
    latest = fold_operator_inbox_entries(entries) if current is None else current
    kept = [
        entry
        for entry in latest.values()
        if _keep_inbox_entry(entry, now=now, ttl_seconds=ttl_seconds)
    ]
    if len(kept) > max_rows:
        kept.sort(key=lambda entry: entry.createdAt, reverse=True)
        kept = kept[:max_rows]
    return {entry.id for entry in kept}


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
    """Ruled invariant (developer, 2026-07-09): system health outranks every row, pending
    included. A pending/unacked row is kept only within :data:`INBOX_PENDING_TTL_SECONDS`;
    consumed rows keep the shorter audit window; ladder-resolved rows drop immediately. This
    supersedes HFX2-L1 R1's immortal-pending rule -- the durable record is the artifact on disk,
    never the inbox row, so purging an old nudge loses nothing the supervisor cannot recreate
    (as one fresh row) while its condition persists."""
    if entry.state == "ladder-resolved":
        return False
    age = age_seconds(entry.createdAt, now)
    if entry.state == "pending":
        return age is None or age <= INBOX_PENDING_TTL_SECONDS
    return age is None or age <= ttl_seconds
