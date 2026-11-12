"""Retention policy for gate records."""

from __future__ import annotations

from datetime import datetime

from agents_remember.controlplane.records import (
    GateRecord,
)
from agents_remember.controlplane.stamps import age_seconds
from agents_remember.models.structural.gates import (
    GateKind,
)

INTERACTION_RECORD_TTL_SECONDS = 24 * 60 * 60.0


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
(the gate log was reclaimed after every one, and an ``agent-question`` being answered was enough)
erased the marker within milliseconds. It was already
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
``agent-question``, ``alarm-ack``, ``provider-retry`` and ``plan-approval`` are never passed to
``evaluate_gate``, so their ``applied`` record refuses nothing -- it is pure history. The retained set is
bounded by approvals a human actually granted and a mutating tool actually consumed: a handful per
lifecycle, in a log that lives beside that lifecycle's events."""

PRUNE_IMMEDIATE_GATE_STATES = frozenset({"cancelled", "expired"})
"""Terminal states a compaction may drop at any age, and why ``applied`` is NOT one of them.

Dropping a gate's last snapshot returns ``evaluate_gate`` to the permitted-gateless verdict. For
these two that is exactly right, and for ``applied`` it is the replay:

* ``cancelled`` -- the gate was WITHDRAWN. It never carried an approval, and cancelling it says
  precisely "this gate no longer governs", so falling back to the chat/commit approval channel is
  the intended outcome.
* ``expired`` -- the gate was SUPERSEDED. ``expire_gate`` is written only when a newer gate opens
  on the same lifecycle, so the gate that replaced it is in the same log with a newer ``ts`` and
  governs the fold. Dropping the expired snapshot drops history, never authority.
* ``applied`` -- the gate was GRANTED AND SPENT. There is nothing to fall back to: the approval
  exists, it was consumed, and the only record saying so is this one. It is retained for the kinds
  above (:data:`CONSUMED_APPROVAL_GATE_KINDS`) with no TTL at all, because a TTL here is a guess
  about how long a human might take before retrying a closeout and this leaf exists because such a
  guess (30 seconds, then zero) was already wrong twice.

These authority records have no automatic age-based reclamation. Only the user's
explicit ``removed-stores`` command removes stored gate logs. Lock-only lifecycle
directories are separately eligible only when a complete native contract census
proves every contract naming the subject terminal. A directory carrying approval
history is never treated as lock-only residue, and the file-lock primitive is unchanged."""


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
