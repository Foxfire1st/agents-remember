"""Active-page (L1) capability view tests.

The L1 view keeps its conservative pre-L2E posture for every feature EXCEPT
``controls.interrupt``: that one verdict is bridged from the L3 control gate
(``control.capabilities.interrupt_capability_for``), which carries the landed
installed-runtime fixture evidence (claude/codex/pi interrupt = supported).
These tests pin the bridge, the untouched conservative features, and the
single-source property — a changed gate verdict must show through the L1 view
with no second copy to edit.
"""

from __future__ import annotations

import pytest
from agents_remember.serving.conversation.active.capabilities import capabilities_for
from agents_remember.serving.conversation.control import (
    capabilities as control_capabilities_module,
)
from agents_remember.serving.conversation.control.capabilities import (
    control_capabilities_for,
    interrupt_capability_for,
)
from agents_remember.serving.conversation.models import (
    CapabilityEvidence,
    ControlCapabilities,
    FeatureCapability,
    HarnessId,
)
from agents_remember.serving.harness_control_models import AdapterSnapshot, ControlIdentity

HARNESSES: tuple[HarnessId, ...] = ("codex", "claude", "pi")

_INTERRUPT_FIXTURES: dict[HarnessId, str] = {
    "codex": "codex-0.144.5-installed-20260718",
    "claude": "claude-2.1.217-installed-20260722",
    "pi": "pi-0.80.7-installed-20260718",
}


def _snapshot() -> AdapterSnapshot:
    # Capability builders never gate on the observed snapshot (the contract is the only gate);
    # a minimal structural snapshot satisfies the signature.
    return AdapterSnapshot(
        identity=ControlIdentity(
            ar_session_id="ar-caps-1",
            tmux_name="caps-1",
            created_at="2026-07-22T00:00:00Z",
        ),
        control="ready",
        activity="idle",
        acceptance="immediate",
    )


@pytest.mark.parametrize("harness", HARNESSES)
def test_active_interrupt_bridges_the_control_gate_verdict(harness: HarnessId) -> None:
    interrupt = capabilities_for(harness, _snapshot()).controls.interrupt

    assert interrupt.state == "supported"
    assert interrupt.evidence_tier == "runtime-fixture"
    assert interrupt.evidence is not None
    assert interrupt.evidence.fixture_id == _INTERRUPT_FIXTURES[harness]
    # The bridge carries the gate verdict verbatim — no L1-local rewrite of reason or evidence.
    assert interrupt == interrupt_capability_for(harness, _snapshot())
    assert interrupt == control_capabilities_for(harness, _snapshot()).interrupt


def test_codex_non_interrupt_features_keep_the_conservative_posture() -> None:
    capabilities = capabilities_for("codex", _snapshot())

    assert capabilities.controls.steer.state == "unavailable"
    assert capabilities.controls.steer.reason == "not an ordinary submit action"
    assert capabilities.controls.follow_up.state == "unavailable"
    assert capabilities.controls.attachments.image.state == "unavailable"
    assert capabilities.controls.policy_read.state == "unverified"
    assert capabilities.live.thinking.state == "unverified"
    assert capabilities.live.thinking.evidence_tier == "adapter"
    assert capabilities.live.text.state == "supported"
    assert capabilities.history.list.state == "unavailable"
    assert capabilities.history.resume.state == "unavailable"
    assert capabilities.telemetry.cost.state == "unavailable"


def test_claude_non_interrupt_features_keep_the_conservative_posture() -> None:
    capabilities = capabilities_for("claude", _snapshot())

    assert capabilities.controls.steer.state == "unavailable"
    assert capabilities.controls.steer.reason == "not an ordinary submit action"
    assert capabilities.controls.follow_up.state == "unavailable"
    assert capabilities.controls.attachments.image.state == "unavailable"
    assert capabilities.controls.policy_read.state == "unverified"
    assert capabilities.live.text.state == "unverified"
    assert capabilities.live.text.evidence_tier == "adapter"
    assert capabilities.history.read.state == "unavailable"
    assert capabilities.telemetry.rate_limit.state == "unverified"


def test_pi_non_interrupt_features_keep_the_conservative_posture() -> None:
    capabilities = capabilities_for("pi", _snapshot())

    assert capabilities.controls.steer.state == "unavailable"
    assert capabilities.controls.steer.reason == "not an ordinary submit action"
    assert capabilities.controls.follow_up.state == "unavailable"
    assert capabilities.controls.attachments.image.state == "unavailable"
    assert capabilities.controls.policy_read.state == "unavailable"
    assert capabilities.live.thinking.state == "unverified"
    assert capabilities.live.thinking.evidence_tier == "adapter"
    assert capabilities.history.read.state == "supported"
    assert capabilities.telemetry.rate_limit.state == "unavailable"


@pytest.mark.parametrize("harness", HARNESSES)
def test_active_interrupt_follows_a_changed_control_gate_verdict(
    harness: HarnessId, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Single-source proof: demote the verdict at the L3 source and the L1 view must follow —
    # there is no second hardcoded copy of the interrupt state in the active leaf.
    demoted_interrupt = FeatureCapability(
        state="unverified",
        reason="control probe regressed; verdict withdrawn pending a new fixture",
        evidence_tier="adapter",
        evidence=CapabilityEvidence(runtime_version="0.0.0", observed_at="2026-07-22T00:00:00Z"),
    )
    original = control_capabilities_for(harness, _snapshot())
    demoted = ControlCapabilities(
        interrupt=demoted_interrupt,
        steer=original.steer,
        follow_up=original.follow_up,
        attachments=original.attachments,
        policy_read=original.policy_read,
    )
    monkeypatch.setattr(
        control_capabilities_module,
        "control_capabilities_for",
        lambda harness_id, snapshot: demoted,
    )

    capabilities = capabilities_for(harness, _snapshot())

    assert capabilities.controls.interrupt == demoted_interrupt
    # Only the interrupt bridges: the L1-local steer verdict is untouched by the gate patch.
    assert capabilities.controls.steer.state == "unavailable"
    assert capabilities.controls.steer.reason == "not an ordinary submit action"
