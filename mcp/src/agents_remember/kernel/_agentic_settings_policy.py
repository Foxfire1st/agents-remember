"""``orchestration.gateDelegation`` parser: policy + seam-verdict flag."""

from __future__ import annotations

from agents_remember.kernel._agentic_settings_core import (
    DEFAULT_GATE_POLICY,
    KNOWN_GATE_DELEGATION_FIELDS,
    KNOWN_GATE_POLICY_KIND_FIELDS,
    AgenticSettingsError,
    _refuse_unknown,
    _require_object,
    _require_string,
)
from agents_remember.kernel.primitives.gate_policy import (
    GatePolicy,
    GatePolicyRule,
    apply_seam_verdict_requirement,
    coerce_decision_role,
    make_gate_policy,
    named_gate_policy,
)
from agents_remember.kernel.primitives.gate_vocab import (
    GateKind,
    coerce_gate_kind,
)


def parse_gate_delegation(
    raw: object, *, source: str
) -> tuple[GatePolicy, bool]:  # pragma: no cover
    """Parse ``orchestration.gateDelegation`` into ``(policy, requireReviewerVerdictAtSeams)``.

    Shared by the agentic loader (the key's home) and the runtime-config
    authority-file loader's one-cycle legacy authority-file fallback. ``None`` means the all-human
    default. Errors name ``source`` (the offending file).
    """
    if raw is None:
        return DEFAULT_GATE_POLICY, False
    delegation = _require_object(raw, "orchestration.gateDelegation", source)
    _refuse_unknown(
        delegation,
        KNOWN_GATE_DELEGATION_FIELDS,
        "orchestration.gateDelegation",
        source,
    )
    policy_name = delegation.get("policy", "all-human")
    policy_name = _require_string(policy_name, "orchestration.gateDelegation.policy", source)
    try:
        policy = named_gate_policy(policy_name)
    except ValueError as error:
        raise AgenticSettingsError(f"{error}; offending file: {source}") from error
    require_verdict_at_seams = delegation.get("requireReviewerVerdictAtSeams", False)
    if not isinstance(require_verdict_at_seams, bool):
        raise AgenticSettingsError(
            "orchestration.gateDelegation.requireReviewerVerdictAtSeams must be a "
            f"boolean: {source}"
        )
    kinds = _require_object(
        delegation.get("kinds", {}), "orchestration.gateDelegation.kinds", source
    )
    rules = {rule.kind: rule for rule in policy.rules}
    for raw_kind, raw_rule in kinds.items():
        if not isinstance(raw_kind, str) or not raw_kind:
            raise AgenticSettingsError(
                f"gate policy kind names must be non-empty strings: {source}"
            )
        try:
            kind = coerce_gate_kind(raw_kind)
            rules[kind] = _parse_gate_policy_rule(
                kind, raw_rule, prior=rules.get(kind), source=source
            )
        except AgenticSettingsError:
            raise
        except ValueError as error:
            raise AgenticSettingsError(f"{error}; offending file: {source}") from error
    try:
        policy = make_gate_policy(list(rules.values()))
    except ValueError as error:
        raise AgenticSettingsError(f"{error}; offending file: {source}") from error
    if require_verdict_at_seams:
        policy = apply_seam_verdict_requirement(policy)
    return policy, require_verdict_at_seams


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/kernel/_agentic_settings_policy.py:80).
def _parse_gate_policy_rule(  # pragma: no cover
    kind: GateKind,
    raw_rule: object,
    *,
    prior: GatePolicyRule | None,
    source: str,
) -> GatePolicyRule:
    if isinstance(raw_rule, str):
        return GatePolicyRule(kind=kind, delegated_role=coerce_decision_role(raw_rule))
    rule = _require_object(raw_rule, f"orchestration.gateDelegation.kinds.{kind}", source)
    _refuse_unknown(
        rule,
        KNOWN_GATE_POLICY_KIND_FIELDS,
        f"orchestration.gateDelegation.kinds.{kind}",
        source,
    )
    role_raw = rule.get("role")
    delegated_role = prior.delegated_role if prior is not None else None
    if role_raw is not None:
        role_value = _require_string(
            role_raw, f"orchestration.gateDelegation.kinds.{kind}.role", source
        )
        delegated_role = coerce_decision_role(role_value)
    require_verdict = rule.get(
        "requireReviewerVerdict",
        prior.require_reviewer_verdict if prior is not None else False,
    )
    if not isinstance(require_verdict, bool):
        raise AgenticSettingsError(
            f"orchestration.gateDelegation.kinds.{kind}.requireReviewerVerdict "
            f"must be a boolean: {source}"
        )
    return GatePolicyRule(
        kind=kind,
        delegated_role=delegated_role,
        require_reviewer_verdict=require_verdict,
    )
