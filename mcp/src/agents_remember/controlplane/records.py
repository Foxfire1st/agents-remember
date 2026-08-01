"""Durable gate records: ``ar-gate-record/v1``.

A gate is an append-only, attributed record of a decision point on a lifecycle
(a closeout to approve, a question an operator must answer, an alarm to
acknowledge). Like the observer event envelope, it is a *persisted, versioned
contract* the dashboard and the mutating tools read back from disk -- so it is a
Pydantic model with Literal-typed state and camelCase wire fields. Each state
change appends a fresh snapshot (same ``id``, new ``ts``); readers fold the log
by ``id``, last-wins (:meth:`GateStore.current`). History stays on disk and the
current gate set is a pure projection of the log.

This is the *record*, not enforcement: the mutating MCP tools obeying gate state
is a later slice. Here we only own the honest, history-preserving fact.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal, cast, get_args

from pydantic import BaseModel, ConfigDict, Field

from agents_remember.controlplane.durable_store import DurableRecord

GATE_RECORD_SCHEMA = "ar-gate-record/v1"

# What needs the human/operator. Extensible: a new gate kind is one literal.
GateKind = Literal[
    "plan-approval",
    "worktree-intent",
    # `closeout-approval` IS the commit gate: closeout is the single commit-of-record for code + memory +
    # ledger (there is no separate `commit-approval`; singular commits route through closeout).
    "closeout-approval",
    "push-approval",
    "integration-approval",
    # `master-handover-approval` is the master-exit seam gate: the manager raises it with the
    # reviewer verdict attached as evidence; the orchestrator decides it on the happy path
    # (delegable, never human-pinned) — human review concentrates at the super gate.
    "master-handover-approval",
    "cleanup-approval",
    "agent-question",
    "provider-retry",
    "alarm-ack",
]
# The gate's lifecycle. ``open`` awaits a decision; ``applied`` is set once a
# mutating tool consumes an approval (a later slice writes it -- modeled here so
# the contract is stable). The rest are terminal decisions.
GateState = Literal[
    "open",
    "approved",
    "rejected",
    "revision-requested",
    "applied",
    "cancelled",
    "expired",
]
# Through what surface the decision arrived -- kept separate from the actor
# (``decidedBy``); "who" and "through what" never share a field, the same rule
# the observer event envelope follows.
DecidedVia = Literal["chat", "dashboard", "cli", "orchestration"]
GateEvidenceKind = Literal["reviewer-verdict"]

GATE_KINDS: tuple[GateKind, ...] = get_args(GateKind)


def coerce_gate_kind(raw: str) -> GateKind:
    """Validate a raw kind string against the :data:`GateKind` literals."""
    if raw not in GATE_KINDS:
        raise ValueError(f"unknown gate kind {raw!r}; expected one of {list(GATE_KINDS)}")
    return cast(GateKind, raw)


class GateEvidenceRef(BaseModel):
    """A durable external artifact reference attached to a gate snapshot."""

    model_config = ConfigDict(extra="forbid")

    kind: GateEvidenceKind
    ref: str
    verdict: str | None = None


class GateRecord(DurableRecord):
    """One ``ar-gate-record/v1`` snapshot.

    Append a fresh snapshot per state change (same ``id``, new ``ts``); fold the
    log by ``id`` (last-wins) for the current state. camelCase wire fields match
    the package's response-model convention; ``schema_version`` carries the lone
    alias because ``schema`` is an awkward attribute name -- always dump with
    ``model_dump_json(by_alias=True, exclude_none=True)`` so it renders as
    ``schema``.

    Two version fields, and they answer different questions: ``schema`` names the record
    vocabulary (what these fields mean), while the inherited ``schemaVersion`` versions the
    durable-store contract this log is written under (how the file behaves -- ownership,
    serialization, torn-line policy). A reader rejects an unknown ``schemaVersion`` major and
    accepts an unknown minor; see ``controlplane/durable_store.py``.
    """

    schema_version: str = Field(default=GATE_RECORD_SCHEMA, alias="schema")
    id: str  # ULID, stable across the gate's life (minted once, reused per snapshot)
    ts: str  # ISO 8601 with offset (UTC) -- the time of THIS snapshot
    kind: GateKind
    state: GateState
    lifecycleId: str | None = None
    enclosure: str | None = None
    repoId: str | None = None
    packet: dict[str, Any] = Field(default_factory=dict)  # the review payload
    requiredDecision: list[str] | None = None
    decidedBy: str | None = None  # actor: developer | model | system
    decidedVia: DecidedVia | None = None  # through what surface
    decidingRole: str | None = None
    decisionNote: str | None = None
    decidedAt: str | None = None
    evidenceRefs: list[GateEvidenceRef] = Field(default_factory=list)


# Decision verbs accepted at the tool boundary, mapped to the resulting state.
DECISION_STATES: dict[str, GateState] = {
    "approve": "approved",
    "reject": "rejected",
    "request-revision": "revision-requested",
    "cancel": "cancelled",
}


@dataclass(frozen=True)
class GateAnchor:
    """What a gate is raised against: the lifecycle that opened it, the enclosure it guards,
    and the repository that enclosure changes. Every reader that matches a gate to work in
    flight matches on this triple, so it is one anchor rather than three loose ids."""

    lifecycle_id: str | None = None
    enclosure: str | None = None
    repo_id: str | None = None


@dataclass(frozen=True)
class GateRequest:
    """What the decider is handed: the packet to read, the decisions the gate will accept, and
    the evidence attached at open time."""

    packet: dict[str, Any] | None = None
    required_decision: list[str] | None = None
    evidence_refs: Sequence[GateEvidenceRef | dict[str, Any]] | None = None


@dataclass(frozen=True)
class GateVerdict:
    """One decider's verdict on a gate: the verb, who decided, through which surface, in which
    role, and the note they left.

    The closeout policy never reads these apart -- a delegated approval is the ``orchestration``
    channel AND a ``manager`` role AND an actor that is not the owning lifecycle -- so a verdict
    assembled field by field is a verdict that can be assembled wrongly.
    """

    decision: str
    via: DecidedVia
    by: str | None = None
    note: str | None = None
    deciding_role: str | None = None


def create_gate(
    kind: GateKind,
    *,
    gate_id: str,
    now: str,
    anchor: GateAnchor | None = None,
    request: GateRequest | None = None,
) -> GateRecord:
    """A freshly opened gate. Pure: the caller mints ``gate_id`` and ``now``."""
    anchor = anchor or GateAnchor()
    request = request or GateRequest()
    return GateRecord(
        id=gate_id,
        ts=now,
        kind=kind,
        state="open",
        lifecycleId=anchor.lifecycle_id,
        enclosure=anchor.enclosure,
        repoId=anchor.repo_id,
        packet=request.packet or {},
        requiredDecision=request.required_decision,
        evidenceRefs=_coerce_evidence_refs(request.evidence_refs),
    )


def decide_gate(
    gate: GateRecord,
    verdict: GateVerdict,
    *,
    now: str,
    evidence_refs: Sequence[GateEvidenceRef | dict[str, Any]] | None = None,
) -> GateRecord:
    """A new snapshot carrying the decision (same ``id``, new ``ts``). Pure.

    ``verdict.decision`` is one of :data:`DECISION_STATES`; an unknown verb is a
    ``KeyError`` here (the tool boundary validates first for a clean message).
    """
    state = DECISION_STATES[verdict.decision]
    attached_evidence = [*gate.evidenceRefs, *_coerce_evidence_refs(evidence_refs)]
    return gate.model_copy(
        update={
            "ts": now,
            "state": state,
            "decidedBy": verdict.by,
            "decidedVia": verdict.via,
            "decidingRole": verdict.deciding_role,
            "decisionNote": verdict.note,
            "decidedAt": now,
            "evidenceRefs": attached_evidence,
        }
    )


def expire_gate(gate: GateRecord, *, now: str) -> GateRecord:
    """A new snapshot expiring an open gate that was replaced by a newer gate."""
    return gate.model_copy(update={"ts": now, "state": "expired"})


def apply_gate(gate: GateRecord, *, now: str) -> GateRecord:
    """A new snapshot marking an approved gate consumed by its mutating tool. Pure.

    The ``applied`` transition this module's docstring anticipates: a mutating
    tool (``worktree_closeout_apply``) records that it acted on the approval, so a
    single approval cannot be replayed by a second closeout. Decision attribution
    (``decidedBy`` / ``decidedVia`` / ``decidedAt`` / ``decisionNote``) carries
    forward unchanged -- only ``state`` and ``ts`` advance.
    """
    return gate.model_copy(update={"ts": now, "state": "applied"})


def reopen_gate(gate: GateRecord, *, now: str) -> GateRecord:
    """A new snapshot returning a decided gate to open after its decision could not be applied.

    The adapter-interaction channel (``hosted_interactions``) consumes a decision by returning
    its note to the pending vendor interaction; when that respond fails, the decision was never
    consumable, so the gate goes back to open -- decision attribution cleared -- and awaits a
    fresh decision instead of staying decided-not-applied forever. The decided record stays in
    the append-only log, so the failed attempt remains auditable history.
    """
    return gate.model_copy(
        update={
            "ts": now,
            "state": "open",
            "decidedBy": None,
            "decidedVia": None,
            "decidingRole": None,
            "decisionNote": None,
            "decidedAt": None,
        }
    )


def _coerce_evidence_refs(
    evidence_refs: Sequence[GateEvidenceRef | dict[str, Any]] | None,
) -> list[GateEvidenceRef]:
    if evidence_refs is None:
        return []
    return [
        ref if isinstance(ref, GateEvidenceRef) else GateEvidenceRef.model_validate(ref)
        for ref in evidence_refs
    ]
