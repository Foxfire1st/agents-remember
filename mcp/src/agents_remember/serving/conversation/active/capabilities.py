"""Exact-session capability evidence for the active conversation surface.

Capabilities are per-session evidence, never a global harness marketing table.
A feature is ``supported``/``partial`` only with landed installed-runtime
fixture evidence through the production seam; native evidence without a passed
gate is ``unverified``; a contract the harness cannot provide is
``unavailable``. An observed runtime/helper version differing from the
capability evidence demotes the feature to ``unverified`` at read time.
"""

from __future__ import annotations

from typing import TypeVar

from agents_remember.serving.conversation.models import (
    AttachmentCapabilities,
    AttachmentCapability,
    CapabilityEvidence,
    CapabilityState,
    ControlCapabilities,
    ConversationCapabilities,
    FeatureCapability,
    HarnessId,
    HistoryCapabilities,
    LiveCapabilities,
    TelemetryCapabilities,
)
from agents_remember.serving.harness_control_models import AdapterSnapshot

_CODEX_FIXTURE = "codex-0.144.5-installed-20260718"
_CLAUDE_FIXTURE = "claude-2.1.211-installed-20260718"
_PI_FIXTURE = "pi-0.80.7-installed-20260718"

_CODEX_RUNTIME = "0.144.5"
_CLAUDE_RUNTIME = "2.1.211"
_CLAUDE_HELPER = "0.3.207"
_PI_RUNTIME = "0.80.7"
_PI_HELPER = "0.80.7"

_OBSERVED_AT = "2026-07-18T09:50:17+02:00"


def _fixture_evidence(
    runtime_version: str,
    fixture_id: str,
    *,
    helper_version: str | None = None,
) -> CapabilityEvidence:
    return CapabilityEvidence(
        runtime_version=runtime_version,
        helper_version=helper_version,
        fixture_id=fixture_id,
        observed_at=_OBSERVED_AT,
    )


