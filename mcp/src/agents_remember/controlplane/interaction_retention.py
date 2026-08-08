"""Retention policy for short-lived gate and operator-inbox interactions."""

from __future__ import annotations

from datetime import datetime

from agents_remember.controlplane.operator_inbox_records import (
    OperatorInboxEntry,
    fold_operator_inbox_entries,
)
from agents_remember.controlplane.records import GateKind, GateRecord
from agents_remember.controlplane.stamps import age_seconds

GATE_RESPONSE_WAIT_TIMEOUT_SECONDS = 300.0
GATE_RESPONSE_WAIT_POLL_SECONDS = 5.0
INTERACTION_RECORD_TTL_SECONDS = 24 * 60 * 60.0
AGENT_PICKUP_TTL_SECONDS = 300.0

INBOX_PENDING_TTL_SECONDS = 48 * 60 * 60.0
"""Ruled invariant (developer, 2026-07-09): no row outranks system health -- pending/unacked rows
age out too. The inbox is a notification surface, not the record; the durable artifact (turn
report, task doc, gate) lives on disk and survives the purge. A nudge nobody consumed within this
window is stale noise; if its condition still holds, the agent-notifier recreates one fresh row.
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

SEAM_CONSUMED_GATE_KINDS: frozenset[GateKind] = frozenset({"master-handover-approval"})
"""The seam approval an integration folds before it lands a master. Not a mutating-tool kind (it
is decided by the orchestrator, not raised against a worktree), but it is consumed by an
enforcement rung all the same, so its ``applied`` snapshot has the same standing."""

CONSUMED_APPROVAL_GATE_KINDS: frozenset[GateKind] = (
    MUTATING_TOOL_GATE_KINDS | SEAM_CONSUMED_GATE_KINDS
)
"""Gate kinds whose ``applied`` snapshot is an AUTHORITY RECORD, not routine garbage.

For these kinds the ``applied`` snapshot is the whole and only proof that a human approval was
spent: ``controlplane/enforcement.py::evaluate_gate`` refuses a second consume solely because it
finds one in the fold. Delete it and the fold returns to *permitted-gateless* -- "no gate; existing
approval channel governs" -- and the same approval buys a second mutation, with no error and no log
line. That is this leaf's defect, and until 260731-EFA-L5 R1 the retention pass below was one of
the things producing it: ``applied`` was pruned at ANY AGE, so any later decision on the lifecycle
(``controlplane/gate_decisions.py::_reclaim_gate_log`` runs after every one, and an
``agent-question`` being answered is enough) erased the marker within milliseconds. It was already
wrong before this leaf --
the same states were pruned on the dashboard's 30s projection tick, so the marker survived at most
half a minute -- and moving reclamation into the deciding process is what made it prompt and
deterministic rather than merely likely.

The set is the kinds a SERVER-SIDE MUTATION consumes: the five ``MUTATING_TOOL_GATE_KINDS`` plus
``master-handover-approval``, the master-exit seam gate ``worktrees/modules/integrate.py`` folds
before it integrates. Only two of those are asked about today (closeout and handover), and the test
suite pins that every kind an enforcement path evaluates is in this set, so adding an enforcement
rung for one of the other four cannot silently outrun its retention. Being a superset costs
nothing: a kind nobody applies contributes no records.

Every OTHER kind's ``applied`` snapshot stays immediately prunable and that is deliberate.
``agent-question`` (applied by ``serving/hosted_interactions.py`` on every answered vendor
interaction), ``alarm-ack``, ``provider-retry`` and ``plan-approval`` are never passed to
``evaluate_gate``, so their ``applied`` record refuses nothing -- it is pure history, and
``agent-question`` in particular is unbounded in a long adapter session. The retained set is
bounded by approvals a human actually granted and a mutating tool actually consumed: a handful per
lifecycle, in a log that lives beside that lifecycle's events."""

