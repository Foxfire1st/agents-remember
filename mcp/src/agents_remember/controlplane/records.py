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
from typing import Any, Literal, cast, get_args

from pydantic import BaseModel, ConfigDict, Field

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


class GateRecord(BaseModel):
    """One ``ar-gate-record/v1`` snapshot.

    Append a fresh snapshot per state change (same ``id``, new ``ts``); fold the
    log by ``id`` (last-wins) for the current state. camelCase wire fields match
    the package's response-model convention; ``schema_version`` carries the lone
    alias because ``schema`` is an awkward attribute name -- always dump with
    ``model_dump_json(by_alias=True, exclude_none=True)`` so it renders as
    ``schema``.
    """

    model_config = ConfigDict(extra="forbid")

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


def create_gate(
    *,
    kind: GateKind,
    lifecycle_id: str | None,
    gate_id: str,
    now: str,
    enclosure: str | None = None,
    repo_id: str | None = None,
    packet: dict[str, Any] | None = None,
    required_decision: list[str] | None = None,
    evidence_refs: Sequence[GateEvidenceRef | dict[str, Any]] | None = None,
) -> GateRecord:
    """A freshly opened gate. Pure: the caller mints ``gate_id`` and ``now``."""
    return GateRecord(
        id=gate_id,
        ts=now,
        kind=kind,
        state="open",
        lifecycleId=lifecycle_id,
        enclosure=enclosure,
        repoId=repo_id,
        packet=packet or {},
        requiredDecision=required_decision,
        evidenceRefs=_coerce_evidence_refs(evidence_refs),
    )


def decide_gate(
    gate: GateRecord,
    *,
    decision: str,
    by: str,
    via: DecidedVia,
    note: str | None,
    now: str,
    deciding_role: str | None = None,
    evidence_refs: Sequence[GateEvidenceRef | dict[str, Any]] | None = None,
) -> GateRecord:
    """A new snapshot carrying the decision (same ``id``, new ``ts``). Pure.

    ``decision`` is one of :data:`DECISION_STATES`; an unknown verb is a
    ``KeyError`` here (the tool boundary validates first for a clean message).
    """
    state = DECISION_STATES[decision]
    attached_evidence = [*gate.evidenceRefs, *_coerce_evidence_refs(evidence_refs)]
    return gate.model_copy(
        update={
            "ts": now,
            "state": state,
            "decidedBy": by,
            "decidedVia": via,
            "decidingRole": deciding_role,
            "decisionNote": note,
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


def _coerce_evidence_refs(
    evidence_refs: Sequence[GateEvidenceRef | dict[str, Any]] | None,
) -> list[GateEvidenceRef]:
    if evidence_refs is None:
        return []
    return [
        ref if isinstance(ref, GateEvidenceRef) else GateEvidenceRef.model_validate(ref)
        for ref in evidence_refs
    ]