def _runtime(
    state: CapabilityState,
    reason: str,
    evidence: CapabilityEvidence,
) -> FeatureCapability:
    return FeatureCapability(
        state=state,
        reason=reason,
        evidence_tier="runtime-fixture",
        evidence=evidence,
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


def _no_attachments() -> AttachmentCapabilities:
    def _none(kind: str) -> AttachmentCapability:
        return AttachmentCapability(
            state="unavailable",
            reason=f"typed {kind} staging is the L3 control leaf; no active-route attachment seam",
            evidence_tier="none",
            max_bytes=0,
            max_count=0,
            description="required",
        )

    return AttachmentCapabilities(image=_none("image"), file=_none("file"), resource=_none("resource"))


def _codex_capabilities() -> ConversationCapabilities:
    live_evidence = _fixture_evidence(_CODEX_RUNTIME, _CODEX_FIXTURE)
    return ConversationCapabilities(
        live=LiveCapabilities(
            text=_runtime(
                "supported",
                "installed fixture observed userMessage/agentMessage items through the production evidence seam",
                live_evidence,
            ),
            thinking=_adapter(
                "unverified",
                "reasoning items are schema-documented but not observed by an installed-runtime fixture",
                _CODEX_RUNTIME,
            ),
            tools=_adapter(
                "unverified",
                "command/file/MCP tool items cross the evidence seam but lack an installed-runtime fixture gate",
                _CODEX_RUNTIME,
            ),
            diffs=_adapter(
                "unverified",
                "fileChange diff items are schema-documented but not observed by an installed-runtime fixture",
                _CODEX_RUNTIME,
            ),
            interactions=_adapter(
                "unverified",
                "approval/permission/MCP interactions ride the existing adapter interaction authority",
                _CODEX_RUNTIME,
            ),
            completeness=_runtime(
                "partial",
                "live items cross in full; the evidence window is bounded and ephemeral threads have no native page",
                live_evidence,
            ),
        ),
        history=HistoryCapabilities(
            list=_unavailable("dormant thread listing is the L2 native-library leaf"),
            read=_runtime(
                "partial",
                "thread/read native pages are fixture-observed on persisted threads; ephemeral threads refuse typed",
                live_evidence,
            ),
            resume=_unavailable("exact resume/open is the L2 native-library leaf"),
            completeness=_runtime(
                "partial",
                "native history is partial: codex documents omitted persisted interactions",
                live_evidence,
            ),
            tool_completeness=_runtime(
                "partial",
                "historical tool details are lossy: codex history omits tool interactions",
                live_evidence,
            ),
        ),
        controls=ControlCapabilities(
            interrupt=_adapter(
                "unverified",
                "native turn/interrupt exists but no AR control seam; the L3 control leaf owns the gate",
                _CODEX_RUNTIME,
            ),
            steer=_unavailable("not an ordinary submit action"),
            follow_up=_unavailable("not an ordinary submit action"),
            attachments=_no_attachments(),
            policy_read=_adapter(
                "unverified",
                "read-only policy projection is the L3 control leaf",
                _CODEX_RUNTIME,
            ),
        ),
        telemetry=TelemetryCapabilities(
            context=_adapter(
                "unverified",
                "tokenUsage frames cross the evidence seam; the telemetry projection is the L3 control leaf",
                _CODEX_RUNTIME,
            ),
            usage=_adapter(
                "unverified",
                "thread/tokenUsage/updated frames are fixture-observed; the telemetry projection is L3",
                _CODEX_RUNTIME,
            ),
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
        ),
    )


def _claude_capabilities() -> ConversationCapabilities:
    reason = (
        "installed 2.1.214 mismatches the locked 2.1.211 gate; the production evidence seam "
        "remains unexercised at the locked version"
    )

    def _gated(feature: str) -> FeatureCapability:
        return _adapter("unverified", f"{feature}: {reason}", _CLAUDE_RUNTIME)

    return ConversationCapabilities(
        live=LiveCapabilities(
            text=_gated("stream-json assistant/user text"),
            thinking=_gated("thinking content blocks"),
            tools=_gated("tool_use/tool_result blocks"),
            diffs=_gated("tool diffs"),
            interactions=_gated("stdio permission and question interactions"),
            completeness=_gated("live-window completeness; claude has no native page (stream/replay-only)"),
        ),
        history=HistoryCapabilities(
            list=_unavailable("dormant session listing is the L2 native-library leaf"),
            read=_unavailable(
                "claude native pages fail closed stream/replay-only; deep history is the L2 library gate"
            ),
            resume=_unavailable("exact resume/open is the L2 native-library leaf"),
            completeness=_gated("history completeness requires the locked 2.1.211 library gate"),
            tool_completeness=_gated("historical tool completeness requires the locked 2.1.211 library gate"),
        ),
        controls=ControlCapabilities(
            interrupt=_gated("headless interrupt"),
            steer=_unavailable("not an ordinary submit action"),
            follow_up=_unavailable("not an ordinary submit action"),
            attachments=_no_attachments(),
            policy_read=_gated("read-only policy projection is the L3 control leaf"),
        ),
        telemetry=TelemetryCapabilities(
            context=_gated("context metrics"),
            usage=_gated("result usage/modelUsage frames"),
            cost=_gated("result cost frames"),
            rate_limit=_gated("rate windows"),
            compaction=_gated("compaction status"),
        ),
    )


def _pi_capabilities() -> ConversationCapabilities:
    live_evidence = _fixture_evidence(_PI_RUNTIME, _PI_FIXTURE, helper_version=_PI_HELPER)
    return ConversationCapabilities(
        live=LiveCapabilities(
            text=_runtime(
                "partial",
                "messages mint from durable entries with native identity; in-flight deltas stay buffered until completion",
                live_evidence,
            ),
            thinking=_adapter(
                "unverified",
                "thinking content parts are schema-documented but not observed by an installed-runtime fixture",
                _PI_RUNTIME,
            ),
            tools=_adapter(
                "unverified",
                "tool_execution lifecycle events are schema-documented but not observed by an installed-runtime fixture",
                _PI_RUNTIME,
            ),
            diffs=_adapter(
                "unverified",
                "tool diff output is schema-documented but not observed by an installed-runtime fixture",
                _PI_RUNTIME,
            ),
            interactions=_adapter(
                "unverified",
                "extension UI dialogs ride the existing adapter interaction authority",
                _PI_RUNTIME,
            ),
            completeness=_runtime(
                "partial",
                "message_end frames and durable entries are fixture-observed; the evidence window is bounded",
                live_evidence,
            ),
        ),
        history=HistoryCapabilities(
            list=_unavailable("dormant session listing is the L2 native-library leaf"),
            read=_runtime(
                "supported",
                "get_entries durable native pages are fixture-observed through the production seam",
                live_evidence,
            ),
            resume=_unavailable("exact session-file open is the L2 native-library leaf"),
            completeness=_runtime(
                "partial",
                "native history is partial: branch/label/custom entries surface as unknown-vendor evidence",
                live_evidence,
            ),
            tool_completeness=_adapter(
                "unverified",
                "historical tool completeness depends on tool execution fixtures not yet observed",
                _PI_RUNTIME,
            ),
        ),
        controls=ControlCapabilities(
            interrupt=_adapter(
                "unverified",
                "native rpc abort exists but no AR control seam; the L3 control leaf owns the gate",
                _PI_RUNTIME,
            ),
            steer=_unavailable("not an ordinary submit action"),
            follow_up=_unavailable("not an ordinary submit action"),
            attachments=_no_attachments(),
            policy_read=_unavailable(
                "pi has no built-in permission popup surface; extension interactions remain possible"
            ),
        ),
        telemetry=TelemetryCapabilities(
            context=_adapter(
                "unverified",
                "session stats are schema-documented but not fixture-observed; the telemetry projection is L3",
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
        ),
    )


def capabilities_for(harness_id: HarnessId, snapshot: AdapterSnapshot) -> ConversationCapabilities:
    """Build the exact-session capability set, demoted on version mismatch."""

    if harness_id == "codex":
        capabilities = _codex_capabilities()
        version = snapshot.raw.get("codexCliVersion")
    elif harness_id == "claude":
        capabilities = _claude_capabilities()
        version = snapshot.raw.get("claudeCodeVersion")
    else:
        capabilities = _pi_capabilities()
        version = None
    if not isinstance(version, str) or not version:
        return capabilities
    return ConversationCapabilities(
        live=_demote_typed(capabilities.live, version),
        history=_demote_typed(capabilities.history, version),
        controls=_demote_typed(capabilities.controls, version),
        telemetry=_demote_typed(capabilities.telemetry, version),
    )


GroupT = TypeVar("GroupT", LiveCapabilities, HistoryCapabilities, ControlCapabilities, TelemetryCapabilities)


def _demote_typed(group: GroupT, version: str) -> GroupT:  # noqa: UP047 - Python 3.11 support
    updates: dict[str, object] = {}
    for name in type(group).model_fields:
        value = getattr(group, name)
        if isinstance(value, FeatureCapability):
            updates[name] = value.for_observed_runtime(version)
    return group.model_copy(update=updates)


__all__ = ["capabilities_for"]
