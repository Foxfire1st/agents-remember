"""Contract tests for the native-authoritative structured conversation boundary."""

from __future__ import annotations

import pytest
from agents_remember.models.conversations.capabilities import (
    AttachmentCapabilities,
    AttachmentCapability,
    CapabilityEvidence,
    ControlCapabilities,
    ConversationCapabilities,
    FeatureCapability,
    HistoryCapabilities,
    LiveCapabilities,
    TelemetryCapabilities,
)
from agents_remember.models.conversations.content import (
    ConversationItem,
    ImageReferenceBlock,
    TextBlock,
)
from agents_remember.models.conversations.cursors import (
    ActiveCursorBinding,
    ActiveEventCursor,
    ActiveEventResume,
    ActivePageCursor,
    LibraryCursorBinding,
    LibraryListCursor,
    LibraryReadCursor,
)
from agents_remember.models.conversations.identity import (
    ActiveConversationRef,
    AuthorizationBinding,
    ProvenanceEvidence,
)
from agents_remember.models.conversations.primitives import (
    OperationFingerprint,
)
from agents_remember.models.conversations.status import (
    CANONICAL_TURN_STATE_BY_EVIDENCE,
    ConversationProcessStatus,
    ConversationStatus,
    ConversationStatusEvidence,
    ConversationTurnOutcome,
    ConversationTurnStatus,
    StatusFreshness,
)
from agents_remember.models.conversations.telemetry import (
    operation_fingerprint,
)
from pydantic import ValidationError


def _authorization(principal: str = "operator-1") -> AuthorizationBinding:
    return AuthorizationBinding(principal_id=principal, tenant_id="local")


def _fingerprint(kind: str = "test") -> OperationFingerprint:
    return operation_fingerprint(kind, _authorization(), {"request": kind})


def _active_ref() -> ActiveConversationRef:
    return ActiveConversationRef(
        harness_id="codex",
        vendor_conversation_id="thread-1",
        project_scope="/workspace/project",
        identity_digest="identity-v1",
        ar_session_id="ar-session-1",
        bridge_epoch="epoch-1",
    )


def _evidence() -> CapabilityEvidence:
    return CapabilityEvidence(
        runtime_version="0.144.5",
        fixture_id="codex-0.144.5-installed",
        observed_at="2026-07-18T08:00:00Z",
    )


def _feature(
    state: str = "supported",
    *,
    reason: str = "installed runtime fixture passed",
) -> FeatureCapability:
    return FeatureCapability.model_validate(
        {
            "state": state,
            "reason": reason,
            "evidenceTier": "runtime-fixture" if state != "unavailable" else "none",
            "evidence": _evidence().model_dump(by_alias=True) if state != "unavailable" else None,
        }
    )


def _history_capabilities() -> HistoryCapabilities:
    return HistoryCapabilities(
        list=_feature(),
        read=_feature(),
        resume=_feature(),
        completeness=_feature("partial", reason="native history is partial"),
        tool_completeness=_feature("partial", reason="historical tool details are lossy"),
    )


def _capabilities() -> ConversationCapabilities:
    unsupported_attachment = AttachmentCapability(
        state="unavailable",
        reason="runtime seam does not expose this attachment type",
        evidence_tier="none",
        max_bytes=0,
        max_count=0,
        description="required",
    )
    return ConversationCapabilities(
        live=LiveCapabilities(
            text=_feature(),
            thinking=_feature(),
            tools=_feature(),
            diffs=_feature(),
            interactions=_feature(),
            completeness=_feature(),
        ),
        history=_history_capabilities(),
        controls=ControlCapabilities(
            interrupt=_feature(),
            steer=_feature("unavailable", reason="not an ordinary submit action"),
            follow_up=_feature("unavailable", reason="not an ordinary submit action"),
            attachments=AttachmentCapabilities(
                image=unsupported_attachment,
                file=unsupported_attachment,
                resource=unsupported_attachment,
            ),
            policy_read=_feature(),
        ),
        telemetry=TelemetryCapabilities(
            context=_feature(),
            usage=_feature(),
            cost=_feature("unavailable", reason="native runtime does not report cost"),
            rate_limit=_feature(),
            compaction=_feature(),
        ),
    )


