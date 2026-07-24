"""Control-domain capability evidence.

Exact-session capability states for the control surface (interrupt, typed
attachments, read-only policy) and the telemetry projection. A feature is
``supported`` only with landed installed-runtime fixture evidence captured
through the production seam (the ``control-plane/*`` rows); a native shape
whose contract has never been probed through a captured fixture is
``unverified``; a contract the harness cannot provide on this surface is
``unavailable``.

THE CONTRACT IS THE ONLY GATE:
no capability is demoted by a version-string comparison against the observed
runtime/helper. The version is informational evidence metadata only.

The active-page capability view keeps its own conservative pre-production-seam evidence
for every feature except ``controls.interrupt``: that one verdict is bridged
from this module (single source — ``interrupt_capability_for``) now that the
interrupt control seam has landed fixture evidence. Nothing here enables a
feature from documentation or changelog text.
"""

from __future__ import annotations

from agents_remember.serving.conversation.models import (
    AttachmentCapabilities,
    AttachmentCapability,
    CapabilityEvidence,
    CapabilityState,
    ControlCapabilities,
    FeatureCapability,
    HarnessId,
    TelemetryCapabilities,
)
from agents_remember.serving.harness_control_models import (
    MAX_SUBMIT_ASSET_BYTES,
    MAX_SUBMIT_ASSETS,
    SUBMIT_ASSET_MIME_TYPES,
    AdapterSnapshot,
)

_CODEX_FIXTURE = "codex-0.144.5-installed-20260718"
_CLAUDE_FIXTURE = "claude-2.1.211-installed-20260718"
_CLAUDE_INTERRUPT_FIXTURE = "claude-2.1.217-installed-20260722"
_PI_FIXTURE = "pi-0.80.7-installed-20260718"

_CODEX_RUNTIME = "0.144.5"
_CLAUDE_RUNTIME = "2.1.211"
_CLAUDE_INTERRUPT_RUNTIME = "2.1.217"
_CLAUDE_HELPER = "0.3.207"
_PI_RUNTIME = "0.80.7"
_PI_HELPER = "0.80.7"

_OBSERVED_AT = "2026-07-20T00:00:00+02:00"
_CLAUDE_INTERRUPT_OBSERVED = "2026-07-22T19:00:00+02:00"

_CLAUDE_MISMATCH = (
    "control contract not yet probed through a captured production fixture; unverified until the "
    "control-plane seam is exercised against the running harness (never a version gate)"
)

_ATTACHMENT_MIME_TYPES = tuple(sorted(SUBMIT_ASSET_MIME_TYPES))


def _fixture(
    state: CapabilityState,
    reason: str,
    runtime_version: str,
    fixture_id: str,
    *,
    helper_version: str | None = None,
    observed_at: str = _OBSERVED_AT,
) -> FeatureCapability:
    return FeatureCapability(
        state=state,
        reason=reason,
        evidence_tier="runtime-fixture",
        evidence=CapabilityEvidence(
            runtime_version=runtime_version,
            helper_version=helper_version,
            fixture_id=fixture_id,
            observed_at=observed_at,
        ),
    )


def _adapter(state: CapabilityState, reason: str, runtime_version: str) -> FeatureCapability:
    return FeatureCapability(
        state=state,
        reason=reason,
        evidence_tier="adapter",
        evidence=CapabilityEvidence(runtime_version=runtime_version, observed_at=_OBSERVED_AT),
    )


def _unavailable(reason: str) -> FeatureCapability:
    return FeatureCapability(state="unavailable", reason=reason, evidence_tier="none")


def _image_capability(
    state: CapabilityState,
    reason: str,
    evidence: FeatureCapability,
) -> AttachmentCapability:
    if state != "supported":
        return AttachmentCapability(
            state=state,
            reason=reason,
            evidence_tier=evidence.evidence_tier,
            evidence=evidence.evidence,
            max_bytes=0,
            max_count=0,
            description="required",
        )
    return AttachmentCapability(
        state="supported",
        reason=reason,
        evidence_tier="runtime-fixture",
        evidence=evidence.evidence,
        allowed_mime_types=_ATTACHMENT_MIME_TYPES,
        max_bytes=MAX_SUBMIT_ASSET_BYTES,
        max_count=MAX_SUBMIT_ASSETS,
        description="filename-type-fallback",
    )