PRUNE_IMMEDIATE_GATE_STATES = frozenset({"cancelled", "expired"})
"""Terminal states a compaction may drop at any age, and why ``applied`` is NOT one of them.

Dropping a gate's last snapshot returns ``evaluate_gate`` to the permitted-gateless verdict. For
these two that is exactly right, and for ``applied`` it is the replay:

* ``cancelled`` -- the gate was WITHDRAWN. It never carried an approval, and cancelling it says
  precisely "this gate no longer governs", so falling back to the chat/commit approval channel is
  the intended outcome. Retention is not even what removes it in production:
  ``application/gate_tools.py::gate_decide_tool`` calls ``GateStore.delete`` on the record the moment
  the cancel is recorded.
* ``expired`` -- the gate was SUPERSEDED. ``expire_gate`` is written only when a newer gate opens
  on the same lifecycle, so the gate that replaced it is in the same log with a newer ``ts`` and
  governs the fold. Dropping the expired snapshot drops history, never authority.
* ``applied`` -- the gate was GRANTED AND SPENT. There is nothing to fall back to: the approval
  exists, it was consumed, and the only record saying so is this one. It is retained for the kinds
  above (:data:`CONSUMED_APPROVAL_GATE_KINDS`) with no TTL at all, because a TTL here is a guess
  about how long a human might take before retrying a closeout and this leaf exists because such a
  guess (30 seconds, then zero) was already wrong twice.

NO TTL IS NOT UNBOUNDED, AND THE OUTER BOUND IS SOMEONE ELSE'S. These records do not live forever;
they live exactly as long as the lifecycle they belong to. ``gates.jsonl`` sits inside the
lifecycle's own directory, and ``observer/event_retention.py::prune_expired_lifecycle_event_logs``
reclaims that directory WHOLESALE (``shutil.rmtree``) once the lifecycle is dormant past its
inactivity TTL -- ``FLEETING_INACTIVE_TTL_SECONDS`` or ``ENCLOSURE_INACTIVE_GRACE_SECONDS``
depending on the kind. So the retention rule here is "keep for the life of the lifecycle", not
"keep forever", and the thing that finally removes an authority record is the disappearance of the
subject it was an authority about -- which is the only expiry that cannot be wrong.

ONE EXEMPTION, and it is the case worth knowing: a lifecycle named by
``series_retained_lifecycle_ids`` -- part of a LIVE MASTER SERIES -- is exempt from inactivity
pruning regardless of age (``prune_expired_lifecycle_event_logs``'s ``protected_lifecycle_ids``).
For those the bound is the series, not the TTL. That is still bounded, and the magnitude is small
(one record per approval a human granted and a tool consumed, so a handful per leaf), but a
long-running master holds them all until it finishes. If that ever stops being small, the fix is a
cap on retained authority records per lifecycle, NOT a TTL.

BOUNDARY, because this reclamation path and the durable-store contract meet here badly:
``shutil.rmtree`` removes the whole directory, which includes ``gates.jsonl.lock``. A process
holding that lock has its lockfile unlinked underneath it, and the next ``exclusive_access``
creates a lockfile at a DIFFERENT INODE -- so two processes stop excluding each other. The window
is narrow (dormant, unprotected lifecycles only) and no known caller hits it, but the contract does
not cover it and a future reclaimer that runs against live lifecycles would."""


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
        gate.id for gate in latest.values() if _keep_gate(gate, now=now, ttl_seconds=ttl_seconds)
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
    """Whether one folded gate snapshot survives reclamation.

    The authority branch comes FIRST and answers with no reference to the clock: an ``applied``
    snapshot of a kind a mutating tool consumes is the record that stops one human approval being
    spent twice, so no age makes it garbage. Everything else keeps the retention it always had --
    withdrawn and superseded gates go immediately, live ones age out on the TTL.
    """
    if gate.state == "applied":
        return gate.kind in CONSUMED_APPROVAL_GATE_KINDS
    if gate.state in PRUNE_IMMEDIATE_GATE_STATES:
        return False
    age = age_seconds(gate.ts, now)
    return age is None or age <= ttl_seconds


def _keep_inbox_entry(entry: OperatorInboxEntry, *, now: datetime, ttl_seconds: float) -> bool:
    """Ruled invariant (developer, 2026-07-09): system health outranks every row, pending
    included. A pending/unacked row is kept only within :data:`INBOX_PENDING_TTL_SECONDS`;
    consumed rows keep the shorter audit window; ladder-resolved rows drop immediately. This
    supersedes HFX2-L1 R1's immortal-pending rule -- the durable record is the artifact on disk,
    never the inbox row, so purging an old nudge loses nothing the agent-notifier cannot recreate
    (as one fresh row) while its condition persists."""
    if entry.state == "ladder-resolved":
        return False
    age = age_seconds(entry.createdAt, now)
    if entry.state == "pending":
        return age is None or age <= INBOX_PENDING_TTL_SECONDS
    return age is None or age <= ttl_seconds