def _unknown_input_item() -> ConversationItem:
    return ConversationItem(
        item_id="native-message-1",
        revision=1,
        global_ordinal=7,
        lane="unknown-input",
        source="native-history",
        provenance=ProvenanceEvidence(
            strength="native-only",
            origin="codex thread/read",
            reason="native role is user but producer correlation is unavailable",
        ),
        role="user",
        kind="message",
        phase="completed",
        blocks=(TextBlock(block_id="block-1", text="redacted fixture text"),),
    )


def _status() -> ConversationStatus:
    return ConversationStatus(
        identity=_active_ref(),
        revision=3,
        observed_at="2026-07-18T08:00:00Z",
        freshness=StatusFreshness(
            state="fresh",
            last_evidence_at="2026-07-18T08:00:00Z",
            age_ms=0,
            stale_after_ms=10_000,
            observation_bound="native event",
        ),
        process=ConversationProcessStatus(state="connected", generation="process-1"),
        turn=ConversationTurnStatus(
            state="interrupted",
            turn_id="turn-1",
            state_since="2026-07-18T07:59:00Z",
            terminal_outcome=ConversationTurnOutcome(
                state="interrupted", operation_ref="interrupt-1"
            ),
        ),
        evidence=ConversationStatusEvidence(
            strength="exact",
            producer="harness",
            origin="codex turn/completed",
            adapter_revision=9,
            native_event_cursor=ActiveEventCursor("ar-aec1.event-9"),
        ),
    )


def test_cursor_families_are_runtime_non_interchangeable() -> None:
    assert ActivePageCursor("ar-apc1.page-1").root == "ar-apc1.page-1"
    assert ActiveEventCursor("ar-aec1.event-1").root == "ar-aec1.event-1"
    assert LibraryListCursor("ar-llc1.list-1").root == "ar-llc1.list-1"
    assert LibraryReadCursor("ar-lrc1.read-1").root == "ar-lrc1.read-1"

    with pytest.raises(ValidationError, match="purpose prefix"):
        ActivePageCursor("ar-aec1.event-1")
    with pytest.raises(ValidationError, match="purpose prefix"):
        LibraryReadCursor("ar-llc1.list-1")


def test_cursor_bindings_preserve_authorization_identity_scope_and_purpose() -> None:
    active = ActiveCursorBinding(
        authorization=_authorization(),
        purpose="active-event",
        identity=_active_ref(),
        projector_generation="projector-2",
    )
    library = LibraryCursorBinding.model_validate(
        {
            "scope": {
                "authorization": {"principalId": "operator-1", "tenantId": "local"},
                "harnessId": "codex",
                "canonicalProjectScope": "/workspace/project",
                "queryDigest": "sort=activity-desc",
            },
            "purpose": "library-read",
            "catalogGeneration": 4,
        }
    )

    assert active.identity.bridge_epoch == "epoch-1"
    assert library.scope.authorization == active.authorization
    assert library.scope.query_digest == "sort=activity-desc"
    with pytest.raises(ValidationError):
        ActiveCursorBinding.model_validate({**active.model_dump(), "purpose": "library-read"})


def test_dual_sse_resume_requires_the_same_active_event_cursor() -> None:
    cursor = ActiveEventCursor("ar-aec1.event-9")
    assert ActiveEventResume(after=cursor).cursor == cursor
    assert ActiveEventResume(last_event_id=cursor).cursor == cursor
    assert ActiveEventResume(after=cursor, last_event_id=cursor).cursor == cursor
    with pytest.raises(ValidationError, match="cursor-conflict"):
        ActiveEventResume(
            after=cursor,
            last_event_id=ActiveEventCursor("ar-aec1.event-10"),
        )
    with pytest.raises(ValidationError, match="required"):
        ActiveEventResume()


def test_stable_identity_revision_ordinal_and_unknown_input_survive_wire_round_trip() -> None:
    item = _unknown_input_item()
    decoded = ConversationItem.model_validate(item.model_dump(mode="json", by_alias=True))

    assert decoded.item_id == "native-message-1"
    assert decoded.revision == 1
    assert decoded.global_ordinal == 7
    assert decoded.lane == "unknown-input"
    assert decoded.provenance.strength == "native-only"


