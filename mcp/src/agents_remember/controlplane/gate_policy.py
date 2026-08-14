"""Gate delegation attribution helpers over durable gate records.

The policy rules live in ``kernel.gate_policy``; this module owns the functions
that read an actual ``GateRecord`` and decide whether a recorded decision
satisfies the configured policy.
"""

from __future__ import annotations

from agents_remember.controlplane.records import GateRecord
from agents_remember.kernel.primitives.gate_policy import (
    HUMAN_DECIDER,
    HUMAN_ROLE,
    ORCHESTRATION_DECIDED_VIA,
    REVIEWER_VERDICT_EVIDENCE_KIND,
    DecisionRole,
    GatePolicy,
    coerce_decision_role,
)


def has_reviewer_verdict_evidence(gate: GateRecord) -> bool:

    return any(ref.kind == REVIEWER_VERDICT_EVIDENCE_KIND for ref in gate.evidenceRefs)


def decision_role_for_gate(gate: GateRecord) -> DecisionRole | None:
    if gate.decidedBy == HUMAN_DECIDER:
        return HUMAN_ROLE
    if gate.decidedVia == ORCHESTRATION_DECIDED_VIA and gate.decidingRole is not None:
        return coerce_decision_role(gate.decidingRole)
    return None


def _decision_attribution_failure_reason(gate: GateRecord) -> str | None:
    """Return why the gate's recorded decision is not a usable orchestration attribution.

    Identity only -- who decided, and through which channel -- before any policy is
    consulted. A decision that fails here names no role the policy could be asked about.
    """
    if gate.decidedVia != ORCHESTRATION_DECIDED_VIA:
        return f"gate {gate.id} was not decided through orchestration"
    if not gate.decidedBy:
        return f"gate {gate.id} has no deciding lifecycle identity"
    if gate.lifecycleId and gate.decidedBy == gate.lifecycleId:
        return f"gate {gate.id} cannot be decided by its owning lifecycle {gate.lifecycleId!r}"
    if gate.decidingRole is None:
        return f"gate {gate.id} has no deciding role"
    return None


def delegated_decision_failure_reason(gate: GateRecord, policy: GatePolicy) -> str | None:
    """Return a refusal reason when an orchestration decision is not policy-valid."""
    attribution_failure = _decision_attribution_failure_reason(gate)
    if attribution_failure is not None:
        return attribution_failure
    assert gate.decidingRole is not None  # the attribution check above proves it
    role = coerce_decision_role(gate.decidingRole)
    rule = policy.rule_for(gate.kind)
    if not rule.allows_role(role) or role == HUMAN_ROLE:
        return f"{gate.kind} is not delegated to role {role!r}"
    if rule.require_reviewer_verdict and not has_reviewer_verdict_evidence(gate):
        return f"{gate.kind} requires reviewer verdict evidence before delegated decisions"
    return None


def approval_failure_reason(gate: GateRecord, policy: GatePolicy) -> str | None:
    """Return why an approved gate does not satisfy the configured policy."""
    try:
        role = decision_role_for_gate(gate)
    except ValueError as error:
        return str(error)
    if role == HUMAN_ROLE:
        return None
    if role is None:
        return (
            f"{gate.kind} gate {gate.id} was approved by {gate.decidedBy!r}, "
            "not the developer or a configured orchestration role"
        )
    try:
        return delegated_decision_failure_reason(gate, policy)
    except ValueError as error:
        return str(error)