def _no_asset_kind(kind: str) -> AttachmentCapability:
    return AttachmentCapability(
        state="unavailable",
        reason=(
            f"typed {kind} staging has no native contract on the landed asset channel; "
            "the fixture-backed MIME allow-list is image-only"
        ),
        evidence_tier="none",
        max_bytes=0,
        max_count=0,
        description="required",
    )


def _codex_controls() -> ControlCapabilities:
    interrupt = _fixture(
        "supported",
        "installed fixture observed one native turn/interrupt accepted on a live 0.144.5 thread "
        "through the production adapter-bridge-IPC seam with replay-once semantics",
        _CODEX_RUNTIME,
        _CODEX_FIXTURE,
    )
    image = _image_capability(
        "supported",
        "installed fixture observed a digest-verified staged PNG reach native acceptance as a "
        "localImage input block on a live 0.144.5 thread",
        interrupt,
    )
    return ControlCapabilities(
        interrupt=interrupt,
        steer=_unavailable("not an ordinary submit action"),
        follow_up=_unavailable("not an ordinary submit action"),
        attachments=AttachmentCapabilities(
            image=image,
            file=_no_asset_kind("file"),
            resource=_no_asset_kind("resource"),
        ),
        policy_read=_adapter(
            "unverified",
            "codex approval/sandbox values are adapter-private at thread/turn start; no public "
            "policy evidence crosses the landed surface",
            _CODEX_RUNTIME,
        ),
    )


def _claude_controls() -> ControlCapabilities:
    interrupt = _fixture(
        "supported",
        "installed probe observed one native control_request interrupt accepted on a live "
        "2.1.217 stream-json session through the production adapter-bridge-IPC seam "
        "(control_response success, still_queued []), with the correlated "
        "error_during_execution result settling interrupted under replay-once semantics",
        _CLAUDE_INTERRUPT_RUNTIME,
        _CLAUDE_INTERRUPT_FIXTURE,
        observed_at=_CLAUDE_INTERRUPT_OBSERVED,
    )
    unprobed = _adapter("unverified", _CLAUDE_MISMATCH, _CLAUDE_RUNTIME)
    return ControlCapabilities(
        interrupt=interrupt,
        steer=_unavailable("not an ordinary submit action"),
        follow_up=_unavailable("not an ordinary submit action"),
        attachments=AttachmentCapabilities(
            image=_image_capability(
                "unverified",
                f"image staging: {_CLAUDE_MISMATCH}",
                unprobed,
            ),
            file=_no_asset_kind("file"),
            resource=_no_asset_kind("resource"),
        ),
        policy_read=_adapter(
            "unverified",
            f"permissionMode crosses the snapshot but the production gate is not passed: "
            f"{_CLAUDE_MISMATCH}",
            _CLAUDE_RUNTIME,
        ),
    )


def _pi_controls() -> ControlCapabilities:
    interrupt = _fixture(
        "supported",
        "installed fixture observed one guarded RPC abort accepted on a live 0.80.7 session "
        "through the production adapter-bridge-IPC seam with replay-once semantics",
        _PI_RUNTIME,
        _PI_FIXTURE,
        helper_version=_PI_HELPER,
    )
    image = _image_capability(
        "supported",
        "installed fixture observed a digest-verified staged PNG reach native acceptance as "
        "base64 image content on a live 0.80.7 RPC prompt",
        interrupt,
    )
    return ControlCapabilities(
        interrupt=interrupt,
        steer=_unavailable("not an ordinary submit action"),
        follow_up=_unavailable("not an ordinary submit action"),
        attachments=AttachmentCapabilities(
            image=image,
            file=_no_asset_kind("file"),
            resource=_no_asset_kind("resource"),
        ),
        policy_read=_unavailable(
            "pi has no built-in permission popup surface; extension interactions remain possible"
        ),
    )