def test_unknown_input_and_controlled_terminal_provenance_fail_closed() -> None:
    item = _unknown_input_item()
    with pytest.raises(ValidationError, match="exact lane/producer/strength"):
        ConversationItem.model_validate({**item.model_dump(), "source": "cockpit-composer"})
    with pytest.raises(ValidationError, match="cannot claim a producer"):
        ConversationItem.model_validate(
            {
                **item.model_dump(),
                "provenance": {"strength": "unknown", "origin": "history", "producer": "operator"},
            }
        )
    with pytest.raises(ValidationError):
        ConversationItem.model_validate({**item.model_dump(), "source": "terminal-legacy"})
    with pytest.raises(ValidationError, match="exact lane/producer/strength"):
        ConversationItem.model_validate(
            {
                **item.model_dump(),
                "lane": "operator",
                "source": "terminal-controlled",
                "provenance": {"strength": "exact", "origin": "bridge"},
            }
        )


def test_unique_item_sources_enforce_the_exact_authority_cross_product() -> None:
    authorities = (
        ("cockpit-composer", "operator", "operator", ("exact", "correlated")),
        ("durable-inbox", "agent-bus", "agent-bus", ("exact", "correlated")),
        (
            "terminal-controlled",
            "operator",
            "controlled-terminal",
            ("exact", "correlated"),
        ),
        ("interaction-response", "interaction", "operator", ("exact",)),
        ("control-authority", "control", "system", ("exact", "correlated")),
    )
    base = _unknown_input_item().model_dump()

    for source, lane, producer, strengths in authorities:
        for strength in strengths:
            item = ConversationItem.model_validate(
                {
                    **base,
                    "lane": lane,
                    "source": source,
                    "provenance": {
                        "strength": strength,
                        "origin": f"{source} authority",
                        "producer": producer,
                    },
                }
            )
            assert (item.source, item.lane, item.provenance.producer) == (
                source,
                lane,
                producer,
            )

        invalid_siblings = (
            {"lane": "harness"},
            {"producer": "harness"},
            {"strength": "native-only"},
        )
        for override in invalid_siblings:
            provenance = {
                "strength": strengths[0],
                "origin": f"{source} authority",
                "producer": producer,
            }
            provenance.update({key: value for key, value in override.items() if key != "lane"})
            with pytest.raises(ValidationError, match="exact lane/producer/strength"):
                ConversationItem.model_validate(
                    {
                        **base,
                        "lane": override.get("lane", lane),
                        "source": source,
                        "provenance": provenance,
                    }
                )


def test_native_and_harness_sources_remain_explicitly_polymorphic() -> None:
    base = _unknown_input_item().model_dump()
    variants = (
        ("harness-live", "harness", "harness", "exact"),
        ("harness-replay", "operator", "operator", "correlated"),
        ("native-history", "agent-bus", "agent-bus", "correlated"),
        ("native-history", "harness", "harness", "native-only"),
    )
    for source, lane, producer, strength in variants:
        item = ConversationItem.model_validate(
            {
                **base,
                "source": source,
                "lane": lane,
                "provenance": {
                    "strength": strength,
                    "origin": "native projector",
                    "producer": producer,
                },
            }
        )
        assert item.provenance.strength == strength


def test_image_reference_requires_accessible_label_and_provenance() -> None:
    block = ImageReferenceBlock(
        block_id="image-1",
        asset_id="asset-1",
        alt="diagram.png, image/png",
        alt_provenance="filename-mime-fallback",
        mime_type="image/png",
    )
    assert block.alt_provenance == "filename-mime-fallback"
    with pytest.raises(ValidationError):
        ImageReferenceBlock(
            block_id="image-1",
            asset_id="asset-1",
            alt="",
            alt_provenance="filename-mime-fallback",
            mime_type="image/png",
        )


def test_canonical_status_mapping_and_terminal_evidence_are_fixed() -> None:
    assert CANONICAL_TURN_STATE_BY_EVIDENCE == {
        "settled-dispatchable": "ready",
        "active-native-turn": "working",
        "declared-external-wait": "waiting",
        "pending-interaction": "needs-input",
        "native-end-reconciling": "settling",
        "native-retry": "retrying",
        "native-compaction": "compacting",
        "interrupt-settled": "interrupted",
        "turn-failed": "failed",
    }
    status = _status()
    assert status.revision == 3
    assert status.turn.terminal_outcome is not None
    assert status.turn.terminal_outcome.operation_ref == "interrupt-1"
    with pytest.raises(ValidationError, match="terminal outcome"):
        ConversationTurnStatus(
            state="interrupted",
            turn_id="turn-1",
            state_since=None,
        )
