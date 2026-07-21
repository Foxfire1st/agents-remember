"""Read-only effective policy projection (260718-CHATS-L3, R5).

Policy is read-only evidence: the projection separates the AR-side policy
posture (the local single-operator authorization ruling and the canonical
project scope) from the effective harness mode, each with origin/evidence,
observed time, freshness, runtime/helper versions, and unavailable/unverified
reasons. Claude's ``permissionMode`` crosses the live snapshot and is reported
with its control-contract capability reason (unverified until the control seam is
probed — never a version gate, per the 2026-07-21 ruling / 260718-CHATS-L5F R4); codex approval
/sandbox values are adapter-private at thread/turn start and never cross, so
they stay honestly unverified; pi has no built-in permission popup surface.
There is no ``PATCH``, ``policyWrite``, preview, or mutation surface anywhere
in this leaf — capability gating alone cannot authorize one.
"""

from __future__ import annotations

from agents_remember.serving.conversation.control.capabilities import (
    control_capabilities_for,
)
from agents_remember.serving.conversation.control.service import (
    ConversationControlService,
)
from agents_remember.serving.conversation.models import (
    ActiveConversationRef,
    AuthorizationBinding,
    CapabilityEvidence,
    FeatureCapability,
    WireModel,
)
from agents_remember.serving.harness_control_models import AdapterSnapshot

_POLICY_ORIGIN = "agents-remember serving composition"


class PolicyPart(WireModel):
    """One read-only policy fact with its capability state and evidence."""

    state: str
    reason: str
    value: str | None = None
    origin: str
    evidence: CapabilityEvidence | None = None


class ConversationPolicyProjection(WireModel):
    """The read-only effective policy answer for one exact active identity."""

    revision: int
    identity: ActiveConversationRef
    observed_at: str
    freshness: str
    repo_policy: PolicyPart
    harness_mode: PolicyPart
    policy_read: FeatureCapability


async def conversation_policy(
    service: ConversationControlService,
    authorization: AuthorizationBinding,
    ar_session_id: str,
    *,
    expected_bridge_epoch: str,
) -> ConversationPolicyProjection:
    """Project the exact session's read-only policy evidence."""

    del authorization  # resolution is the route's proof; the projection needs no claim
    entry = service.resolve_entry(ar_session_id)
    epoch = await service.verify_epoch(entry, expected_bridge_epoch)
    snapshot = await service.live_snapshot(entry)
    identity = service.build_identity(entry, bridge_epoch=epoch, snapshot=snapshot)
    capability = control_capabilities_for(identity.harness_id, snapshot).policy_read
    channel = service.channel(ar_session_id, epoch)
    observed_at = service.clock()
    harness_mode = _harness_mode(identity.harness_id, snapshot, capability)
    repo_policy = PolicyPart(
        state="supported",
        reason="the landed L0 single-operator local authorization ruling and canonical scope",
        value=(
            f"local single-operator loopback authority; canonical project scope "
            f"{identity.project_scope}"
        ),
        origin=_POLICY_ORIGIN,
        evidence=None,
    )
    semantic_key = (
        f"{repo_policy.state}|{repo_policy.value}|{harness_mode.state}|{harness_mode.value}"
        f"|{capability.state}"
    )
    if semantic_key != channel.policy_key:
        channel.policy_key = semantic_key
        channel.policy_revision += 1
    return ConversationPolicyProjection(
        revision=max(1, channel.policy_revision),
        identity=identity,
        observed_at=observed_at,
        freshness=_freshness(snapshot),
        repo_policy=repo_policy,
        harness_mode=harness_mode,
        policy_read=capability,
    )


def _harness_mode(
    harness_id: str, snapshot: AdapterSnapshot, capability: FeatureCapability
) -> PolicyPart:
    if harness_id == "claude":
        mode = snapshot.raw.get("permissionMode")
        return PolicyPart(
            state=capability.state,
            reason=capability.reason,
            value=mode if isinstance(mode, str) and mode else None,
            origin="claude system/init permissionMode",
            evidence=capability.evidence,
        )
    if harness_id == "codex":
        return PolicyPart(
            state=capability.state,
            reason=capability.reason,
            value=None,
            origin="codex thread/turn start inputs (adapter-private)",
            evidence=capability.evidence,
        )
    return PolicyPart(
        state=capability.state,
        reason=capability.reason,
        value=None,
        origin="pi session (no built-in permission popup surface)",
        evidence=capability.evidence,
    )


def _freshness(snapshot: AdapterSnapshot) -> str:
    return "fresh" if snapshot.control == "ready" else "unknown"


__all__ = ["ConversationPolicyProjection", "PolicyPart", "conversation_policy"]
