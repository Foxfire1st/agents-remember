"""Gate delegation policy for durable lifecycle gates.

The default policy is intentionally today's behavior: every gate is human
decided. Orchestration delegation adds one configured role for specific gate
kinds; it never removes the human path and it never changes human-pinned kinds.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, cast, get_args

from agents_remember.controlplane.records import GateKind, GateRecord

DecisionRole = Literal["human", "manager", "orchestrator"]

DECISION_ROLES: tuple[DecisionRole, ...] = get_args(DecisionRole)
HUMAN_ROLE: DecisionRole = "human"
HUMAN_DECIDER = "developer"
ORCHESTRATION_DECIDED_VIA = "orchestration"
REVIEWER_VERDICT_EVIDENCE_KIND = "reviewer-verdict"

HUMAN_PINNED_GATE_KINDS: frozenset[GateKind] = frozenset(
    {"integration-approval", "push-approval", "cleanup-approval"}
)
DELEGABLE_GATE_KINDS: frozenset[GateKind] = frozenset(
    {"plan-approval", "closeout-approval", "master-handover-approval"}
)
# The seam kinds `requireReviewerVerdictAtSeams` binds: delegated decisions on these kinds
# demand reviewer-verdict evidence. (The super-exit seam stays human via the pinned
# integration/push kinds, where the human sees the attached verdict directly.)
SEAM_GATE_KINDS: frozenset[GateKind] = frozenset({"master-handover-approval"})


@dataclass(frozen=True)
class GatePolicyRule:
    """The configured delegation rule for one gate kind.

    ``delegated_role`` is additive: humans remain allowed, and a non-human role
    becomes allowed only for kinds that are explicitly safe to delegate.
    """

    kind: GateKind
    delegated_role: DecisionRole | None = None
    require_reviewer_verdict: bool = False

    def allows_role(self, role: DecisionRole) -> bool:
        return role in (HUMAN_ROLE, self.delegated_role)


@dataclass(frozen=True)
class GatePolicy:
    """Validated gate policy, defaulting every unlisted kind to human-only."""

    rules: tuple[GatePolicyRule, ...] = ()

    def rule_for(self, kind: GateKind) -> GatePolicyRule:
        for rule in self.rules:
            if rule.kind == kind:
                return rule
        return GatePolicyRule(kind=kind)


DEFAULT_GATE_POLICY = GatePolicy()


def coerce_decision_role(raw: str) -> DecisionRole:
    if raw not in DECISION_ROLES:
        raise ValueError(f"unknown decision role {raw!r}; expected one of {list(DECISION_ROLES)}")
    return cast(DecisionRole, raw)


def make_gate_policy(rules: list[GatePolicyRule] | tuple[GatePolicyRule, ...]) -> GatePolicy:
    """Validate and canonicalize policy rules.

    Human-pinned kinds reject non-human delegation. Other non-human delegation is
    currently limited to the leaf gate kinds this orchestration leaf implements.
    That fail-loud boundary prevents a typo or over-broad policy from silently
    weakening a gate.
    """
    by_kind: dict[GateKind, GatePolicyRule] = {}
    for rule in rules:
        normalized = rule
        if rule.delegated_role == HUMAN_ROLE:
            normalized = GatePolicyRule(
                kind=rule.kind,
                delegated_role=None,
                require_reviewer_verdict=rule.require_reviewer_verdict,
            )
        if normalized.delegated_role is not None and normalized.kind in HUMAN_PINNED_GATE_KINDS:
            raise ValueError(
                f"{normalized.kind} is human-pinned and cannot delegate to "
                f"{normalized.delegated_role}"
            )
        if normalized.delegated_role is not None and normalized.kind not in DELEGABLE_GATE_KINDS:
            allowed = ", ".join(sorted(DELEGABLE_GATE_KINDS))
            raise ValueError(
                f"{normalized.kind} cannot be delegated; allowed delegated kinds: {allowed}"
            )
        if normalized.require_reviewer_verdict and normalized.delegated_role is None:
            raise ValueError(
                f"{normalized.kind} cannot require reviewer verdicts without a delegated role"
            )
        by_kind[normalized.kind] = normalized
    return GatePolicy(tuple(by_kind[key] for key in sorted(by_kind)))


def named_gate_policy(name: str) -> GatePolicy:
    """Built-in policy names accepted by MCP authority settings."""
    if name == "all-human":
        return DEFAULT_GATE_POLICY
    if name == "manager-decides-leaf-gates":
        # Leaf gates -> the manager; the master-exit handover -> the orchestrator (developer
        # ruling 2026-07-05: happy path through the orchestrator, human review at the super gate).
        return make_gate_policy(
            [
                GatePolicyRule(kind="plan-approval", delegated_role="manager"),
                GatePolicyRule(kind="closeout-approval", delegated_role="manager"),
                GatePolicyRule(kind="master-handover-approval", delegated_role="orchestrator"),
            ]
        )
    raise ValueError(
        "unknown gate delegation policy "
        f"{name!r}; expected one of ['all-human', 'manager-decides-leaf-gates']"
    )


def apply_seam_verdict_requirement(policy: GatePolicy) -> GatePolicy:
    """Bind reviewer-verdict evidence to every DELEGATED seam-kind rule.

    The `requireReviewerVerdictAtSeams` switch: seam kinds without a delegated role stay
    human-decided (the human sees the attached verdict on the gate), so only delegated seam
    rules gain the hard requirement.
    """
    rules = []
    for rule in policy.rules:
        if rule.kind in SEAM_GATE_KINDS and rule.delegated_role is not None:
            rules.append(
                GatePolicyRule(
                    kind=rule.kind,
                    delegated_role=rule.delegated_role,
                    require_reviewer_verdict=True,
                )
            )
        else:
            rules.append(rule)
    return GatePolicy(tuple(rules))


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
