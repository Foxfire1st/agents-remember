from __future__ import annotations

import json

import pytest
from agents_remember.models.conversations.attachments import (
    AttachmentOperationProjection,
)
from agents_remember.models.conversations.capabilities import (
    AttachmentCapability,
    FeatureCapability,
)
from agents_remember.models.conversations.interrupts import (
    InterruptOperation,
)
from agents_remember.models.conversations.opening import (
    OpenConversationOperation,
)
from agents_remember.models.conversations.primitives import (
    OperationFingerprint,
)
from agents_remember.models.conversations.status import (
    ConversationStatus,
    ConversationTurnStatus,
    ConversationTurnWaiting,
)
from agents_remember.models.conversations.submissions import (
    CockpitQueueIdentity,
    OperationQueueItem,
    OperationQueueProjection,
)
from agents_remember.models.conversations.telemetry import (
    operation_fingerprint,
)
from agents_remember.models.conversations.withdrawals import (
    AttachmentRecoveryRef,
    FailedWithdrawalResponse,
    PendingWithdrawalRecoveryList,
    PendingWithdrawalRecoveryProjection,
    WithdrawalOperationProjection,
    WithdrawalRecovery,
    WithdrawnQueueResponse,
    WithdrawQueueRequest,
)
from pydantic import ValidationError
from test_conversation_contracts import (
    _active_ref,
    _authorization,
    _capabilities,
    _evidence,
    _fingerprint,
    _status,
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
