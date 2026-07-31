"""Contract tests for the native-authoritative structured conversation boundary."""

from __future__ import annotations

import json
from typing import get_args

import pytest
from agents_remember.serving.conversation.models import (
    CANONICAL_TURN_STATE_BY_EVIDENCE,
    ActiveConversationRef,
    ActiveCursorBinding,
    ActiveEventCursor,
    ActiveEventResume,
    ActivePageCursor,
    AttachmentCapabilities,
    AttachmentCapability,
    AttachmentOperationProjection,
    AttachmentRecoveryRef,
    AuthorizationBinding,
    CapabilityEvidence,
    CockpitQueueIdentity,
    ContextMetricValue,
    ControlCapabilities,
    ConversationAgentRef,
    ConversationAgentStatus,
    ConversationCapabilities,
    ConversationItem,
    ConversationLibraryAgentRow,
    ConversationLibraryPage,
    ConversationLibraryPageScope,
    ConversationLibraryRow,
    ConversationProcessStatus,
    ConversationStatus,
    ConversationStatusEvidence,
    ConversationTelemetry,
    ConversationTurnOutcome,
    ConversationTurnStatus,
    ConversationTurnWaiting,
    FailedWithdrawalResponse,
    FeatureCapability,
    GapMutation,
    HistoricalConversationPage,
    HistoryCapabilities,
    ImageReferenceBlock,
    InterruptOperation,
    LibraryConversationKey,
    LibraryCursorBinding,
    LibraryListCursor,
    LibraryReadCursor,
    LiveCapabilities,
    MetricEvidence,
    MetricScope,
    OpenConversationOperation,
    OperationFingerprint,
    OperationQueueItem,
    OperationQueueProjection,
    PendingWithdrawalRecoveryList,
    PendingWithdrawalRecoveryProjection,
    ProvenanceEvidence,
    RuntimeFixtureEvidence,
    StatusFreshness,
    TelemetryCapabilities,
    TextBlock,
    WithdrawalOperationProjection,
    WithdrawalRecovery,
    WithdrawnQueueResponse,
    WithdrawQueueRequest,
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


def test_turn_status_rejects_every_irrelevant_waiting_and_terminal_product() -> None:
    valid_turns = {
        "ready": {"terminalOutcome": {"state": "completed"}},
        "working": {},
        "waiting": {"waiting": {"reason": "external dependency"}},
        "needs-input": {
            "waiting": {"reason": "permission required", "interactionId": "interaction-1"}
        },
        "settling": {"terminalOutcome": {"state": "unknown"}},
        "retrying": {},
        "compacting": {},
        "interrupted": {"terminalOutcome": {"state": "interrupted"}},
        "failed": {"terminalOutcome": {"state": "failed"}},
    }
    allowed_terminal_states = {
        "ready": {None, "completed"},
        "working": {None},
        "waiting": {None},
        "needs-input": {None},
        "settling": {None, "completed", "unknown"},
        "retrying": {None},
        "compacting": {None},
        "interrupted": {"interrupted"},
        "failed": {"failed"},
    }

    for state, state_fields in valid_turns.items():
        valid = ConversationTurnStatus.model_validate(
            {"state": state, "turnId": "turn-1", "stateSince": None, **state_fields}
        )
        assert valid.state == state

        if state not in {"waiting", "needs-input"}:
            with pytest.raises(ValidationError, match="waiting evidence"):
                ConversationTurnStatus.model_validate(
                    {
                        "state": state,
                        "turnId": "turn-1",
                        "stateSince": None,
                        **state_fields,
                        "waiting": {"reason": "irrelevant"},
                    }
                )

        for outcome in (None, "completed", "interrupted", "failed", "unknown"):
            if outcome in allowed_terminal_states[state]:
                continue
            payload = {
                "state": state,
                "turnId": "turn-1",
                "stateSince": None,
                **state_fields,
                "terminalOutcome": None if outcome is None else {"state": outcome},
            }
            with pytest.raises(ValidationError, match="terminal outcome"):
                ConversationTurnStatus.model_validate(payload)

    with pytest.raises(ValidationError, match="reason record"):
        ConversationTurnStatus(state="waiting", turn_id="turn-1", state_since=None)
    with pytest.raises(ValidationError, match="interactionId"):
        ConversationTurnStatus(
            state="needs-input",
            turn_id="turn-1",
            state_since=None,
            waiting=ConversationTurnWaiting(reason="question without exact interaction"),
        )
    with pytest.raises(ValidationError, match="cannot carry an interactionId"):
        ConversationTurnStatus(
            state="waiting",
            turn_id="turn-1",
            state_since=None,
            waiting=ConversationTurnWaiting(
                reason="not an answer control", interaction_id="interaction-1"
            ),
        )


def test_unknown_status_evidence_can_never_establish_ready() -> None:
    payload = _status().model_dump(mode="json", by_alias=True)
    payload["turn"] = {
        "state": "ready",
        "turnId": "turn-1",
        "stateSince": "2026-07-18T08:00:00Z",
        "terminalOutcome": {"state": "completed"},
    }
    payload["evidence"] = {
        "strength": "unknown",
        "origin": "silence",
        "reason": "no authoritative observation",
    }
    with pytest.raises(ValidationError, match="unknown evidence cannot establish ready"):
        ConversationStatus.model_validate(payload)

    payload["evidence"] = {
        "strength": "exact",
        "producer": "harness",
        "origin": "settled dispatch authority",
    }
    assert ConversationStatus.model_validate(payload).turn.state == "ready"


def test_capability_has_no_version_demotion_predicate() -> None:
    # 260718-CHATS-L5F R4 (developer ruling 2026-07-21): THE CONTRACT IS THE ONLY GATE. There is no
    # ``for_observed_runtime`` version-comparison predicate on FeatureCapability at all — a
    # capability is never demoted because an installed runtime differs from a fixture's version.
    assert not hasattr(FeatureCapability, "for_observed_runtime")

    capabilities = _capabilities()
    assert capabilities.live.completeness.state == "supported"
    assert capabilities.history.completeness.state == "partial"
    assert capabilities.history.tool_completeness.state == "partial"


def test_capability_state_tier_and_evidence_matrix_fails_closed() -> None:
    runtime_evidence = _evidence().model_dump(mode="json", by_alias=True)
    declared_evidence = {
        "runtimeVersion": "2.1.211",
        "helperVersion": "0.3.207",
        "observedAt": "2026-07-18T08:00:00Z",
    }
    valid_products = (
        ("supported", "runtime-fixture", runtime_evidence),
        ("partial", "runtime-fixture", runtime_evidence),
        ("unverified", "runtime-fixture", runtime_evidence),
        ("unverified", "adapter", declared_evidence),
        ("unavailable", "none", None),
        ("unavailable", "native-declaration", declared_evidence),
    )
    for state, tier, evidence in valid_products:
        capability = FeatureCapability.model_validate(
            {
                "state": state,
                "reason": f"{state} evidence",
                "evidenceTier": tier,
                "evidence": evidence,
            }
        )
        assert (capability.state, capability.evidence_tier) == (state, tier)

    invalid_products = (
        ("supported", "none", None),
        ("supported", "adapter", declared_evidence),
        ("partial", "none", None),
        ("partial", "native-declaration", declared_evidence),
        ("unverified", "none", None),
        ("unavailable", "none", declared_evidence),
        ("unavailable", "adapter", None),
        (
            "supported",
            "runtime-fixture",
            {key: value for key, value in runtime_evidence.items() if key != "fixtureId"},
        ),
    )
    for state, tier, evidence in invalid_products:
        with pytest.raises(ValidationError):
            FeatureCapability.model_validate(
                {
                    "state": state,
                    "reason": "invalid evidence product",
                    "evidenceTier": tier,
                    "evidence": evidence,
                }
            )


def test_supported_attachment_capability_requires_nonzero_exact_limits() -> None:
    with pytest.raises(ValidationError, match="MIME/count/byte"):
        AttachmentCapability(
            state="supported",
            reason="claimed without a fixture-backed limit",
            evidence_tier="runtime-fixture",
            evidence=_evidence(),
            max_bytes=0,
            max_count=0,
            description="required",
        )


def test_operation_fingerprint_is_order_stable_and_payload_sensitive() -> None:
    first = operation_fingerprint(
        "open",
        _authorization(),
        {"conversationKey": "key-1", "cwd": "/workspace/project"},
    )
    reordered = operation_fingerprint(
        "open",
        _authorization(),
        {"cwd": "/workspace/project", "conversationKey": "key-1"},
    )
    changed = operation_fingerprint(
        "open",
        _authorization(),
        {"conversationKey": "key-2", "cwd": "/workspace/project"},
    )
    other_caller = operation_fingerprint(
        "open",
        _authorization("operator-2"),
        {"conversationKey": "key-1", "cwd": "/workspace/project"},
    )

    assert first == reordered
    assert first != changed
    assert first != other_caller
    assert str(first).startswith("sha256:")
    assert len(str(first)) == len("sha256:") + 64
    with pytest.raises(ValidationError, match="canonical lowercase SHA-256"):
        OperationFingerprint("sha256:abc")
    with pytest.raises(ValidationError, match="canonical lowercase SHA-256"):
        OperationFingerprint(f"sha256:{'A' * 64}")


def test_open_and_attachment_operations_carry_semantic_revision_and_fingerprint() -> None:
    fingerprint = _fingerprint("open-attachment")
    opened = OpenConversationOperation(
        request_id="open-1",
        request_fingerprint=fingerprint,
        revision=4,
        phase="opened",
        outcome="opened",
        identity=_active_ref(),
        ar_session_id="ar-session-1",
        bridge_epoch="epoch-1",
        catalog_generation=2,
        rollback="not-needed",
    )
    attachment = AttachmentOperationProjection(
        request_id="submit-1",
        request_fingerprint=fingerprint,
        revision=3,
        phase="recoverable",
        outcome="withdrawn",
        asset_ids=("asset-1",),
        recovery_expires_at="2026-07-18T09:00:00Z",
    )
    assert opened.revision == 4
    assert attachment.revision == 3
    with pytest.raises(ValidationError, match="exact identity"):
        OpenConversationOperation(
            request_id="open-2",
            request_fingerprint=fingerprint,
            revision=1,
            phase="opened",
            outcome="opened",
            rollback="not-needed",
        )


def test_open_operation_requires_one_exact_catalog_proven_identity() -> None:
    valid = {
        "requestId": "open-1",
        "requestFingerprint": str(_fingerprint("open")),
        "revision": 4,
        "phase": "opened",
        "outcome": "opened",
        "identity": _active_ref().model_dump(mode="json", by_alias=True),
        "arSessionId": "ar-session-1",
        "bridgeEpoch": "epoch-1",
        "catalogGeneration": 2,
        "rollback": "not-needed",
    }
    assert OpenConversationOperation.model_validate(valid).outcome == "opened"

    invalid_opened = (
        {"arSessionId": "different-session"},
        {"bridgeEpoch": "different-epoch"},
        {"catalogGeneration": None},
        {"rollback": "retire-failed"},
        {"phase": "catalog-wait"},
    )
    for override in invalid_opened:
        with pytest.raises(ValidationError):
            OpenConversationOperation.model_validate({**valid, **override})

    with pytest.raises(ValidationError, match="identity tuple must be complete"):
        OpenConversationOperation.model_validate(
            {
                **valid,
                "phase": "catalog-wait",
                "outcome": "pending",
                "identity": None,
                "catalogGeneration": None,
            }
        )


def test_open_failure_identity_rollback_and_catalog_products_are_bidirectional() -> None:
    base = {
        "requestId": "open-failure-1",
        "requestFingerprint": str(_fingerprint("open-failure")),
        "revision": 4,
    }
    identity = {
        "identity": _active_ref().model_dump(mode="json", by_alias=True),
        "arSessionId": "ar-session-1",
        "bridgeEpoch": "epoch-1",
    }
    valid_products = (
        {"phase": "failed", "outcome": "unsupported", "rollback": "not-needed"},
        {"phase": "failed", "outcome": "stale-identity", "rollback": "not-needed"},
        {"phase": "failed", "outcome": "launch-failed", "rollback": "not-needed"},
        {
            **identity,
            "phase": "retiring",
            "outcome": "launch-failed",
            "rollback": "retire-pending",
        },
        {
            **identity,
            "phase": "failed",
            "outcome": "launch-failed",
            "rollback": "retired",
        },
        {
            **identity,
            "phase": "failed",
            "outcome": "launch-failed",
            "rollback": "retire-failed",
        },
        {
            **identity,
            "catalogGeneration": 3,
            "phase": "retiring",
            "outcome": "identity-mismatch",
            "rollback": "retire-pending",
        },
        {
            **identity,
            "catalogGeneration": 3,
            "phase": "failed",
            "outcome": "identity-mismatch",
            "rollback": "retired",
        },
        {
            **identity,
            "catalogGeneration": 3,
            "phase": "failed",
            "outcome": "identity-mismatch",
            "rollback": "retire-failed",
        },
    )
    for product in valid_products:
        operation = OpenConversationOperation.model_validate({**base, **product})
        assert (operation.phase, operation.outcome, operation.rollback) == (
            product["phase"],
            product["outcome"],
            product["rollback"],
        )

    invalid_products = (
        {
            **identity,
            "phase": "failed",
            "outcome": "unsupported",
            "rollback": "not-needed",
        },
        {
            **identity,
            "phase": "failed",
            "outcome": "stale-identity",
            "rollback": "not-needed",
        },
        {
            **identity,
            "phase": "failed",
            "outcome": "launch-failed",
            "rollback": "not-needed",
        },
        {"phase": "retiring", "outcome": "launch-failed", "rollback": "not-needed"},
        {
            **identity,
            "phase": "failed",
            "outcome": "identity-mismatch",
            "rollback": "retired",
        },
        {
            **identity,
            "phase": "retiring",
            "outcome": "launch-failed",
            "rollback": "retired",
        },
        {
            **identity,
            "phase": "failed",
            "outcome": "launch-failed",
            "rollback": "retire-pending",
        },
        {
            "phase": "failed",
            "outcome": "launch-failed",
            "rollback": "retired",
        },
        {
            "catalogGeneration": 3,
            "phase": "failed",
            "outcome": "unsupported",
            "rollback": "not-needed",
        },
    )
    for product in invalid_products:
        with pytest.raises(ValidationError):
            OpenConversationOperation.model_validate({**base, **product})


def test_interrupt_operation_enforces_acknowledgement_settlement_products() -> None:
    base = {
        "requestId": "interrupt-1",
        "requestFingerprint": str(_fingerprint("interrupt")),
        "revision": 1,
        "bridgeEpoch": "epoch-1",
        "turnId": "turn-1",
        "requestedAt": "2026-07-18T08:00:00Z",
    }
    valid_products = (
        ("requested", "pending", None),
        ("accepted", "pending", None),
        ("unknown", "pending", None),
        ("accepted", "interrupted", "2026-07-18T08:00:01Z"),
        ("unknown", "already-settled", "2026-07-18T08:00:01Z"),
        ("rejected", "failed", "2026-07-18T08:00:01Z"),
    )
    for acknowledgement, settlement, settled_at in valid_products:
        operation = InterruptOperation.model_validate(
            {
                **base,
                "acknowledgement": acknowledgement,
                "settlement": settlement,
                "settledAt": settled_at,
            }
        )
        assert operation.settlement == settlement

    invalid_products = (
        ("rejected", "interrupted", "2026-07-18T08:00:01Z"),
        ("requested", "already-settled", "2026-07-18T08:00:01Z"),
        ("accepted", "interrupted", None),
        ("accepted", "pending", "2026-07-18T08:00:01Z"),
    )
    for acknowledgement, settlement, settled_at in invalid_products:
        with pytest.raises(ValidationError):
            InterruptOperation.model_validate(
                {
                    **base,
                    "acknowledgement": acknowledgement,
                    "settlement": settlement,
                    "settledAt": settled_at,
                }
            )


def test_withdrawal_operation_enforces_phase_outcome_recovery_products() -> None:
    base = {
        "withdrawRequestId": "withdraw-1",
        "requestFingerprint": str(_fingerprint("withdraw-projection")),
        "operationRef": "operation-1",
        "revision": 2,
    }
    valid_products = (
        ("requested", "pending", "none", None),
        ("linearizing", "pending", "none", None),
        ("unknown", "delivery-unknown", "none", None),
        (
            "settled",
            "withdrawn",
            "recovery-unacknowledged",
            "2026-07-18T09:00:00Z",
        ),
        ("settled", "withdrawn", "acknowledged", None),
        ("settled", "already-dispatching", "none", None),
        ("settled", "epoch-mismatch", "none", None),
    )
    for phase, outcome, recovery_state, expires_at in valid_products:
        operation = WithdrawalOperationProjection.model_validate(
            {
                **base,
                "phase": phase,
                "outcome": outcome,
                "recoveryState": recovery_state,
                "recoveryExpiresAt": expires_at,
            }
        )
        assert operation.phase == phase

    invalid_products = (
        ("requested", "pending", "acknowledged", None),
        ("settled", "withdrawn", "none", None),
        ("settled", "withdrawn", "recovery-unacknowledged", None),
        ("unknown", "withdrawn", "recovery-unacknowledged", "later"),
        ("settled", "not-found", "recovery-unacknowledged", "later"),
    )
    for phase, outcome, recovery_state, expires_at in invalid_products:
        with pytest.raises(ValidationError):
            WithdrawalOperationProjection.model_validate(
                {
                    **base,
                    "phase": phase,
                    "outcome": outcome,
                    "recoveryState": recovery_state,
                    "recoveryExpiresAt": expires_at,
                }
            )


def test_attachment_operation_enforces_phase_outcome_recovery_products() -> None:
    base = {
        "requestId": "attachment-1",
        "requestFingerprint": str(_fingerprint("attachment")),
        "revision": 1,
        "assetIds": ["asset-1"],
    }
    valid_products = (
        ("staging", "pending", None),
        ("staged", "pending", None),
        ("queued", "pending", None),
        ("dispatching", "pending", None),
        ("accepted", "accepted", None),
        ("recoverable", "withdrawn", "2026-07-18T09:00:00Z"),
        ("failed", "rejected", None),
        ("failed", "failed", None),
        ("expired", "expired", None),
        ("unknown", "unknown", None),
    )
    for phase, outcome, expires_at in valid_products:
        operation = AttachmentOperationProjection.model_validate(
            {
                **base,
                "phase": phase,
                "outcome": outcome,
                "recoveryExpiresAt": expires_at,
            }
        )
        assert operation.outcome == outcome

    invalid_products = (
        ("accepted", "failed", None),
        ("recoverable", "withdrawn", None),
        ("failed", "accepted", None),
        ("staged", "pending", "irrelevant-expiry"),
    )
    for phase, outcome, expires_at in invalid_products:
        with pytest.raises(ValidationError):
            AttachmentOperationProjection.model_validate(
                {
                    **base,
                    "phase": phase,
                    "outcome": outcome,
                    "recoveryExpiresAt": expires_at,
                }
            )


def test_public_withdrawal_request_cannot_claim_server_fingerprint() -> None:
    request = WithdrawQueueRequest(
        operation_ref="operation-1",
        withdrawal_ref="withdrawal-1",
        withdraw_request_id="request-1",
    )
    assert set(request.model_dump(mode="json", by_alias=True)) == {
        "operationRef",
        "withdrawalRef",
        "withdrawRequestId",
    }
    with pytest.raises(ValidationError, match="Extra inputs"):
        WithdrawQueueRequest.model_validate(
            {
                **request.model_dump(mode="json", by_alias=True),
                "requestFingerprint": str(_fingerprint("caller-claimed")),
            }
        )


def test_queue_projection_exposes_withdrawal_identity_only_for_queued_cockpit_work() -> None:
    cockpit = CockpitQueueIdentity(
        withdrawal_ref="withdrawal-ref-1",
        redacted_preview="review the staged change",
        preview_truncated=False,
        content_digest="sha256:content",
    )
    own_row = OperationQueueItem(
        operation_ref="operation-1",
        revision=1,
        sequence=1,
        kind="prompt",
        source="cockpit",
        phase="queued",
        withdrawable=True,
        safe_label="your queued prompt",
        cockpit=cockpit,
    )
    terminal_row = OperationQueueItem(
        operation_ref="operation-2",
        revision=1,
        sequence=2,
        kind="prompt",
        source="terminal",
        phase="queued",
        withdrawable=False,
        safe_label="terminal prompt",
    )
    queue = OperationQueueProjection(
        bridge_epoch="epoch-1",
        revision=2,
        items=(own_row, terminal_row),
    )

    assert queue.items[0].cockpit is not None
    assert queue.items[1].cockpit is None
    with pytest.raises(ValidationError, match="only a queued cockpit"):
        OperationQueueItem.model_validate({**terminal_row.model_dump(), "withdrawable": True})
    with pytest.raises(ValidationError, match="private"):
        OperationQueueItem.model_validate(
            {**terminal_row.model_dump(), "cockpit": cockpit.model_dump()}
        )


def test_withdrawal_raw_recovery_exists_only_on_authoritative_withdrawn_response() -> None:
    fingerprint = _fingerprint("withdraw")
    attachment = AttachmentRecoveryRef(
        recovery_asset_ref="asset-recovery-1",
        revision=1,
        kind="image",
        name="diagram.png",
        mime_type="image/png",
        size_bytes=128,
        sha256="asset-digest",
        alt="diagram.png, image/png",
        alt_provenance="filename-mime-fallback",
        expires_at="2026-07-18T10:00:00Z",
    )
    withdrawn = WithdrawnQueueResponse(
        withdraw_request_id="withdraw-1",
        request_fingerprint=fingerprint,
        revision=2,
        operation_ref="operation-1",
        withdrawn_at="2026-07-18T09:00:00Z",
        recovery=WithdrawalRecovery(
            recovery_ref="recovery-1",
            text="exact cockpit text",
            content_digest="sha256:content",
            submitted_draft_revision=4,
            attachments=(attachment,),
        ),
    )
    failure = FailedWithdrawalResponse(
        withdraw_request_id="withdraw-2",
        request_fingerprint=fingerprint,
        revision=1,
        outcome="delivery-unknown",
        operation_ref="operation-2",
        detail="dispatch outcome is unknown",
    )
    pending = PendingWithdrawalRecoveryList(
        bridge_epoch="epoch-1",
        revision=2,
        items=(
            PendingWithdrawalRecoveryProjection(
                recovery_ref="recovery-1",
                operation_ref="operation-1",
                withdraw_request_id="withdraw-1",
                revision=2,
                recovery_expires_at="2026-07-18T10:00:00Z",
            ),
        ),
    )

    assert withdrawn.recovery.text == "exact cockpit text"
    assert "recovery" not in failure.model_dump(mode="json")
    pending_json = pending.model_dump(mode="json", by_alias=True)
    assert "text" not in json.dumps(pending_json)
    assert "attachments" not in json.dumps(pending_json)


def test_historical_page_total_is_optional_and_native_items_keep_global_ordinals() -> None:
    page = HistoricalConversationPage(
        ref=_active_ref(),
        items=(_unknown_input_item(),),
        older_cursor=LibraryReadCursor("ar-lrc1.read-1"),
        has_older=True,
        historical_capabilities=_history_capabilities(),
    )
    assert page.total_items is None
    assert page.items[0].global_ordinal == 7


def test_gap_contract_closes_and_requires_authoritative_repage() -> None:
    gap = GapMutation(
        requested_after=ActiveEventCursor("ar-aec1.event-9"),
        reason="projector-restart",
    )
    assert gap.requires_repage is True
    assert gap.close_after_event is True
    assert "reset" not in gap.model_dump(mode="json")


def test_every_metric_retains_scope_freshness_precision_and_runtime_evidence() -> None:
    metric = MetricEvidence[ContextMetricValue](
        value=ContextMetricValue(used=1024, limit=4096, percent=25),
        unit="tokens",
        origin="codex thread/tokenUsage/updated",
        scope=MetricScope(kind="conversation", safe_id="thread-digest"),
        observed_at="2026-07-18T08:00:00Z",
        freshness="fresh",
        precision="exact",
        runtime_version="0.144.5",
        fixture_id="codex-0.144.5-installed",
    )
    telemetry = ConversationTelemetry(
        revision=2,
        identity=_active_ref(),
        context=metric,
    )
    assert telemetry.context is not None
    assert telemetry.context.scope.kind == "conversation"
    assert telemetry.context.precision == "exact"
    assert telemetry.context.runtime_version == "0.144.5"


def test_runtime_fixture_is_evidence_and_cannot_enable_capabilities() -> None:
    fixture = RuntimeFixtureEvidence.model_validate(
        {
            "schema": "ar-conversation-runtime-fixture/v1",
            "fixtureId": "claude-2.1.211-installed",
            "harnessId": "claude",
            "runtimeVersion": "2.1.211",
            "helperVersion": "0.3.207",
            "capturedAt": "2026-07-18T08:00:00Z",
            "productionSeam": "ClaudeStreamJsonAdapter.start",
            "redactionPolicy": "allowlist-v1",
            "enablesCapabilities": False,
            "observations": [
                {
                    "operation": "locked-helper/list-read-resume",
                    "shape": [],
                    "result": "not-exercised",
                    "reason": "installed 2.1.211 interoperability remains unproved",
                }
            ],
        }
    )
    assert fixture.enables_capabilities is False
    assert fixture.observations[0].result == "not-exercised"
    claude_history = FeatureCapability(
        state="unverified",
        reason="installed Claude/helper history gate was not exercised",
        evidence_tier="runtime-fixture",
        evidence=CapabilityEvidence(
            runtime_version=fixture.runtime_version,
            helper_version=fixture.helper_version,
            fixture_id=fixture.fixture_id,
            observed_at=fixture.captured_at,
        ),
    )
    assert claude_history.state == "unverified"
    with pytest.raises(ValidationError):
        RuntimeFixtureEvidence.model_validate(
            {**fixture.model_dump(by_alias=True), "enablesCapabilities": True}
        )


def test_agent_ref_pins_status_vocabulary_and_additive_camel_case_shape() -> None:
    # The agent ref is additive — only agentId is required and the
    # identity fields stay absent (never null-claiming) until native evidence binds them.
    assert get_args(ConversationAgentStatus) == (
        "registered",
        "running",
        "completed",
        "interrupted",
        "failed",
        "unknown",
    )
    minimal = ConversationAgentRef.model_validate({"agentId": "agent-1"})
    assert minimal.status == "unknown"
    assert minimal.model_dump(mode="json", by_alias=True, exclude_none=True) == {
        "agentId": "agent-1",
        "status": "unknown",
    }

    full = ConversationAgentRef.model_validate(
        {
            "agentId": "agent-1",
            "agentPath": "threads/agent-1.jsonl",
            "nickname": "explorer",
            "role": "worker",
            "joinKey": "tool-use-1",
            "parentAgentId": "parent-thread-1",
            "status": "running",
        }
    )
    assert ConversationAgentRef.model_validate(full.model_dump(mode="json", by_alias=True)) == full
    for status in get_args(ConversationAgentStatus):
        assert ConversationAgentRef(agent_id="agent-1", status=status).status == status

    with pytest.raises(ValidationError):
        ConversationAgentRef.model_validate({"agentId": "agent-1", "status": "spun-up"})
    with pytest.raises(ValidationError):
        ConversationAgentRef.model_validate({"status": "running"})
    with pytest.raises(ValidationError):
        ConversationAgentRef.model_validate({"agentId": ""})
    with pytest.raises(ValidationError, match="Extra inputs"):
        ConversationAgentRef.model_validate({"agentId": "agent-1", "vendorGuess": "x"})


def test_item_agent_is_absent_by_default_and_the_pre_l7_wire_stays_identical() -> None:
    item = _unknown_input_item()
    assert item.agent is None
    wire = item.model_dump(mode="json", by_alias=True, exclude_none=True)
    assert "agent" not in wire
    # An old consumer reading the pre-multiplex wire still decodes to no agent.
    assert ConversationItem.model_validate(wire).agent is None

    with_agent = ConversationItem.model_validate(
        {**item.model_dump(), "agent": {"agentId": "agent-1", "status": "running"}}
    )
    agent_wire = with_agent.model_dump(mode="json", by_alias=True, exclude_none=True)
    assert agent_wire["agent"] == {"agentId": "agent-1", "status": "running"}
    # Every other key keeps the exact pre-multiplex shape; the agent is purely additive.
    assert {key: value for key, value in agent_wire.items() if key != "agent"} == wire
    assert ConversationItem.model_validate(agent_wire) == with_agent


def test_library_agent_row_is_additive_and_evidence_bound() -> None:
    key = LibraryConversationKey("ar-lck1.conversation-1")
    minimal = ConversationLibraryAgentRow(
        conversation_key=key,
        identity_digest="digest-1",
        title="agent abcd1234",
    )
    assert minimal.model_dump(mode="json", by_alias=True, exclude_none=True) == {
        "conversationKey": "ar-lck1.conversation-1",
        "identityDigest": "digest-1",
        "title": "agent abcd1234",
    }

    full = ConversationLibraryAgentRow.model_validate(
        {
            "conversationKey": "ar-lck1.conversation-1",
            "identityDigest": "digest-1",
            "title": "explorer",
            "agentPath": "threads/agent-1.jsonl",
            "nickname": "explorer",
            "role": "worker",
            "model": "gpt-5",
            "joinKey": "tool-use-1",
            "safeNativeIdSuffix": "abcd1234",
            "lastActivityAt": "2026-07-18T08:00:00Z",
        }
    )
    assert (
        ConversationLibraryAgentRow.model_validate(full.model_dump(mode="json", by_alias=True))
        == full
    )
    with pytest.raises(ValidationError):
        ConversationLibraryAgentRow.model_validate(
            {"conversationKey": "ar-lck1.conversation-1", "identityDigest": "digest-1"}
        )
    with pytest.raises(ValidationError, match="Extra inputs"):
        ConversationLibraryAgentRow.model_validate(
            {**minimal.model_dump(by_alias=True), "vendorGuess": "x"}
        )


def test_library_row_agents_and_page_agents_note_default_to_absent() -> None:
    key = LibraryConversationKey("ar-lck1.conversation-1")
    agent_row = ConversationLibraryAgentRow(
        conversation_key=key,
        identity_digest="digest-2",
        title="agent ef567890",
    )
    row = ConversationLibraryRow(
        conversation_key=key,
        identity_digest="digest-1",
        title="parent conversation",
        capabilities=_history_capabilities(),
    )
    assert row.agents == ()

    grouped = ConversationLibraryRow.model_validate(
        {**row.model_dump(), "agents": [agent_row.model_dump()]}
    )
    assert grouped.agents == (agent_row,)
    assert (
        ConversationLibraryRow.model_validate(grouped.model_dump(mode="json", by_alias=True))
        == grouped
    )

    page = ConversationLibraryPage(
        scope=ConversationLibraryPageScope(
            harness_id="codex",
            canonical_project_scope="/workspace/project",
            query_digest="sort=activity-desc",
        ),
        rows=(row,),
        next_cursor=None,
    )
    assert page.agents_note is None
    assert "agentsNote" not in page.model_dump(mode="json", by_alias=True, exclude_none=True)

    noted = ConversationLibraryPage.model_validate(
        {
            **page.model_dump(by_alias=True, exclude={"agents_note"}),
            "agentsNote": "installed runtime does not expose sub-agent threads",
        }
    )
    assert noted.agents_note == "installed runtime does not expose sub-agent threads"