def _codex_telemetry() -> TelemetryCapabilities:
    usage = _fixture(
        "supported",
        "installed fixture observed thread/tokenUsage/updated frames cross the production "
        "evidence seam on a live 0.144.5 thread",
        _CODEX_RUNTIME,
        _CODEX_FIXTURE,
    )
    return TelemetryCapabilities(
        context=_unavailable(
            "no context-window used/limit evidence crosses the landed evidence surface"
        ),
        usage=usage,
        cost=_unavailable("codex exposes no native cost metric on this surface"),
        rate_limit=_adapter(
            "unverified",
            "account/rateLimits/updated is schema-documented but not fixture-observed",
            _CODEX_RUNTIME,
        ),
        compaction=_adapter(
            "unverified",
            "contextCompaction items are schema-documented but not fixture-observed",
            _CODEX_RUNTIME,
        ),
    )


def _claude_telemetry() -> TelemetryCapabilities:
    def _gated(feature: str) -> FeatureCapability:
        return _adapter("unverified", f"{feature}: {_CLAUDE_MISMATCH}", _CLAUDE_RUNTIME)

    return TelemetryCapabilities(
        context=_gated("context metrics"),
        usage=_gated("result usage/modelUsage frames"),
        cost=_gated("result cost frames"),
        rate_limit=_gated("rate windows"),
        compaction=_gated("compaction status"),
    )


def _pi_telemetry() -> TelemetryCapabilities:
    return TelemetryCapabilities(
        context=_adapter(
            "unverified",
            "session stats are schema-documented but not fixture-observed",
            _PI_RUNTIME,
        ),
        usage=_adapter(
            "unverified",
            "session token stats are schema-documented but not fixture-observed",
            _PI_RUNTIME,
        ),
        cost=_adapter(
            "unverified",
            "session cost stats are schema-documented but not fixture-observed",
            _PI_RUNTIME,
        ),
        rate_limit=_unavailable("pi exposes no native rate-limit window contract on this surface"),
        compaction=_adapter(
            "unverified",
            "compaction events are schema-documented but not fixture-observed",
            _PI_RUNTIME,
        ),
    )


_CONTROLS = {
    "codex": _codex_controls,
    "claude": _claude_controls,
    "pi": _pi_controls,
}

_TELEMETRY = {
    "codex": _codex_telemetry,
    "claude": _claude_telemetry,
    "pi": _pi_telemetry,
}


def control_capabilities_for(harness_id: HarnessId, snapshot: AdapterSnapshot) -> ControlCapabilities:
    """Build the exact-session control capability set.

    The contract is the only gate: the fixture-declared state is never
    demoted by a version-string comparison. The observed version is informational metadata only.
    """

    del snapshot  # the observed version is no longer a gate; it is informational evidence
    return _CONTROLS[harness_id]()


def interrupt_capability_for(harness_id: HarnessId, snapshot: AdapterSnapshot) -> FeatureCapability:
    """The exact-session interrupt verdict — the single source of truth for that one control.

    The active-page capability view bridges its ``controls.interrupt`` from this accessor, so
    the gate's verdict lives in exactly one place (the per-harness declarations above); when the
    verdict changes here, the active-page view follows with no second copy to edit.
    """

    return control_capabilities_for(harness_id, snapshot).interrupt


def telemetry_capabilities_for(
    harness_id: HarnessId, snapshot: AdapterSnapshot
) -> TelemetryCapabilities:
    """Build the exact-session telemetry capability set.

    The contract is the only gate: no version-string demotion; the
    observed version rides the evidence record as informational metadata only.
    """

    del snapshot  # the observed version is no longer a gate; it is informational evidence
    return _TELEMETRY[harness_id]()


__all__ = ["control_capabilities_for", "interrupt_capability_for", "telemetry_capabilities_for"]
